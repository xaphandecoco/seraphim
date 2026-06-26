"""S24 — Volunteer scoring tests.

Acceptance criteria:
- confirm action awards +1 point.
- edit action awards +2 points.
- add action awards +1 point.
- total_points accumulates correctly across mixed actions.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detection, Event, Contact, Task, VolunteerStat
from app.services.task_service import TaskService
from app.utils.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_user(db: AsyncSession, email: str, role: str = "volunteer"):
    from app.models import User
    user = User(email=email, password_hash=hash_password("pass"), role=role, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _seed_contact(db: AsyncSession, email: str = "score_c@test.com") -> Contact:
    c = Contact(
        first_name="Score",
        last_name="Tester",
        contact_type="individual",
        email=email,
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _seed_event(db: AsyncSession) -> Event:
    e = Event(title="Score Event", start_at=utc_now())
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def _seed_detection(
    db: AsyncSession,
    *,
    contact_id: int | None = None,
    event_id: int | None = None,
    tier: str = "91-99",
) -> Detection:
    matched = f"member:{contact_id}" if contact_id else None
    det = Detection(
        image_path="score/face.jpg",
        matched_name=matched,
        event_id=event_id,
        tier=tier,
        confidence=0.95,
        status="tasked",
    )
    db.add(det)
    await db.commit()
    await db.refresh(det)
    return det


async def _seed_task(
    db: AsyncSession,
    detection: Detection,
    *,
    required_approvals: int = 1,
) -> Task:
    task = Task(
        detection_id=detection.id,
        status="pending",
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


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_awards_one_point(db_session: AsyncSession):
    """confirm action gives +1 point to total_points."""
    vol = await _seed_user(db_session, "score_conf@test.com")
    contact = await _seed_contact(db_session, "score_conf_c@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(
        db_session, contact_id=contact.id, event_id=event.id, tier="91-99"
    )
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.confirm_task(task.id, vol.id)

    stat = await db_session.get(VolunteerStat, (vol.id, _month_key()))
    assert stat is not None
    assert stat.tasks_confirmed == 1
    assert stat.total_points == 1


@pytest.mark.asyncio
async def test_edit_awards_two_points(db_session: AsyncSession):
    """edit action gives +2 points to total_points."""
    vol = await _seed_user(db_session, "score_edit@test.com")
    contact = await _seed_contact(db_session, "score_edit_c@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(db_session, contact_id=None, event_id=event.id, tier="91-99")
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.edit_task(task.id, vol.id, member_id=contact.id)

    stat = await db_session.get(VolunteerStat, (vol.id, _month_key()))
    assert stat is not None
    assert stat.tasks_edited == 1
    assert stat.total_points == 2


@pytest.mark.asyncio
async def test_add_awards_one_point(db_session: AsyncSession):
    """add action gives +1 point to total_points."""
    vol = await _seed_user(db_session, "score_add@test.com")
    contact = await _seed_contact(db_session, "score_add_c@test.com")
    event = await _seed_event(db_session)
    detection = await _seed_detection(db_session, contact_id=None, event_id=event.id, tier="below90")
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.add_task(task.id, vol.id, member_id=contact.id)

    stat = await db_session.get(VolunteerStat, (vol.id, _month_key()))
    assert stat is not None
    assert stat.tasks_added == 1
    assert stat.total_points == 1


@pytest.mark.asyncio
async def test_total_points_accumulates_across_actions(db_session: AsyncSession):
    """confirm+1, edit+2, add+1 = total_points of 4 for one volunteer."""
    vol = await _seed_user(db_session, "score_accum@test.com")
    contact = await _seed_contact(db_session, "score_accum_c@test.com")
    event = await _seed_event(db_session)

    # Task 1: confirm (+1)
    det1 = await _seed_detection(
        db_session, contact_id=contact.id, event_id=event.id, tier="91-99"
    )
    task1 = await _seed_task(db_session, det1, required_approvals=1)

    service = TaskService(db_session)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.confirm_task(task1.id, vol.id)

    # We need a second contact to avoid participant uniqueness issue for event
    contact2 = await _seed_contact(db_session, "score_accum_c2@test.com")
    event2 = await _seed_event(db_session)

    # Task 2: edit (+2)
    det2 = await _seed_detection(db_session, contact_id=None, event_id=event2.id, tier="91-99")
    task2 = await _seed_task(db_session, det2, required_approvals=1)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.edit_task(task2.id, vol.id, member_id=contact2.id)

    # Task 3: add (+1)
    contact3 = await _seed_contact(db_session, "score_accum_c3@test.com")
    event3 = await _seed_event(db_session)
    det3 = await _seed_detection(db_session, contact_id=None, event_id=event3.id, tier="below90")
    task3 = await _seed_task(db_session, det3, required_approvals=1)
    with patch.object(service, "_maybe_auto_enroll", new_callable=AsyncMock):
        await service.add_task(task3.id, vol.id, member_id=contact3.id)

    stat = await db_session.get(VolunteerStat, (vol.id, _month_key()))
    assert stat is not None
    assert stat.tasks_confirmed == 1
    assert stat.tasks_edited == 1
    assert stat.tasks_added == 1
    assert stat.total_points == 4  # 1 + 2 + 1
