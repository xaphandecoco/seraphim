import io
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_faces_unauthorized(client: AsyncClient):
    """POST /uploads/faces without auth → 401."""
    res = await client.post("/uploads/faces", files={"file": ("test.jpg", b"fake", "image/jpeg")})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_upload_faces_volunteer_forbidden(client: AsyncClient, volunteer_auth_headers):
    """POST /uploads/faces as volunteer → 403."""
    res = await client.post(
        "/uploads/faces",
        files={"file": ("test.jpg", b"fake", "image/jpeg")},
        headers=volunteer_auth_headers,
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_upload_faces_no_file(client: AsyncClient, admin_auth_headers):
    """POST /uploads/faces without file → 422."""
    res = await client.post("/uploads/faces", headers=admin_auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_upload_faces_invalid_image(client: AsyncClient, admin_auth_headers):
    """POST /uploads/faces with non-image → 400."""
    res = await client.post(
        "/uploads/faces",
        files={"file": ("test.txt", b"not an image", "text/plain")},
        headers=admin_auth_headers,
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_upload_faces_single_face_tier_100(
    client: AsyncClient, admin_auth_headers, db_session, sample_member
):
    """Single face, quality pass, tier 100 → auto-logged."""
    from app.models import Attendance, ComprefaceSubject, Detection, Task

    # Link member to a Compreface subject
    subject = ComprefaceSubject(
        subject_name="test_subject",
        compreface_subject_id="sub_001",
        contact_id=sample_member.contact_id,
        enrollment_status="active",
    )
    db_session.add(subject)
    await db_session.commit()

    # Create a valid 200x200 BGR JPEG in memory
    import cv2

    img = np.zeros((200, 200, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    mock_recognition = MagicMock()
    mock_recognition.subject_id = "sub_001"
    mock_recognition.similarity_score = 0.99
    mock_recognition.tier = "100"
    mock_recognition.box = None

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        instance.detect = AsyncMock(return_value=[{"x": 0, "y": 0, "w": 200, "h": 200}])
        instance.recognize = AsyncMock(return_value=mock_recognition)
        instance.close = AsyncMock()

        res = await client.post(
            "/uploads/faces",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["faces_detected"] == 1
    assert data["quality_passed"] == 1
    assert data["auto_logged"] == 1
    assert data["tasks_created"] == 0
    assert data["skipped"] == 0

    # Verify Detection record
    result = await db_session.execute(Detection.__table__.select())
    detections = result.scalars().all()
    assert len(detections) == 1
    assert detections[0].camera_id is None
    assert detections[0].tier == "100"
    assert detections[0].status == "auto_logged"

    # Verify Attendance record
    result = await db_session.execute(Attendance.__table__.select())
    attendances = result.scalars().all()
    assert len(attendances) == 1
    assert attendances[0].contact_id == sample_member.contact_id

    # No task created
    result = await db_session.execute(Task.__table__.select())
    tasks = result.scalars().all()
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_upload_faces_single_face_tier_91_99(
    client: AsyncClient, admin_auth_headers, db_session
):
    """Single face, quality pass, tier 91-99 → task created."""
    from app.models import Detection, Task

    import cv2

    img = np.zeros((200, 200, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    mock_recognition = MagicMock()
    mock_recognition.subject_id = "sub_002"
    mock_recognition.similarity_score = 0.95
    mock_recognition.tier = "91-99"
    mock_recognition.box = None

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        instance.detect = AsyncMock(return_value=[{"x": 0, "y": 0, "w": 200, "h": 200}])
        instance.recognize = AsyncMock(return_value=mock_recognition)
        instance.close = AsyncMock()

        res = await client.post(
            "/uploads/faces",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["faces_detected"] == 1
    assert data["quality_passed"] == 1
    assert data["tasks_created"] == 1
    assert data["auto_logged"] == 0

    result = await db_session.execute(Task.__table__.select())
    tasks = result.scalars().all()
    assert len(tasks) == 1
    assert tasks[0].required_approvals == 1

    result = await db_session.execute(Detection.__table__.select())
    detections = result.scalars().all()
    assert len(detections) == 1
    assert detections[0].status == "tasked"


@pytest.mark.asyncio
async def test_upload_faces_quality_fail(
    client: AsyncClient, admin_auth_headers, db_session
):
    """Single face too small → skipped."""
    from app.models import Detection

    import cv2

    # 50x50 image — below MIN_FACE_SIZE of 100
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        instance.detect = AsyncMock(return_value=[{"x": 0, "y": 0, "w": 50, "h": 50}])
        instance.close = AsyncMock()

        res = await client.post(
            "/uploads/faces",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["faces_detected"] == 1
    assert data["quality_failed"] == 1
    assert data["skipped"] == 1
    assert data["quality_passed"] == 0

    result = await db_session.execute(Detection.__table__.select())
    detections = result.scalars().all()
    assert len(detections) == 1
    assert detections[0].status == "skipped"
    assert detections[0].tier == "unknown"


@pytest.mark.asyncio
async def test_upload_faces_multiple_faces(
    client: AsyncClient, admin_auth_headers, db_session
):
    """Multiple faces with mixed results."""
    from app.models import Detection, Task

    import cv2

    img = np.zeros((300, 300, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    def make_mock(tier, subject_id):
        m = MagicMock()
        m.subject_id = subject_id
        m.similarity_score = 0.95 if tier == "91-99" else 0.85
        m.tier = tier
        m.box = None
        return m

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        # One small (quality fail), one tier 91-99, one tier below90
        instance.detect = AsyncMock(
            return_value=[
                {"x": 0, "y": 0, "w": 50, "h": 50},
                {"x": 100, "y": 0, "w": 150, "h": 150},
                {"x": 0, "y": 100, "w": 150, "h": 150},
            ]
        )
        instance.recognize = AsyncMock(side_effect=[
            make_mock("91-99", "sub_a"),
            make_mock("below90", "sub_b"),
        ])
        instance.close = AsyncMock()

        res = await client.post(
            "/uploads/faces",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["faces_detected"] == 3
    assert data["quality_failed"] == 1
    assert data["skipped"] == 1
    assert data["quality_passed"] == 2
    assert data["tasks_created"] == 2

    result = await db_session.execute(Task.__table__.select())
    tasks = result.scalars().all()
    assert len(tasks) == 2
    approvals = {t.required_approvals for t in tasks}
    assert approvals == {1, 2}


@pytest.mark.asyncio
async def test_upload_faces_no_faces_detected(
    client: AsyncClient, admin_auth_headers
):
    """Image with no faces → all zeros."""
    import cv2

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        instance.detect = AsyncMock(return_value=[])
        instance.close = AsyncMock()

        res = await client.post(
            "/uploads/faces",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert data["faces_detected"] == 0
    assert data["quality_passed"] == 0
    assert data["quality_failed"] == 0
    assert data["tasks_created"] == 0
    assert data["auto_logged"] == 0
    assert data["skipped"] == 0
    assert data["deduplicated"] == 0


@pytest.mark.asyncio
async def test_upload_faces_with_event_id(
    client: AsyncClient, admin_auth_headers, db_session, sample_event
):
    """Optional event_id query param is persisted on Detection."""
    from app.models import Detection

    import cv2

    img = np.zeros((200, 200, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    image_bytes = buf.tobytes()

    mock_recognition = MagicMock()
    mock_recognition.subject_id = None
    mock_recognition.similarity_score = None
    mock_recognition.tier = "unknown"
    mock_recognition.box = None

    with patch("app.routers.uploads.ComprefaceClient") as MockClient:
        instance = MockClient.return_value
        instance.detect = AsyncMock(return_value=[{"x": 0, "y": 0, "w": 200, "h": 200}])
        instance.recognize = AsyncMock(return_value=mock_recognition)
        instance.close = AsyncMock()

        res = await client.post(
            f"/uploads/faces?event_id={sample_event.event_id}",
            files={"file": ("face.jpg", io.BytesIO(image_bytes), "image/jpeg")},
            headers=admin_auth_headers,
        )

    assert res.status_code == 200

    result = await db_session.execute(Detection.__table__.select())
    detections = result.scalars().all()
    assert len(detections) == 1
    assert detections[0].event_id == sample_event.event_id
