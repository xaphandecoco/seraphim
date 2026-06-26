"""Admin analytics endpoints — attendance per event, volunteer stats, tier distribution, CSV export."""

from __future__ import annotations

import csv
import io
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import Contact, Detection, Event, Log, Participant, User, VolunteerStat
from app.utils.db_helpers import has_table

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Schemas (S23 additions)
# ---------------------------------------------------------------------------


class RecomputeMemberStatusRequest(BaseModel):
    contact_ids: Optional[list[int]] = None


# ---------------------------------------------------------------------------
# Existing endpoints
# ---------------------------------------------------------------------------


@router.get("/attendance-by-event")
async def attendance_by_event(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Attendance count per event (attended records)."""
    result = await db.execute(
        select(Event.id, Event.title, Event.start_at,
               func.count(Participant.id).label("count"))
        .join(Participant, Participant.event_id == Event.id, isouter=True)
        .where(Participant.status == "attended")
        .group_by(Event.id, Event.title, Event.start_at)
        .order_by(Event.start_at.desc())
        .limit(20)
    )
    rows = result.all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "start_at": r.start_at.isoformat() if r.start_at else None,
            "count": r.count,
        }
        for r in rows
    ]


@router.get("/tier-distribution")
async def tier_distribution(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Count of detections by confidence tier."""
    result = await db.execute(
        select(Detection.tier, func.count(Detection.id).label("count"))
        .group_by(Detection.tier)
    )
    rows = result.all()
    return [{"tier": r.tier or "unknown", "count": r.count} for r in rows]


@router.get("/volunteer-stats")
async def volunteer_stats(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Aggregated volunteer performance metrics."""
    result = await db.execute(
        select(
            User.id,
            User.name,
            User.email,
            func.sum(VolunteerStat.tasks_confirmed).label("confirmed"),
            func.sum(VolunteerStat.tasks_edited).label("edited"),
            func.sum(VolunteerStat.tasks_added).label("added"),
            func.sum(VolunteerStat.total_points).label("points"),
        )
        .join(VolunteerStat, VolunteerStat.volunteer_id == User.id, isouter=True)
        .group_by(User.id, User.name, User.email)
        .order_by(func.sum(VolunteerStat.total_points).desc())
    )
    rows = result.all()
    return [
        {
            "volunteer_id": r.id,
            "name": r.name or r.email,
            "confirmed": int(r.confirmed or 0),
            "edited": int(r.edited or 0),
            "added": int(r.added or 0),
            "points": int(r.points or 0),
        }
        for r in rows
    ]


@router.get("/queue-health")
async def queue_health(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Daily detection and resolution counts for the last 14 days."""
    from sqlalchemy import cast, Date
    result = await db.execute(
        select(
            cast(Detection.created_at, Date).label("day"),
            func.count(Detection.id).label("total"),
            func.count(Detection.id).filter(Detection.status == "resolved").label("resolved"),
        )
        .group_by(cast(Detection.created_at, Date))
        .order_by(cast(Detection.created_at, Date).desc())
        .limit(14)
    )
    rows = result.all()
    return [
        {"day": str(r.day), "total": r.total, "resolved": r.resolved}
        for r in reversed(rows)
    ]


@router.get("/export/logs")
async def export_logs_csv(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Stream system log entries as CSV."""
    result = await db.execute(
        select(Log.id, Log.timestamp, Log.action, Log.matched_name,
               Log.confidence, Log.tier, Log.camera_id)
        .order_by(Log.timestamp.desc())
        .limit(5000)
    )
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "action", "matched_name", "confidence", "tier", "camera_id"])
    for r in rows:
        writer.writerow([r.id, r.timestamp.isoformat() if r.timestamp else "",
                         r.action, r.matched_name or "", r.confidence or "", r.tier or "",
                         r.camera_id or ""])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=logs.csv"},
    )


# ---------------------------------------------------------------------------
# S23 — Member status endpoints
# ---------------------------------------------------------------------------


@router.post("/recompute-member-status")
async def recompute_member_status(
    body: Optional[RecomputeMemberStatusRequest] = Body(default=None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Trigger a synchronous member-status snapshot recompute.

    Admin only.  Optional body: {"contact_ids": [1, 2, 3]} to limit scope.
    Maximum 500 contact_ids per call.
    """
    from app.services.member_status_service import recompute_all, recompute_contacts

    contact_ids: Optional[list[int]] = body.contact_ids if body is not None else None

    if contact_ids is not None and len(contact_ids) > 500:
        raise HTTPException(
            status_code=422,
            detail="contact_ids may not exceed 500 entries.",
        )

    if contact_ids is None:
        result = await recompute_all(db)
    else:
        result = await recompute_contacts(db, contact_ids)

    return {
        "contact_count": result.contact_count,
        "duration_ms": result.duration_ms,
        "job_run_id": result.job_run_id,
    }


@router.get("/member-status-summary")
async def member_status_summary(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Fast aggregate summary of member status snapshot columns.

    Admin only.  S15 will widen access.
    Returns tier_counts (values sum to total_contacts, includes 'null' key),
    is_active_count, is_inactive_count, is_regular_count, is_connected_count,
    total_contacts, and last_recomputed_at.
    """
    in_scope_filters = (
        Contact.contact_type == "individual",
        Contact.is_deleted == False,  # noqa: E712
    )

    # --- tier distribution (including NULL tier) ---
    tier_result = await db.execute(
        select(Contact.tier, func.count(Contact.id).label("cnt"))
        .where(*in_scope_filters)
        .group_by(Contact.tier)
    )
    tier_rows = tier_result.all()

    tier_counts: dict[str, int] = {}
    total_contacts = 0
    for row in tier_rows:
        key = row.tier if row.tier is not None else "null"
        tier_counts[key] = int(row.cnt)
        total_contacts += int(row.cnt)

    # Always include the "null" key even when zero
    if "null" not in tier_counts:
        tier_counts["null"] = 0

    # --- aggregated boolean counts ---
    stats_result = await db.execute(
        select(
            func.count(Contact.id).filter(Contact.is_active == True).label("active"),
            func.count(Contact.id).filter(Contact.is_active == False).label("inactive"),
            func.count(Contact.id).filter(Contact.is_regular == True).label("regular"),
            func.count(Contact.id).filter(Contact.is_connected == True).label("connected"),
        )
        .where(*in_scope_filters)
    )
    stats = stats_result.one()

    # --- last_recomputed_at (S16-guarded) ---
    last_recomputed_at: Optional[str] = None
    if await has_table(db, "job_runs"):
        try:
            lr_result = await db.execute(
                text(
                    "SELECT MAX(started_at) AS max_ran_at FROM job_runs"
                    " WHERE job_name = :job_name"
                ),
                {"job_name": "member_status_recompute"},
            )
            row = lr_result.one_or_none()
            if row and row[0] is not None:
                val = row[0]
                last_recomputed_at = val.isoformat() if hasattr(val, "isoformat") else str(val)
        except Exception:
            last_recomputed_at = None

    return {
        "tier_counts": tier_counts,
        "is_active_count": int(stats.active or 0),
        "is_inactive_count": int(stats.inactive or 0),
        "is_regular_count": int(stats.regular or 0),
        "is_connected_count": int(stats.connected or 0),
        "total_contacts": total_contacts,
        "last_recomputed_at": last_recomputed_at,
    }
