from datetime import datetime, timezone

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import legacy_settings
from app.database import get_db
from app.dependencies import require_admin
from app.models import Detection, Participant, PitQueue, Task
from app.schemas import PitEnrollRequest, TaskResponse
from app.services.compreface import ComprefaceClient
from app.services.enrollment import EnrollmentService
from app.services.face_storage import FaceStorage, to_storage_url

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
                face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
                camera_name=f"Camera {detection.camera_id}" if detection else None,
                detected_at=detection.timestamp if detection else None,
                expiry_date=task.expiry_date,
            )
        )
    return items


@router.post("/{task_id}/enroll", response_model=TaskResponse)
async def enroll_pit_task(
    task_id: int,
    body: PitEnrollRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Enroll an unidentified pit face to a contact."""
    task = await db.get(Task, task_id)
    if not task or task.status != "pit":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pit task not found"
        )

    detection = await db.get(Detection, task.detection_id)

    if not detection or not detection.image_path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pit task has no associated detection image; cannot enroll",
        )

    # Enroll the face sample via EnrollmentService before mutating task/detection state.
    client = ComprefaceClient()
    storage = FaceStorage(legacy_settings.STORAGE_PATH)
    service = EnrollmentService(client, storage)
    try:
        image_bytes = storage.read_detection_full_image(detection.image_path)
        if not image_bytes:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Detection image file not found on disk; cannot enroll",
            )
        face_crop = cv2.imdecode(
            np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if face_crop is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Detection image could not be decoded; cannot enroll",
            )
        await service.enroll_contact_face(
            session=db,
            contact_id=body.contact_id,
            face_crop=face_crop,
            source="detection",
        )
    finally:
        await client.close()

    task.pit_status = "enrolled"
    task.status = "resolved"

    if detection:
        detection.matched_name = f"member:{body.contact_id}"
        detection.is_enrolled = True

    # Write a Participant record for attendance tracking when the detection is
    # linked to an active event.  Guard with a pre-SELECT on (contact_id, event_id)
    # to honour the uq_participant_event_contact unique constraint, and catch any
    # concurrent IntegrityError as a fallback so the enroll itself still succeeds.
    if detection and detection.event_id is not None:
        existing_participant = await db.execute(
            select(Participant).where(
                (Participant.contact_id == body.contact_id)
                & (Participant.event_id == detection.event_id)
            )
        )
        if existing_participant.scalar_one_or_none() is None:
            try:
                db.add(
                    Participant(
                        contact_id=body.contact_id,
                        event_id=detection.event_id,
                        detection_id=task.detection_id,
                        status="attended",
                        source="face",
                    )
                )
                await db.flush()
            except IntegrityError:
                await db.rollback()

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
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
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
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
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
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )
