from datetime import datetime, timezone
from typing import List, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import Attendance, CiviCRMEvent, CiviCRMMember, Detection, Task
from app.schemas import AttendanceRecord, AttendeeSummary, DeadLetterListResponse, DeadLetterRecord, DeadLetterRetryResponse, PushDiff

router = APIRouter(prefix="/attendance", tags=["attendance"])

logger = logging.getLogger(__name__)


@router.get("", response_model=List[AttendanceRecord])
async def list_attendance(
    event_id: Optional[int] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List attendance records with optional filters (bounded; newest first)."""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = select(Attendance).order_by(Attendance.created_at.desc())

    if event_id:
        query = query.where(Attendance.event_id == event_id)
    if status:
        query = query.where(Attendance.status == status)
    if date:
        try:
            # Naive UTC to match the naive DateTime columns
            start = datetime.strptime(date, "%Y-%m-%d")
            end = start.replace(hour=23, minute=59, second=59)
            query = query.where(Attendance.created_at >= start, Attendance.created_at <= end)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return records


@router.post("/push-preview", response_model=PushDiff)
async def push_preview(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Dry-run preview of attendance push to CiviCRM."""
    event = await db.get(CiviCRMEvent, event_id)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
        )

    # Get pending attendance for this event
    result = await db.execute(
        select(Attendance, Detection)
        .join(Detection, Attendance.detection_id == Detection.id)
        .where(Attendance.event_id == event_id)
        .where(Attendance.push_status == "pending")
    )
    rows = result.all()

    will_attend: List[AttendeeSummary] = []
    duplicates_warn: List[str] = []
    seen_names = set()

    for attendance, detection in rows:
        # Resolve "member:{id}" to a real display name for the preview
        raw_name = detection.matched_name or ""
        if raw_name.startswith("member:"):
            try:
                contact_id = int(raw_name.split(":", 1)[1])
                member = await db.get(CiviCRMMember, contact_id)
                raw_name = f"{member.first_name} {member.last_name}".strip() if member else raw_name
            except (ValueError, IndexError):
                pass
        name = raw_name or "Unknown"
        if name in seen_names:
            duplicates_warn.append(name)
        seen_names.add(name)

        will_attend.append(
            AttendeeSummary(
                member_id=attendance.contact_id or 0,
                name=name,
                detected_at=detection.timestamp,
                camera_name=f"Camera {detection.camera_id}" if detection.camera_id else None,
                included=True,
            )
        )

    # TODO: Query CiviCRM for RSVP'd but not detected members
    missing: List[AttendeeSummary] = []

    return PushDiff(
        event_id=event_id,
        event_title=event.title,
        will_attend=will_attend,
        missing=missing,
        duplicates_warn=duplicates_warn,
    )


@router.post("/push")
async def push_attendance(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Execute attendance push to CiviCRM."""
    event = await db.get(CiviCRMEvent, event_id)
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found"
        )

    result = await db.execute(
        select(Attendance).where(
            (Attendance.event_id == event_id)
            & (Attendance.push_status == "pending")
        )
    )
    records = result.scalars().all()

    if not records:
        return {"message": "No pending records to push", "pushed": 0}

    # Queue for background worker
    for record in records:
        record.push_status = "queued"

    await db.commit()

    return {
        "message": f"Queued {len(records)} attendance records for push",
        "pushed": len(records),
        "event_id": event_id,
    }


# ---------------------------------------------------------------------------
# Dead-letter queue endpoints (admin only)
# ---------------------------------------------------------------------------


@router.get(
    "/dead-letter",
    response_model=DeadLetterListResponse,
    summary="List dead-letter attendance records",
)
async def list_dead_letter(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
) -> DeadLetterListResponse:
    """Return all attendance records whose push has permanently failed
    (``push_status = 'dead_letter'``).

    Requires admin role. Use ``POST /attendance/dead-letter/{id}/retry`` to
    reset a record for manual re-processing.
    """
    count_result = await db.execute(
        select(func.count()).where(Attendance.push_status == "dead_letter")
    )
    total: int = count_result.scalar_one()

    records_result = await db.execute(
        select(Attendance)
        .where(Attendance.push_status == "dead_letter")
        .order_by(Attendance.created_at.desc())
    )
    records = records_result.scalars().all()

    return DeadLetterListResponse(
        total=total,
        items=[DeadLetterRecord.model_validate(r) for r in records],
    )


@router.post(
    "/dead-letter/{attendance_id}/retry",
    response_model=DeadLetterRetryResponse,
    summary="Reset a dead-letter record for manual retry",
)
async def retry_dead_letter(
    attendance_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
) -> DeadLetterRetryResponse:
    """Reset ``push_attempts`` to 0 and ``push_status`` back to ``'pending'``
    so the background worker will attempt to push the record again.

    Requires admin role.
    """
    record: Optional[Attendance] = await db.get(Attendance, attendance_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attendance record {attendance_id} not found.",
        )

    if record.push_status != "dead_letter":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Record {attendance_id} is not in dead_letter state "
                f"(current status: '{record.push_status}')."
            ),
        )

    record.push_status = "pending"
    record.push_attempts = 0
    record.last_push_error = None
    await db.commit()
    await db.refresh(record)

    logger.info(
        "Dead-letter record reset for retry by admin: attendance_id=%s admin_id=%s",
        attendance_id,
        getattr(user, "id", "unknown"),
    )

    return DeadLetterRetryResponse(
        id=record.id,
        push_status=record.push_status,
        push_attempts=record.push_attempts,
        message=f"Attendance record {attendance_id} has been reset to 'pending' for retry.",
    )
