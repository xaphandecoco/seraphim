"""
QA tests for TaskService._maybe_auto_enroll (patch s07-task-service-auto-enroll).

Acceptance criteria checked:
  AC1 - Method signature: _maybe_auto_enroll(self, detection, contact_id) -> None
  AC2 - Guard: returns early if detection is None
  AC3 - Guard: returns early if detection.image_path is falsy
  AC4 - Guard: returns early if an active ComprefaceSubject with sample_count>0 already exists
  AC5 - When no active subject exists, constructs ComprefaceClient + FaceStorage + EnrollmentService
        and calls enroll_contact_face
  AC6 - try/except/finally: exceptions are caught and logged, client.close() called in finally
  AC7 - Client is closed in finally even when no exception occurs
  AC8 - Call site: _maybe_auto_enroll is called from _log_attendance only when member_id is not None
  AC9 - Imports present: EnrollmentService, ComprefaceClient, FaceStorage, ComprefaceSubject
  AC10 - Never raises (exception in enroll_contact_face is swallowed)
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, Contact, Detection
from app.services.task_service import TaskService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_detection(image_path: str = "/data/faces/face.jpg"):
    """Return a MagicMock standing in for a Detection ORM row."""
    d = MagicMock(spec=Detection)
    d.id = 42
    d.image_path = image_path
    d.event_id = 1
    d.matched_name = "member:1"
    d.camera_id = None
    d.confidence = 0.99
    d.tier = "91-99"
    d.timestamp = None
    return d


# ---------------------------------------------------------------------------
# AC1 - Method exists with the right signature
# ---------------------------------------------------------------------------


def test_maybe_auto_enroll_method_exists():
    assert hasattr(TaskService, "_maybe_auto_enroll"), (
        "TaskService must have a _maybe_auto_enroll method"
    )
    sig = inspect.signature(TaskService._maybe_auto_enroll)
    params = list(sig.parameters.keys())
    # Expected: self, detection, contact_id
    assert "detection" in params, f"Missing 'detection' param; got {params}"
    assert "contact_id" in params, f"Missing 'contact_id' param; got {params}"


def test_maybe_auto_enroll_is_coroutine():
    assert inspect.iscoroutinefunction(TaskService._maybe_auto_enroll), (
        "_maybe_auto_enroll must be async"
    )


# ---------------------------------------------------------------------------
# AC9 - Required imports are present in the module
# ---------------------------------------------------------------------------


def test_imports_enrollmentservice():
    from app.services.task_service import EnrollmentService  # noqa: F401


def test_imports_comprefaceclient():
    from app.services.task_service import ComprefaceClient  # noqa: F401


def test_imports_facestorage():
    from app.services.task_service import FaceStorage  # noqa: F401


def test_imports_comprefacesubject():
    import app.services.task_service as ts
    import ast, pathlib

    src = pathlib.Path(ts.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "models" in node.module:
            for alias in node.names:
                if alias.name == "ComprefaceSubject":
                    found = True
    assert found, "ComprefaceSubject must be imported from app.models in task_service.py"


# ---------------------------------------------------------------------------
# AC2 + AC3 - Early-return guards (no DB call needed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_early_when_detection_is_none(db_session: AsyncSession):
    svc = TaskService(db_session)
    # Should not raise, should return None silently
    result = await svc._maybe_auto_enroll(None, 1)
    assert result is None


@pytest.mark.asyncio
async def test_returns_early_when_image_path_is_empty(db_session: AsyncSession):
    detection = _make_detection(image_path="")
    svc = TaskService(db_session)
    result = await svc._maybe_auto_enroll(detection, 1)
    assert result is None


@pytest.mark.asyncio
async def test_returns_early_when_image_path_is_none(db_session: AsyncSession):
    detection = _make_detection()
    detection.image_path = None  # type: ignore[assignment]
    svc = TaskService(db_session)
    result = await svc._maybe_auto_enroll(detection, 1)
    assert result is None


# ---------------------------------------------------------------------------
# AC4 - Returns early when active subject with sample_count > 0 exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_enrollment_when_active_subject_exists(db_session: AsyncSession):
    """If an active ComprefaceSubject with sample_count>0 already exists, no
    enrollment should be attempted."""
    from app.models import Contact

    # Create a real contact
    contact = Contact(
        first_name="Jane",
        last_name="Doe",
        email="jane@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.flush()

    subject = ComprefaceSubject(
        subject_name="Jane Doe",
        compreface_subject_id=f"contact_{contact.id}",
        contact_id=contact.id,
        enrollment_status="active",
        sample_count=3,
    )
    db_session.add(subject)
    await db_session.commit()

    detection = _make_detection()

    svc = TaskService(db_session)

    # EnrollmentService.enroll_contact_face should NOT be called
    with patch(
        "app.services.task_service.ComprefaceClient"
    ) as mock_client_cls:
        await svc._maybe_auto_enroll(detection, contact.id)
        mock_client_cls.assert_not_called()


# ---------------------------------------------------------------------------
# AC5 - Constructs services and calls enroll_contact_face when no active subject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calls_enroll_when_no_active_subject(db_session: AsyncSession):
    """With no existing active subject, _maybe_auto_enroll must construct
    ComprefaceClient, FaceStorage, EnrollmentService, and call enroll_contact_face."""
    from app.models import Contact

    contact = Contact(
        first_name="Bob",
        last_name="Smith",
        email="bob@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()

    detection = _make_detection()

    mock_client_instance = AsyncMock()
    mock_client_instance.close = AsyncMock()

    mock_svc_instance = AsyncMock()
    mock_svc_instance.enroll_contact_face = AsyncMock()

    import numpy as np
    fake_crop = np.zeros((100, 100, 3), dtype="uint8")

    with patch("app.services.task_service.ComprefaceClient", return_value=mock_client_instance) as mock_client_cls, \
         patch("app.services.task_service.FaceStorage") as mock_storage_cls, \
         patch("app.services.task_service.EnrollmentService", return_value=mock_svc_instance) as mock_svc_cls, \
         patch("cv2.imread", return_value=fake_crop):

        svc = TaskService(db_session)
        await svc._maybe_auto_enroll(detection, contact.id)

        # ComprefaceClient was instantiated
        mock_client_cls.assert_called_once()

        # FaceStorage was instantiated
        mock_storage_cls.assert_called_once()

        # EnrollmentService was instantiated with client and storage
        mock_svc_cls.assert_called_once()

        # enroll_contact_face was called
        mock_svc_instance.enroll_contact_face.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC6 - Exceptions are swallowed (never re-raised)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exception_in_enroll_is_swallowed(db_session: AsyncSession):
    """enroll_contact_face raising must not propagate out of _maybe_auto_enroll."""
    from app.models import Contact

    contact = Contact(
        first_name="Carol",
        last_name="White",
        email="carol@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()

    detection = _make_detection()

    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_svc = AsyncMock()
    mock_svc.enroll_contact_face = AsyncMock(side_effect=RuntimeError("compreface down"))

    import numpy as np
    fake_crop = np.zeros((50, 50, 3), dtype="uint8")

    with patch("app.services.task_service.ComprefaceClient", return_value=mock_client), \
         patch("app.services.task_service.FaceStorage"), \
         patch("app.services.task_service.EnrollmentService", return_value=mock_svc), \
         patch("cv2.imread", return_value=fake_crop):

        svc = TaskService(db_session)
        # Must NOT raise
        result = await svc._maybe_auto_enroll(detection, contact.id)
        assert result is None


# ---------------------------------------------------------------------------
# AC7 - client.close() is always called in finally (even on success)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_close_called_on_success(db_session: AsyncSession):
    from app.models import Contact

    contact = Contact(
        first_name="Dave",
        last_name="Green",
        email="dave@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()

    detection = _make_detection()

    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_svc = AsyncMock()
    mock_svc.enroll_contact_face = AsyncMock()

    import numpy as np
    fake_crop = np.zeros((50, 50, 3), dtype="uint8")

    with patch("app.services.task_service.ComprefaceClient", return_value=mock_client), \
         patch("app.services.task_service.FaceStorage"), \
         patch("app.services.task_service.EnrollmentService", return_value=mock_svc), \
         patch("cv2.imread", return_value=fake_crop):

        svc = TaskService(db_session)
        await svc._maybe_auto_enroll(detection, contact.id)
        mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_close_called_on_exception(db_session: AsyncSession):
    from app.models import Contact

    contact = Contact(
        first_name="Eve",
        last_name="Black",
        email="eve@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()

    detection = _make_detection()

    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    mock_svc = AsyncMock()
    mock_svc.enroll_contact_face = AsyncMock(side_effect=ConnectionError("timeout"))

    import numpy as np
    fake_crop = np.zeros((50, 50, 3), dtype="uint8")

    with patch("app.services.task_service.ComprefaceClient", return_value=mock_client), \
         patch("app.services.task_service.FaceStorage"), \
         patch("app.services.task_service.EnrollmentService", return_value=mock_svc), \
         patch("cv2.imread", return_value=fake_crop):

        svc = TaskService(db_session)
        await svc._maybe_auto_enroll(detection, contact.id)  # must not raise
        mock_client.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC8 - Call site: _maybe_auto_enroll fired from _log_attendance when member_id set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_enroll_called_from_log_attendance_when_member_id(db_session: AsyncSession):
    """_log_attendance must call _maybe_auto_enroll when member_id is not None."""
    from app.models import Camera, Detection, Event, Task

    cam = Camera(name="Cam", rtsp_url="rtsp://x/y", fps=1, enable_health_check=False, status="streaming")
    db_session.add(cam)
    await db_session.flush()

    event = Event(title="Test Event")
    db_session.add(event)
    await db_session.flush()

    from app.models import Contact
    contact = Contact(first_name="Frank", last_name="Oz", email="frank@test.test", contact_type="Individual")
    db_session.add(contact)
    await db_session.flush()

    det = Detection(
        camera_id=cam.id,
        image_path="/data/face.jpg",
        confidence=0.95,
        tier="91-99",
        status="tasked",
        matched_name=f"member:{contact.id}",
        event_id=event.id,
    )
    db_session.add(det)
    await db_session.flush()

    from datetime import datetime, timedelta, timezone
    task = Task(
        detection_id=det.id,
        status="pending",
        required_approvals=1,
        current_approvals=0,
        skip_count=0,
        skip_reasons=[],
        expiry_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=31),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    svc = TaskService(db_session)

    with patch.object(svc, "_maybe_auto_enroll", new_callable=AsyncMock) as mock_enroll:
        await svc._log_attendance(task)
        mock_enroll.assert_awaited_once()
        # The call should be _maybe_auto_enroll(detection, contact.id)
        call_args = mock_enroll.call_args
        assert call_args[0][1] == contact.id, (
            f"Expected contact_id={contact.id} but got {call_args[0][1]}"
        )


@pytest.mark.asyncio
async def test_auto_enroll_not_called_when_no_member_id(db_session: AsyncSession):
    """_log_attendance must NOT call _maybe_auto_enroll when detection has no member:N name."""
    from app.models import Camera, Detection, Event, Task

    cam = Camera(name="Cam2", rtsp_url="rtsp://x/z", fps=1, enable_health_check=False, status="streaming")
    db_session.add(cam)
    await db_session.flush()

    event = Event(title="Test Event 2")
    db_session.add(event)
    await db_session.flush()

    det = Detection(
        camera_id=cam.id,
        image_path="/data/face2.jpg",
        confidence=0.90,
        tier="91-99",
        status="tasked",
        matched_name="unknown person",  # no member: prefix
        event_id=event.id,
    )
    db_session.add(det)
    await db_session.flush()

    from datetime import datetime, timedelta, timezone
    task = Task(
        detection_id=det.id,
        status="pending",
        required_approvals=1,
        current_approvals=0,
        skip_count=0,
        skip_reasons=[],
        expiry_date=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=31),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    svc = TaskService(db_session)

    with patch.object(svc, "_maybe_auto_enroll", new_callable=AsyncMock) as mock_enroll:
        await svc._log_attendance(task)
        mock_enroll.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC10 - cv2.imread returning None causes early return (no enrollment attempt)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_early_when_cv2_imread_fails(db_session: AsyncSession):
    """If cv2.imread returns None (file not found), no enrollment is attempted."""
    from app.models import Contact

    contact = Contact(
        first_name="Ghost",
        last_name="Image",
        email="ghost@test.test",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()

    detection = _make_detection()

    with patch("cv2.imread", return_value=None), \
         patch("app.services.task_service.EnrollmentService") as mock_svc_cls:

        svc = TaskService(db_session)
        await svc._maybe_auto_enroll(detection, contact.id)
        mock_svc_cls.assert_not_called()
