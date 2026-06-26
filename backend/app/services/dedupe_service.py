"""dedupe_service.py — S11 Find & Merge Duplicates service.

Service name: dedupe_service  (NOT dedup.py which handles video-frame dedup).

CN-25: imports only from app.models, app.database, app.services.audit,
app.services.migration.normalize, app.utils.db_helpers, SQLAlchemy, FastAPI,
and stdlib.  Never imports from any router module.
"""

from __future__ import annotations

import difflib
import logging
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import async_session
from app.models import (
    AuditLog,  # noqa: F401  — imported for test fixtures that check AuditLog rows
    BiometricConsent,
    CommunityReport,
    ComprefaceSubject,
    Contact,
    CustomFieldDef,
    DedupeRuleSet,
    Detection,
    FaceSample,
    GroupMember,
    Log,
    NameAlias,
    NameMatchReviewQueue,
    Participant,
    utc_now,  # noqa: F401  — kept for potential future service helpers
)
from app.services import audit as audit_svc
from app.services.migration.normalize import name_key
from app.utils.db_helpers import has_table

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level registry  (introspection test checks completeness)
# ---------------------------------------------------------------------------

# Complete manifest: (table_name, col_name) tuples for every contacts.id FK
# PLUS string keys for the two non-FK rewrite paths.
# When S12 lands and adds activities.target_contact_id, the introspection test
# (test_manifest_covers_all_contact_fks) will fail — add the tuple then.
_REASSIGNMENT_TARGETS: frozenset = frozenset({
    # ── PLAIN UPDATE ──────────────────────────────────────────────────────────
    ("face_samples", "contact_id"),
    ("community_report", "submitted_by_contact_id"),
    ("community_report", "event_leader_contact_id"),
    ("name_match_review_queue", "contact_id"),
    ("name_match_review_queue", "candidate_contact_id"),
    # ── COLLISION-AWARE ───────────────────────────────────────────────────────
    ("activities", "target_contact_id"),   # S12: plain UPDATE; has_table guard in _reassign_all
    ("participants", "contact_id"),        # UNIQUE(event_id, contact_id)
    ("group_members", "contact_id"),       # UNIQUE(group_id, contact_id)
    ("biometric_consent", "contact_id"),   # UNIQUE(contact_id)
    ("name_alias", "contact_id"),          # uq_name_alias_text globally unique
    ("compreface_subjects", "contact_id"), # SET NULL FK → detach if survivor active
    # ── NON-FK REWRITE PATHS ─────────────────────────────────────────────────
    "detections.matched_name",
    "contacts.custom_data:contact_reference",
})

# Participant status precedence for collision-aware promotion.
_STATUS_PRECEDENCE: dict[str, int] = {
    "attended": 4,
    "registered": 3,
    "no_show": 2,
    "cancelled": 1,
}

# Valid dedupe scoring fields.
DEDUPE_RULE_FIELDS: frozenset[str] = frozenset({
    "first_name", "last_name", "suffix", "gender",
    "birth_date", "phone", "email", "street_address",
})

# Core contact fields exposed in preview field_conflicts.
_CORE_FIELDS: list[str] = [
    "first_name", "last_name", "nickname", "suffix", "gender",
    "birth_date", "phone", "email", "street_address",
    "contact_type", "contact_subtype",
]

# Fields that callers may override via MergeRequest.field_choices.
_CHOOSABLE_FIELDS: frozenset[str] = frozenset({
    "first_name", "last_name", "nickname", "suffix", "gender",
    "birth_date", "phone", "email", "street_address",
    "contact_type", "contact_subtype",
})


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _norm(first: str, last: str) -> str:
    """Normalised same-name key — delegates to migration.normalize.name_key."""
    return name_key(first, last)


def _max_status(s1: str, s2: str) -> str:
    """Return the higher-precedence participant status."""
    return s1 if _STATUS_PRECEDENCE.get(s1, 0) >= _STATUS_PRECEDENCE.get(s2, 0) else s2


def _get_field_str(contact: Contact, field: str) -> str:
    """Lower-cased, stripped string value of *field* on *contact* (empty if None)."""
    val = getattr(contact, field, None)
    return str(val).strip().lower() if val is not None else ""


def _score_pair(
    a: Contact,
    b: Contact,
    rules: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    """Score a candidate pair with SequenceMatcher + rule weights.

    Returns (composite_0_to_100, matched_field_names).
    """
    total_weight = sum(float(r.get("weight", 0)) for r in rules) or 1.0
    score = 0.0
    matched_fields: list[str] = []

    for rule in rules:
        field = str(rule.get("field", ""))
        if field not in DEDUPE_RULE_FIELDS:
            continue
        weight = float(rule.get("weight", 0))
        if weight <= 0:
            continue
        a_val = _get_field_str(a, field)
        b_val = _get_field_str(b, field)
        if a_val and b_val:
            ratio = difflib.SequenceMatcher(None, a_val, b_val).ratio()
            score += weight * ratio
            if ratio >= 0.8:
                matched_fields.append(field)

    return (score / total_weight) * 100.0, matched_fields


def _snapshot_contact(contact: Contact) -> dict[str, Any]:
    """JSON-serialisable snapshot of a contact's core fields."""
    return {
        "id": contact.id,
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "nickname": contact.nickname,
        "suffix": contact.suffix,
        "gender": contact.gender,
        "birth_date": str(contact.birth_date) if contact.birth_date else None,
        "phone": contact.phone,
        "email": contact.email,
        "street_address": contact.street_address,
        "contact_type": contact.contact_type,
        "contact_subtype": contact.contact_subtype,
        "custom_data": dict(contact.custom_data or {}),
        "is_deleted": contact.is_deleted,
    }


async def _get_active_rule_set(
    db: AsyncSession,
    rule_set_id: Optional[int],
) -> DedupeRuleSet:
    """Return the rule set to use for detection/scoring."""
    if rule_set_id is not None:
        rs = await db.get(DedupeRuleSet, rule_set_id)
        if rs is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"Rule set {rule_set_id} not found",
            )
        return rs

    rs = (await db.execute(
        select(DedupeRuleSet)
        .where(
            DedupeRuleSet.is_default == True,   # noqa: E712
            DedupeRuleSet.is_active == True,    # noqa: E712
        )
        .limit(1)
    )).scalar_one_or_none()
    if rs:
        return rs

    rs = (await db.execute(
        select(DedupeRuleSet)
        .where(DedupeRuleSet.is_active == True)  # noqa: E712
        .limit(1)
    )).scalar_one_or_none()
    if rs is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="No active rule set found. Create one via POST /dedupe/rule-sets",
        )
    return rs


async def _get_participant_counts(
    db: AsyncSession, contact_ids: list[int]
) -> dict[int, int]:
    if not contact_ids:
        return {}
    rows = (await db.execute(
        select(Participant.contact_id, func.count(Participant.id).label("cnt"))
        .where(Participant.contact_id.in_(contact_ids))
        .group_by(Participant.contact_id)
    )).all()
    return {r.contact_id: r.cnt for r in rows}


async def _get_face_counts(
    db: AsyncSession, contact_ids: list[int]
) -> dict[int, int]:
    if not contact_ids:
        return {}
    rows = (await db.execute(
        select(FaceSample.contact_id, func.count(FaceSample.id).label("cnt"))
        .where(
            FaceSample.contact_id.isnot(None),
            FaceSample.contact_id.in_(contact_ids),
        )
        .group_by(FaceSample.contact_id)
    )).all()
    return {r.contact_id: r.cnt for r in rows}


async def _reassign_all(
    db: AsyncSession,
    survivor: Contact,
    loser: Contact,
) -> tuple[dict[str, dict[str, int]], list[str], list[str]]:
    """Execute every FK reassignment in the manifest.

    Returns:
        stats          — {key: {reassigned: int, deleted: int}}
        warnings       — non-fatal issues
        cf_ids_to_purge — compreface_subject_id values to clean up post-commit
    """
    sid = survivor.id
    lid = loser.id
    stats: dict[str, dict[str, int]] = {}
    warnings: list[str] = []
    cf_ids_to_purge: list[str] = []

    # ── PLAIN UPDATE ──────────────────────────────────────────────────────────

    r = await db.execute(
        update(FaceSample).where(FaceSample.contact_id == lid).values(contact_id=sid)
    )
    stats["face_samples.contact_id"] = {"reassigned": r.rowcount, "deleted": 0}

    r = await db.execute(
        update(CommunityReport)
        .where(CommunityReport.submitted_by_contact_id == lid)
        .values(submitted_by_contact_id=sid)
    )
    stats["community_report.submitted_by_contact_id"] = {
        "reassigned": r.rowcount, "deleted": 0
    }

    r = await db.execute(
        update(CommunityReport)
        .where(CommunityReport.event_leader_contact_id == lid)
        .values(event_leader_contact_id=sid)
    )
    stats["community_report.event_leader_contact_id"] = {
        "reassigned": r.rowcount, "deleted": 0
    }

    r = await db.execute(
        update(NameMatchReviewQueue)
        .where(NameMatchReviewQueue.contact_id == lid)
        .values(contact_id=sid)
    )
    stats["name_match_review_queue.contact_id"] = {
        "reassigned": r.rowcount, "deleted": 0
    }

    r = await db.execute(
        update(NameMatchReviewQueue)
        .where(NameMatchReviewQueue.candidate_contact_id == lid)
        .values(candidate_contact_id=sid)
    )
    stats["name_match_review_queue.candidate_contact_id"] = {
        "reassigned": r.rowcount, "deleted": 0
    }

    # ── COLLISION-AWARE: participants  UNIQUE(event_id, contact_id) ───────────
    loser_event_ids: list[int] = list(
        (await db.execute(
            select(Participant.event_id).where(Participant.contact_id == lid)
        )).scalars().all()
    )

    reassigned_p = 0
    deleted_p = 0

    if loser_event_ids:
        survivor_event_ids: set[int] = set(
            (await db.execute(
                select(Participant.event_id).where(
                    Participant.contact_id == sid,
                    Participant.event_id.in_(loser_event_ids),
                )
            )).scalars().all()
        )
        colliding_ev = [e for e in loser_event_ids if e in survivor_event_ids]
        non_colliding_ev = [e for e in loser_event_ids if e not in survivor_event_ids]

        for event_id in colliding_ev:
            s_part = (await db.execute(
                select(Participant).where(
                    Participant.contact_id == sid,
                    Participant.event_id == event_id,
                )
            )).scalar_one()
            l_part = (await db.execute(
                select(Participant).where(
                    Participant.contact_id == lid,
                    Participant.event_id == event_id,
                )
            )).scalar_one()
            s_part.status = _max_status(s_part.status, l_part.status)
            await db.delete(l_part)
            deleted_p += 1

        if non_colliding_ev:
            r = await db.execute(
                update(Participant)
                .where(
                    Participant.contact_id == lid,
                    Participant.event_id.in_(non_colliding_ev),
                )
                .values(contact_id=sid)
            )
            reassigned_p += r.rowcount

    stats["participants.contact_id"] = {"reassigned": reassigned_p, "deleted": deleted_p}

    # ── COLLISION-AWARE: group_members  UNIQUE(group_id, contact_id) ──────────
    loser_group_ids: list[int] = list(
        (await db.execute(
            select(GroupMember.group_id).where(GroupMember.contact_id == lid)
        )).scalars().all()
    )

    reassigned_gm = 0
    deleted_gm = 0

    if loser_group_ids:
        survivor_group_ids: set[int] = set(
            (await db.execute(
                select(GroupMember.group_id).where(
                    GroupMember.contact_id == sid,
                    GroupMember.group_id.in_(loser_group_ids),
                )
            )).scalars().all()
        )
        colliding_gm = [g for g in loser_group_ids if g in survivor_group_ids]
        non_colliding_gm = [g for g in loser_group_ids if g not in survivor_group_ids]

        if colliding_gm:
            r = await db.execute(
                delete(GroupMember).where(
                    GroupMember.contact_id == lid,
                    GroupMember.group_id.in_(colliding_gm),
                )
            )
            deleted_gm += r.rowcount

        if non_colliding_gm:
            r = await db.execute(
                update(GroupMember)
                .where(
                    GroupMember.contact_id == lid,
                    GroupMember.group_id.in_(non_colliding_gm),
                )
                .values(contact_id=sid)
            )
            reassigned_gm += r.rowcount

    stats["group_members.contact_id"] = {"reassigned": reassigned_gm, "deleted": deleted_gm}

    # ── COLLISION-AWARE: biometric_consent  UNIQUE(contact_id) ───────────────
    survivor_consent = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == sid)
    )).scalar_one_or_none()

    loser_consent = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == lid)
    )).scalar_one_or_none()

    if loser_consent is not None:
        if survivor_consent is not None:
            # Loser's row is already captured in the before-snapshot; DELETE it.
            await db.delete(loser_consent)
            stats["biometric_consent.contact_id"] = {"reassigned": 0, "deleted": 1}
        else:
            loser_consent.contact_id = sid
            stats["biometric_consent.contact_id"] = {"reassigned": 1, "deleted": 0}
    else:
        stats["biometric_consent.contact_id"] = {"reassigned": 0, "deleted": 0}

    # ── COLLISION-AWARE: name_alias  (alias_text globally unique) ─────────────
    loser_aliases: list[NameAlias] = list(
        (await db.execute(
            select(NameAlias).where(NameAlias.contact_id == lid)
        )).scalars().all()
    )

    reassigned_na = 0
    deleted_na = 0

    for alias in loser_aliases:
        existing = (await db.execute(
            select(NameAlias).where(
                NameAlias.alias_text == alias.alias_text,
                NameAlias.contact_id != lid,
            )
        )).scalar_one_or_none()

        if existing is not None:
            # alias_text taken globally — delete this loser alias
            await db.delete(alias)
            deleted_na += 1
        else:
            alias.contact_id = sid
            reassigned_na += 1

    stats["name_alias.contact_id"] = {"reassigned": reassigned_na, "deleted": deleted_na}

    # ── COLLISION-AWARE: compreface_subjects  (SET NULL FK) ───────────────────
    survivor_active = (await db.execute(
        select(ComprefaceSubject).where(
            ComprefaceSubject.contact_id == sid,
            ComprefaceSubject.enrollment_status == "active",
        )
    )).scalar_one_or_none()

    loser_subjects: list[ComprefaceSubject] = list(
        (await db.execute(
            select(ComprefaceSubject).where(ComprefaceSubject.contact_id == lid)
        )).scalars().all()
    )

    reassigned_cf = 0
    detached_cf = 0

    for subj in loser_subjects:
        if survivor_active is not None:
            # Survivor has an active subject — detach loser's subject (SET NULL)
            subj.contact_id = None
            if subj.compreface_subject_id:
                cf_ids_to_purge.append(subj.compreface_subject_id)
            detached_cf += 1
        else:
            subj.contact_id = sid
            reassigned_cf += 1

    stats["compreface_subjects.contact_id"] = {
        "reassigned": reassigned_cf,
        "deleted": detached_cf,
    }

    # ── NON-FK: detections.matched_name + logs.matched_name ──────────────────
    old_pat = f"member:{lid}"
    new_pat = f"member:{sid}"

    r = await db.execute(
        update(Detection)
        .where(Detection.matched_name == old_pat)
        .values(matched_name=new_pat)
    )
    stats["detections.matched_name"] = {"reassigned": r.rowcount, "deleted": 0}

    r = await db.execute(
        update(Log).where(Log.matched_name == old_pat).values(matched_name=new_pat)
    )
    stats["logs.matched_name"] = {"reassigned": r.rowcount, "deleted": 0}

    # ── NON-FK: contacts.custom_data contact_reference fields ─────────────────
    ref_field_names: list[str] = list(
        (await db.execute(
            select(CustomFieldDef.name).where(
                CustomFieldDef.data_type == "contact_reference"
            )
        )).scalars().all()
    )

    custom_ref_updated = 0
    if ref_field_names:
        contacts_to_scan: list[Contact] = list(
            (await db.execute(
                select(Contact).where(
                    Contact.is_deleted == False,   # noqa: E712
                    Contact.id != lid,             # loser is being deleted; skip it
                )
            )).scalars().all()
        )

        for contact in contacts_to_scan:
            cd = dict(contact.custom_data or {})
            modified = False
            for fname in ref_field_names:
                if fname not in cd:
                    continue
                val = cd[fname]
                if isinstance(val, list):
                    new_list = list(dict.fromkeys(
                        sid if item == lid else item for item in val
                    ))
                    if new_list != list(val):
                        cd[fname] = new_list
                        modified = True
                elif val == lid:
                    cd[fname] = sid
                    modified = True
            if modified:
                contact.custom_data = cd
                custom_ref_updated += 1

    stats["contacts.custom_data:contact_reference"] = {
        "reassigned": custom_ref_updated,
        "deleted": 0,
    }

    # ── S12 GUARD: activities.target_contact_id ───────────────────────────────
    if await has_table(db, "activities"):
        r = await db.execute(
            text(
                "UPDATE activities SET target_contact_id = :sid"
                " WHERE target_contact_id = :lid"
            ),
            {"sid": sid, "lid": lid},
        )
        stats["activities.target_contact_id"] = {
            "reassigned": r.rowcount, "deleted": 0
        }

    return stats, warnings, cf_ids_to_purge


# ---------------------------------------------------------------------------
# Public API — Detection
# ---------------------------------------------------------------------------


async def detect_candidates(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 25,
    rule_set_id: Optional[int] = None,
    max_pairs: int = 10_000,
) -> dict[str, Any]:
    """Return paginated duplicate-candidate pairs scored against the active rule set.

    Detection flow:
      1. SQL blocking buckets (exact email / last_name / phone / first_name-3-prefix)
         exclude is_deleted; self-pairs excluded via a.id < b.id.
      2. Python SequenceMatcher scoring per active rule weights.
      3. Filter at threshold; rank by score desc (tiebreak a.id asc, b.id asc).
      4. Cap to max_pairs; paginate.
    """
    rule_set = await _get_active_rule_set(db, rule_set_id)
    rules: list[dict[str, Any]] = list(rule_set.rules or [])
    threshold: float = float(rule_set.threshold)

    # ── Blocking: SQL self-join ────────────────────────────────────────────────
    c1 = aliased(Contact)
    c2 = aliased(Contact)

    blocking = or_(
        and_(c1.email.isnot(None), c1.email == c2.email),
        and_(c1.last_name.isnot(None), c1.last_name == c2.last_name),
        and_(c1.phone.isnot(None), c1.phone == c2.phone),
        and_(
            func.length(c1.first_name) >= 3,
            func.substr(c1.first_name, 1, 3) == func.substr(c2.first_name, 1, 3),
        ),
    )

    pair_rows = (await db.execute(
        select(c1.id.label("id_a"), c2.id.label("id_b"))
        .where(
            c1.id < c2.id,
            c1.is_deleted == False,   # noqa: E712
            c2.is_deleted == False,   # noqa: E712
            blocking,
        )
    )).all()

    if not pair_rows:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}

    # ── Load contacts for scoring ─────────────────────────────────────────────
    all_ids: set[int] = set()
    for row in pair_rows:
        all_ids.add(row.id_a)
        all_ids.add(row.id_b)

    contacts_map: dict[int, Contact] = {
        c.id: c
        for c in (await db.execute(
            select(Contact).where(Contact.id.in_(all_ids))
        )).scalars().all()
    }

    # ── Score, filter, rank ───────────────────────────────────────────────────
    scored: list[dict[str, Any]] = []
    for row in pair_rows:
        a = contacts_map.get(row.id_a)
        b = contacts_map.get(row.id_b)
        if a is None or b is None:
            continue
        pair_score, matched_fields = _score_pair(a, b, rules)
        if pair_score >= threshold:
            same_name = (
                _norm(a.first_name or "", a.last_name or "")
                == _norm(b.first_name or "", b.last_name or "")
            )
            scored.append(
                {
                    "a": a,
                    "b": b,
                    "score": pair_score,
                    "matched_fields": matched_fields,
                    "same_name": same_name,
                }
            )

    scored.sort(key=lambda x: (-x["score"], x["a"].id, x["b"].id))
    scored = scored[:max_pairs]
    total = len(scored)

    # ── Paginate ──────────────────────────────────────────────────────────────
    offset = (page - 1) * page_size
    page_items = scored[offset : offset + page_size]

    # Subquery counts for page contacts only
    page_ids: set[int] = set()
    for item in page_items:
        page_ids.add(item["a"].id)
        page_ids.add(item["b"].id)

    participant_counts = await _get_participant_counts(db, list(page_ids))
    face_counts = await _get_face_counts(db, list(page_ids))

    def _lite(c: Contact) -> dict[str, Any]:
        return {
            "id": c.id,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "email": c.email,
            "phone": c.phone,
            "participant_count": participant_counts.get(c.id, 0),
            "face_sample_count": face_counts.get(c.id, 0),
        }

    items = [
        {
            "contact_a": _lite(item["a"]),
            "contact_b": _lite(item["b"]),
            "score": item["score"],
            "matched_fields": item["matched_fields"],
            "same_name": item["same_name"],
        }
        for item in page_items
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ---------------------------------------------------------------------------
# Public API — Preview (dry-run, zero writes)
# ---------------------------------------------------------------------------


async def merge_preview(
    db: AsyncSession,
    survivor_id: int,
    loser_id: int,
) -> dict[str, Any]:
    """Dry-run preview — reads only, never flushes or commits."""
    survivor = (await db.execute(
        select(Contact).where(Contact.id == survivor_id)
    )).scalar_one_or_none()
    loser = (await db.execute(
        select(Contact).where(Contact.id == loser_id)
    )).scalar_one_or_none()

    if survivor is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Survivor contact {survivor_id} not found"
        )
    if loser is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Loser contact {loser_id} not found"
        )

    # ── Field conflicts ───────────────────────────────────────────────────────
    field_conflicts: dict[str, dict[str, Any]] = {}
    for field in _CORE_FIELDS:
        s_val = getattr(survivor, field, None)
        l_val = getattr(loser, field, None)
        # Coerce date/datetime to str for JSON-safety in preview
        s_str = str(s_val) if s_val is not None else None
        l_str = str(l_val) if l_val is not None else None
        field_conflicts[field] = {
            "survivor": s_str,
            "loser": l_str,
            "differs": s_str != l_str,
        }

    # ── Custom field conflicts ────────────────────────────────────────────────
    s_cd = dict(survivor.custom_data or {})
    l_cd = dict(loser.custom_data or {})
    custom_field_conflicts: dict[str, dict[str, Any]] = {}
    for key in set(s_cd) | set(l_cd):
        sv = s_cd.get(key)
        lv = l_cd.get(key)
        custom_field_conflicts[key] = {
            "survivor": sv,
            "loser": lv,
            "differs": sv != lv,
        }

    # ── Reassignment counts ───────────────────────────────────────────────────
    reassignments: dict[str, dict[str, int]] = {}

    # PLAIN UPDATE — simple total COUNT
    async def _count(model: Any, col: Any) -> int:
        return (await db.execute(
            select(func.count()).where(col == loser_id)
        )).scalar() or 0

    reassignments["face_samples.contact_id"] = {
        "reassigned": await _count(FaceSample, FaceSample.contact_id), "deleted": 0
    }
    reassignments["community_report.submitted_by_contact_id"] = {
        "reassigned": await _count(CommunityReport, CommunityReport.submitted_by_contact_id),
        "deleted": 0,
    }
    reassignments["community_report.event_leader_contact_id"] = {
        "reassigned": await _count(CommunityReport, CommunityReport.event_leader_contact_id),
        "deleted": 0,
    }
    reassignments["name_match_review_queue.contact_id"] = {
        "reassigned": await _count(NameMatchReviewQueue, NameMatchReviewQueue.contact_id),
        "deleted": 0,
    }
    reassignments["name_match_review_queue.candidate_contact_id"] = {
        "reassigned": await _count(
            NameMatchReviewQueue, NameMatchReviewQueue.candidate_contact_id
        ),
        "deleted": 0,
    }

    # participants.contact_id — split into reassigned vs. deleted
    total_parts = await _count(Participant, Participant.contact_id)
    l_event_ids: list[int] = list(
        (await db.execute(
            select(Participant.event_id).where(Participant.contact_id == loser_id)
        )).scalars().all()
    )
    collision_parts = 0
    if l_event_ids:
        collision_parts = (await db.execute(
            select(func.count(Participant.id)).where(
                Participant.contact_id == survivor_id,
                Participant.event_id.in_(l_event_ids),
            )
        )).scalar() or 0
    reassignments["participants.contact_id"] = {
        "reassigned": max(0, total_parts - collision_parts),
        "deleted": collision_parts,
    }

    # group_members.contact_id
    total_gm = await _count(GroupMember, GroupMember.contact_id)
    l_group_ids: list[int] = list(
        (await db.execute(
            select(GroupMember.group_id).where(GroupMember.contact_id == loser_id)
        )).scalars().all()
    )
    collision_gm = 0
    if l_group_ids:
        collision_gm = (await db.execute(
            select(func.count(GroupMember.id)).where(
                GroupMember.contact_id == survivor_id,
                GroupMember.group_id.in_(l_group_ids),
            )
        )).scalar() or 0
    reassignments["group_members.contact_id"] = {
        "reassigned": max(0, total_gm - collision_gm),
        "deleted": collision_gm,
    }

    # biometric_consent.contact_id
    l_consent = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == loser_id)
    )).scalar_one_or_none()
    s_consent = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == survivor_id)
    )).scalar_one_or_none()
    if l_consent and s_consent:
        reassignments["biometric_consent.contact_id"] = {"reassigned": 0, "deleted": 1}
    elif l_consent:
        reassignments["biometric_consent.contact_id"] = {"reassigned": 1, "deleted": 0}
    else:
        reassignments["biometric_consent.contact_id"] = {"reassigned": 0, "deleted": 0}

    # name_alias.contact_id
    total_na = await _count(NameAlias, NameAlias.contact_id)
    loser_alias_texts: list[str] = list(
        (await db.execute(
            select(NameAlias.alias_text).where(NameAlias.contact_id == loser_id)
        )).scalars().all()
    )
    collision_na = 0
    if loser_alias_texts:
        collision_na = (await db.execute(
            select(func.count(NameAlias.id)).where(
                NameAlias.alias_text.in_(loser_alias_texts),
                NameAlias.contact_id != loser_id,
            )
        )).scalar() or 0
    reassignments["name_alias.contact_id"] = {
        "reassigned": max(0, total_na - collision_na),
        "deleted": collision_na,
    }

    # compreface_subjects.contact_id
    total_cf = await _count(ComprefaceSubject, ComprefaceSubject.contact_id)
    s_active_cf = (await db.execute(
        select(ComprefaceSubject).where(
            ComprefaceSubject.contact_id == survivor_id,
            ComprefaceSubject.enrollment_status == "active",
        )
    )).scalar_one_or_none()
    if s_active_cf and total_cf > 0:
        reassignments["compreface_subjects.contact_id"] = {
            "reassigned": 0, "deleted": total_cf
        }
    else:
        reassignments["compreface_subjects.contact_id"] = {
            "reassigned": total_cf, "deleted": 0
        }

    # Non-FK rewrites
    member_pattern = f"member:{loser_id}"
    det_count = (await db.execute(
        select(func.count(Detection.id)).where(Detection.matched_name == member_pattern)
    )).scalar() or 0
    log_count = (await db.execute(
        select(func.count(Log.id)).where(Log.matched_name == member_pattern)
    )).scalar() or 0
    reassignments["detections.matched_name"] = {"reassigned": det_count, "deleted": 0}
    reassignments["logs.matched_name"] = {"reassigned": log_count, "deleted": 0}
    reassignments["contacts.custom_data:contact_reference"] = {"reassigned": 0, "deleted": 0}

    # ── Same-name + warnings ──────────────────────────────────────────────────
    same_name = (
        _norm(survivor.first_name or "", survivor.last_name or "")
        == _norm(loser.first_name or "", loser.last_name or "")
    )
    preview_warnings: list[str] = []
    if same_name:
        preview_warnings.append(
            "Contacts have the same name — set confirm_same_name=true to merge"
        )

    return {
        "field_conflicts": field_conflicts,
        "custom_field_conflicts": custom_field_conflicts,
        "reassignments": reassignments,
        "same_name": same_name,
        "warnings": preview_warnings,
    }


# ---------------------------------------------------------------------------
# Public API — Merge (atomic)
# ---------------------------------------------------------------------------


async def merge_contacts(
    db: AsyncSession,
    *,
    survivor_id: int,
    loser_id: int,
    actor_id: Optional[int],
    confirm_same_name: bool = False,
    field_choices: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Atomically merge loser into survivor.

    Phases:
      1. Guards (422 self / 404 missing / 409 deleted / 409 same-name w/o confirm)
      2. with_for_update() locks on both rows
      3. Snapshot before-state
      4. _reassign_all (full manifest)
      4b. Apply field_choices
      5. loser.is_deleted = True
      6. ONE audit.record()
      7. db.commit()
      8. POST-COMMIT (non-fatal): CompreFace cleanup, recompute_contacts
    """
    # ── Phase 1: Guards ───────────────────────────────────────────────────────
    if survivor_id == loser_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="survivor_id and loser_id must be different contacts",
        )

    # Pre-lock fetch for guard checks
    survivor_pre = await db.get(Contact, survivor_id)
    loser_pre = await db.get(Contact, loser_id)

    if survivor_pre is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Survivor contact {survivor_id} not found",
        )
    if loser_pre is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Loser contact {loser_id} not found",
        )
    if survivor_pre.is_deleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Survivor contact {survivor_id} is already deleted",
        )
    if loser_pre.is_deleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Loser contact {loser_id} is already deleted",
        )

    same_name = (
        _norm(survivor_pre.first_name or "", survivor_pre.last_name or "")
        == _norm(loser_pre.first_name or "", loser_pre.last_name or "")
    )
    if same_name and not confirm_same_name:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Contacts have the same name — set confirm_same_name=true to proceed"
            ),
        )

    # ── Phase 2: Row-level locks ──────────────────────────────────────────────
    # with_for_update() is a no-op on SQLite; prevents races on PostgreSQL.
    # Lock survivor first, then loser (consistent ordering avoids deadlock).
    # populate_existing=True forces the ORM to re-read from the DB result
    # instead of returning the stale identity-map version — this is what closes
    # the TOCTOU window between the pre-lock guard (Phase 1) and the lock itself.
    survivor = (await db.execute(
        select(Contact)
        .where(Contact.id == survivor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    loser = (await db.execute(
        select(Contact)
        .where(Contact.id == loser_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()

    # ── Post-lock re-validation (TOCTOU guard) ─────────────────────────────────
    # A concurrent merge may have set is_deleted=True between the Phase 1 guard
    # and the lock acquisition.  The locked rows above are guaranteed fresh.
    if survivor.is_deleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Survivor contact {survivor_id} was deleted by a concurrent operation",
        )
    if loser.is_deleted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Loser contact {loser_id} was already merged or deleted",
        )

    # ── Phase 3: Snapshot ─────────────────────────────────────────────────────
    before_snapshot = _snapshot_contact(survivor)
    before_snapshot["_loser"] = _snapshot_contact(loser)

    # Capture loser consent row for before-snapshot if it will be deleted
    l_consent_snap: Optional[dict[str, Any]] = None
    l_consent_chk = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == loser_id)
    )).scalar_one_or_none()
    s_consent_chk = (await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == survivor_id)
    )).scalar_one_or_none()
    if l_consent_chk and s_consent_chk:
        l_consent_snap = {
            "id": l_consent_chk.id,
            "contact_id": l_consent_chk.contact_id,
            "consent_given": l_consent_chk.consent_given,
            "consented_at": str(l_consent_chk.consented_at) if l_consent_chk.consented_at else None,
        }
        before_snapshot["_loser_consent"] = l_consent_snap

    # Get active rule_set_id for audit
    try:
        rule_set = await _get_active_rule_set(db, None)
        rule_set_id: Optional[int] = rule_set.id
    except HTTPException:
        rule_set_id = None

    # ── Phases 4-7: Transactional mutations ──────────────────────────────────
    try:
        # Phase 4: Reassign all FK targets
        stats, warnings, cf_ids_to_purge = await _reassign_all(db, survivor, loser)

        # Phase 4b: Apply caller field choices (overwrite survivor with loser's value)
        if field_choices:
            for field, choice in field_choices.items():
                if field in _CHOOSABLE_FIELDS and choice == "loser":
                    setattr(survivor, field, getattr(loser, field, None))

        # Phase 5: Mark loser deleted
        loser.is_deleted = True

        # Phase 6: Single audit record
        after_data: dict[str, Any] = {
            **_snapshot_contact(survivor),
            "_reassignments": stats,
            "_rule_set_id": rule_set_id,
            "_same_name": same_name,
            "_loser_id": loser_id,
        }
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="contact_merge",
            entity="contacts",
            entity_id=survivor_id,
            before=before_snapshot,
            after=after_data,
        )

        # Phase 7: Commit
        await db.commit()

    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        # Log full detail internally; do NOT expose internal exc str to the client.
        logger.exception("merge_contacts(%d→%d) rolled back", loser_id, survivor_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Merge failed and was rolled back",
        ) from exc

    # ── Phase 8: POST-COMMIT (non-fatal) ──────────────────────────────────────
    post_warnings: list[str] = []

    # CompreFace cleanup for detached subjects (import-guarded)
    if cf_ids_to_purge:
        try:
            from app.services.compreface import ComprefaceClient  # type: ignore

            cf_client = ComprefaceClient()
            for cf_id in cf_ids_to_purge:
                try:
                    await cf_client.delete_subject(cf_id)
                except Exception as cf_exc:
                    logger.warning(
                        "CompreFace cleanup failed for subject %s: %s", cf_id, cf_exc
                    )
                    post_warnings.append(
                        "post-merge CompreFace cleanup incomplete; will retry on next purge run"
                    )
        except Exception as import_exc:
            logger.warning("CompreFace import/init failed during merge cleanup: %s", import_exc)
            post_warnings.append(
                "post-merge CompreFace cleanup skipped; will retry on next purge run"
            )

    # Recompute member status for survivor (uses its own session — it commits internally)
    try:
        import app.services.member_status_service as _mss  # type: ignore

        if hasattr(_mss, "recompute_contacts"):
            async with async_session() as _pc_db:
                await _mss.recompute_contacts(_pc_db, [survivor_id])
    except Exception as rc_exc:
        logger.warning("Post-commit recompute failed after merge %d→%d: %s", loser_id, survivor_id, rc_exc)
        post_warnings.append(
            "post-merge recompute failed; will retry on next nightly run"
        )

    warnings.extend(post_warnings)

    return {
        "survivor_id": survivor_id,
        "reassignments": stats,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Public API — Rule Set CRUD
# ---------------------------------------------------------------------------


async def list_rule_sets(db: AsyncSession) -> list[DedupeRuleSet]:
    """Return all rule sets ordered by name."""
    return list(
        (await db.execute(
            select(DedupeRuleSet).order_by(DedupeRuleSet.name)
        )).scalars().all()
    )


async def get_rule_set(db: AsyncSession, rule_set_id: int) -> DedupeRuleSet:
    rs = await db.get(DedupeRuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Rule set {rule_set_id} not found",
        )
    return rs


async def create_rule_set(
    db: AsyncSession,
    data: Any,  # RuleSetIn — imported at call site to avoid circular
) -> DedupeRuleSet:
    """Create a new rule set; clears is_default on others if this one is default."""
    if data.is_default:
        await db.execute(update(DedupeRuleSet).values(is_default=False))

    rules_raw = [r.model_dump() for r in data.rules]
    rs = DedupeRuleSet(
        name=data.name,
        is_default=data.is_default,
        is_active=data.is_active,
        threshold=data.threshold,
        rules=rules_raw,
    )
    db.add(rs)
    await db.commit()
    await db.refresh(rs)
    return rs


async def update_rule_set(
    db: AsyncSession,
    rule_set_id: int,
    data: Any,  # RuleSetIn
) -> DedupeRuleSet:
    rs = await db.get(DedupeRuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Rule set {rule_set_id} not found"
        )

    if data.is_default and not rs.is_default:
        # Clear is_default on all other rows in the same transaction
        await db.execute(
            update(DedupeRuleSet)
            .where(DedupeRuleSet.id != rule_set_id)
            .values(is_default=False)
        )

    rs.name = data.name
    rs.is_default = data.is_default
    rs.is_active = data.is_active
    rs.threshold = data.threshold
    rs.rules = [r.model_dump() for r in data.rules]

    await db.commit()
    await db.refresh(rs)
    return rs


async def delete_rule_set(db: AsyncSession, rule_set_id: int) -> None:
    """Delete a rule set; 409 if it is the only active set OR is_default."""
    rs = await db.get(DedupeRuleSet, rule_set_id)
    if rs is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Rule set {rule_set_id} not found"
        )

    if rs.is_default:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Cannot delete the default rule set",
        )

    if rs.is_active:
        other_active = (await db.execute(
            select(func.count(DedupeRuleSet.id)).where(
                DedupeRuleSet.id != rule_set_id,
                DedupeRuleSet.is_active == True,   # noqa: E712
            )
        )).scalar() or 0
        if other_active == 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Cannot delete the only active rule set",
            )

    await db.delete(rs)
    await db.commit()
