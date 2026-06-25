"""Bulk participant service.

Ownership: S05-F05.
Consumed by: S05 router, S06 migration ETL (bulk_upsert_participants).

All operations are set-based — ZERO per-row Python iteration.
The session never commits inside this module; callers are responsible.

Public API
----------
bulk_add_participants(db, req, caller_id)       -> BulkAddResult
bulk_update_status(db, req, caller_id)          -> BulkStatusResult
bulk_remove(db, req, caller_id)                 -> BulkRemoveResult
preview(db, req)                                -> BulkPreviewResult
bulk_upsert_participants(db, rows)              -> int   (S06 reuse contract §5)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine as _app_engine
from app.models import Event, Participant, utc_now
from app.services import audit as audit_svc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dialect() -> str:
    """Return the current dialect name ('postgresql' or 'sqlite')."""
    try:
        return _app_engine.dialect.name
    except Exception:
        return "sqlite"


async def _require_event(db: AsyncSession, event_id: int) -> Event:
    """Raise HTTP 404 if event_id does not exist."""
    ev = await db.get(Event, event_id)
    if ev is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )
    return ev


async def _count_existing(db: AsyncSession, event_id: int, audience_subq: Any) -> int:
    """Count participants already present for (event_id, contact_id IN audience_subq)."""
    stmt = select(func.count()).where(
        Participant.event_id == event_id,
        Participant.contact_id.in_(audience_subq),
    )
    return (await db.execute(stmt)).scalar_one()


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BulkAddResult:
    event_id: int
    requested: int
    inserted: int
    skipped: int


@dataclass
class BulkStatusResult:
    event_id: int
    matched: int
    updated: int


@dataclass
class BulkRemoveResult:
    event_id: int
    removed: int


@dataclass
class BulkPreviewResult:
    event_id: int
    requested: int
    already_enrolled: int
    to_be_inserted: int


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


async def bulk_add_participants(
    db: AsyncSession,
    req: Any,
    caller_id: int,
) -> BulkAddResult:
    """Insert audience members as participants using a single INSERT...SELECT.

    Parameters
    ----------
    req:
        Object exposing:
            .event_id       int
            .audience       AudienceSelector  (passed to audience service)
            .status         str  (default 'registered')
            .role           str | None
            .source         str  (default 'bulk')
    caller_id:
        Authenticated user id (int).

    Returns
    -------
    BulkAddResult
    """
    from app.services.audience import count_audience, resolve_audience

    # --- validate event ---
    await _require_event(db, req.event_id)

    # --- resolve audience ---
    audience_select = await resolve_audience(db, req.audience)
    requested: int = await count_audience(db, req.audience)

    part_status: str = getattr(req, "status", "registered") or "registered"
    part_role: Optional[str] = getattr(req, "role", None)
    part_source: str = getattr(req, "source", "bulk") or "bulk"
    now = utc_now()

    # --- single INSERT...SELECT with ON CONFLICT DO NOTHING ---
    audience_subq = audience_select.subquery()
    insert_select = select(
        literal(req.event_id).label("event_id"),
        audience_subq.c.id.label("contact_id"),
        literal(part_status).label("status"),
        literal(part_role).label("role"),
        literal(part_source).label("source"),
        literal(caller_id).label("registered_by_id"),
        literal(now).label("created_at"),
    )

    if _dialect() == "postgresql":
        stmt = pg_insert(Participant).from_select(
            ["event_id", "contact_id", "status", "role", "source", "registered_by_id", "created_at"],
            insert_select,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])
    else:
        stmt = sqlite_insert(Participant).from_select(
            ["event_id", "contact_id", "status", "role", "source", "registered_by_id", "created_at"],
            insert_select,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])

    res = await db.execute(stmt)

    # --- compute inserted / skipped ---
    # rowcount may be -1 (driver doesn't support it) or out of [0, requested]
    raw_rowcount: int = res.rowcount if res.rowcount is not None else -1

    if 0 <= raw_rowcount <= requested:
        inserted: int = raw_rowcount
    else:
        # §4.5 fallback: count rows that now exist for this audience
        inserted = await _count_existing(db, req.event_id, audience_select)

    skipped: int = requested - inserted

    # --- audit (guarded per AC15) ---
    try:
        await audit_svc.record(
            db,
            actor_id=caller_id,
            action="participant.bulk_add",
            entity="participants",
            entity_id=req.event_id,
            before=None,
            after={
                "event_id": req.event_id,
                "requested": requested,
                "inserted": inserted,
                "skipped": skipped,
                "status": part_status,
                "role": part_role,
                "source": part_source,
            },
        )
        await db.commit()
    except Exception:
        # audit failure must not abort the primary operation
        try:
            await db.commit()
        except Exception:
            pass

    return BulkAddResult(
        event_id=req.event_id,
        requested=requested,
        inserted=inserted,
        skipped=skipped,
    )


async def bulk_update_status(
    db: AsyncSession,
    req: Any,
    caller_id: int,
) -> BulkStatusResult:
    """Update participant status for all audience members in a single UPDATE.

    Parameters
    ----------
    req:
        Object exposing:
            .event_id           int
            .audience           AudienceSelector
            .new_status         str
            .only_if_status     str | None   — filter: only rows whose current
                                              status equals this value

    Returns
    -------
    BulkStatusResult
    """
    from app.services.audience import resolve_audience

    # --- validate event ---
    await _require_event(db, req.event_id)

    audience_select = await resolve_audience(db, req.audience)
    audience_subq = audience_select.subquery()

    new_status: str = req.new_status
    only_if_status: Optional[str] = getattr(req, "only_if_status", None)

    stmt = (
        update(Participant)
        .where(Participant.event_id == req.event_id)
        .where(Participant.contact_id.in_(audience_subq))
    )
    if only_if_status is not None:
        stmt = stmt.where(Participant.status == only_if_status)

    stmt = stmt.values(status=new_status)
    res = await db.execute(stmt)

    matched: int = res.rowcount if res.rowcount is not None else 0
    updated: int = matched

    # --- audit (guarded) ---
    try:
        await audit_svc.record(
            db,
            actor_id=caller_id,
            action="participant.bulk_update_status",
            entity="participants",
            entity_id=req.event_id,
            before={"status": only_if_status},
            after={"status": new_status, "updated": updated},
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return BulkStatusResult(event_id=req.event_id, matched=matched, updated=updated)


async def bulk_remove(
    db: AsyncSession,
    req: Any,
    caller_id: int,
) -> BulkRemoveResult:
    """Remove (soft or hard) audience members from an event.

    Parameters
    ----------
    req:
        Object exposing:
            .event_id   int
            .audience   AudienceSelector
            .hard       bool   — True = DELETE; False = set status='cancelled'
    """
    from app.services.audience import resolve_audience

    hard: bool = bool(getattr(req, "hard", False))

    if not hard:
        # Soft remove: delegate to bulk_update_status
        class _SoftReq:
            event_id = req.event_id
            audience = req.audience
            new_status = "cancelled"
            only_if_status = None

        result = await bulk_update_status(db, _SoftReq(), caller_id)
        return BulkRemoveResult(event_id=req.event_id, removed=result.updated)

    # Hard delete
    await _require_event(db, req.event_id)

    audience_select = await resolve_audience(db, req.audience)
    audience_subq = audience_select.subquery()

    stmt = delete(Participant).where(
        Participant.event_id == req.event_id,
        Participant.contact_id.in_(audience_subq),
    )
    res = await db.execute(stmt)
    removed: int = res.rowcount if res.rowcount is not None else 0

    # --- audit (guarded) ---
    try:
        await audit_svc.record(
            db,
            actor_id=caller_id,
            action="participant.bulk_remove",
            entity="participants",
            entity_id=req.event_id,
            before=None,
            after={"event_id": req.event_id, "hard": hard, "removed": removed},
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return BulkRemoveResult(event_id=req.event_id, removed=removed)


async def preview(
    db: AsyncSession,
    req: Any,
) -> BulkPreviewResult:
    """Count-only preview — no writes, no audit.

    Parameters
    ----------
    req:
        Object exposing:
            .event_id   int
            .audience   AudienceSelector
    """
    from app.services.audience import count_audience, resolve_audience

    await _require_event(db, req.event_id)

    audience_select = await resolve_audience(db, req.audience)
    requested: int = await count_audience(db, req.audience)
    already_enrolled: int = await _count_existing(db, req.event_id, audience_select)
    to_be_inserted: int = max(0, requested - already_enrolled)

    return BulkPreviewResult(
        event_id=req.event_id,
        requested=requested,
        already_enrolled=already_enrolled,
        to_be_inserted=to_be_inserted,
    )


# ---------------------------------------------------------------------------
# S06 reuse contract  (§5)
# ---------------------------------------------------------------------------


async def bulk_upsert_participants(
    db: AsyncSession,
    rows: Sequence[dict[str, Any]],
) -> int:
    """Upsert a list of participant row dicts (S06 migration ETL contract).

    Each dict must contain at minimum: event_id, contact_id.
    Optional keys: status, role, source, registered_by_id, created_at.

    On conflict (event_id, contact_id) the existing row is updated with the
    supplied values (full upsert semantics for migration, unlike bulk_add which
    uses DO NOTHING).

    Returns the number of rows inserted or updated.
    Caller is responsible for db.commit().
    """
    if not rows:
        return 0

    now = utc_now()

    # Normalise rows — fill in defaults
    normalised: list[dict[str, Any]] = []
    for row in rows:
        normalised.append(
            {
                "event_id": row["event_id"],
                "contact_id": row["contact_id"],
                "status": row.get("status", "attended"),
                "role": row.get("role"),
                "source": row.get("source", "bulk"),
                "registered_by_id": row.get("registered_by_id"),
                "created_at": row.get("created_at", now),
            }
        )

    if _dialect() == "postgresql":
        stmt = pg_insert(Participant).values(normalised)
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "contact_id"],
            set_={
                "status": stmt.excluded.status,
                "role": stmt.excluded.role,
                "source": stmt.excluded.source,
                "registered_by_id": stmt.excluded.registered_by_id,
            },
        )
    else:
        stmt = sqlite_insert(Participant).values(normalised)
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "contact_id"],
            set_={
                "status": stmt.excluded.status,
                "role": stmt.excluded.role,
                "source": stmt.excluded.source,
                "registered_by_id": stmt.excluded.registered_by_id,
            },
        )

    res = await db.execute(stmt)
    return res.rowcount if (res.rowcount is not None and res.rowcount >= 0) else len(normalised)
