"""Member status snapshot recompute service — S23.

Computes the 7 derived snapshot columns on Contact for all in-scope
(individual, not-deleted) contacts:
    last_attended_at, attendance_count, weeks_absent, tier,
    is_active, is_regular, is_connected

Public API
----------
compute_snapshot_for_contacts(db, contact_ids, *, today) -> list[ContactSnapshot]
recompute_all(db, *, today)                               -> RecomputeResult
recompute_contacts(db, contact_ids, *, today)             -> RecomputeResult
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_CONNECTED_FIELD_NAMES
from app.models import AdminSetting, Contact, Event, Participant, utc_now as _utc_now
from app.services import audit as audit_svc
from app.utils.db_helpers import has_table


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ContactSnapshot:
    contact_id: int
    last_attended_at: Optional[datetime]
    attendance_count: int
    weeks_absent: Optional[int]
    tier: Optional[str]
    is_active: Optional[bool]
    is_regular: bool
    is_connected: bool


@dataclass
class RecomputeResult:
    contact_count: int
    duration_ms: int
    job_run_id: Optional[int]


# ---------------------------------------------------------------------------
# Tier helper
# ---------------------------------------------------------------------------


def _tier_from_weeks_absent(weeks: int | None) -> str | None:
    """Map weeks-absent to a tier string.

    None  → None
    0     → "tier0"
    1–4   → "tier1"
    5–8   → "tier2"
    9–12  → "tier3"
    ≥13   → "inactive"
    """
    if weeks is None:
        return None
    if weeks == 0:
        return "tier0"
    if weeks <= 4:
        return "tier1"
    if weeks <= 8:
        return "tier2"
    if weeks <= 12:
        return "tier3"
    return "inactive"


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------


async def compute_snapshot_for_contacts(
    db: AsyncSession,
    contact_ids: list[int] | None,
    *,
    today: datetime | None = None,
) -> list[ContactSnapshot]:
    """Compute snapshot fields for every in-scope contact.

    In-scope = contact_type == 'individual' AND is_deleted == False,
    optionally filtered to the given contact_ids list.

    Parameters
    ----------
    db:
        Active async session.
    contact_ids:
        Optional list of contact IDs to restrict computation.
        None = all in-scope contacts.
    today:
        Override for "now" (naive UTC datetime).  When None, uses utc_now()
        truncated to the start of day.

    Returns
    -------
    List of ContactSnapshot dataclasses (one per in-scope contact).
    """
    # --- resolve connected field names ---
    setting_result = await db.execute(
        select(AdminSetting).where(AdminSetting.key == "connected_field_names")
    )
    setting = setting_result.scalar_one_or_none()
    if setting is not None:
        val = setting.value
        if isinstance(val, list):
            connected_field_names: frozenset[str] = frozenset(val)
        elif isinstance(val, dict) and "names" in val:
            connected_field_names = frozenset(val["names"])
        else:
            connected_field_names = DEFAULT_CONNECTED_FIELD_NAMES
    else:
        connected_field_names = DEFAULT_CONNECTED_FIELD_NAMES

    # --- today midnight (naive UTC start-of-day) ---
    base_dt: datetime = today if today is not None else _utc_now()
    today_midnight = base_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- query in-scope contacts ---
    contacts_q = select(Contact).where(
        Contact.contact_type == "individual",
        Contact.is_deleted == False,  # noqa: E712
    )
    if contact_ids is not None:
        contacts_q = contacts_q.where(Contact.id.in_(contact_ids))

    contacts_result = await db.execute(contacts_q)
    contacts = contacts_result.scalars().all()

    if not contacts:
        return []

    contact_id_list = [c.id for c in contacts]

    # --- attendance aggregate: MAX(start_at), COUNT(*) per contact ---
    agg_q = (
        select(
            Participant.contact_id,
            func.max(Event.start_at).label("last_attended_at"),
            func.count(Participant.id).label("attendance_count"),
        )
        .join(Event, Event.id == Participant.event_id)
        .where(
            Participant.status == "attended",
            Participant.contact_id.in_(contact_id_list),
        )
        .group_by(Participant.contact_id)
    )

    agg_result = await db.execute(agg_q)
    agg_rows = agg_result.all()

    agg_by_contact: dict[int, tuple[Optional[datetime], int]] = {
        row.contact_id: (row.last_attended_at, int(row.attendance_count))
        for row in agg_rows
    }

    # --- build snapshot per contact ---
    snapshots: list[ContactSnapshot] = []
    for contact in contacts:
        if contact.id in agg_by_contact:
            last_attended_at, attendance_count = agg_by_contact[contact.id]
        else:
            last_attended_at = None
            attendance_count = 0

        if last_attended_at is not None:
            # Truncate last_attended_at to start-of-day so weeks_absent is
            # based on calendar days rather than clock time (avoids rounding
            # errors when event.start_at has a non-midnight time component).
            last_attended_midnight = last_attended_at.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            weeks_absent: Optional[int] = (today_midnight - last_attended_midnight).days // 7
        else:
            weeks_absent = None

        tier = _tier_from_weeks_absent(weeks_absent)
        is_regular = attendance_count >= 10

        if last_attended_at is None:
            is_active: Optional[bool] = None
        else:
            is_active = tier != "inactive"

        # is_connected: True iff any connected-field key exists and is truthy
        custom_data: dict = contact.custom_data or {}
        is_connected = False
        for fname in connected_field_names:
            if custom_data.get(fname):
                is_connected = True
                break

        snapshots.append(
            ContactSnapshot(
                contact_id=contact.id,
                last_attended_at=last_attended_at,
                attendance_count=attendance_count,
                weeks_absent=weeks_absent,
                tier=tier,
                is_active=is_active,
                is_regular=is_regular,
                is_connected=is_connected,
            )
        )

    return snapshots


# ---------------------------------------------------------------------------
# Shared recompute logic
# ---------------------------------------------------------------------------


async def _recompute(
    db: AsyncSession,
    contact_ids: list[int] | None,
    *,
    today: datetime | None = None,
    write_job_run: bool = True,
) -> RecomputeResult:
    t0 = time.monotonic()

    snapshots = await compute_snapshot_for_contacts(db, contact_ids, today=today)
    contact_count = len(snapshots)

    # --- bulk UPDATE Contact rows (Core-level executemany via Table.update()) ---
    # Use Contact.__table__ (Core Table) rather than the ORM update() construct
    # so SQLAlchemy does not attempt ORM-session synchronization, which is
    # incompatible with the executemany + custom WHERE-bindparam pattern.
    if snapshots:
        _tbl = Contact.__table__
        bulk_stmt = (
            _tbl.update()
            .where(_tbl.c.id == bindparam("b_id"))
            .values(
                last_attended_at=bindparam("b_last_attended_at"),
                attendance_count=bindparam("b_attendance_count"),
                weeks_absent=bindparam("b_weeks_absent"),
                tier=bindparam("b_tier"),
                is_active=bindparam("b_is_active"),
                is_regular=bindparam("b_is_regular"),
                is_connected=bindparam("b_is_connected"),
            )
        )
        params = [
            {
                "b_id": snap.contact_id,
                "b_last_attended_at": snap.last_attended_at,
                "b_attendance_count": snap.attendance_count,
                "b_weeks_absent": snap.weeks_absent,
                "b_tier": snap.tier,
                "b_is_active": snap.is_active,
                "b_is_regular": snap.is_regular,
                "b_is_connected": snap.is_connected,
            }
            for snap in snapshots
        ]
        await db.execute(bulk_stmt, params)

    # --- commit the snapshot UPDATE FIRST so it is durable regardless of the
    # audit outcome (AC15/B16: an audit-write failure must NOT roll back the
    # snapshot write). The previous "retry the same commit" pattern could
    # silently drop the snapshot if audit.record() poisoned the transaction. ---
    await db.commit()

    # --- audit (best-effort, in its own transaction) ---
    try:
        await audit_svc.record(
            db,
            actor_id=None,
            action="member_status.recompute",
            entity="contact",
            entity_id=None,
            before=None,
            after={"contact_count": contact_count},
        )
        await db.commit()
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass

    # --- job_runs INSERT (S16-guarded; only for full recompute_all runs) ---
    job_run_id: Optional[int] = None
    if write_job_run and await has_table(db, "job_runs"):
        try:
            ins_result = await db.execute(
                text(
                    "INSERT INTO job_runs (job_name, status, detail, ran_at)"
                    " VALUES (:job_name, :status, :detail, :ran_at)"
                ),
                {
                    "job_name": "member_status_recompute",
                    "status": "success",
                    "detail": str(contact_count),
                    "ran_at": _utc_now(),
                },
            )
            await db.commit()
            job_run_id = ins_result.lastrowid if ins_result.lastrowid else None
        except Exception:
            job_run_id = None

    duration_ms = int((time.monotonic() - t0) * 1000)
    return RecomputeResult(
        contact_count=contact_count,
        duration_ms=duration_ms,
        job_run_id=job_run_id,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def recompute_all(
    db: AsyncSession,
    *,
    today: datetime | None = None,
) -> RecomputeResult:
    """Recompute snapshots for ALL in-scope (individual, not-deleted) contacts."""
    return await _recompute(db, None, today=today)


async def recompute_all_contacts(
    db: AsyncSession,
    *,
    today: datetime | None = None,
) -> RecomputeResult:
    """Public name the S06/S10 post-import recompute hook calls (CN-24).

    ``app/services/migration/runner.py`` invokes
    ``member_status_service.recompute_all_contacts(db)`` after a live import.
    This is the already-committed consumer contract, so S23 must expose it.
    Delegates to :func:`recompute_all`.
    """
    return await recompute_all(db, today=today)


async def recompute_contacts(
    db: AsyncSession,
    contact_ids: list[int],
    *,
    today: datetime | None = None,
) -> RecomputeResult:
    """Recompute snapshots for a specific set of contacts by ID.

    Partial runs do NOT write a job_runs row (spec S23: only full recomputes log).
    Still writes one audit_log row.
    """
    return await _recompute(db, contact_ids, today=today, write_job_run=False)
