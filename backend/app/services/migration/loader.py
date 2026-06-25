"""Migration loader: upsert helpers for Contact, Event, and Participant rows.

Ownership: S06 migration ETL (T05).

Public API
----------
upsert_contact(session, mapped, dry_run)           -> (outcome, entity_id | None, message)
upsert_event(session, mapped, dry_run)             -> (outcome, entity_id | None, message)
bulk_insert_participants(session, rows,
    contact_id_map, event_id_map, dry_run)         -> list[(outcome, entity_id | None, message)]
write_people_link(session, source_contact_id,
    field_name, resolved_contact_id)               -> None

Circular-import rule (CN-25): this module must NOT import from any router module.

Design notes
------------
- SELECT-then-insert/update (never raw ON CONFLICT) for full SQLite test parity.
- Contact.external_id and Event.external_id are Integer columns; the raw
  external_id token (a String from the import row) is coerced to int before any
  WHERE clause.  A non-numeric token yields an 'error' outcome immediately.
- custom_data merge: read existing dict -> update only mapped keys -> write back.
- bulk_insert_participants delegates the actual upsert to
  app.services.bulk_service.bulk_upsert_participants (which handles ON CONFLICT
  and returns an int rowcount).  Per-row entity_id is None for this path.
- audit_svc.record() calls in write_people_link are guarded with try/except so a
  missing audit_log table (e.g. fresh test DB) never crashes the loader.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Event
from app.services import audit as audit_svc
from app.services import bulk_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

Outcome = Literal["created", "updated", "skipped", "error", "review"]
LoaderResult = tuple[Outcome, Optional[int], str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_external_id(raw: Any) -> tuple[Optional[int], Optional[str]]:
    """Coerce a raw external_id token to int.

    Returns (int_val, None) on success or (None, error_message) on failure.
    """
    if raw is None:
        return None, "external_id is missing"
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, f"external_id {raw!r} is not a valid integer"


def _merge_custom_data(existing: dict, updates: dict) -> dict:
    """Merge only the keys present in *updates* into *existing*.

    Returns the merged dict (mutates and returns *existing* for efficiency).
    """
    if not isinstance(existing, dict):
        existing = {}
    existing.update(updates)
    return existing


# ---------------------------------------------------------------------------
# upsert_contact
# ---------------------------------------------------------------------------


async def upsert_contact(
    session: AsyncSession,
    mapped: dict[str, Any],
    dry_run: bool = False,
) -> LoaderResult:
    """Upsert a single Contact row keyed on external_id.

    Parameters
    ----------
    mapped:
        Dict produced by the mapper phase.  Must contain 'external_id' (raw
        string token) plus zero or more Contact column keys.  May contain a
        'custom_data' sub-dict of extra key/value pairs to merge.
    dry_run:
        If True, the session is never flushed and no rows are written.

    Returns
    -------
    (outcome, entity_id | None, message)
    """
    raw_ext = mapped.get("external_id")
    ext_id, err = _coerce_external_id(raw_ext)
    if err:
        return "error", None, err

    # Split scalar columns from custom_data payload
    custom_updates: dict = mapped.pop("custom_data", {}) or {}
    # Remove the raw external_id string key — we use the int below
    mapped.pop("external_id", None)

    # SELECT existing row
    result = await session.execute(
        select(Contact).where(Contact.external_id == ext_id)
    )
    existing: Optional[Contact] = result.scalar_one_or_none()

    if dry_run:
        outcome: Outcome = "updated" if existing else "created"
        entity_id = existing.id if existing else None
        return outcome, entity_id, "dry_run"

    if existing:
        # UPDATE — set only provided scalar columns
        for key, val in mapped.items():
            if hasattr(Contact, key):
                setattr(existing, key, val)
        # Merge custom_data
        if custom_updates:
            current_cd = existing.custom_data or {}
            existing.custom_data = _merge_custom_data(dict(current_cd), custom_updates)
        await session.flush()
        return "updated", existing.id, "updated from external_id"
    else:
        # INSERT
        contact = Contact(
            external_id=ext_id,
            custom_data=custom_updates,
            **{k: v for k, v in mapped.items() if hasattr(Contact, k)},
        )
        session.add(contact)
        await session.flush()
        return "created", contact.id, "created from external_id"


# ---------------------------------------------------------------------------
# upsert_event
# ---------------------------------------------------------------------------


async def upsert_event(
    session: AsyncSession,
    mapped: dict[str, Any],
    dry_run: bool = False,
) -> LoaderResult:
    """Upsert a single Event row keyed on external_id.

    Parameters
    ----------
    mapped:
        Dict produced by the mapper phase.  Must contain 'external_id' (raw
        string token) plus zero or more Event column keys.
    dry_run:
        If True, the session is never flushed and no rows are written.

    Returns
    -------
    (outcome, entity_id | None, message)
    """
    raw_ext = mapped.get("external_id")
    ext_id, err = _coerce_external_id(raw_ext)
    if err:
        return "error", None, err

    mapped.pop("external_id", None)

    # SELECT existing row
    result = await session.execute(
        select(Event).where(Event.external_id == ext_id)
    )
    existing: Optional[Event] = result.scalar_one_or_none()

    if dry_run:
        outcome: Outcome = "updated" if existing else "created"
        entity_id = existing.id if existing else None
        return outcome, entity_id, "dry_run"

    if existing:
        for key, val in mapped.items():
            if hasattr(Event, key):
                setattr(existing, key, val)
        await session.flush()
        return "updated", existing.id, "updated from external_id"
    else:
        event = Event(
            external_id=ext_id,
            **{k: v for k, v in mapped.items() if hasattr(Event, k)},
        )
        session.add(event)
        await session.flush()
        return "created", event.id, "created from external_id"


# ---------------------------------------------------------------------------
# bulk_insert_participants
# ---------------------------------------------------------------------------


async def bulk_insert_participants(
    session: AsyncSession,
    rows: list[dict[str, Any]],
    contact_id_map: dict[str, int],
    event_id_map: dict[str, int],
    dry_run: bool = False,
) -> list[LoaderResult]:
    """Resolve external IDs and bulk-upsert participant rows.

    Parameters
    ----------
    rows:
        List of mapped participant dicts.  Each dict must contain
        'contact_external_id' and 'event_external_id' (raw string tokens).
    contact_id_map:
        Pre-loaded mapping of contact external_id string -> Contact.id PK.
    event_id_map:
        Pre-loaded mapping of event external_id string -> Event.id PK.
    dry_run:
        If True, resolution is performed but no rows are written.

    Returns
    -------
    Per-row list of (outcome, entity_id | None, message).
    entity_id is always None for bulk-inserted participants because
    bulk_upsert_participants returns only a rowcount, not individual PKs.
    """
    outcomes: list[LoaderResult] = []
    resolved_rows: list[dict[str, Any]] = []

    for row in rows:
        c_ext = str(row.get("contact_external_id", ""))
        e_ext = str(row.get("event_external_id", ""))

        contact_id = contact_id_map.get(c_ext)
        event_id = event_id_map.get(e_ext)

        if contact_id is None:
            outcomes.append(
                ("error", None, f"contact external_id {c_ext!r} not found in id map")
            )
            continue
        if event_id is None:
            outcomes.append(
                ("error", None, f"event external_id {e_ext!r} not found in id map")
            )
            continue

        resolved: dict[str, Any] = {
            k: v
            for k, v in row.items()
            if k not in ("contact_external_id", "event_external_id")
        }
        resolved["contact_id"] = contact_id
        resolved["event_id"] = event_id
        resolved.setdefault("source", "migration")

        resolved_rows.append(resolved)
        # Placeholder — will be overwritten with 'created' or 'skipped' below
        outcomes.append(("created", None, "pending"))

    if not resolved_rows or dry_run:
        # Replace 'pending' placeholders with dry_run outcome
        final: list[LoaderResult] = []
        pending_iter = iter(resolved_rows)
        for outcome_item in outcomes:
            if outcome_item[2] == "pending":
                next(pending_iter)
                final.append(("created", None, "dry_run"))
            else:
                final.append(outcome_item)
        return final

    # Delegate to bulk_service — it handles ON CONFLICT dedup
    count = await bulk_service.bulk_upsert_participants(session, resolved_rows)

    # Build final outcomes: replace 'pending' slots with insert/skip based on count
    # bulk_upsert_participants returns total rowcount (inserted + updated).
    # We cannot distinguish per-row; mark all resolved rows as 'created' when
    # count > 0, otherwise 'skipped'.
    insert_outcome: Outcome = "created" if count > 0 else "skipped"
    final_outcomes: list[LoaderResult] = []
    pending_seen = 0
    for outcome_item in outcomes:
        if outcome_item[2] == "pending":
            pending_seen += 1
            final_outcomes.append((insert_outcome, None, f"bulk upsert rowcount={count}"))
        else:
            final_outcomes.append(outcome_item)

    return final_outcomes


# ---------------------------------------------------------------------------
# write_people_link
# ---------------------------------------------------------------------------


async def write_people_link(
    session: AsyncSession,
    source_contact_id: int,
    field_name: str,
    resolved_contact_id: int,
) -> None:
    """Idempotently write a contact-reference link into Contact.custom_data.

    Reads the existing custom_data dict, skips the write if field_name is
    already set (idempotent), otherwise merges {field_name: resolved_contact_id}
    and writes an audit record.

    The audit_svc.record() call is wrapped in try/except so a missing
    audit_log table (e.g. bare test DB) never crashes the migration runner.
    """
    result = await session.execute(
        select(Contact).where(Contact.id == source_contact_id)
    )
    contact: Optional[Contact] = result.scalar_one_or_none()
    if contact is None:
        logger.warning(
            "write_people_link: contact id=%s not found, skipping", source_contact_id
        )
        return

    current_cd: dict = dict(contact.custom_data or {})
    if field_name in current_cd:
        # Already set — idempotent skip
        return

    current_cd[field_name] = resolved_contact_id
    contact.custom_data = current_cd
    await session.flush()

    try:
        await audit_svc.record(
            db=session,
            actor_id=None,
            action="migration_link",
            entity="contact",
            entity_id=source_contact_id,
            before={field_name: None},
            after={field_name: resolved_contact_id},
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "write_people_link: audit_svc.record failed (table may not exist) "
            "for contact id=%s field=%s — continuing",
            source_contact_id,
            field_name,
        )
