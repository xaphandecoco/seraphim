"""S24 — Pipeline Participant tests.

Acceptance criteria:
- Tier-100 detection in process_face_crop writes Participant(source='face').
- confirm/edit/add task resolution writes Participant(source='face').
- pit enroll writes Participant(source='face').
- source is never None on pipeline-written Participants.
- Dedup: insert tier-100 Participant(contact_id=1, event_id=1), then call
  _log_attendance for a different task same contact+event → no duplicate,
  no IntegrityError.
"""

import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ComprefaceSubject,
    Contact,
    Detection,
    Event,
    Participant,
    Task,
)
from app.services.task_service import TaskService
from app.utils.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _checkerboard(size: int = 200, block: int = 10):
    """Deterministic high-variance image that passes the blur quality gate."""
    import numpy as np
    img = __import__("numpy").zeros((size, size, 3), dtype=__import__("numpy").uint8)
    for i in range(0, size, block):
        for j in range(0, size, block):
            if ((i // block) + (j // block)) % 2 == 0:
                img[i:i + block, j:j + block] = 255
    return img


async def _seed_user(db: AsyncSession, email: str, role: str = "volunteer"):
    from app.models import User
    user = User(email=email, password_hash=hash_password("pass"), role=role, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_contact(db: AsyncSession, *, email: str = "c@test.com") -> Contact:
    c = Contact(
        first_name="Test",
        last_name="Member",
        contact_type="individual",
        email=email,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _seed_event(db: AsyncSession) -> Event:
    e = Event(title="Test Event", start_at=utc_now())
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def _seed_detection(
    db: AsyncSession,
    *,
    contact_id: int | None = None,
    event_id: int | None = None,
    tier: str = "100",
    image_path: str = "face/thumb.jpg",
) -> Detection:
    matched_name = f"member:{contact_id}" if contact_id else None
    det = Detection(
        image_path=image_path,
        matched_name=matched_name,
        event_id=event_id,
        tier=tier,
        confidence=1.0 if tier == "100" else 0.95,
        status="auto_logged" if tier == "100" else "tasked",
    )
    db.add(det)
    await db.commit()
    await db.refresh(det)
    return det


async def _seed_task(
    db: AsyncSession,
    detection: Detection,
    *,
    status: str = "pending",
    required_approvals: int = 1,
) -> Task:
    task = Task(
        detection_id=detection.id,
        status=status,
        required_approvals=required_approvals,
        current_approvals=0,
        skip_count=0,
        skip_reasons=[],
        expiry_date=utc_now() + timedelta(days=31),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


# ---------------------------------------------------------------------------
# Test: tier-100 pipeline writes Participant(source='face')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_face_pipeline_tier100_writes_participant_with_source_face(
    db_session: AsyncSession,
):
    """process_face_crop with tier-100 should write Participant(source='face').

    Uses a fake_db_factory that wraps db_session so the pipeline uses the
    same session as the test (mirrors pattern from test_face_pipeline.py).
    """
    import numpy as np
    from app.services.face_pipeline import process_face_crop

    contact = await _seed_contact(db_session, email="pipe100@test.com")
    event = await _seed_event(db_session)

    # Seed ComprefaceSubject so _find_member_id resolves
    subj = ComprefaceSubject(
        subject_name=f"member:{contact.id}",
        compreface_subject_id="cf-pipe-100",
        contact_id=contact.id,
        enrollment_status="active",
    )
    db_session.add(subj)
    await db_session.commit()

    # Use a checkerboard to pass the quality gate
    face_crop = _checkerboard()

    mock_result = MagicMock()
    mock_result.subject_id = "cf-pipe-100"
    mock_result.similarity_score = 1.0
    mock_result.tier = "100"

    mock_compreface = MagicMock()
    mock_compreface.recognize = AsyncMock(return_value=mock_result)

    mock_storage = MagicMock()
    mock_storage.save_detection = AsyncMock(return_value=("face/full.jpg", "face/thumb.jpg"))

    mock_dedup = MagicMock()
    mock_dedup.is_duplicate = MagicMock(return_value=False)

    ctx = MagicMock()
    ctx.compreface = mock_compreface
    ctx.storage = mock_storage
    ctx.dedup = mock_dedup

    # Use same session as db_session fixture (mirrors test_face_pipeline.py pattern)
    @asynccontextmanager
    async def fake_db_factory():
        yield db_session

    with patch("app.sse.broadcaster.publish", new_callable=AsyncMock):
        result = await process_face_crop(
            face_crop=face_crop,
            camera_id=None,
            event_id=event.id,
            ctx=ctx,
            db_session_factory=fake_db_factory,
        )

    assert result["action"] == "auto_logged"

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1
    p = participants[0]
    assert p.source == "face"
    assert p.source is not None


# ---------------------------------------------------------------------------
# Test: confirm resolution writes Participant(source='face')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_task_writes_participant_source_face(db_session: AsyncSession):
    """Confirming a task that resolves writes Participant(source='face')."""
    vol = await _seed_user(db_session, "conf@test.com")
    contact = await _seed_contact(db_session, email="conf_contact@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(
        db_session, contact_id=contact.id, event_id=event.id, tier="91-99"
    )
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)

    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.confirm_task(task.id, vol.id)

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1
    assert participants[0].source == "face"
    assert participants[0].source is not None


# ---------------------------------------------------------------------------
# Test: edit resolution writes Participant(source='face')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_task_writes_participant_source_face(db_session: AsyncSession):
    """Editing a task (assigning a different member) writes Participant(source='face')."""
    vol = await _seed_user(db_session, "edit@test.com")
    contact = await _seed_contact(db_session, email="edit_contact@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(
        db_session, contact_id=None, event_id=event.id, tier="91-99"
    )
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)

    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.edit_task(task.id, vol.id, member_id=contact.id)

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1
    assert participants[0].source == "face"
    assert participants[0].source is not None


# ---------------------------------------------------------------------------
# Test: add resolution writes Participant(source='face')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_task_writes_participant_source_face(db_session: AsyncSession):
    """Adding an unmatched task (add action) writes Participant(source='face')."""
    vol = await _seed_user(db_session, "add@test.com")
    contact = await _seed_contact(db_session, email="add_contact@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(
        db_session, contact_id=None, event_id=event.id, tier="below90"
    )
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)

    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.add_task(task.id, vol.id, member_id=contact.id)

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1
    assert participants[0].source == "face"
    assert participants[0].source is not None


# ---------------------------------------------------------------------------
# Test: pit enroll writes Participant(source='face')
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pit_enroll_writes_participant_source_face(client, db_session: AsyncSession):
    """Pit enroll endpoint writes Participant(source='face') linked to the event."""
    from app.models import PitQueue
    from tests.conftest import make_token
    from app.models import User

    admin = User(
        email="pitadmin_s24@test.com",
        password_hash=hash_password("pass"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    token = make_token(admin.id, admin.email, "admin")

    contact = await _seed_contact(db_session, email="pit_contact@test.com")
    event = await _seed_event(db_session)

    detection = Detection(
        image_path="pit/path_thumb.jpg",
        event_id=event.id,
        tier="below90",
        status="pit",
    )
    db_session.add(detection)
    await db_session.commit()
    await db_session.refresh(detection)

    task = Task(
        detection_id=detection.id,
        status="pit",
        pit_status="awaiting",
        required_approvals=1,
        current_approvals=0,
        skip_count=4,
        skip_reasons=[],
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    pq = PitQueue(task_id=task.id)
    db_session.add(pq)
    await db_session.commit()

    import numpy as np
    import cv2

    face = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", face)
    real_bytes = buf.tobytes()

    with (
        patch("app.routers.pit.ComprefaceClient", return_value=MagicMock(close=AsyncMock())),
        patch(
            "app.routers.pit.FaceStorage",
            return_value=MagicMock(
                read_detection_full_image=MagicMock(return_value=real_bytes)
            ),
        ),
        patch(
            "app.routers.pit.EnrollmentService",
            return_value=MagicMock(enroll_contact_face=AsyncMock(return_value=MagicMock())),
        ),
    ):
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": contact.id},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1
    p = participants[0]
    assert p.source == "face"
    assert p.source is not None


# ---------------------------------------------------------------------------
# Dedup regression test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_no_duplicate_participant_for_same_contact_event(db_session: AsyncSession):
    """Insert tier-100 Participant, then _log_attendance for different task same
    contact+event → no duplicate, no IntegrityError.

    This is the key regression: (contact_id, event_id) dedup must use contact_id,
    not detection_id.
    """
    contact = await _seed_contact(db_session, email="dedup@test.com")
    event = await _seed_event(db_session)

    # Pre-insert a tier-100 Participant (simulates the auto-log path)
    existing = Participant(
        contact_id=contact.id,
        event_id=event.id,
        status="attended",
        source="face",
    )
    db_session.add(existing)
    await db_session.commit()

    # Now create a second detection for the same (contact, event) and call _log_attendance
    detection2 = await _seed_detection(
        db_session, contact_id=contact.id, event_id=event.id, tier="91-99"
    )
    task2 = await _seed_task(db_session, detection2, required_approvals=1)

    # Manually invoke _log_attendance via TaskService
    service = TaskService(db_session)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service._log_attendance(task2)

    # Should not raise IntegrityError and count must still be exactly 1
    await db_session.commit()

    rows = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == contact.id,
            Participant.event_id == event.id,
        )
    )
    participants = rows.scalars().all()
    assert len(participants) == 1, (
        f"Expected 1 Participant, got {len(participants)} — dedup failed"
    )
