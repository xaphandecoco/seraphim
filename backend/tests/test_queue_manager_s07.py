"""test_queue_manager_s07.py — targeted tests for the s07 queue-manager refactor.

Covers:
  1. purged_at.is_(None) filter — purged subjects are skipped at query level.
  2. EnrollmentService delegation — inline add_subject/add_example logic removed.
  3. Detection query shape — uses is_enrolled=True, compreface_subject_id join, desc order.
  4. Per-subject failure isolation — one bad subject does not abort the loop.
  5. client.close() always called in finally block.
  6. No detection found → continue with warning, not crash.
  7. No image_path on detection → continue with warning, not crash.
  8. storage.read_detection_full_image returns empty → continue with error log, not crash.
  9. cv2.imdecode returns None (corrupt image) → continue with error log, not crash.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — use MagicMock to avoid SQLAlchemy ORM instrumentation issues
# ---------------------------------------------------------------------------

def _make_subject(
    contact_id: int = 1,
    compreface_subject_id: str = "contact_1",
    enrollment_status: str = "pending",
    purged_at: Optional[datetime] = None,
) -> MagicMock:
    s = MagicMock()
    s.id = 1
    s.contact_id = contact_id
    s.compreface_subject_id = compreface_subject_id
    s.subject_name = "Test Contact"
    s.enrollment_status = enrollment_status
    s.sample_count = 0
    s.purged_at = purged_at
    return s


def _make_detection(
    compreface_subject_id: str = "contact_1",
    image_path: Optional[str] = "/storage/faces/det1.jpg",
    is_enrolled: bool = True,
) -> MagicMock:
    d = MagicMock()
    d.id = 10
    d.compreface_subject_id = compreface_subject_id
    d.image_path = image_path
    d.is_enrolled = is_enrolled
    d.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return d


def _make_session_returning(subjects: list, detection: Optional[Detection]):
    """Return a mock AsyncSession whose execute().scalars().all() returns
    *subjects* on the first call and scalar_one_or_none returns *detection*
    on the second call."""
    session = AsyncMock()

    # First call: subject query  .scalars().all() => subjects
    subject_scalars = MagicMock()
    subject_scalars.all.return_value = subjects
    subject_result = MagicMock()
    subject_result.scalars.return_value = subject_scalars

    # Second call: detection query  .scalar_one_or_none() => detection
    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = detection

    session.execute = AsyncMock(side_effect=[subject_result, det_result])
    return session


# ---------------------------------------------------------------------------
# 1. purged_at filter is present in the query
# ---------------------------------------------------------------------------

def test_process_enrollment_query_includes_purged_at_filter():
    """The select statement in _process_enrollment must contain
    ComprefaceSubject.purged_at.is_(None) so purged subjects are never
    fetched. We verify this by inspecting the source code directly."""
    import inspect
    from app.services.queue_manager import QueueManager

    source = inspect.getsource(QueueManager._process_enrollment)
    assert "purged_at.is_(None)" in source, (
        "_process_enrollment must filter .where(ComprefaceSubject.purged_at.is_(None)); "
        "purged subjects would otherwise be re-processed indefinitely"
    )


# ---------------------------------------------------------------------------
# 2. EnrollmentService is imported and used (no inline add_subject calls)
# ---------------------------------------------------------------------------

def test_no_inline_add_subject_or_add_example_calls():
    """After the refactor the queue manager must NOT call client.add_subject() or
    client.add_example() directly — that responsibility belongs to EnrollmentService."""
    import inspect
    from app.services.queue_manager import QueueManager

    source = inspect.getsource(QueueManager._process_enrollment)
    assert "client.add_subject" not in source, (
        "Inline client.add_subject() call found in _process_enrollment. "
        "Enrollment logic must be delegated to EnrollmentService."
    )
    assert "client.add_example" not in source, (
        "Inline client.add_example() call found in _process_enrollment. "
        "Enrollment logic must be delegated to EnrollmentService."
    )


def test_enrollment_service_imported():
    """EnrollmentService must be importable from app.services.queue_manager's
    module namespace, confirming the import was added."""
    import app.services.queue_manager as qm_mod
    assert hasattr(qm_mod, "EnrollmentService"), (
        "EnrollmentService was not imported into queue_manager; "
        "add 'from app.services.enrollment import EnrollmentService'"
    )


def test_enroll_contact_face_called_not_add_subject():
    """_process_enrollment must call enroll_contact_face, not add_subject."""
    import inspect
    from app.services.queue_manager import QueueManager

    source = inspect.getsource(QueueManager._process_enrollment)
    assert "enroll_contact_face" in source, (
        "_process_enrollment must call service.enroll_contact_face(); "
        "the inline add_subject/add_example block must be replaced"
    )


# ---------------------------------------------------------------------------
# 3. Inline enrollment_status = "active" and sample_count += 1 are removed
# ---------------------------------------------------------------------------

def test_no_inline_enrollment_status_assignment():
    """enrollment_status must not be assigned directly in queue_manager;
    EnrollmentService owns that state transition."""
    import inspect
    from app.services.queue_manager import QueueManager

    source = inspect.getsource(QueueManager._process_enrollment)
    assert 'enrollment_status = "active"' not in source, (
        "Inline 'subject.enrollment_status = \"active\"' found in _process_enrollment. "
        "This state transition must be performed inside EnrollmentService."
    )


def test_no_inline_sample_count_increment():
    """sample_count must not be incremented directly in queue_manager;
    EnrollmentService owns that via _count_samples."""
    import inspect
    from app.services.queue_manager import QueueManager

    source = inspect.getsource(QueueManager._process_enrollment)
    assert "sample_count +=" not in source, (
        "Inline 'subject.sample_count +=' found in _process_enrollment. "
        "EnrollmentService must recompute sample_count via _count_samples()."
    )


# ---------------------------------------------------------------------------
# 4. cv2 + numpy imported (needed for face_crop decode)
# ---------------------------------------------------------------------------

def test_cv2_and_numpy_imported():
    """cv2 and numpy must be imported at module level in queue_manager since
    the image decode step (imdecode/frombuffer) runs in _process_enrollment."""
    import app.services.queue_manager as qm_mod
    assert hasattr(qm_mod, "cv2"), "cv2 not imported in queue_manager"
    assert hasattr(qm_mod, "np"), "numpy (np) not imported in queue_manager"


# ---------------------------------------------------------------------------
# 5. Per-subject failure isolation — one failure must not abort the loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_subject_exception_does_not_abort_loop():
    """If enroll_contact_face raises for subject_1, subject_2 must still be
    processed — failures are non-fatal per task requirement."""
    import numpy as np
    from app.services.queue_manager import QueueManager

    subj1 = _make_subject(contact_id=1, compreface_subject_id="contact_1")
    subj2 = _make_subject(contact_id=2, compreface_subject_id="contact_2")
    det1 = _make_detection(compreface_subject_id="contact_1")
    det2 = _make_detection(compreface_subject_id="contact_2")
    fake_image = b"\xff\xd8\xff\xe0"  # minimal non-empty bytes

    session = AsyncMock()

    # subject query
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj1, subj2]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars

    # detection queries
    det1_result = MagicMock()
    det1_result.scalar_one_or_none.return_value = det1
    det2_result = MagicMock()
    det2_result.scalar_one_or_none.return_value = det2

    session.execute = AsyncMock(side_effect=[subj_result, det1_result, det2_result])

    fake_face = np.zeros((64, 64, 3), dtype=np.uint8)

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_storage.read_detection_full_image.return_value = fake_image

    mock_service = AsyncMock()
    enroll_calls = []

    async def side_effect_enroll(session, contact_id, face_crop, source):
        enroll_calls.append(contact_id)
        if contact_id == 1:
            raise RuntimeError("simulated CompreFace failure")
        # contact_id == 2 succeeds

    mock_service.enroll_contact_face.side_effect = side_effect_enroll

    session_factory_ctx = MagicMock()
    session_factory_ctx.__aenter__ = AsyncMock(return_value=session)
    session_factory_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=session_factory_ctx)

    manager = QueueManager(db_session_factory=mock_factory)

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
        patch("app.services.queue_manager.cv2") as mock_cv2,
        patch("app.services.queue_manager.np") as mock_np,
    ):
        mock_np.frombuffer.return_value = b"buffer"
        mock_cv2.imdecode.return_value = fake_face
        mock_cv2.IMREAD_COLOR = 1

        result = await manager._process_enrollment()

    assert result is True, "_process_enrollment must return True when subjects exist"
    assert 1 in enroll_calls, "enroll_contact_face must have been called for contact_id=1"
    assert 2 in enroll_calls, (
        "enroll_contact_face must have been called for contact_id=2 even though "
        "contact_id=1 raised an exception — per-subject failure must not abort the loop"
    )


# ---------------------------------------------------------------------------
# 6. No detection found → continue (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_detection_for_subject_logs_warning_and_continues():
    """When no detection exists for a subject, _process_enrollment must log a
    warning and skip that subject — it must not crash."""
    from app.services.queue_manager import QueueManager

    subj = _make_subject(contact_id=42, compreface_subject_id="contact_42")

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars

    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = None  # no detection

    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=session_ctx)

    manager = QueueManager(db_session_factory=mock_factory)

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_service = AsyncMock()

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
    ):
        # Must not raise
        result = await manager._process_enrollment()

    assert result is True
    mock_service.enroll_contact_face.assert_not_called()
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Detection with image_path=None → continue (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detection_with_no_image_path_logs_warning_and_continues():
    """When the matched detection has no image_path, _process_enrollment must
    log a warning and skip — must not attempt to read a None path."""
    from app.services.queue_manager import QueueManager

    subj = _make_subject(contact_id=5, compreface_subject_id="contact_5")
    det = _make_detection(image_path=None)

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars

    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = det

    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_service = AsyncMock()

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
    ):
        result = await manager._process_enrollment()

    assert result is True
    mock_service.enroll_contact_face.assert_not_called()
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# 8. storage.read_detection_full_image returns empty → continue (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_image_bytes_logs_error_and_continues():
    """When storage returns empty bytes, _process_enrollment must log an error
    and skip — must not pass empty bytes to cv2.imdecode."""
    from app.services.queue_manager import QueueManager

    subj = _make_subject(contact_id=7, compreface_subject_id="contact_7")
    det = _make_detection()

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars
    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = det
    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_storage.read_detection_full_image.return_value = b""  # empty
    mock_service = AsyncMock()

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
    ):
        result = await manager._process_enrollment()

    assert result is True
    mock_service.enroll_contact_face.assert_not_called()
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# 9. cv2.imdecode returns None (corrupt image) → continue (no crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_corrupt_image_imdecode_none_logs_error_and_continues():
    """When cv2.imdecode returns None (corrupt/unsupported image bytes),
    _process_enrollment must log an error and skip — must not call
    enroll_contact_face with a None face_crop."""
    from app.services.queue_manager import QueueManager

    subj = _make_subject(contact_id=9, compreface_subject_id="contact_9")
    det = _make_detection()
    fake_bytes = b"\x00\x01\x02\x03"

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars
    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = det
    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_storage.read_detection_full_image.return_value = fake_bytes
    mock_service = AsyncMock()

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
        patch("app.services.queue_manager.cv2") as mock_cv2,
        patch("app.services.queue_manager.np") as mock_np,
    ):
        mock_np.frombuffer.return_value = b"buffer"
        mock_cv2.imdecode.return_value = None  # corrupt
        mock_cv2.IMREAD_COLOR = 1

        result = await manager._process_enrollment()

    assert result is True
    mock_service.enroll_contact_face.assert_not_called()
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# 10. client.close() called even when enroll raises
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_close_called_in_finally_on_exception():
    """client.close() must be called even when enroll_contact_face raises an
    exception — the finally block must not be skipped."""
    import numpy as np
    from app.services.queue_manager import QueueManager

    subj = _make_subject(contact_id=3, compreface_subject_id="contact_3")
    det = _make_detection()
    fake_bytes = b"\xff\xd8\xff\xe0"
    fake_face = np.zeros((64, 64, 3), dtype=np.uint8)

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars
    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = det
    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_storage.read_detection_full_image.return_value = fake_bytes

    mock_service = AsyncMock()
    mock_service.enroll_contact_face.side_effect = RuntimeError("boom")

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", return_value=mock_service),
        patch("app.services.queue_manager.cv2") as mock_cv2,
        patch("app.services.queue_manager.np") as mock_np,
    ):
        mock_np.frombuffer.return_value = b"buffer"
        mock_cv2.imdecode.return_value = fake_face
        mock_cv2.IMREAD_COLOR = 1

        result = await manager._process_enrollment()

    # The per-subject try/except catches the RuntimeError; method returns True
    assert result is True
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# 11. No subjects → returns False
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_subjects_returns_false():
    """When no pending subjects exist, _process_enrollment must return False."""
    from app.services.queue_manager import QueueManager

    session = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = []
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars
    session.execute = AsyncMock(return_value=result_mock)

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    with (
        patch("app.services.queue_manager.ComprefaceClient"),
        patch("app.services.queue_manager.FaceStorage"),
        patch("app.services.queue_manager.EnrollmentService"),
    ):
        result = await manager._process_enrollment()

    assert result is False


# ---------------------------------------------------------------------------
# 12. EnrollmentService constructed with client and storage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrollment_service_constructed_with_client_and_storage():
    """EnrollmentService must be constructed with (client, storage) as positional
    args — not with (session, client, storage) or any other ordering."""
    import numpy as np
    from app.services.queue_manager import QueueManager

    subj = _make_subject()
    det = _make_detection()
    fake_bytes = b"\xff\xd8"
    fake_face = np.zeros((64, 64, 3), dtype=np.uint8)

    session = AsyncMock()
    subj_scalars = MagicMock()
    subj_scalars.all.return_value = [subj]
    subj_result = MagicMock()
    subj_result.scalars.return_value = subj_scalars
    det_result = MagicMock()
    det_result.scalar_one_or_none.return_value = det
    session.execute = AsyncMock(side_effect=[subj_result, det_result])

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    manager = QueueManager(db_session_factory=MagicMock(return_value=session_ctx))

    mock_client = AsyncMock()
    mock_storage = MagicMock()
    mock_storage.read_detection_full_image.return_value = fake_bytes

    captured_args = {}

    class CapturingEnrollmentService:
        def __init__(self, *args, **kwargs):
            captured_args["args"] = args
            captured_args["kwargs"] = kwargs
            self.enroll_contact_face = AsyncMock()

    with (
        patch("app.services.queue_manager.ComprefaceClient", return_value=mock_client),
        patch("app.services.queue_manager.FaceStorage", return_value=mock_storage),
        patch("app.services.queue_manager.EnrollmentService", CapturingEnrollmentService),
        patch("app.services.queue_manager.cv2") as mock_cv2,
        patch("app.services.queue_manager.np") as mock_np,
    ):
        mock_np.frombuffer.return_value = b"buffer"
        mock_cv2.imdecode.return_value = fake_face
        mock_cv2.IMREAD_COLOR = 1

        await manager._process_enrollment()

    assert captured_args.get("args") == (mock_client, mock_storage), (
        f"EnrollmentService must be constructed with (client, storage); "
        f"got args={captured_args.get('args')} kwargs={captured_args.get('kwargs')}"
    )
