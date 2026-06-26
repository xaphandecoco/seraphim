"""Tests for POST /pit/{task_id}/enroll (s07-pit-enroll-wiring).

Acceptance criteria verified:
1. Endpoint accepts JSON body with contact_id (PitEnrollRequest), not query param.
2. PitEnrollRequest imported from app.schemas.
3. EnrollmentService, ComprefaceClient, FaceStorage, legacy_settings all wired.
4. EnrollmentService.enroll_contact_face called inside try/finally that closes client.
5. detection.matched_name set to f'member:{body.contact_id}'.

Edge cases:
- Task not found → 404
- Task wrong status → 404
- Detection has no image_path → 422
- Detection image missing on disk → 404 (via service call or storage read)
- Non-admin user → 403
- contact_id passed as query param (old API) returns 422 (FastAPI body validation)
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient

from app.models import Detection, Task, PitQueue
from app.utils.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_detection(db_session, image_path: str = "some/path_thumb.jpg") -> Detection:
    d = Detection(image_path=image_path)
    db_session.add(d)
    await db_session.commit()
    await db_session.refresh(d)
    return d


async def _make_pit_task(db_session, detection: Detection) -> Task:
    t = Task(
        detection_id=detection.id,
        status="pit",
        pit_status="awaiting",
        required_approvals=1,
        current_approvals=0,
        skip_count=4,
        skip_reasons=[],
    )
    db_session.add(t)
    await db_session.commit()
    await db_session.refresh(t)
    pq = PitQueue(task_id=t.id)
    db_session.add(pq)
    await db_session.commit()
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_token(db_session) -> str:
    from app.models import User
    from app.utils.auth import create_access_token
    from datetime import timedelta
    from tests.conftest import TEST_JWT_SECRET

    u = User(
        email="pitadmin@test.com",
        password_hash=hash_password("x"),
        role="admin",
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return create_access_token(
        {"sub": str(u.id), "email": u.email, "name": "", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=30),
    )


@pytest_asyncio.fixture
async def volunteer_token(db_session) -> str:
    from app.models import User
    from app.utils.auth import create_access_token
    from datetime import timedelta
    from tests.conftest import TEST_JWT_SECRET

    u = User(
        email="pitvol@test.com",
        password_hash=hash_password("x"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return create_access_token(
        {"sub": str(u.id), "email": u.email, "name": "", "role": "volunteer"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=30),
    )


# Shared mock patch for EnrollmentService.enroll_contact_face and ComprefaceClient
def _enroll_patches(image_bytes: bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100):
    """Return a list of context-manager patches for a successful enroll call."""
    import numpy as np
    import cv2

    face = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", face)
    real_bytes = buf.tobytes() if ok else image_bytes

    return [
        patch(
            "app.routers.pit.ComprefaceClient",
            return_value=MagicMock(close=AsyncMock()),
        ),
        patch(
            "app.routers.pit.FaceStorage",
            return_value=MagicMock(
                read_detection_full_image=MagicMock(return_value=real_bytes)
            ),
        ),
        patch(
            "app.routers.pit.EnrollmentService",
            return_value=MagicMock(
                enroll_contact_face=AsyncMock(return_value=MagicMock())
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# AC1: JSON body (not query param)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enroll_accepts_json_body(client: AsyncClient, db_session, admin_token):
    """contact_id must be sent in JSON body — verifies AC1."""
    detection = await _make_detection(db_session)
    task = await _make_pit_task(db_session, detection)

    patches = _enroll_patches()
    with patches[0], patches[1], patches[2]:
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": 42},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    # Should not be 422 from body validation
    assert resp.status_code != 422, f"Body parsing failed: {resp.json()}"


@pytest.mark.asyncio
async def test_enroll_rejects_query_param_only(client: AsyncClient, db_session, admin_token):
    """Passing contact_id as query param (old API) must be rejected with 422 — body required."""
    detection = await _make_detection(db_session)
    task = await _make_pit_task(db_session, detection)

    patches = _enroll_patches()
    with patches[0], patches[1], patches[2]:
        resp = await client.post(
            f"/pit/{task.id}/enroll?contact_id=42",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 422, (
        f"Expected 422 for missing body, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# AC5: matched_name set correctly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enroll_sets_matched_name(client: AsyncClient, db_session, admin_token):
    """detection.matched_name must be 'member:<contact_id>' after enroll — verifies AC5."""
    detection = await _make_detection(db_session)
    task = await _make_pit_task(db_session, detection)
    contact_id = 99

    patches = _enroll_patches()
    with patches[0], patches[1], patches[2]:
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": contact_id},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matched_name"] == f"member:{contact_id}", (
        f"Expected 'member:{contact_id}', got '{body['matched_name']}'"
    )
    assert body["status"] == "resolved"

    # Verify DB was mutated
    await db_session.refresh(detection)
    assert detection.matched_name == f"member:{contact_id}"
    assert detection.is_enrolled is True


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enroll_task_not_found_returns_404(client: AsyncClient, db_session, admin_token):
    resp = await client.post(
        "/pit/99999/enroll",
        json={"contact_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_enroll_task_wrong_status_returns_404(client: AsyncClient, db_session, admin_token):
    """A task with status != 'pit' must return 404."""
    detection = await _make_detection(db_session)
    task = Task(
        detection_id=detection.id,
        status="pending",
        required_approvals=1,
        current_approvals=0,
        skip_count=0,
        skip_reasons=[],
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    resp = await client.post(
        f"/pit/{task.id}/enroll",
        json={"contact_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_enroll_no_image_path_returns_422(client: AsyncClient, db_session, admin_token):
    """Detection with empty image_path must return 422 before constructing client — AC4."""
    detection = await _make_detection(db_session, image_path="")
    task = await _make_pit_task(db_session, detection)

    # No patches needed: the guard fires before client construction
    resp = await client.post(
        f"/pit/{task.id}/enroll",
        json={"contact_id": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422, (
        f"Expected 422 for missing image_path, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_enroll_image_file_not_on_disk_returns_404(
    client: AsyncClient, db_session, admin_token
):
    """Storage returns None (file not found) → 404; client.close() still called — AC4."""
    detection = await _make_detection(db_session, image_path="ghost/path_thumb.jpg")
    task = await _make_pit_task(db_session, detection)

    mock_client = MagicMock(close=AsyncMock())
    mock_storage = MagicMock(read_detection_full_image=MagicMock(return_value=None))

    with (
        patch("app.routers.pit.ComprefaceClient", return_value=mock_client),
        patch("app.routers.pit.FaceStorage", return_value=mock_storage),
        patch("app.routers.pit.EnrollmentService"),
    ):
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 404, (
        f"Expected 404 for missing file, got {resp.status_code}: {resp.text}"
    )
    mock_client.close.assert_awaited_once()  # finally block ran


@pytest.mark.asyncio
async def test_enroll_corrupt_image_returns_422(client: AsyncClient, db_session, admin_token):
    """Storage returns un-decodeable bytes → 422; client.close() still called — AC4."""
    detection = await _make_detection(db_session, image_path="bad/image_thumb.jpg")
    task = await _make_pit_task(db_session, detection)

    mock_client = MagicMock(close=AsyncMock())
    # Return bytes that cv2.imdecode cannot decode
    mock_storage = MagicMock(
        read_detection_full_image=MagicMock(return_value=b"\x00\x00\x00\x00")
    )

    with (
        patch("app.routers.pit.ComprefaceClient", return_value=mock_client),
        patch("app.routers.pit.FaceStorage", return_value=mock_storage),
        patch("app.routers.pit.EnrollmentService"),
    ):
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 422, (
        f"Expected 422 for corrupt image, got {resp.status_code}: {resp.text}"
    )
    mock_client.close.assert_awaited_once()  # finally block ran


@pytest.mark.asyncio
async def test_enroll_non_admin_returns_403(client: AsyncClient, db_session, volunteer_token):
    """require_admin must block non-admin users — verifies AC3 keeps require_admin."""
    detection = await _make_detection(db_session)
    task = await _make_pit_task(db_session, detection)

    resp = await client.post(
        f"/pit/{task.id}/enroll",
        json={"contact_id": 1},
        headers={"Authorization": f"Bearer {volunteer_token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 for non-admin, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# AC4: client.close() always called (resource leak prevention)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enroll_client_closed_on_service_exception(
    client: AsyncClient, db_session, admin_token
):
    """If EnrollmentService raises, client.close() must still be awaited — AC4 finally."""
    import numpy as np
    import cv2

    face = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", face)
    real_bytes = buf.tobytes()

    detection = await _make_detection(db_session, image_path="ok/path_thumb.jpg")
    task = await _make_pit_task(db_session, detection)

    mock_client = MagicMock(close=AsyncMock())
    mock_storage = MagicMock(read_detection_full_image=MagicMock(return_value=real_bytes))
    from fastapi import HTTPException
    mock_service = MagicMock(
        enroll_contact_face=AsyncMock(
            side_effect=HTTPException(status_code=422, detail="quality gate fail")
        )
    )

    with (
        patch("app.routers.pit.ComprefaceClient", return_value=mock_client),
        patch("app.routers.pit.FaceStorage", return_value=mock_storage),
        patch("app.routers.pit.EnrollmentService", return_value=mock_service),
    ):
        resp = await client.post(
            f"/pit/{task.id}/enroll",
            json={"contact_id": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert resp.status_code == 422
    mock_client.close.assert_awaited_once()  # finally block ran despite exception


# ---------------------------------------------------------------------------
# AC3: All required imports present (smoke — import the module)
# ---------------------------------------------------------------------------

def test_pit_module_imports():
    """Verify all AC3 imports are resolvable without error."""
    from app.routers.pit import router  # noqa: F401
    from app.schemas import PitEnrollRequest  # noqa: F401
    from app.services.enrollment import EnrollmentService  # noqa: F401
    from app.services.compreface import ComprefaceClient  # noqa: F401
    from app.services.face_storage import FaceStorage  # noqa: F401
    from app.config import legacy_settings  # noqa: F401
