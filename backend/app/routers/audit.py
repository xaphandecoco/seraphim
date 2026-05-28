"""Audit tasks router — quality verification for enrolled faces.

Surfaced when the main task queue is empty. Volunteers review already-enrolled
faces to confirm, deny, or correct the match. Actions are logged and feed into
volunteer accuracy metrics.
"""

import random
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Camera, Detection, Log, Task, User, VolunteerStat
from app.schemas import AuditActionRequest, AuditTaskResponse

router = APIRouter(prefix="/api/audit", tags=["audit"])


def _extract_contact_id(matched_name: Optional[str]) -> Optional[int]:
    """Parse 'member:123' into integer contact_id."""
    if matched_name and matched_name.startswith("member:"):
        try:
            return int(matched_name.split(":", 1)[1])
        except (ValueError, IndexError):
            pass
    return None


@router.get("/tasks", response_model=List[AuditTaskResponse])
async def get_audit_tasks(
    limit: int = 10,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return enrolled detections for volunteer audit review.

    Only surfaces when the main task queue is empty (no pending tasks).
    Returns a random selection of enrolled faces for quality verification.
    """
    # Check main queue — only serve audit tasks when empty
    pending_count_result = await session.execute(
        select(func.count(Task.id)).where(Task.status == "pending")
    )
    pending_count = pending_count_result.scalar() or 0
    if pending_count > 0:
        return []

    # Get enrolled detections with camera info
    result = await session.execute(
        select(Detection, Camera.name.label("camera_name"))
        .outerjoin(Camera, Detection.camera_id == Camera.id)
        .where(Detection.is_enrolled.is_(True))
        .where(Detection.matched_name.isnot(None))
        .order_by(func.random())
        .limit(limit)
    )

    rows = result.all()
    tasks = []
    for detection, camera_name in rows:
        contact_id = _extract_contact_id(detection.matched_name)
        tasks.append(
            AuditTaskResponse(
                detection_id=detection.id,
                face_thumbnail_path=detection.image_path,
                matched_name=detection.matched_name,
                confidence=float(detection.confidence) if detection.confidence else None,
                tier=detection.tier,
                camera_name=camera_name,
                detected_at=detection.timestamp,
                contact_id=contact_id,
            )
        )
    return tasks


async def _record_audit_action(
    session: AsyncSession,
    detection_id: int,
    volunteer_id: int,
    action: str,
    new_contact_id: Optional[int] = None,
):
    """Record an audit action in logs and update volunteer accuracy."""
    detection = await session.get(Detection, detection_id)
    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Detection not found"
        )

    # Log the action
    log = Log(
        detection_id=detection_id,
        timestamp=detection.timestamp if detection else datetime.now(timezone.utc),
        camera_id=detection.camera_id if detection else None,
        matched_name=detection.matched_name if detection else None,
        confidence=detection.confidence if detection else None,
        tier=detection.tier if detection else None,
        action=action,
        volunteer_id=volunteer_id,
        event_id=detection.event_id if detection else None,
    )
    session.add(log)

    # Update volunteer stats (audit actions don't add points but track accuracy)
    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    stat = await session.get(VolunteerStat, (volunteer_id, month_key))
    if not stat:
        stat = VolunteerStat(volunteer_id=volunteer_id, month=month_key)
        session.add(stat)

    # Simple accuracy model: confirm = +0.5% accuracy, deny/edit = -0.2%
    # Real honeypot logic would be more sophisticated
    if action == "audit_confirmed":
        stat.accuracy_score = min(100.0, float(stat.accuracy_score) + 0.5)
    elif action in ("audit_denied", "audit_edited"):
        stat.accuracy_score = max(0.0, float(stat.accuracy_score) - 0.2)

    await session.commit()


@router.post("/{detection_id}/confirm")
async def audit_confirm(
    detection_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Volunteer confirms the enrolled face/name match is correct."""
    await _record_audit_action(
        session, detection_id, current_user.id, "audit_confirmed"
    )
    return {"detail": "Audit confirmation recorded"}


@router.post("/{detection_id}/deny")
async def audit_deny(
    detection_id: int,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Volunteer flags the enrolled face/name match as incorrect.

    Admin should review denied audits in the logs/analytics.
    """
    await _record_audit_action(
        session, detection_id, current_user.id, "audit_denied"
    )
    return {"detail": "Audit denial recorded — flagged for admin review"}


@router.post("/{detection_id}/change")
async def audit_change(
    detection_id: int,
    request: AuditActionRequest,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Volunteer changes the enrolled face to a different contact.

    Updates the detection's matched_name and records the audit edit.
    In production, this should also update Compreface training data.
    """
    if request.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="contact_id is required for change action",
        )

    detection = await session.get(Detection, detection_id)
    if not detection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Detection not found"
        )

    # Update detection with new member reference
    detection.matched_name = f"member:{request.contact_id}"

    await _record_audit_action(
        session, detection_id, current_user.id, "audit_edited", request.contact_id
    )
    return {"detail": "Audit change recorded — enrollment updated"}
