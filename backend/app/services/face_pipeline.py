import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import cv2
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Attendance, ComprefaceSubject, Detection, Task
from app.services.quality_gate import FaceQualityGate
from app.sse import broadcaster

logger = logging.getLogger(__name__)


async def _encode_frame(frame: np.ndarray) -> bytes:
    """Encode numpy BGR frame to JPEG bytes."""
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not success:
        raise RuntimeError("Failed to encode frame")
    return encoded.tobytes()


async def _find_member_id(session: AsyncSession, subject_id: str) -> Optional[int]:
    """Lookup CiviCRM contact_id from Compreface subject_id."""
    result = await session.execute(
        select(ComprefaceSubject.contact_id).where(
            ComprefaceSubject.compreface_subject_id == subject_id
        )
    )
    return result.scalar_one_or_none()


async def process_face_crop(
    face_crop: np.ndarray,
    camera_id: Optional[int],
    event_id: Optional[int],
    ctx,
    db_session_factory: Callable,
) -> dict:
    """Run quality gate, recognition, dedup, storage, DB insertion, SSE broadcast.

    Args:
        face_crop: BGR numpy array of the cropped face.
        camera_id: Camera ID or None for uploads.
        event_id: Optional event ID to associate.
        ctx: PipelineContext with compreface, storage, dedup.
        db_session_factory: Callable returning an async DB session context manager.

    Returns:
        dict with "action" key: "auto_logged" | "tasked" | "skipped" | "deduplicated"
    """
    now = datetime.now(timezone.utc)

    # 1. Quality gate
    quality_ok, quality_reason = FaceQualityGate.check(face_crop)
    if not quality_ok:
        logger.info("Quality gate failed for camera %s: %s", camera_id, quality_reason)
        async with db_session_factory() as session:
            full_path, thumb_path = await ctx.storage.save_detection(
                face_crop, {"x": 0, "y": 0, "w": face_crop.shape[1], "h": face_crop.shape[0]}
            )
            detection = Detection(
                camera_id=camera_id,
                image_path=thumb_path,
                timestamp=now,
                tier="unknown",
                status="skipped",
                matched_name=None,
                confidence=None,
                event_id=event_id,
            )
            session.add(detection)
            await session.commit()
        return {"action": "skipped"}

    # 2. Recognition
    try:
        image_bytes = await _encode_frame(face_crop)
        rec_result = await ctx.compreface.recognize(image_bytes)
    except Exception as exc:
        logger.exception("Recognition failed for camera %s: %s", camera_id, exc)
        rec_result = None

    subject_id = rec_result.subject_id if rec_result else None
    similarity = rec_result.similarity_score if rec_result else None
    tier = rec_result.tier if rec_result else "unknown"

    # 3. Dedup
    is_dup = ctx.dedup.is_duplicate(subject_id, face_crop if subject_id is None else None)
    if is_dup:
        logger.info("Deduplicated face for camera %s, subject=%s", camera_id, subject_id)
        return {"action": "deduplicated"}

    # 4. Save snapshot
    full_path, thumb_path = await ctx.storage.save_detection(
        face_crop, {"x": 0, "y": 0, "w": face_crop.shape[1], "h": face_crop.shape[0]}
    )

    # 5. Insert Detection and route based on tier
    async with db_session_factory() as session:
        detection = Detection(
            camera_id=camera_id,
            image_path=thumb_path,
            timestamp=now,
            compreface_subject_id=subject_id,
            confidence=similarity,
            tier=tier,
            status="auto_logged" if tier == "100" else "tasked",
            matched_name=None,
            event_id=event_id,
        )
        session.add(detection)
        await session.flush()

        # Auto-log attendance for 100% tier
        if tier == "100" and subject_id:
            member_id = await _find_member_id(session, subject_id)
            if member_id:
                attendance = Attendance(
                    contact_id=member_id,
                    event_id=event_id,
                    detection_id=detection.id,
                    status="confirmed",
                    push_status="pending",
                )
                session.add(attendance)

        # Create task for tiers requiring volunteer approval
        if tier != "100":
            required_approvals = 1 if tier == "91-99" else 2
            task = Task(
                detection_id=detection.id,
                status="pending",
                required_approvals=required_approvals,
                current_approvals=0,
                skip_count=0,
                skip_reasons=[],
            )
            session.add(task)

        await session.commit()

    # 6. Broadcast SSE
    event_data = json.dumps(
        {
            "type": "detection",
            "camera_id": camera_id,
            "subject_id": subject_id,
            "similarity": similarity,
            "tier": tier,
            "timestamp": now.isoformat(),
        }
    )
    try:
        await broadcaster.publish(event_data)
    except Exception as exc:
        logger.warning("SSE broadcast failed: %s", exc)

    return {"action": "auto_logged" if tier == "100" else "tasked"}
