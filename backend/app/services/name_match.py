"""Name-matching pipeline for Seraphim (S22-F02).

FROZEN PUBLIC API CONTRACT
==========================
This module exports a pipeline that resolves a raw name token to a Contact id.
The signatures below are frozen; consumers (S06/S10/S13/S18) may depend on them
without negotiation.  The master §2.6 lists a narrower signature
``match_name(raw_name, event_id=None, source='manual')``; the spec §4.3/4.2.6
richer db-passing form is authoritative for S22.  All params are
keyword-compatible so master can be patched to add db without breaking callers.

Stage order (alias=3, fuzzy=4, claude=4b — per spec §4.2):
  1. normalize_name(raw) -> str                      [pure, sync]
  2. lookup_alias(normalized, db) -> MatchCandidate?  [stage 3 in spec §4.2.3]
  3. lookup_deterministic(normalized, db, ...)        [fuzzy, stage 4 in spec §4.2.4]
  4. lookup_claude(raw_name, normalized, candidates, db) -> MatchCandidate?

Exported functions (keyword-compatible frozen signatures):
  normalize_name(raw: str) -> str
  async lookup_alias(normalized: str, db: AsyncSession) -> Optional[MatchCandidate]
  async lookup_deterministic(normalized: str, db: AsyncSession,
                             min_score: float = 0.88) -> list[MatchCandidate]
  async lookup_claude(raw_name: str, normalized: str,
                      candidates: list[MatchCandidate],
                      db: AsyncSession) -> Optional[MatchCandidate]
  async match_name(raw_name: str, db: AsyncSession, source: str,
                   event_id: Optional[int] = None,
                   community_report_id: Optional[int] = None,
                   payload: Optional[dict] = None,
                   auto_enqueue: bool = True) -> NameMatchResult
  async match_name_batch(names: list[str], db: AsyncSession, source: str,
                         event_id: Optional[int] = None,
                         community_report_id: Optional[int] = None) -> list[NameMatchResult]
  async teach_alias_from_resolution(db: AsyncSession, review_queue_id: int,
                                    contact_id: int,
                                    alias_text: Optional[str] = None,
                                    actor_id: Optional[int] = None) -> NameAlias

TypedDicts (frozen):
  MatchCandidate: contact_id, first_name, last_name, nickname, score, method
  NameMatchResult: raw_name, normalized, outcome, contact_id, score, method,
                   candidates, review_queue_id

Circular-import rule: this module imports ONLY from app.models, app.config,
app.database, jellyfish, anthropic, and sqlalchemy.  It MUST NEVER import from
any router module (CN-25).

Injectable Claude client factory
---------------------------------
The module exposes ``set_claude_client_factory(fn)`` so tests can inject a fake
without hitting the network.  The default factory returns
``anthropic.AsyncAnthropic()``.  lookup_claude gates on:
  - os.environ.get('ENVIRONMENT') == 'test'  -> skip (return None)
  - ANTHROPIC_API_KEY unset/empty           -> skip
  - dynamic_settings.get_bool('name_match.claude_enabled', False) is False -> skip
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import unicodedata
from typing import Any, Callable, Optional

import jellyfish
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import engine as _app_engine
from app.models import Contact, NameAlias, NameMatchReviewQueue, utc_now

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TypedDicts — FROZEN contract (do not change field names or types)
# ---------------------------------------------------------------------------


class MatchCandidate(TypedDict):
    """One scored contact candidate returned by a lookup stage."""
    contact_id: int
    first_name: str
    last_name: str
    nickname: Optional[str]
    score: float
    method: str   # alias | fuzzy_forward | fuzzy_reversed | fuzzy_nickname


class NameMatchResult(TypedDict):
    """Final result returned by match_name / match_name_batch."""
    raw_name: str
    normalized: str
    outcome: str          # SINGLE | AMBIGUOUS | UNMATCHED
    contact_id: Optional[int]
    score: Optional[float]
    method: Optional[str]
    candidates: list[MatchCandidate]
    review_queue_id: Optional[int]


# ---------------------------------------------------------------------------
# Honorific / title strip list  (spec §4.2.2)
# ---------------------------------------------------------------------------

_HONORIFICS: frozenset[str] = frozenset({
    "tita", "kuya", "ate", "bro", "sis",
    "pastor", "ptr", "rev", "dtr", "dr",
    "mr", "mrs", "ms", "sir", "ma",
})

# Suffix tokens to strip (Ptr. Jr. Sr. II III IV etc.)
_SUFFIX_PATTERN = re.compile(
    r"\b(jr|sr|ii|iii|iv|v|ptr|rev|dr|mr|mrs|ms|dtr)\b\.?",
    re.IGNORECASE,
)

# Nickname map for scoring  (expand as needed)
_NICKNAMES: dict[str, list[str]] = {
    "elizabeth": ["liz", "beth", "eliza", "eli"],
    "grace": ["gracie"],
    "maria": ["mary", "marites", "mario", "mar"],
    "josefina": ["josie", "joey"],
    "jose": ["joe", "joey", "pepe"],
    "rodolfo": ["rodel", "rudy", "dolfo"],
    "rodel": ["rodolfo", "rudy"],
    "nino": ["nino", "ninyo"],
    "angelina": ["angel", "geline"],
    "marichu": ["chu", "marichu"],
}


def _build_reverse_nick() -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {}
    for canonical, nicks in _NICKNAMES.items():
        for n in nicks:
            rev.setdefault(n, []).append(canonical)
    return rev


_REVERSE_NICK = _build_reverse_nick()


# ---------------------------------------------------------------------------
# normalize_name  (spec §4.2.2) — pure, sync, not async
# ---------------------------------------------------------------------------


def normalize_name(raw: str) -> str:
    """Normalize a raw name token to a lowercase, diacritic-stripped, honorific-removed string.

    Spec §4.2.2 vectors:
      "Ptr. Rodel Aquino Jr."  -> "rodel aquino"
      "EVANGELISTA, Guada"     -> "evangelista guada"
      "Tita Cely"              -> "cely"
      "Marichu P."             -> "marichu"
      diacritics (Niño)        -> "nino"
      honorific list: tita/kuya/ate/bro/sis/pastor/ptr/rev/dtr/dr/mr/mrs/ms/sir/ma
    """
    if not raw or not raw.strip():
        return ""

    # 1. Strip diacritics / accent marks via NFD decomposition
    nfd = unicodedata.normalize("NFD", raw)
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")

    # 2. Lowercase
    text = stripped.lower()

    # 3. Remove suffix tokens (Jr., Sr., Ptr., Rev., etc.) before comma handling
    text = _SUFFIX_PATTERN.sub("", text)

    # 4. Handle "LASTNAME, Firstname" -> "firstname lastname" (comma-delimited inversion)
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        # parts[0] is last name, parts[1] is first name
        text = " ".join(reversed(parts))

    # 5. Remove punctuation except spaces
    text = re.sub(r"[^\w\s]", " ", text)

    # 6. Tokenize and strip honorifics + single-character tokens (middle initials)
    tokens = text.split()
    filtered: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        # Skip honorifics
        if tok in _HONORIFICS:
            continue
        # Skip single-char tokens (middle initials like "P." already stripped to "p")
        if len(tok) == 1:
            continue
        filtered.append(tok)

    return " ".join(filtered)


# ---------------------------------------------------------------------------
# Dialect helper (mirrors bulk_service pattern)
# ---------------------------------------------------------------------------


def _dialect() -> str:
    try:
        return _app_engine.dialect.name
    except Exception:
        return "sqlite"


# ---------------------------------------------------------------------------
# Stage 3: Alias lookup
# ---------------------------------------------------------------------------


async def lookup_alias(
    normalized: str,
    db: AsyncSession,
) -> Optional[MatchCandidate]:
    """Return a MatchCandidate if normalized exactly matches a NameAlias row.

    This is stage 3 in the pipeline (alias-first, spec §4.2.3).
    Returns None if no alias found.
    """
    if not normalized:
        return None

    stmt = (
        select(NameAlias, Contact)
        .join(Contact, Contact.id == NameAlias.contact_id)
        .where(NameAlias.alias_text == normalized)
        .where(Contact.is_deleted.is_(False))
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None

    alias, contact = row
    return MatchCandidate(
        contact_id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        nickname=contact.nickname,
        score=1.0,
        method="alias",
    )


# ---------------------------------------------------------------------------
# Stage 4: Deterministic fuzzy lookup  (spec §4.2.4)
# ---------------------------------------------------------------------------

_FUZZY_MAX_ROWS = 200  # surname prefix LIKE cap


async def lookup_deterministic(
    normalized: str,
    db: AsyncSession,
    min_score: float = 0.88,
) -> list[MatchCandidate]:
    """Return candidates scored by Jaro-Winkler similarity.

    Steps:
      1. Extract first surname token from normalized (used for a LIKE prefix DB query).
      2. Fetch up to 200 contacts whose last_name starts with the first 3 chars
         of the first token.
      3. Score forward (normalized vs "first last"), reversed ("last first"),
         and nickname variants.
      4. Apply ambiguity rules (spec §4.2.3):
         - top score >= min_score AND (next score < min_score OR gap >= 0.02)
           -> return [top]
         - otherwise return all >= min_score sorted desc
    Uses jellyfish.jaro_winkler_similarity (NOT difflib).
    """
    if not normalized:
        return []

    tokens = normalized.split()
    if not tokens:
        return []

    # Use the last token as surname anchor (handles "first last" order after normalization)
    # For a single token, use it for both directions
    surname_anchor = tokens[-1] if len(tokens) > 1 else tokens[0]
    prefix = surname_anchor[:3].lower()

    # Surname LIKE prefix query — capped at _FUZZY_MAX_ROWS
    stmt = (
        select(Contact)
        .where(Contact.is_deleted.is_(False))
        .where(func.lower(Contact.last_name).like(f"{prefix}%"))
        .limit(_FUZZY_MAX_ROWS)
    )
    rows = (await db.execute(stmt)).scalars().all()

    # If no rows, the input may be surname-first ("Cruz Juan") — try first token as surname.
    if not rows and len(tokens) >= 2:
        first_prefix = tokens[0][:3].lower()
        if first_prefix != prefix:
            stmt2 = (
                select(Contact)
                .where(Contact.is_deleted.is_(False))
                .where(func.lower(Contact.last_name).like(f"{first_prefix}%"))
                .limit(_FUZZY_MAX_ROWS)
            )
            rows = (await db.execute(stmt2)).scalars().all()

    # Final fallback: try first-name prefix (single-token or unusual formats)
    if not rows and len(tokens) >= 1:
        first_prefix = tokens[0][:3].lower()
        if first_prefix != prefix:
            stmt3 = (
                select(Contact)
                .where(Contact.is_deleted.is_(False))
                .where(func.lower(Contact.first_name).like(f"{first_prefix}%"))
                .limit(_FUZZY_MAX_ROWS)
            )
            rows = (await db.execute(stmt3)).scalars().all()

    if not rows:
        return []

    scored: list[MatchCandidate] = []

    for contact in rows:
        best_score = 0.0
        best_method = "fuzzy_forward"

        # Canonical forms to compare against normalized input
        first = (contact.first_name or "").lower()
        last = (contact.last_name or "").lower()
        nick = (contact.nickname or "").lower() if contact.nickname else None

        full_forward = f"{first} {last}".strip()
        full_reversed = f"{last} {first}".strip()

        # Forward score
        s_forward = jellyfish.jaro_winkler_similarity(normalized, full_forward)
        if s_forward > best_score:
            best_score = s_forward
            best_method = "fuzzy_forward"

        # Reversed score (handles input in "Last First" or surname-first format)
        s_reversed = jellyfish.jaro_winkler_similarity(normalized, full_reversed)
        if s_reversed > best_score:
            best_score = s_reversed
            best_method = "fuzzy_reversed"

        # Nickname score: if contact has a nickname, compare normalized vs "nick last"
        if nick:
            nick_form = f"{nick} {last}".strip()
            s_nick = jellyfish.jaro_winkler_similarity(normalized, nick_form)
            if s_nick > best_score:
                best_score = s_nick
                best_method = "fuzzy_nickname"

        # Also try input tokens against nickname expansions
        if tokens:
            first_tok = tokens[0]
            expanded_nicks = _NICKNAMES.get(first_tok, []) + _REVERSE_NICK.get(first_tok, [])
            for exp_nick in expanded_nicks:
                if len(tokens) > 1:
                    exp_form_f = f"{exp_nick} {' '.join(tokens[1:])}".strip()
                else:
                    exp_form_f = exp_nick
                s_exp = jellyfish.jaro_winkler_similarity(full_forward, exp_form_f)
                if s_exp > best_score:
                    best_score = s_exp
                    best_method = "fuzzy_nickname"

        if best_score >= min_score:
            scored.append(MatchCandidate(
                contact_id=contact.id,
                first_name=contact.first_name,
                last_name=contact.last_name,
                nickname=contact.nickname,
                score=round(best_score, 6),
                method=best_method,
            ))

    if not scored:
        return []

    # Sort descending by score
    scored.sort(key=lambda c: c["score"], reverse=True)

    # Ambiguity rules (spec §4.2.3):
    # top score >= min_score AND gap to next >= 0.02 -> unambiguous single result
    if len(scored) == 1:
        return scored

    top = scored[0]["score"]
    second = scored[1]["score"]
    gap = top - second
    if top >= min_score and (second < min_score or gap >= 0.02):
        return [scored[0]]

    # Ambiguous: return all that cleared min_score
    return scored


# ---------------------------------------------------------------------------
# Claude injectable factory
# ---------------------------------------------------------------------------

_claude_semaphore = asyncio.Semaphore(5)


def _default_claude_factory():
    """Default factory: returns a fresh AsyncAnthropic client."""
    import anthropic  # lazy import so module loads even when anthropic not installed
    return anthropic.AsyncAnthropic()


_claude_client_factory: Callable[[], Any] = _default_claude_factory


def set_claude_client_factory(fn: Callable[[], Any]) -> None:
    """Replace the Claude client factory.  Call in tests to inject a fake client."""
    global _claude_client_factory
    _claude_client_factory = fn


# Claude prompt template (spec §4.2.5 Filipino-localized)
_CLAUDE_PROMPT_TEMPLATE = """\
You are helping a Filipino church attendance system match a raw name to the correct contact record.

Raw name submitted: "{raw_name}"
Normalized form: "{normalized}"

Here are the candidate contact records (JSON):
{candidates_json}

Instructions:
- Return a JSON object with a single key "contact_id" whose value is an integer.
- Choose the candidate whose name best matches the raw name, considering Filipino naming conventions (reversed surnames, nicknames like Tita/Kuya/Ate, diacritics, etc.).
- If you are not confident, or if no candidate is a good match, set "contact_id" to null.
- NEVER return a contact_id that is not in the candidate list above.
- NEVER guess. Returning null is always the correct answer when unsure.
- Example valid response: {{"contact_id": 42}}
- Example when unsure: {{"contact_id": null}}
"""


async def lookup_claude(
    raw_name: str,
    normalized: str,
    candidates: list[MatchCandidate],
    db: AsyncSession,
) -> Optional[MatchCandidate]:
    """Ask Claude to pick the best candidate from a list.

    Stub gate — returns None immediately (without constructing a client) when:
      - ENVIRONMENT env var == 'test'
      - ANTHROPIC_API_KEY is unset or empty
      - dynamic_settings.get_bool('name_match.claude_enabled', False) is False

    Model is configurable via dynamic_settings.get_str('name_match.claude_model',
    'claude-sonnet-4-6').  Default is 'claude-sonnet-4-6' per sprint constraint.

    On anthropic.APIError / APIStatusError: logs warning, returns None.
    Validates returned contact_id is in candidates (rejects hallucinations).
    Rate-limited to 5 concurrent calls via module-level asyncio.Semaphore(5).
    """
    import anthropic  # lazy; will fail gracefully below if not installed

    # --- stub gate ---
    if os.environ.get("ENVIRONMENT") == "test":
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    if not dynamic_settings.get_bool("name_match.claude_enabled", False):
        return None

    if not candidates:
        return None

    model = dynamic_settings.get_str("name_match.claude_model", "claude-sonnet-4-6")

    candidate_ids = {c["contact_id"] for c in candidates}
    candidates_summary = [
        {
            "contact_id": c["contact_id"],
            "first_name": c["first_name"],
            "last_name": c["last_name"],
            "nickname": c["nickname"],
            "score": c["score"],
        }
        for c in candidates
    ]
    candidates_json = json.dumps(candidates_summary, ensure_ascii=False, indent=2)

    prompt = _CLAUDE_PROMPT_TEMPLATE.format(
        raw_name=raw_name,
        normalized=normalized,
        candidates_json=candidates_json,
    )

    async with _claude_semaphore:
        try:
            client = _claude_client_factory()
            response = await client.messages.create(
                model=model,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Parse JSON from response
            # Claude may wrap in markdown code fence — strip it
            if "```" in text:
                # Extract content between first ``` and last ```
                parts = text.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        text = part
                        break

            parsed = json.loads(text)
            contact_id = parsed.get("contact_id")

            if contact_id is None:
                return None

            contact_id = int(contact_id)
            if contact_id not in candidate_ids:
                logger.warning(
                    "name_match: Claude returned hallucinated contact_id=%d "
                    "(not in candidates=%s) for raw_name=%r — falling through",
                    contact_id,
                    sorted(candidate_ids),
                    raw_name,
                )
                return None

            # Find the candidate
            for c in candidates:
                if c["contact_id"] == contact_id:
                    return MatchCandidate(
                        contact_id=c["contact_id"],
                        first_name=c["first_name"],
                        last_name=c["last_name"],
                        nickname=c["nickname"],
                        score=c["score"],
                        method="claude",
                    )

        except anthropic.APIError as exc:
            logger.warning("name_match: Claude APIError for %r: %s", raw_name, exc)
            return None
        except anthropic.APIStatusError as exc:
            logger.warning(
                "name_match: Claude APIStatusError %s for %r: %s",
                exc.status_code,
                raw_name,
                exc.message,
            )
            return None
        except (json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "name_match: Claude response parse error for %r: %s", raw_name, exc
            )
            return None
        except Exception as exc:
            logger.warning(
                "name_match: unexpected Claude error for %r: %s", raw_name, exc
            )
            return None

    return None


# ---------------------------------------------------------------------------
# Enqueue to review queue
# ---------------------------------------------------------------------------


async def _enqueue_review(
    db: AsyncSession,
    raw_name: str,
    event_id: Optional[int],
    community_report_id: Optional[int],
    candidates: list[MatchCandidate],
) -> Optional[int]:
    """Insert a NameMatchReviewQueue row and return its id.

    Returns None if the insert fails (non-fatal — caller still returns result).
    """
    try:
        top_candidate_id: Optional[int] = None
        top_score: Optional[float] = None
        if candidates:
            top_candidate_id = candidates[0]["contact_id"]
            top_score = candidates[0]["score"]

        queue_row = NameMatchReviewQueue(
            community_report_id=community_report_id,
            event_id=event_id,
            raw_name=raw_name,
            candidate_contact_id=top_candidate_id,
            score=top_score,
            status="pending",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(queue_row)
        await db.flush()
        queue_id = queue_row.id
        await db.commit()
        return queue_id
    except Exception as exc:
        logger.warning("name_match: failed to enqueue review for %r: %s", raw_name, exc)
        try:
            await db.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# match_name — main pipeline entry point  (spec §4.2.6)
# ---------------------------------------------------------------------------


async def match_name(
    raw_name: str,
    db: AsyncSession,
    source: str,
    event_id: Optional[int] = None,
    community_report_id: Optional[int] = None,
    payload: Optional[dict] = None,
    auto_enqueue: bool = True,
) -> NameMatchResult:
    """4-stage name-matching pipeline.

    Stage order:
      1. normalize_name(raw_name)
      2. lookup_alias(normalized, db)             — exact alias match
      3. lookup_deterministic(normalized, db)     — Jaro-Winkler fuzzy
      4. lookup_claude(raw_name, normalized, ...)  — AI tiebreaker

    Outcomes:
      SINGLE    — one confident match found; contact_id is set
      AMBIGUOUS — multiple candidates but no clear winner; enqueued for review
      UNMATCHED — no candidates cleared threshold; enqueued for review

    Never silently returns contact id=1 or any default-magic contact.
    """
    normalized = normalize_name(raw_name)

    # Stage 3 alias lookup (alias-first per spec §4.2.3)
    alias_hit = await lookup_alias(normalized, db)
    if alias_hit is not None:
        return NameMatchResult(
            raw_name=raw_name,
            normalized=normalized,
            outcome="SINGLE",
            contact_id=alias_hit["contact_id"],
            score=alias_hit["score"],
            method="alias",
            candidates=[alias_hit],
            review_queue_id=None,
        )

    # Stage 4a: deterministic fuzzy
    candidates = await lookup_deterministic(normalized, db)

    if len(candidates) == 1:
        # Unambiguous fuzzy match
        return NameMatchResult(
            raw_name=raw_name,
            normalized=normalized,
            outcome="SINGLE",
            contact_id=candidates[0]["contact_id"],
            score=candidates[0]["score"],
            method=candidates[0]["method"],
            candidates=candidates,
            review_queue_id=None,
        )

    if len(candidates) > 1:
        # Stage 4b: use Claude as tiebreaker
        claude_hit = await lookup_claude(raw_name, normalized, candidates, db)
        if claude_hit is not None:
            return NameMatchResult(
                raw_name=raw_name,
                normalized=normalized,
                outcome="SINGLE",
                contact_id=claude_hit["contact_id"],
                score=claude_hit["score"],
                method="claude",
                candidates=candidates,
                review_queue_id=None,
            )

        # Still ambiguous — enqueue for review
        review_queue_id: Optional[int] = None
        if auto_enqueue:
            review_queue_id = await _enqueue_review(
                db, raw_name, event_id, community_report_id, candidates
            )

        return NameMatchResult(
            raw_name=raw_name,
            normalized=normalized,
            outcome="AMBIGUOUS",
            contact_id=None,
            score=candidates[0]["score"] if candidates else None,
            method=None,
            candidates=candidates,
            review_queue_id=review_queue_id,
        )

    # No candidates — unmatched
    # Try Claude with no candidates (it will return None — but try anyway for future proofing)
    # Actually: if no fuzzy candidates, Claude has nothing to pick from; skip.
    review_queue_id = None
    if auto_enqueue:
        review_queue_id = await _enqueue_review(
            db, raw_name, event_id, community_report_id, []
        )

    return NameMatchResult(
        raw_name=raw_name,
        normalized=normalized,
        outcome="UNMATCHED",
        contact_id=None,
        score=None,
        method=None,
        candidates=[],
        review_queue_id=review_queue_id,
    )


# ---------------------------------------------------------------------------
# match_name_batch
# ---------------------------------------------------------------------------


async def match_name_batch(
    names: list[str],
    db: AsyncSession,
    source: str,
    event_id: Optional[int] = None,
    community_report_id: Optional[int] = None,
) -> list[NameMatchResult]:
    """Run match_name for each name in sequence and return all results.

    Names are processed sequentially to avoid hammering the DB.
    Claude calls within each match_name invocation are rate-limited by
    the module-level asyncio.Semaphore(5).
    """
    results: list[NameMatchResult] = []
    for raw_name in names:
        result = await match_name(
            raw_name=raw_name,
            db=db,
            source=source,
            event_id=event_id,
            community_report_id=community_report_id,
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# teach_alias_from_resolution  (spec §4.2.7)
# ---------------------------------------------------------------------------


async def teach_alias_from_resolution(
    db: AsyncSession,
    review_queue_id: int,
    contact_id: int,
    alias_text: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> NameAlias:
    """Upsert a NameAlias after a human resolves a review queue entry.

    If alias_text is None, derives it from the review queue row's raw_name
    (normalized).

    ON CONFLICT(alias_text) DO UPDATE SET contact_id — dual-dialect like
    bulk_service._dialect().

    Returns the upserted NameAlias (refreshed from DB).
    """
    # Fetch the review queue row to get raw_name if alias_text not provided
    queue_row = await db.get(NameMatchReviewQueue, review_queue_id)
    if queue_row is None:
        raise ValueError(f"NameMatchReviewQueue id={review_queue_id} not found")

    if alias_text is None:
        alias_text = normalize_name(queue_row.raw_name)

    if not alias_text:
        raise ValueError("alias_text is empty after normalization")

    now = utc_now()

    # Dual-dialect ON CONFLICT upsert
    if _dialect() == "postgresql":
        stmt = pg_insert(NameAlias).values(
            alias_text=alias_text,
            alias_type="preferred",
            contact_id=contact_id,
            created_by_id=actor_id,
            source="community_report",
            meta={},
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["alias_text"],
            set_={
                "contact_id": contact_id,
                "updated_at": now,
                "created_by_id": actor_id,
            },
        )
        await db.execute(stmt)
        await db.commit()

        # Fetch the upserted row
        select_stmt = select(NameAlias).where(NameAlias.alias_text == alias_text)
        alias_row = (await db.execute(select_stmt)).scalar_one()
    else:
        # SQLite
        stmt = sqlite_insert(NameAlias).values(
            alias_text=alias_text,
            alias_type="preferred",
            contact_id=contact_id,
            created_by_id=actor_id,
            source="community_report",
            meta={},
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["alias_text"],
            set_={
                "contact_id": contact_id,
                "updated_at": now,
                "created_by_id": actor_id,
            },
        )
        await db.execute(stmt)
        await db.commit()

        select_stmt = select(NameAlias).where(NameAlias.alias_text == alias_text)
        alias_row = (await db.execute(select_stmt)).scalar_one()

    # Update the review queue row to accepted
    try:
        queue_row.status = "accepted"
        queue_row.resolved_by_id = actor_id
        queue_row.resolved_at = now
        queue_row.contact_id = contact_id
        await db.commit()
    except Exception as exc:
        logger.warning(
            "name_match: failed to update review queue row id=%d: %s",
            review_queue_id,
            exc,
        )

    return alias_row
