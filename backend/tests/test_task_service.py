"""
Unit tests for TaskService.

These tests use the in-memory SQLite AsyncSession fixture from conftest.py.
TaskService is tightly coupled to SQLAlchemy, so we use the real session with
seeded test data rather than mocking the ORM layer — this is the correct approach
for data-layer services and gives meaningful coverage of all business logic.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detection, Task, TaskAction, VolunteerStat
from app.services.task_service import TaskService


# ============================================================================
# Helpers / shared fixtures
# ============================================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _seed_detection(
    db_session: AsyncSession,
    *,
    matched_name: str = "member:1",
    event_id: int = None,
    camera_id: int = None,
) -> Detection:
    detection = Detection(
        image_path="/fake/path.jpg",
        matched_name=matched_name,
        event_id=event_id,
        camera_id=camera_id,
        tier="100",
        confidence=0.99,
    )
    db_session.add(detection)
    await db_session.commit()
    await db_session.refresh(detection)
    return detection


async def _seed_task(
    db_session: AsyncSession,
    detection: Detection,
    *,
    status: str = "pending",
    required_approvals: int = 1,
    current_approvals: int = 0,
    skip_count: int = 0,
    expiry_date: datetime = None,
    pit_status: str = None,
) -> Task:
    if expiry_date is None:
        expiry_date = utc_now() + timedelta(days=31)
    task = Task(
        detection_id=detection.id,
        status=status,
        required_approvals=required_approvals,
        current_approvals=current_approvals,
        skip_count=skip_count,
        skip_reasons=[],
        expiry_date=expiry_date,
        pit_status=pit_status,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


async def _seed_volunteer(db_session: AsyncSession, email: str, role: str = "volunteer"):
    from app.models import User
    from app.utils.auth import hash_password

    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ============================================================================
# get_next_task() tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_next_task_returns_pending_task(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "vol1@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is not None
    assert result.id == task.id
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_get_next_task_no_tasks_returns_none(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "vol2@lnc.test")

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is None


@pytest.mark.asyncio
async def test_get_next_task_skips_expired_task(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "vol3@lnc.test")
    detection = await _seed_detection(db_session)
    # Create an already-expired task
    await _seed_task(
        db_session,
        detection,
        expiry_date=utc_now() - timedelta(days=1),
    )

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is None


@pytest.mark.asyncio
async def test_get_next_task_skips_tasks_already_acted_on(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "vol4@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    # Record that this volunteer has already acted on the task
    action = TaskAction(task_id=task.id, volunteer_id=vol.id, action="skip")
    db_session.add(action)
    await db_session.commit()

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is None


@pytest.mark.asyncio
async def test_get_next_task_skips_pit_tasks(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "vol5@lnc.test")
    detection = await _seed_detection(db_session)
    await _seed_task(db_session, detection, pit_status="awaiting")

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is None


# ============================================================================
# confirm_task() tests
# ============================================================================


@pytest.mark.asyncio
async def test_confirm_task_single_approval_resolves_task(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "conf1@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    result = await service.confirm_task(task.id, vol.id)

    assert result.status == "resolved"
    assert result.current_approvals == 1


@pytest.mark.asyncio
async def test_confirm_task_dual_approval_first_confirm_stays_pending(
    db_session: AsyncSession,
):
    vol = await _seed_volunteer(db_session, "conf2@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, required_approvals=2)

    service = TaskService(db_session)
    result = await service.confirm_task(task.id, vol.id)

    assert result.status == "pending"
    assert result.current_approvals == 1


@pytest.mark.asyncio
async def test_confirm_task_dual_approval_second_confirm_resolves_task(
    db_session: AsyncSession,
):
    vol1 = await _seed_volunteer(db_session, "conf3a@lnc.test")
    vol2 = await _seed_volunteer(db_session, "conf3b@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, required_approvals=2)

    service = TaskService(db_session)
    await service.confirm_task(task.id, vol1.id)
    result = await service.confirm_task(task.id, vol2.id)

    assert result.status == "resolved"
    assert result.current_approvals == 2


@pytest.mark.asyncio
async def test_confirm_task_not_found_raises_404(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "conf4@lnc.test")

    service = TaskService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.confirm_task(99999, vol.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_confirm_task_already_resolved_raises_400(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "conf5@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, status="resolved")

    service = TaskService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.confirm_task(task.id, vol.id)

    assert exc_info.value.status_code == 400
    assert "no longer pending" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_confirm_task_volunteer_already_acted_raises_400(
    db_session: AsyncSession,
):
    vol = await _seed_volunteer(db_session, "conf6@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, required_approvals=2)

    # Volunteer confirms once
    service = TaskService(db_session)
    await service.confirm_task(task.id, vol.id)

    # Volunteer tries to confirm again
    with pytest.raises(HTTPException) as exc_info:
        await service.confirm_task(task.id, vol.id)

    assert exc_info.value.status_code == 400
    assert "already" in exc_info.value.detail.lower()


# ============================================================================
# skip_task() tests
# ============================================================================


@pytest.mark.asyncio
async def test_skip_task_with_reason_records_reason(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "skip1@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    service = TaskService(db_session)
    result = await service.skip_task(task.id, vol.id, reason="blurry image")

    assert result.status == "pending"
    assert result.skip_count == 1
    assert "blurry image" in result.skip_reasons


@pytest.mark.asyncio
async def test_skip_task_without_reason_does_not_add_reason(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "skip2@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    service = TaskService(db_session)
    result = await service.skip_task(task.id, vol.id, reason=None)

    assert result.skip_count == 1
    assert result.skip_reasons == []


@pytest.mark.asyncio
async def test_skip_task_not_found_raises_404(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "skip3@lnc.test")

    service = TaskService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.skip_task(99999, vol.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_skip_task_twice_by_same_volunteer_raises_400(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "skip4@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    service = TaskService(db_session)
    await service.skip_task(task.id, vol.id, reason="first skip")
    await service.skip_task(task.id, vol.id, reason="second skip")

    with pytest.raises(HTTPException) as exc_info:
        await service.skip_task(task.id, vol.id, reason="third skip")

    assert exc_info.value.status_code == 400
    assert "skipped this task twice" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_skip_task_four_skips_from_two_volunteers_moves_to_pit(
    db_session: AsyncSession,
):
    vol1 = await _seed_volunteer(db_session, "skip5a@lnc.test")
    vol2 = await _seed_volunteer(db_session, "skip5b@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection)

    service = TaskService(db_session)
    await service.skip_task(task.id, vol1.id)
    await service.skip_task(task.id, vol1.id)
    await service.skip_task(task.id, vol2.id)
    result = await service.skip_task(task.id, vol2.id)

    assert result.status == "pit"
    assert result.pit_status == "awaiting"


# ============================================================================
# add_task() tests
# ============================================================================


@pytest.mark.asyncio
async def test_add_task_success_single_approval_resolves(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "add1@lnc.test")
    detection = await _seed_detection(db_session, matched_name=None)
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    result = await service.add_task(task.id, vol.id, member_id=99)

    assert result.status == "resolved"
    assert result.current_approvals == 1

    # Verify detection was updated
    await db_session.refresh(detection)
    assert detection.matched_name == "member:99"
    assert detection.is_enrolled is True


@pytest.mark.asyncio
async def test_add_task_not_found_raises_404(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "add2@lnc.test")

    service = TaskService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.add_task(99999, vol.id, member_id=1)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_add_task_already_resolved_raises_400(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "add3@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, status="resolved")

    service = TaskService(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await service.add_task(task.id, vol.id, member_id=1)

    assert exc_info.value.status_code == 400


# ============================================================================
# Volunteer stats tests
# ============================================================================


@pytest.mark.asyncio
async def test_confirm_task_increments_volunteer_stats(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "stats1@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    await service.confirm_task(task.id, vol.id)

    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    stat = await db_session.get(VolunteerStat, (vol.id, month_key))

    assert stat is not None
    assert stat.tasks_confirmed == 1
    assert stat.total_points == 1


@pytest.mark.asyncio
async def test_add_task_increments_tasks_added_stat(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "stats2@lnc.test")
    detection = await _seed_detection(db_session, matched_name=None)
    task = await _seed_task(db_session, detection, required_approvals=1)

    service = TaskService(db_session)
    await service.add_task(task.id, vol.id, member_id=10)

    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    stat = await db_session.get(VolunteerStat, (vol.id, month_key))

    assert stat is not None
    assert stat.tasks_added == 1
    assert stat.total_points == 1


# ============================================================================
# Expiry logic tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_next_task_future_expiry_is_returned(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "expiry1@lnc.test")
    detection = await _seed_detection(db_session)
    task = await _seed_task(
        db_session,
        detection,
        expiry_date=utc_now() + timedelta(days=10),
    )

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is not None
    assert result.id == task.id


@pytest.mark.asyncio
async def test_get_next_task_past_expiry_is_not_returned(db_session: AsyncSession):
    vol = await _seed_volunteer(db_session, "expiry2@lnc.test")
    detection = await _seed_detection(db_session)
    await _seed_task(
        db_session,
        detection,
        expiry_date=utc_now() - timedelta(seconds=1),
    )

    service = TaskService(db_session)
    result = await service.get_next_task(vol.id)

    assert result is None


# ============================================================================
# get_task_count() tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_task_count_no_tasks_returns_zero(db_session: AsyncSession):
    service = TaskService(db_session)
    count = await service.get_task_count()
    assert count == 0


@pytest.mark.asyncio
async def test_get_task_count_returns_pending_only(db_session: AsyncSession):
    detection1 = await _seed_detection(db_session)
    detection2 = await _seed_detection(db_session)
    detection3 = await _seed_detection(db_session)

    await _seed_task(db_session, detection1, status="pending")
    await _seed_task(db_session, detection2, status="pending")
    await _seed_task(db_session, detection3, status="resolved")

    service = TaskService(db_session)
    count = await service.get_task_count()

    assert count == 2
