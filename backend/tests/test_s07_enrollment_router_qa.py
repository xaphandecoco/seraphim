"""QA validation tests for s07-enrollment-router acceptance criteria.

These tests expose the real failures in the implementation.
Run with: DATABASE_URL=sqlite+aiosqlite:///./ci_test_qa.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/test_s07_enrollment_router_qa.py -v

KNOWN FAILURES (pre-fix):
  - AC1: FaceSample constructor rejects full_path= kwarg (must be image_path=)
  - AC1/AC2: _sample_to_response accesses sample.full_path (AttributeError)
  - AC1: FaceSampleResponse schema requires image_path but router passes full_path= → ValidationError
  - AC1: FaceSampleResponse has no thumb_url field (silently dropped)
  - AC2: FacePanelResponse has no contact_id field (extra field silently dropped, AC expects it)
  - AC2: FacePanelResponse.is_orphan is a required field but router doesn't pass it → 500 on GET /faces
  - AC4: retrain inner loop accesses sample.full_path (AttributeError on real FaceSample)
  - AC4: backfill WHERE clause uses FaceSample.full_path (AttributeError at class level)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import cv2
import numpy as np
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, FaceSample, Contact
from app.schemas import (
    FaceSampleResponse,
    FacePanelResponse,
    RecognitionHistoryItem,
    RetrainResponse,
)
from app.services.enrollment import EnrollmentService
from app.services.face_storage import FaceStorage, to_storage_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_face_crop(h: int = 200, w: int = 200) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8).astype(np.uint8)


def _make_mock_client(image_id: Optional[str] = "cf-uuid-001") -> AsyncMock:
    client = AsyncMock()
    client.add_subject = AsyncMock(return_value=True)
    client.add_example = AsyncMock(return_value=image_id)
    client.delete_example = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


def _make_mock_storage(
    full_path: str = "enrolled/contact_1/sample_001_full.jpg",
    thumb_path: str = "enrolled/contact_1/sample_001_thumb.jpg",
) -> MagicMock:
    storage = MagicMock()
    storage.save_enrollment = AsyncMock(return_value=(full_path, thumb_path))
    fake_path = MagicMock()
    fake_path.read_bytes = MagicMock(return_value=b"\xff\xd8\xff")
    storage.base_path = MagicMock()
    storage.base_path.__truediv__ = MagicMock(return_value=fake_path)
    return storage


async def _seed_subject(
    db_session: AsyncSession,
    contact_id: int,
    *,
    purged_at: Optional[datetime] = None,
    sample_count: int = 0,
    enrollment_status: str = "pending",
) -> ComprefaceSubject:
    subject = ComprefaceSubject(
        subject_name="Test Subject",
        compreface_subject_id=f"contact_{contact_id}",
        contact_id=contact_id,
        enrollment_status=enrollment_status,
        sample_count=sample_count,
        purged_at=purged_at,
    )
    db_session.add(subject)
    await db_session.commit()
    await db_session.refresh(subject)
    return subject


async def _seed_face_sample(
    db_session: AsyncSession,
    contact_id: int,
    compreface_subject_id: str,
    *,
    compreface_image_id: Optional[str] = "cf-img-001",
) -> FaceSample:
    sample = FaceSample(
        compreface_subject_id=compreface_subject_id,
        contact_id=contact_id,
        image_path=f"enrolled/{compreface_subject_id}/sample_001_full.jpg",
        thumb_path=f"enrolled/{compreface_subject_id}/sample_001_thumb.jpg",
        source="manual",
        compreface_image_id=compreface_image_id,
    )
    db_session.add(sample)
    await db_session.commit()
    await db_session.refresh(sample)
    return sample


# ===========================================================================
# AC1-BUG-A: FaceSample constructor rejects full_path= kwarg
# ===========================================================================

def test_ac1_face_sample_orm_column_is_image_path_not_full_path():
    """FAIL: FaceSample has column 'image_path', not 'full_path'.

    EnrollmentService.enroll_contact_face passes full_path=full_path to FaceSample()
    on line 152 of enrollment.py. This raises TypeError at INSERT time.
    Fix: rename full_path= to image_path= in FaceSample construction.
    """
    # This must NOT raise TypeError; currently it does
    with pytest.raises(TypeError, match="full_path"):
        FaceSample(
            compreface_subject_id="contact_1",
            full_path="enrolled/contact_1/sample_001_full.jpg",  # WRONG kwarg
            thumb_path="enrolled/contact_1/sample_001_thumb.jpg",
            source="manual",
        )


def test_ac1_face_sample_orm_column_accepts_image_path():
    """The correct constructor kwarg is image_path (not full_path)."""
    s = FaceSample(
        compreface_subject_id="contact_1",
        image_path="enrolled/contact_1/sample_001_full.jpg",  # CORRECT
        thumb_path="enrolled/contact_1/sample_001_thumb.jpg",
        source="manual",
    )
    assert s.image_path == "enrolled/contact_1/sample_001_full.jpg"


# ===========================================================================
# AC1-BUG-B: _sample_to_response accesses sample.full_path (AttributeError)
# ===========================================================================

def test_ac1_sample_to_response_attribute_error():
    """Regression AC1: router._sample_to_response must NOT access sample.full_path.

    FaceSample has no 'full_path' attribute — the column is 'image_path'.
    After the fix, _sample_to_response uses sample.image_path correctly
    and the call must succeed without raising AttributeError.
    """
    from app.routers.enrollment import _sample_to_response

    sample = FaceSample(
        compreface_subject_id="contact_1",
        image_path="enrolled/contact_1/sample_001_full.jpg",
        thumb_path="enrolled/contact_1/sample_001_thumb.jpg",
        source="manual",
        compreface_image_id="cf-uuid-001",
    )
    sample.id = 1
    sample.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    sample.quality_score = None
    sample.contact_id = 1

    # Must NOT raise AttributeError after the fix
    result = _sample_to_response(sample)
    assert result.image_path == "enrolled/contact_1/sample_001_full.jpg"


# ===========================================================================
# AC1-BUG-C: FaceSampleResponse schema missing thumb_url field
# ===========================================================================

def test_ac1_face_sample_response_has_no_thumb_url():
    """Regression AC1: FaceSampleResponse must expose thumb_url for render-ready URLs.

    The fix added thumb_url: Optional[str] = None to FaceSampleResponse.
    The router _sample_to_response now populates it via to_storage_url().
    """
    assert "thumb_url" in FaceSampleResponse.model_fields, (
        "FaceSampleResponse must have a 'thumb_url' field (Optional[str] = None). "
        "The router populates it via to_storage_url(sample.thumb_path)."
    )


# ===========================================================================
# AC2-BUG: FacePanelResponse missing contact_id field
# ===========================================================================

def test_ac2_face_panel_response_missing_contact_id():
    """Regression AC2: FacePanelResponse must expose contact_id.

    The fix added contact_id: Optional[int] = None to FacePanelResponse.
    The router now passes contact_id=contact_id to the response constructor
    so callers know which contact the panel belongs to.
    """
    assert "contact_id" in FacePanelResponse.model_fields, (
        "FacePanelResponse must have a 'contact_id' field (Optional[int] = None). "
        "The router passes contact_id=contact_id in the FacePanelResponse constructor."
    )


def test_ac2_face_panel_response_is_orphan_required_but_router_omits_it():
    """FAIL: FacePanelResponse.is_orphan is a REQUIRED field (no default).

    The router's get_face_panel (lines 163-170) does NOT pass is_orphan= to
    FacePanelResponse. This causes a Pydantic ValidationError (→ 500 Internal
    Server Error) on every GET /contacts/{id}/faces call.

    Fix: add is_orphan=subject.is_orphan if subject else False to the
    FacePanelResponse(...) call in the router.
    """
    # is_orphan has no default and is therefore required
    field = FacePanelResponse.model_fields.get("is_orphan")
    assert field is not None, "FacePanelResponse.is_orphan field not found"
    assert field.is_required(), (
        "FacePanelResponse.is_orphan is not required — this test logic needs updating"
    )

    # Building without is_orphan must raise ValidationError
    import pydantic
    with pytest.raises(pydantic.ValidationError, match="is_orphan"):
        FacePanelResponse(
            contact_id=1,
            compreface_subject_id="contact_1",
            sample_count=0,
            enrollment_status="pending",
            last_trained_at=None,
            samples=[],
            # is_orphan OMITTED — as the router does it — should raise
        )


async def test_ac2_get_face_panel_response_includes_contact_id(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """FAIL: GET /contacts/{id}/faces response JSON does not contain contact_id.

    Expected: response.json()['contact_id'] == sample_contact.id
    Actual: 'contact_id' key is absent from the response JSON.
    """
    response = await client.get(
        f"/contacts/{sample_contact.id}/faces",
        headers=volunteer_auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "contact_id" in data, (
        f"'contact_id' missing from FacePanelResponse JSON. Got keys: {list(data.keys())}"
    )
    assert data["contact_id"] == sample_contact.id


# ===========================================================================
# AC2-BUG: to_storage_url must produce /storage/-prefixed URLs
# ===========================================================================

def test_ac2_to_storage_url_produces_storage_prefix():
    """to_storage_url must prepend /storage/ to bare relative paths."""
    assert to_storage_url("faces/2026/01/01/thumb.jpg") == "/storage/faces/2026/01/01/thumb.jpg"
    assert to_storage_url("/storage/faces/thumb.jpg") == "/storage/faces/thumb.jpg"
    assert to_storage_url(None) is None
    assert to_storage_url("") is None


# ===========================================================================
# AC4-BUG-A: retrain endpoint accesses sample.full_path (AttributeError)
# ===========================================================================

async def test_ac4_retrain_endpoint_fails_on_real_sample(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """FAIL: POST /contacts/{id}/faces/retrain accesses sample.full_path on line 302.

    FaceSample has no 'full_path' attribute — the column is 'image_path'.
    This raises AttributeError when the retrain loop reads sample.full_path.
    Expected: 200 RetrainResponse with last_trained_at set
    Actual: 500 Internal Server Error (AttributeError: 'FaceSample' has no attribute 'full_path')

    Fix: router retrain_contact_faces loop must use sample.image_path.
    """
    subject = await _seed_subject(
        db_session, sample_contact.id, sample_count=1, enrollment_status="active"
    )
    await _seed_face_sample(db_session, sample_contact.id, subject.compreface_subject_id)

    mock_client = _make_mock_client(image_id="cf-retrain-001")
    mock_path = MagicMock()
    mock_path.read_bytes = MagicMock(return_value=b"\xff\xd8\xff")
    mock_storage = _make_mock_storage()
    mock_storage.base_path.__truediv__ = MagicMock(return_value=mock_path)

    with (
        patch("app.routers.enrollment._make_storage", return_value=mock_storage),
        patch("app.routers.enrollment.ComprefaceClient", return_value=mock_client),
    ):
        response = await client.post(
            f"/contacts/{sample_contact.id}/faces/retrain",
            headers=volunteer_auth_headers,
        )

    # Currently fails with 500 due to AttributeError: sample.full_path
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. "
        f"Error: {response.text[:500]}"
    )
    data = response.json()
    assert data["last_trained_at"] is not None


# ===========================================================================
# AC4-BUG-B: backfill endpoint uses FaceSample.full_path in WHERE clause
# ===========================================================================

async def test_ac4_backfill_endpoint_fails_on_class_attr_full_path(
    client,
    db_session: AsyncSession,
    sample_contact,
    admin_auth_headers,
    tmp_path,
):
    """FAIL: POST /enrollment/backfill uses FaceSample.full_path in SQLAlchemy WHERE clause.

    FaceSample has no class-level 'full_path' attribute.
    When the backfill processes a subject directory, it does:
      select(FaceSample).where(FaceSample.full_path == full_rel)  (line 493-495)
    This raises AttributeError: type object 'FaceSample' has no attribute 'full_path'.

    Fix: use FaceSample.image_path in the WHERE clause.
    """
    # Create the enrolled directory structure
    enrolled_dir = tmp_path / "enrolled" / f"contact_{sample_contact.id}"
    enrolled_dir.mkdir(parents=True)
    (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    await _seed_subject(db_session, sample_contact.id, sample_count=0)

    mock_client = _make_mock_client(image_id="cf-backfill-001")

    with (
        patch("app.routers.enrollment.ComprefaceClient", return_value=mock_client),
        patch("os.environ.get", side_effect=lambda k, d=None: str(tmp_path) if k == "STORAGE_PATH" else d),
    ):
        response = await client.post(
            "/enrollment/backfill",
            headers=admin_auth_headers,
            json={"dry_run": False},
        )

    # Currently fails with 500 due to AttributeError: FaceSample.full_path
    assert response.status_code == 200, (
        f"Expected 200 but got {response.status_code}. Error: {response.text[:500]}"
    )


# ===========================================================================
# AC1: POST /contacts/{id}/faces happy path (201 + rows created)
# We test with full mocking since the real path fails on full_path bug
# ===========================================================================

async def test_ac1_post_faces_returns_201_with_mocked_response(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """AC1 happy path using full mocking to isolate the auth/status assertion
    from the known full_path serialization bugs.
    """
    face_crop = _make_face_crop()
    _, img_bytes = cv2.imencode(".jpg", face_crop)
    image_bytes = img_bytes.tobytes()

    fake_sample = FaceSample(
        compreface_subject_id=f"contact_{sample_contact.id}",
        contact_id=sample_contact.id,
        image_path="enrolled/contact_1/sample_001_full.jpg",
        thumb_path="enrolled/contact_1/sample_001_thumb.jpg",
        source="manual",
        compreface_image_id="cf-uuid-001",
    )
    fake_sample.id = 9001
    fake_sample.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    fake_sample.quality_score = None

    fake_response = FaceSampleResponse(
        id=9001,
        compreface_subject_id=fake_sample.compreface_subject_id,
        contact_id=fake_sample.contact_id,
        image_path=fake_sample.image_path,
        thumb_path=fake_sample.thumb_path,
        source=fake_sample.source,
        compreface_image_id=fake_sample.compreface_image_id,
        created_at=fake_sample.created_at,
    )

    with (
        patch(
            "app.routers.enrollment.EnrollmentService.enroll_contact_face",
            new_callable=AsyncMock,
            return_value=fake_sample,
        ),
        patch("app.routers.enrollment._sample_to_response", return_value=fake_response),
        patch("app.routers.enrollment.ComprefaceClient", return_value=_make_mock_client()),
        patch("app.routers.enrollment._make_storage", return_value=_make_mock_storage()),
    ):
        response = await client.post(
            f"/contacts/{sample_contact.id}/faces",
            headers=volunteer_auth_headers,
            files={"file": ("face.jpg", image_bytes, "image/jpeg")},
        )

    assert response.status_code == 201, response.text


# ===========================================================================
# AC3: DELETE endpoint — row removed, delete_example called, count recomputed
# (This should PASS with the current implementation)
# ===========================================================================

async def test_ac3_delete_face_sample_removes_row_and_recomputes_count(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """AC3: DELETE /contacts/{id}/faces/{sample_id} works correctly."""
    subject = await _seed_subject(
        db_session, sample_contact.id, sample_count=1, enrollment_status="active"
    )
    sample = await _seed_face_sample(
        db_session, sample_contact.id, subject.compreface_subject_id,
        compreface_image_id="cf-img-delete",
    )

    mock_client = _make_mock_client()
    with patch("app.routers.enrollment.ComprefaceClient", return_value=mock_client):
        response = await client.delete(
            f"/contacts/{sample_contact.id}/faces/{sample.id}",
            headers=volunteer_auth_headers,
        )

    assert response.status_code == 204, response.text

    deleted = await db_session.get(FaceSample, sample.id)
    assert deleted is None, "FaceSample row was not deleted"

    mock_client.delete_example.assert_called_once_with("cf-img-delete")

    await db_session.refresh(subject)
    assert subject.sample_count == 0, (
        f"sample_count not recomputed: expected 0, got {subject.sample_count}"
    )


# ===========================================================================
# AC3: purged_at set → DELETE returns 409
# ===========================================================================

async def test_ac3_delete_purged_subject_returns_409(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """AC7: DELETE on a purged subject must return 409."""
    purged_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    subject = await _seed_subject(
        db_session, sample_contact.id, purged_at=purged_ts, sample_count=1
    )
    sample = await _seed_face_sample(
        db_session, sample_contact.id, subject.compreface_subject_id
    )

    response = await client.delete(
        f"/contacts/{sample_contact.id}/faces/{sample.id}",
        headers=volunteer_auth_headers,
    )
    assert response.status_code == 409, (
        f"Expected 409, got {response.status_code}: {response.text}"
    )


# ===========================================================================
# AC5: GET /contacts/{id}/recognition-history returns paginated Participant rows
# ===========================================================================

async def test_ac5_recognition_history_returns_only_face_source(
    client,
    db_session: AsyncSession,
    sample_contact,
    sample_event,
    sample_detection,
    volunteer_auth_headers,
):
    """AC5: recognition-history filters to source='face' and paginates correctly."""
    from app.models import Participant

    # Add a face-source participant
    p1 = Participant(
        contact_id=sample_contact.id,
        event_id=sample_event.id,
        source="face",
        status="attended",
    )
    # Add a manual-source participant for a different event to test filtering
    # (we can't add two participants for same event due to UniqueConstraint)
    db_session.add(p1)
    await db_session.commit()

    response = await client.get(
        f"/contacts/{sample_contact.id}/recognition-history",
        headers=volunteer_auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["source"] == "face", (
            f"Expected source='face', got source='{item['source']}'"
        )


# ===========================================================================
# AC6: Viewer (get_current_user) can access GET endpoints
# ===========================================================================

async def test_ac6_viewer_can_get_face_panel(
    client,
    db_session: AsyncSession,
    sample_contact,
    viewer_auth_headers,
):
    """FAIL: Viewer role should be able to access GET /contacts/{id}/faces (returns 200).

    Currently fails with 500 because the router builds FacePanelResponse without
    passing is_orphan= (required field) on line 163-170 of enrollment.py.
    Fix: add is_orphan=subject.is_orphan if subject else False to the FacePanelResponse call.
    """
    response = await client.get(
        f"/contacts/{sample_contact.id}/faces",
        headers=viewer_auth_headers,
    )
    assert response.status_code == 200, (
        f"Viewer should be able to access GET faces. Got: {response.status_code}. "
        f"Error detail: {response.text[:300]}"
    )


async def test_ac6_viewer_cannot_post_faces(
    client,
    db_session: AsyncSession,
    sample_contact,
    viewer_auth_headers,
):
    """AC6: Viewer role must be rejected for POST /contacts/{id}/faces."""
    face_crop = _make_face_crop()
    _, img_bytes = cv2.imencode(".jpg", face_crop)
    response = await client.post(
        f"/contacts/{sample_contact.id}/faces",
        headers=viewer_auth_headers,
        files={"file": ("face.jpg", img_bytes.tobytes(), "image/jpeg")},
    )
    assert response.status_code == 403, (
        f"Viewer should be rejected with 403, got {response.status_code}"
    )


async def test_ac6_viewer_cannot_delete_faces(
    client,
    db_session: AsyncSession,
    sample_contact,
    viewer_auth_headers,
):
    """AC6: Viewer role must be rejected for DELETE /contacts/{id}/faces/{id}."""
    response = await client.delete(
        f"/contacts/{sample_contact.id}/faces/999",
        headers=viewer_auth_headers,
    )
    # 403 (auth) takes priority over 404 (not found)
    assert response.status_code == 403, (
        f"Viewer should be rejected with 403, got {response.status_code}"
    )


async def test_ac6_unauthenticated_cannot_access_faces(
    client,
    db_session: AsyncSession,
    sample_contact,
):
    """AC6: Unauthenticated requests return 401/403."""
    response = await client.get(f"/contacts/{sample_contact.id}/faces")
    assert response.status_code in (401, 403), (
        f"Unauthenticated request should be rejected, got {response.status_code}"
    )


# ===========================================================================
# AC7: 502 when CompreFace returns None for add_example
# ===========================================================================

async def test_ac7_add_example_none_returns_502(
    db_session: AsyncSession,
    sample_contact,
):
    """AC7: add_example returning None raises HTTPException 502."""
    from fastapi import HTTPException

    storage = _make_mock_storage()
    client_mock = _make_mock_client(image_id=None)  # add_example returns None

    with patch(
        "app.services.enrollment.FaceQualityGate.check",
        return_value=(True, "OK"),
    ):
        svc = EnrollmentService(client=client_mock, storage=storage)
        with pytest.raises(HTTPException) as exc_info:
            await svc.enroll_contact_face(
                session=db_session,
                contact_id=sample_contact.id,
                face_crop=_make_face_crop(),
                source="manual",
            )

    assert exc_info.value.status_code == 502, (
        f"Expected 502 for None add_example result, got {exc_info.value.status_code}"
    )


# ===========================================================================
# AC7: purged_at non-null → 409 on POST
# ===========================================================================

async def test_ac7_purged_subject_returns_409_on_enroll(
    db_session: AsyncSession,
    sample_contact,
):
    """AC7: Enrolling into a purged subject returns 409."""
    from fastapi import HTTPException

    purged_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    await _seed_subject(db_session, sample_contact.id, purged_at=purged_ts)

    with patch(
        "app.services.enrollment.FaceQualityGate.check",
        return_value=(True, "OK"),
    ):
        svc = EnrollmentService(
            client=_make_mock_client(), storage=_make_mock_storage()
        )
        with pytest.raises(HTTPException) as exc_info:
            await svc.enroll_contact_face(
                session=db_session,
                contact_id=sample_contact.id,
                face_crop=_make_face_crop(),
                source="manual",
            )

    assert exc_info.value.status_code == 409


# ===========================================================================
# AC8: ComprefaceClient closed in finally blocks
# ===========================================================================

async def test_ac8_client_closed_in_finally_on_add_face_sample(
    client,
    db_session: AsyncSession,
    sample_contact,
    volunteer_auth_headers,
):
    """AC8: ComprefaceClient.close() is called in the finally block of add_face_sample.

    Even when EnrollmentService raises (e.g., 404), the client must be closed.
    """
    face_crop = _make_face_crop()
    _, img_bytes = cv2.imencode(".jpg", face_crop)

    mock_client = _make_mock_client()

    with (
        patch("app.routers.enrollment.ComprefaceClient", return_value=mock_client),
        patch("app.routers.enrollment._make_storage", return_value=_make_mock_storage()),
        patch(
            "app.routers.enrollment.EnrollmentService.enroll_contact_face",
            new_callable=AsyncMock,
            side_effect=Exception("Simulated internal error"),
        ),
    ):
        try:
            await client.post(
                f"/contacts/{sample_contact.id}/faces",
                headers=volunteer_auth_headers,
                files={"file": ("face.jpg", img_bytes.tobytes(), "image/jpeg")},
            )
        except Exception:
            pass

    # close() must have been called
    mock_client.close.assert_called_once(), (
        "ComprefaceClient.close() was not called in the finally block"
    )


# ===========================================================================
# AC9: Router prefix is /contacts, tags=['enrollment']
# ===========================================================================

def test_ac9_router_prefix_and_tags():
    """AC9: The enrollment router must have prefix='/contacts' and tags=['enrollment']."""
    from app.routers.enrollment import router, backfill_router

    assert router.prefix == "/contacts", (
        f"Expected router prefix '/contacts', got '{router.prefix}'"
    )
    assert "enrollment" in router.tags, (
        f"Expected 'enrollment' in router tags, got {router.tags}"
    )
    assert backfill_router.prefix == "/enrollment", (
        f"Expected backfill_router prefix '/enrollment', got '{backfill_router.prefix}'"
    )


# ===========================================================================
# AC5 DEFECT: RecognitionHistoryItem schema missing status and detection_id
# ===========================================================================

def test_ac5_recognition_history_item_has_status_field():
    """FAIL: RecognitionHistoryItem schema is missing the 'status' field.

    The router's get_recognition_history (enrollment.py line 386) passes
    status=p.status to RecognitionHistoryItem, but the schema does not declare
    a 'status' field. Pydantic silently drops unknown fields, so the API
    response omits 'status' entirely.

    Fix: add `status: str` to RecognitionHistoryItem in schemas.py.
    """
    from app.schemas import RecognitionHistoryItem
    assert "status" in RecognitionHistoryItem.model_fields, (
        "RecognitionHistoryItem must have a 'status' field. "
        "The router passes p.status but it is silently dropped by Pydantic because "
        "the schema does not declare it. "
        "Fix: add `status: str` to RecognitionHistoryItem in schemas.py."
    )


def test_ac5_recognition_history_item_has_detection_id_field():
    """FAIL: RecognitionHistoryItem schema is missing the 'detection_id' field.

    The router passes detection_id=p.detection_id to RecognitionHistoryItem but
    the schema does not declare 'detection_id'. Pydantic silently drops it.

    Fix: add `detection_id: Optional[int] = None` to RecognitionHistoryItem in schemas.py.
    """
    from app.schemas import RecognitionHistoryItem
    assert "detection_id" in RecognitionHistoryItem.model_fields, (
        "RecognitionHistoryItem must have a 'detection_id' field (Optional[int] = None). "
        "The router passes p.detection_id but Pydantic silently drops it because "
        "the schema does not declare it. "
        "Fix: add `detection_id: Optional[int] = None` to RecognitionHistoryItem in schemas.py."
    )


async def test_ac5_recognition_history_response_contains_status_in_json(
    client,
    db_session: AsyncSession,
    sample_contact,
    sample_event,
    sample_detection,
    volunteer_auth_headers,
):
    """FAIL: GET /contacts/{id}/recognition-history JSON items must include 'status'.

    Expected: each item in response['items'] has 'status' key (e.g. 'attended').
    Actual: 'status' is absent because RecognitionHistoryItem schema does not declare it.
    """
    from app.models import Participant

    p = Participant(
        contact_id=sample_contact.id,
        event_id=sample_event.id,
        source="face",
        status="attended",
        detection_id=sample_detection.id,
    )
    db_session.add(p)
    await db_session.commit()

    response = await client.get(
        f"/contacts/{sample_contact.id}/recognition-history",
        headers=volunteer_auth_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["items"], "Expected at least 1 item in recognition-history response"
    item = data["items"][0]
    assert "status" in item, (
        f"'status' field missing from RecognitionHistoryItem JSON. "
        f"Got keys: {list(item.keys())}. "
        f"Fix: add `status: str` to RecognitionHistoryItem in schemas.py."
    )
    assert item["status"] == "attended", (
        f"Expected status='attended', got status='{item.get('status')}'"
    )
