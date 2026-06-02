"""
Comprehensive task endpoint tests.

Coverage:
  - GET  /tasks/next → returns pending task
  - GET  /tasks/next when none available → 404
  - GET  /tasks?status=pending → paginated list
  - POST /tasks/{id}/confirm → resolves task + creates attendance
  - POST /tasks/{id}/confirm with cooldown active → 429
  - POST /tasks/{id}/confirm task not found → 404
  - POST /tasks/{id}/confirm already confirmed → 400
  - POST /tasks/{id}/edit → updates matched_name on detection
  - POST /tasks/{id}/edit with cooldown active → 429
  - POST /tasks/{id}/add → sets is_enrolled, resolves task
  - POST /tasks/{id}/add with cooldown active → 429
  - POST /tasks/{id}/skip with reason → task remains pending
  - POST /tasks/{id}/skip with cooldown active → 429
  - POST /tasks/{id}/override (admin) → force-resolves task
  - POST /tasks/{id}/override (non-admin volunteer) → 403
  - Expired task: confirm returns 400 (task no longer pending after expiry logic)

Cooldown strategy: patch ``app.middleware.cooldown.check_cooldown`` using
``unittest.mock.AsyncMock`` to avoid any Redis dependency.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# Patch target — the function imported directly by the router module
COOLDOWN_PATH = "app.routers.tasks.check_cooldown"


# ---------------------------------------------------------------------------
# Helper: build a pending task + detection in DB
# ---------------------------------------------------------------------------


async def _make_task(
    db_session: AsyncSession,
    *,
    status: str = "pending",
    required_approvals: int = 1,
    current_approvals: int = 0,
    expiry_days: int = 31,
    matched_name: str = "Juan dela Cruz",
    event_id=None,
):
    """Create a Camera, Detection, and Task row in the test DB."""
    from app.models import Camera, Detection, Task

    cam = Camera(
        name="Cam",
        rtsp_url="rtsp://x/y",
        fps=1,
        enable_health_check=False,
        status="streaming",
    )
    db_session.add(cam)
    await db_session.flush()

    det = Detection(
        camera_id=cam.id,
        image_path="/data/faces/face.jpg",
        confidence=0.95,
        tier="91-99",
        status="tasked",
        matched_name=matched_name,
        event_id=event_id,
    )
    db_session.add(det)
    await db_session.flush()

    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
        days=expiry_days
    )
    task = Task(
        detection_id=det.id,
        status=status,
        required_approvals=required_approvals,
        current_approvals=current_approvals,
        skip_count=0,
        skip_reasons=[],
        expiry_date=expiry,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task, det


async def _seed_member(db_session: AsyncSession, contact_id: int):
    """edit_task/add_task now 404 if the assigned member does not exist."""
    from app.models import CiviCRMMember

    member = CiviCRMMember(
        contact_id=contact_id,
        first_name="Member",
        last_name=str(contact_id),
        email=f"member{contact_id}@lnc.test",
    )
    db_session.add(member)
    await db_session.commit()
    return member


# ---------------------------------------------------------------------------
# GET /tasks/next
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_next_task_returns_pending_task(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.get("/tasks/next", headers=volunteer_auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task.id
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_next_task_no_pending_returns_404(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    # No tasks in DB at all
    resp = await client.get("/tasks/next", headers=volunteer_auth_headers)
    assert resp.status_code == 404
    assert "No pending tasks" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_next_task_excludes_expired_tasks(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    """Expired tasks (expiry_date in the past) must not be served."""
    await _make_task(db_session, expiry_days=-1)  # already expired

    resp = await client.get("/tasks/next", headers=volunteer_auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /tasks (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_returns_paginated_response(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    await _make_task(db_session)

    resp = await client.get("/tasks?status=pending&page=1&page_size=10", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["page_size"] == 10


# ---------------------------------------------------------------------------
# POST /tasks/{id}/confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_task_resolves_task_and_returns_task_response(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/confirm",
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task.id
    # Single-approval task → should be "resolved"
    assert data["status"] == "resolved"


@pytest.mark.asyncio
async def test_confirm_task_dual_approval_increments_approvals(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    """Dual-approval task: first confirm keeps it pending."""
    task, _ = await _make_task(db_session, required_approvals=2)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/confirm",
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["current_approvals"] == 1
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_confirm_task_with_cooldown_active_returns_429(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=2.5)):
        resp = await client.post(
            f"/tasks/{task.id}/confirm",
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 429
    assert "Cooldown active" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_task_not_found_returns_404(
    client: AsyncClient,
    volunteer_user,
    volunteer_auth_headers,
):
    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/tasks/999999/confirm",
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_confirm_task_already_confirmed_returns_400(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    """Volunteer tries to confirm the same task twice → 400."""
    task, _ = await _make_task(db_session, required_approvals=2)

    # First confirm (allowed)
    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp1 = await client.post(
            f"/tasks/{task.id}/confirm",
            headers=volunteer_auth_headers,
        )
    assert resp1.status_code == 200

    # Second confirm by same volunteer
    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp2 = await client.post(
            f"/tasks/{task.id}/confirm",
            headers=volunteer_auth_headers,
        )
    assert resp2.status_code == 400
    assert "already acted" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_confirm_task_requires_auth(
    client: AsyncClient,
    db_session: AsyncSession,
):
    task, _ = await _make_task(db_session)

    resp = await client.post(f"/tasks/{task.id}/confirm")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /tasks/{id}/edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_task_updates_matched_member(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, detection = await _make_task(db_session)
    await _seed_member(db_session, 42)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/edit",
            json={"member_id": 42},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task.id

    # Verify detection matched_name was updated
    await db_session.refresh(detection)
    assert detection.matched_name == "member:42"


@pytest.mark.asyncio
async def test_edit_task_with_cooldown_active_returns_429(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=1.8)):
        resp = await client.post(
            f"/tasks/{task.id}/edit",
            json={"member_id": 10},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_edit_task_not_found_returns_404(
    client: AsyncClient,
    volunteer_user,
    volunteer_auth_headers,
):
    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/tasks/999999/edit",
            json={"member_id": 1},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /tasks/{id}/add
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_task_assigns_member_and_resolves(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, detection = await _make_task(db_session, matched_name=None)
    await _seed_member(db_session, 99)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/add",
            json={"member_id": 99},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"

    await db_session.refresh(detection)
    assert detection.matched_name == "member:99"
    assert detection.is_enrolled is True


@pytest.mark.asyncio
async def test_add_task_with_cooldown_active_returns_429(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session, matched_name=None)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=3.0)):
        resp = await client.post(
            f"/tasks/{task.id}/add",
            json={"member_id": 5},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# POST /tasks/{id}/skip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_task_with_reason_keeps_task_pending(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/skip",
            json={"reason": "Face unclear"},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    # After a single skip, task goes back to pending
    assert data["status"] == "pending"
    assert data["skip_count"] == 1
    assert "Face unclear" in data["skip_reasons"]


@pytest.mark.asyncio
async def test_skip_task_without_reason_is_valid(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=None)):
        resp = await client.post(
            f"/tasks/{task.id}/skip",
            json={"reason": ""},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_skip_task_with_cooldown_active_returns_429(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session)

    with patch(COOLDOWN_PATH, new=AsyncMock(return_value=1.0)):
        resp = await client.post(
            f"/tasks/{task.id}/skip",
            json={"reason": "unsure"},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# POST /tasks/{id}/override (admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_override_resolves_task(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_user,
    admin_auth_headers,
):
    task, _ = await _make_task(db_session, required_approvals=2)

    resp = await client.post(
        f"/tasks/{task.id}/override",
        headers=admin_auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "resolved"


@pytest.mark.asyncio
async def test_admin_override_by_non_admin_returns_403(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user,
    volunteer_auth_headers,
):
    task, _ = await _make_task(db_session, required_approvals=2)

    resp = await client.post(
        f"/tasks/{task.id}/override",
        headers=volunteer_auth_headers,
    )

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_endpoints_require_authentication(
    client: AsyncClient,
    db_session: AsyncSession,
):
    task, _ = await _make_task(db_session)

    endpoints = [
        ("GET", "/tasks/next"),
        ("POST", f"/tasks/{task.id}/confirm"),
        ("POST", f"/tasks/{task.id}/skip"),
        ("POST", f"/tasks/{task.id}/edit"),
        ("POST", f"/tasks/{task.id}/add"),
        ("POST", f"/tasks/{task.id}/override"),
    ]

    for method, url in endpoints:
        if method == "GET":
            resp = await client.get(url)
        else:
            resp = await client.post(url, json={})
        assert resp.status_code == 401, f"Expected 401 for {method} {url}, got {resp.status_code}"
