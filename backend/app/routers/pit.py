from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import Detection, PitQueue, Task
from app.schemas import TaskResponse

router = APIRouter(prefix="/pit", tags=["pit"])


@router.get("", response_model=list[TaskResponse])
async def list_pit_tasks(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """List all tasks in the admin pit queue."""
    result = await db.execute(
        select(Task)
        .where(Task.status == "pit")
        .order_by(Task.created_at.desc())
    )
    tasks = result.scalars().all()

    items = []
    for task in tasks:
        detection = await db.get(Detection, task.detection_id)
        items.append(
            TaskResponse(
                id=task.id,
                detection_id=task.detection_id,
                status=task.status,
                required_approvals=task.required_approvals,
                current_approvals=task.current_approvals,
                skip_count=task.skip_count,
                skip_reasons=task.skip_reasons or [],
                tier=detection.tier if detection else None,
                confidence=float(detection.confidence) if detection and detection.confidence else None,
                matched_name=detection.matched_name if detection else None,
                face_thumbnail_path=detection.image_path if detection else None,
                camera_name=f"Camera {detection.camera_id}" if detection else None,
                detected_at=detection.timestamp if detection else None,
                expiry_date=task.expiry_date,
            )
        )
    return items


@router.post("/{task_id}/enroll", response_model=TaskResponse)
async def enroll_pit_task(
    task_id: int,
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Enroll an unidentified pit face to a CiviCRM member."""
    task = await db.get(Task, task_id)
    if not task or task.status != "pit":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pit task not found"
        )

    task.pit_status = "enrolled"
    task.status = "resolved"

    detection = await db.get(Detection, task.detection_id)
    if detection:
        detection.matched_name = f"member:{contact_id}"
        detection.is_enrolled = True

    pit = await db.execute(
        select(PitQueue).where(PitQueue.task_id == task_id)
    )
    pit_record = pit.scalar_one_or_none()
    if pit_record:
        pit_record.admin_action = "enroll"
        pit_record.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(task)

    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=detection.image_path if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/delete", response_model=TaskResponse)
async def delete_pit_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Mark pit task as trash. Image will be purged per retention rules."""
    task = await db.get(Task, task_id)
    if not task or task.status != "pit":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pit task not found"
        )

    task.pit_status = "deleted"
    task.status = "resolved"

    pit = await db.execute(
        select(PitQueue).where(PitQueue.task_id == task_id)
    )
    pit_record = pit.scalar_one_or_none()
    if pit_record:
        pit_record.admin_action = "delete"
        pit_record.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(task)

    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=detection.image_path if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/non-person", response_model=TaskResponse)
async def mark_non_person(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Mark pit task as false detection (lighting glitch, poster, shadow)."""
    task = await db.get(Task, task_id)
    if not task or task.status != "pit":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pit task not found"
        )

    task.pit_status = "non_person"
    task.status = "resolved"

    pit = await db.execute(
        select(PitQueue).where(PitQueue.task_id == task_id)
    )
    pit_record = pit.scalar_one_or_none()
    if pit_record:
        pit_record.admin_action = "non_person"
        pit_record.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(task)

    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=detection.image_path if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )
