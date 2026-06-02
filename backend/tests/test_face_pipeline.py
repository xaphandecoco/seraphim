from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.face_pipeline import process_face_crop
from app.services.quality_gate import FaceQualityGate


class FakePipelineContext:
    def __init__(self):
        self.compreface = MagicMock()
        self.storage = MagicMock()
        self.dedup = MagicMock()


@pytest.fixture
def fake_ctx():
    return FakePipelineContext()


@pytest.fixture
def fake_db_factory(db_session: AsyncSession):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory():
        # Shared session; don't close it here (the db_session fixture owns its lifecycle)
        yield db_session

    return factory


@pytest.mark.asyncio
async def test_process_face_crop_quality_fail(db_session: AsyncSession, fake_ctx, fake_db_factory):
    """Face too small → skipped detection, no task."""
    from app.models import Detection, Task

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))

    tiny_face = np.zeros((50, 50, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=tiny_face,
        camera_id=None,
        event_id=None,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "skipped"

    detections = (await db_session.execute(Detection.__table__.select())).scalars().all()
    assert len(detections) == 1
    assert detections[0].status == "skipped"
    assert detections[0].tier == "unknown"

    tasks = (await db_session.execute(Task.__table__.select())).scalars().all()
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_process_face_crop_tier_100_auto_log(
    db_session: AsyncSession, fake_ctx, fake_db_factory, sample_member, sample_event
):
    """Tier 100 with known subject + active event → auto-log attendance."""
    from app.models import Attendance, ComprefaceSubject, Detection, Task

    subject = ComprefaceSubject(
        subject_name="sub_tier100",
        compreface_subject_id="sub_100",
        contact_id=sample_member.contact_id,
        enrollment_status="active",
    )
    db_session.add(subject)
    await db_session.commit()

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=False)

    mock_rec = MagicMock()
    mock_rec.subject_id = "sub_100"
    mock_rec.similarity_score = 0.99
    mock_rec.tier = "100"
    fake_ctx.compreface.recognize = AsyncMock(return_value=mock_rec)

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=face,
        camera_id=None,
        event_id=sample_event.event_id,  # auto-log now requires an active event
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "auto_logged"

    detections = (await db_session.execute(Detection.__table__.select())).scalars().all()
    assert len(detections) == 1
    assert detections[0].tier == "100"
    assert detections[0].status == "auto_logged"

    attendances = (await db_session.execute(Attendance.__table__.select())).scalars().all()
    assert len(attendances) == 1
    assert attendances[0].contact_id == sample_member.contact_id

    tasks = (await db_session.execute(Task.__table__.select())).scalars().all()
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_process_face_crop_tier_91_99_task(
    db_session: AsyncSession, fake_ctx, fake_db_factory
):
    """Tier 91-99 → task with required_approvals=1."""
    from app.models import Detection, Task

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=False)

    mock_rec = MagicMock()
    mock_rec.subject_id = "sub_99"
    mock_rec.similarity_score = 0.95
    mock_rec.tier = "91-99"
    fake_ctx.compreface.recognize = AsyncMock(return_value=mock_rec)

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=face,
        camera_id=None,
        event_id=None,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "tasked"

    tasks = (await db_session.execute(Task.__table__.select())).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].required_approvals == 1


@pytest.mark.asyncio
async def test_process_face_crop_tier_below90_task(
    db_session: AsyncSession, fake_ctx, fake_db_factory
):
    """Tier below90 → task with required_approvals=2."""
    from app.models import Task

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=False)

    mock_rec = MagicMock()
    mock_rec.subject_id = None
    mock_rec.similarity_score = 0.80
    mock_rec.tier = "below90"
    fake_ctx.compreface.recognize = AsyncMock(return_value=mock_rec)

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=face,
        camera_id=None,
        event_id=None,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "tasked"

    tasks = (await db_session.execute(Task.__table__.select())).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].required_approvals == 2


@pytest.mark.asyncio
async def test_process_face_crop_deduplicated(db_session: AsyncSession, fake_ctx, fake_db_factory):
    """Duplicate face → no DB record."""
    from app.models import Detection

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=True)

    mock_rec = MagicMock()
    mock_rec.subject_id = "sub_dup"
    mock_rec.similarity_score = 0.99
    mock_rec.tier = "100"
    fake_ctx.compreface.recognize = AsyncMock(return_value=mock_rec)

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=face,
        camera_id=None,
        event_id=None,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "deduplicated"

    detections = (await db_session.execute(Detection.__table__.select())).scalars().all()
    assert len(detections) == 0


@pytest.mark.asyncio
async def test_process_face_crop_recognition_failure(
    db_session: AsyncSession, fake_ctx, fake_db_factory
):
    """Recognition raises exception → treated as unknown, task created."""
    from app.models import Detection, Task

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=False)
    fake_ctx.compreface.recognize = AsyncMock(side_effect=Exception("compreface down"))

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    result = await process_face_crop(
        face_crop=face,
        camera_id=None,
        event_id=None,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    assert result["action"] == "tasked"

    detections = (await db_session.execute(Detection.__table__.select())).scalars().all()
    assert len(detections) == 1
    assert detections[0].tier == "unknown"

    tasks = (await db_session.execute(Task.__table__.select())).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].required_approvals == 2


@pytest.mark.asyncio
async def test_process_face_crop_with_camera_id_and_event_id(
    db_session: AsyncSession, fake_ctx, fake_db_factory, sample_camera, sample_event
):
    """camera_id and event_id are persisted on Detection."""
    from app.models import Detection

    fake_ctx.storage.save_detection = AsyncMock(return_value=("/full.jpg", "/thumb.jpg"))
    fake_ctx.dedup.is_duplicate = MagicMock(return_value=False)

    mock_rec = MagicMock()
    mock_rec.subject_id = None
    mock_rec.similarity_score = None
    mock_rec.tier = "unknown"
    fake_ctx.compreface.recognize = AsyncMock(return_value=mock_rec)

    face = np.zeros((200, 200, 3), dtype=np.uint8)
    await process_face_crop(
        face_crop=face,
        camera_id=sample_camera.id,
        event_id=sample_event.event_id,
        ctx=fake_ctx,
        db_session_factory=fake_db_factory,
    )

    detections = (await db_session.execute(Detection.__table__.select())).scalars().all()
    assert len(detections) == 1
    assert detections[0].camera_id == sample_camera.id
    assert detections[0].event_id == sample_event.event_id
