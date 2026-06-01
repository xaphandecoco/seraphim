"""Admin analytics endpoints — attendance per event, volunteer stats, tier distribution, CSV export."""

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import Attendance, CiviCRMEvent, Detection, Log, User, VolunteerStat

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/attendance-by-event")
async def attendance_by_event(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Attendance count per event (confirmed records)."""
    result = await db.execute(
        select(CiviCRMEvent.event_id, CiviCRMEvent.title, CiviCRMEvent.start_date,
               func.count(Attendance.id).label("count"))
        .join(Attendance, Attendance.event_id == CiviCRMEvent.event_id, isouter=True)
        .where(Attendance.status == "confirmed")
        .group_by(CiviCRMEvent.event_id, CiviCRMEvent.title, CiviCRMEvent.start_date)
        .order_by(CiviCRMEvent.start_date.desc())
        .limit(20)
    )
    rows = result.all()
    return [
        {
            "event_id": r.event_id,
            "title": r.title,
            "start_date": r.start_date.isoformat() if r.start_date else None,
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


@router.get("/export/attendance")
async def export_attendance_csv(
    event_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Stream attendance records as CSV."""
    query = (
        select(
            Attendance.id,
            Attendance.contact_id,
            Attendance.event_id,
            Attendance.status,
            Attendance.push_status,
            Attendance.created_at,
        )
        .order_by(Attendance.created_at.desc())
    )
    if event_id:
        query = query.where(Attendance.event_id == event_id)

    result = await db.execute(query)
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "contact_id", "event_id", "status", "push_status", "created_at"])
    for r in rows:
        writer.writerow([r.id, r.contact_id, r.event_id, r.status, r.push_status,
                         r.created_at.isoformat() if r.created_at else ""])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=attendance.csv"},
    )


@router.get("/export/logs")
async def export_logs_csv(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
):
    """Stream system log entries as CSV."""
    result = await db.execute(
        select(Log.id, Log.timestamp, Log.action, Log.matched_name,
               Log.confidence, Log.tier, Log.camera_id, Log.push_status)
        .order_by(Log.timestamp.desc())
        .limit(5000)
    )
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "timestamp", "action", "matched_name", "confidence", "tier", "camera_id", "push_status"])
    for r in rows:
        writer.writerow([r.id, r.timestamp.isoformat() if r.timestamp else "",
                         r.action, r.matched_name or "", r.confidence or "", r.tier or "",
                         r.camera_id or "", r.push_status or ""])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=logs.csv"},
    )
