"""
Regression tests for S07 enrollment defects (QA pass → fixed).

Each defect was found during the S07 QA pass.  These tests now verify the
FIXES are in place.  A test failure here means a regression was introduced.

DEFECT-1 (FIXED): EnrollmentService.enroll_contact_face was constructing
    FaceSample with kwarg `full_path=` but the ORM column is `image_path`.
    Fix: use `image_path=full_path` in enrollment.py.

DEFECT-2 (FIXED): enrollment router _sample_to_response was passing
    `full_path=sample.full_path` and `thumb_url=` to FaceSampleResponse,
    but neither field exists in the schema.
    Fix: use `image_path=sample.image_path`; drop `thumb_url=` kwarg.

DEFECT-3 (FIXED): S07 migration declared face_samples.compreface_subject_id
    as sa.Integer() but the FK target is VARCHAR(255).
    Fix: changed to sa.String(255).

DEFECT-4 (FIXED): S07 migration photo_ingest_batches used column names that
    diverged from the ORM (submitted_by_id/total_files/faces_found/started_at).
    Fix: updated to ORM names (uploaded_by_id/total_images/faces_detected).

DEFECT-5 (FIXED): enrollment router get_face_panel was constructing
    FacePanelResponse without the required field `is_orphan`, causing a
    Pydantic ValidationError on every GET /contacts/{id}/faces call.
    Fix: pass is_orphan=subject.is_orphan if subject else False.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, FaceSample
from app.schemas import FaceSampleResponse, FacePanelResponse
from app.services.enrollment import EnrollmentService


# ---------------------------------------------------------------------------
# Helpers (same as test_enrollment.py so this file is self-contained)
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


# ---------------------------------------------------------------------------
# DEFECT-1 REGRESSION: FaceSample ORM uses image_path, not full_path
# ---------------------------------------------------------------------------

def test_defect1_face_sample_column_is_image_path_not_full_path():
    """Regression DEFECT-1: FaceSample ORM column must be `image_path`, not `full_path`."""
    cols = {c.key for c in FaceSample.__table__.columns}
    assert "image_path" in cols, (
        "FaceSample ORM does not have an 'image_path' column — check models.py"
    )
    assert "full_path" not in cols, (
        "FaceSample ORM unexpectedly has a 'full_path' column — if added, "
        "update enrollment.py to use it and close DEFECT-1."
    )


def test_defect1_face_sample_rejects_full_path_kwarg():
    """Regression DEFECT-1: FaceSample() must raise TypeError for unknown kwarg `full_path`.

    This confirms the ORM does not accept `full_path=` — validating the service
    must use `image_path=` instead.
    """
    with pytest.raises(TypeError, match="full_path"):
        FaceSample(
            compreface_subject_id="contact_1",
            full_path="enrolled/contact_1/sample_001_full.jpg",
            thumb_path="enrolled/contact_1/sample_001_thumb.jpg",
            source="manual",
            compreface_image_id="cf-uuid-001",
        )


async def test_defect1_enroll_contact_face_succeeds_with_image_path(
    db_session: AsyncSession,
    sample_contact,
):
    """Regression DEFECT-1: enroll_contact_face must succeed and commit a FaceSample row.

    Before the fix the service used `full_path=full_path` which raised TypeError
    (caught as 502).  After the fix it uses `image_path=full_path` and the row
    is committed successfully.
    """
    svc = EnrollmentService(
        client=_make_mock_client(),
        storage=_make_mock_storage(),
    )
    with patch(
        "app.services.enrollment.FaceQualityGate.check",
        return_value=(True, "OK"),
    ):
        sample = await svc.enroll_contact_face(
            session=db_session,
            contact_id=sample_contact.id,
            face_crop=_make_face_crop(),
            source="manual",
        )

    assert sample is not None
    assert sample.image_path == "enrolled/contact_1/sample_001_full.jpg"

    count = (await db_session.execute(select(func.count(FaceSample.id)))).scalar()
    assert count == 1, (
        f"Expected exactly 1 FaceSample row after successful enrollment, got {count}"
    )


# ---------------------------------------------------------------------------
# DEFECT-2 REGRESSION: _sample_to_response schema field mismatch
# ---------------------------------------------------------------------------

def test_defect2_face_sample_response_has_image_path_not_full_path():
    """Regression DEFECT-2: FaceSampleResponse schema must use `image_path`, not `full_path`."""
    fields = set(FaceSampleResponse.model_fields.keys())
    assert "image_path" in fields, (
        "FaceSampleResponse does not have 'image_path' field — check schemas.py"
    )
    assert "full_path" not in fields, (
        "FaceSampleResponse unexpectedly has a 'full_path' field; if added "
        "intentionally, update _sample_to_response accordingly."
    )
    # thumb_url is an intentional field exposing render-ready URLs to the frontend
    assert "thumb_url" in fields, (
        "FaceSampleResponse should have a 'thumb_url' field for render-ready thumbnail URLs"
    )


def test_defect2_sample_to_response_uses_correct_field_names():
    """Regression DEFECT-2: _sample_to_response must use image_path= not full_path=.

    Reads the router source to confirm the fix is in place.
    """
    router_path = Path(__file__).parent.parent / "app" / "routers" / "enrollment.py"
    source = router_path.read_text()
    assert "full_path=sample.full_path" not in source, (
        "DEFECT-2 regression: _sample_to_response still uses `full_path=sample.full_path`. "
        "Fix: change to `image_path=sample.image_path`."
    )
    assert "image_path=sample.image_path" in source, (
        "DEFECT-2: _sample_to_response must use `image_path=sample.image_path`."
    )
    assert "thumb_url=to_storage_url" in source, (
        "DEFECT-2 regression: _sample_to_response must pass `thumb_url=to_storage_url(...)` "
        "since thumb_url is now a declared field on FaceSampleResponse."
    )


# ---------------------------------------------------------------------------
# DEFECT-3 REGRESSION: Migration face_samples.compreface_subject_id type
# ---------------------------------------------------------------------------

def test_defect3_migration_face_samples_compreface_subject_id_is_string():
    """Regression DEFECT-3: face_samples.compreface_subject_id must be sa.String(255).

    The referenced column compreface_subjects.compreface_subject_id is VARCHAR(255).
    The FK column type must match the referenced column type.
    """
    migration_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "i3j4k5l6m7n8_s07_face_enrollment_and_samples.py"
    )
    source = migration_path.read_text()

    # Find the face_samples create_table block
    fs_block_match = re.search(
        r'create_table\(\s*["\']face_samples["\'].*?(?=create_table|\ndef )',
        source,
        re.DOTALL,
    )
    assert fs_block_match, "Could not locate face_samples create_table block in migration"
    fs_block = fs_block_match.group(0)

    # compreface_subject_id column definition
    subj_match = re.search(
        r'"compreface_subject_id".*?(?=sa\.Column|\),$)',
        fs_block,
        re.DOTALL,
    )
    assert subj_match, "Could not find compreface_subject_id column in face_samples block"
    col_def = subj_match.group(0)

    assert "sa.Integer()" not in col_def, (
        "DEFECT-3 regression: face_samples.compreface_subject_id is still sa.Integer(). "
        "Fix: change to sa.String(255) to match the FK target VARCHAR(255)."
    )
    assert "sa.String(255)" in col_def or "sa.String" in col_def, (
        "DEFECT-3: compreface_subject_id must be sa.String(255), not sa.Integer()."
    )


# ---------------------------------------------------------------------------
# DEFECT-4 REGRESSION: Migration photo_ingest_batches column name divergence
# ---------------------------------------------------------------------------

def test_defect4_migration_photo_ingest_batches_column_names_match_orm():
    """Regression DEFECT-4: photo_ingest_batches migration must use ORM column names.

    ORM uses: uploaded_by_id, total_images, faces_detected (no started_at).
    The migration must not use the old spec names: submitted_by_id, total_files,
    faces_found, started_at.
    """
    migration_path = (
        Path(__file__).parent.parent
        / "alembic"
        / "versions"
        / "i3j4k5l6m7n8_s07_face_enrollment_and_samples.py"
    )
    source = migration_path.read_text()

    regressions = []
    if "submitted_by_id" in source:
        regressions.append("submitted_by_id (old spec name; ORM uses uploaded_by_id)")
    if '"total_files"' in source:
        regressions.append("total_files (old spec name; ORM uses total_images)")
    if '"faces_found"' in source:
        regressions.append("faces_found (old spec name; ORM uses faces_detected)")
    if '"started_at"' in source and "photo_ingest_batches" in source:
        # started_at appears only inside the photo_ingest_batches block
        pib_match = re.search(
            r'create_table\(\s*["\']photo_ingest_batches["\'].*?(?=create_table|\ndef )',
            source,
            re.DOTALL,
        )
        if pib_match and '"started_at"' in pib_match.group(0):
            regressions.append("started_at (not in ORM; ORM uses created_at)")

    assert not regressions, (
        "DEFECT-4 regression: photo_ingest_batches migration column name mismatches: "
        + "; ".join(regressions)
    )

    # Also assert the correct ORM names are present
    assert "uploaded_by_id" in source, (
        "DEFECT-4: migration must use uploaded_by_id (ORM column name)"
    )
    assert "total_images" in source, (
        "DEFECT-4: migration must use total_images (ORM column name)"
    )
    assert "faces_detected" in source, (
        "DEFECT-4: migration must use faces_detected (ORM column name)"
    )


# ---------------------------------------------------------------------------
# DEFECT-5 REGRESSION: FacePanelResponse missing required is_orphan
# ---------------------------------------------------------------------------

def test_defect5_face_panel_response_requires_is_orphan():
    """Regression DEFECT-5: FacePanelResponse must have `is_orphan: bool` (required)."""
    fields = FacePanelResponse.model_fields
    assert "is_orphan" in fields, (
        "FacePanelResponse does not have is_orphan field — check schemas.py"
    )
    field = fields["is_orphan"]
    assert field.is_required(), (
        "is_orphan must be a required field (no default) in FacePanelResponse."
    )


def test_defect5_router_passes_is_orphan_to_face_panel_response():
    """Regression DEFECT-5: get_face_panel must pass is_orphan to FacePanelResponse.

    Before the fix, is_orphan was omitted, causing a Pydantic ValidationError
    on every GET /contacts/{id}/faces request.
    """
    router_path = Path(__file__).parent.parent / "app" / "routers" / "enrollment.py"
    source = router_path.read_text()
    panel_match = re.search(r"return FacePanelResponse\((.*?)\)", source, re.DOTALL)
    assert panel_match is not None, "Could not find FacePanelResponse construction in router"
    construction = panel_match.group(1)
    assert "is_orphan" in construction, (
        "DEFECT-5 regression: get_face_panel does not pass `is_orphan` to FacePanelResponse. "
        "Fix: add `is_orphan=subject.is_orphan if subject else False`."
    )
    assert "contact_id" in construction, (
        "DEFECT-5 regression: contact_id= must be passed to FacePanelResponse "
        "(it is now a declared field on the schema)."
    )
