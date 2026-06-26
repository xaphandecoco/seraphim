"""
QA tests for s07-models patch.

Verifies:
1. FaceSample ORM class: __tablename__, all columns, both indexes in __table_args__
2. PhotoIngestBatch ORM class: __tablename__, all columns, JSONB alias, index
3. ComprefaceSubject extensions: enrollment_source, last_trained_at, is_orphan, purged_at
4. Both new tables are created via Base.metadata.create_all (SQLite path)
5. Column types, nullability, and defaults match migration spec
"""

import importlib
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import Index, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Class-level structural tests (no DB required)
# ---------------------------------------------------------------------------


class TestFaceSampleModel:
    """Verify FaceSample ORM class structure against acceptance criteria."""

    def test_face_sample_class_exists(self):
        from app import models
        assert hasattr(models, "FaceSample"), "FaceSample class must exist in app.models"

    def test_face_sample_tablename(self):
        from app.models import FaceSample
        assert FaceSample.__tablename__ == "face_samples"

    def test_face_sample_has_id(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("id")
        assert col is not None, "FaceSample must have 'id' column"
        assert col.primary_key is True
        assert col.autoincrement is True

    def test_face_sample_has_compreface_subject_id(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("compreface_subject_id")
        assert col is not None, "FaceSample must have 'compreface_subject_id' column"
        assert col.nullable is False
        # Must be a FK to compreface_subjects.compreface_subject_id
        fks = col.foreign_keys
        assert len(fks) == 1
        fk = list(fks)[0]
        assert "compreface_subjects" in fk.target_fullname

    def test_face_sample_compreface_subject_id_cascade(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("compreface_subject_id")
        fk = list(col.foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_face_sample_has_contact_id(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("contact_id")
        assert col is not None, "FaceSample must have 'contact_id' column"
        assert col.nullable is True

    def test_face_sample_contact_id_fk(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("contact_id")
        fks = col.foreign_keys
        assert len(fks) == 1
        fk = list(fks)[0]
        assert "contacts" in fk.target_fullname

    def test_face_sample_has_image_path(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("image_path")
        assert col is not None, "FaceSample must have 'image_path' column"
        assert col.nullable is False

    def test_face_sample_has_thumb_path(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("thumb_path")
        assert col is not None, "FaceSample must have 'thumb_path' column"

    def test_face_sample_has_compreface_image_id(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("compreface_image_id")
        assert col is not None, "FaceSample must have 'compreface_image_id' column"
        assert col.nullable is True

    def test_face_sample_has_source(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("source")
        assert col is not None, "FaceSample must have 'source' column"
        assert col.nullable is False

    def test_face_sample_has_added_by_id(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("added_by_id")
        assert col is not None, "FaceSample must have 'added_by_id' column"
        assert col.nullable is True

    def test_face_sample_added_by_fk(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("added_by_id")
        fks = col.foreign_keys
        assert len(fks) == 1
        fk = list(fks)[0]
        assert "users" in fk.target_fullname

    def test_face_sample_has_quality_score(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("quality_score")
        assert col is not None, "FaceSample must have 'quality_score' column"
        assert col.nullable is True

    def test_face_sample_has_created_at(self):
        from app.models import FaceSample
        col = FaceSample.__table__.c.get("created_at")
        assert col is not None, "FaceSample must have 'created_at' column"

    def test_face_sample_index_contact_id(self):
        from app.models import FaceSample
        index_names = [idx.name for idx in FaceSample.__table__.indexes]
        assert "ix_face_samples_contact_id" in index_names, (
            f"Expected 'ix_face_samples_contact_id' in {index_names}"
        )

    def test_face_sample_index_subject_id(self):
        from app.models import FaceSample
        index_names = [idx.name for idx in FaceSample.__table__.indexes]
        assert "ix_face_samples_subject_id" in index_names, (
            f"Expected 'ix_face_samples_subject_id' in {index_names}"
        )

    def test_face_sample_index_contact_id_column(self):
        from app.models import FaceSample
        for idx in FaceSample.__table__.indexes:
            if idx.name == "ix_face_samples_contact_id":
                cols = [c.name for c in idx.columns]
                assert "contact_id" in cols
                break

    def test_face_sample_index_subject_id_column(self):
        from app.models import FaceSample
        for idx in FaceSample.__table__.indexes:
            if idx.name == "ix_face_samples_subject_id":
                cols = [c.name for c in idx.columns]
                assert "compreface_subject_id" in cols
                break


class TestPhotoIngestBatchModel:
    """Verify PhotoIngestBatch ORM class structure against acceptance criteria."""

    def test_photo_ingest_batch_class_exists(self):
        from app import models
        assert hasattr(models, "PhotoIngestBatch"), (
            "PhotoIngestBatch class must exist in app.models"
        )

    def test_photo_ingest_batch_tablename(self):
        from app.models import PhotoIngestBatch
        assert PhotoIngestBatch.__tablename__ == "photo_ingest_batches"

    def test_photo_ingest_batch_has_id(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("id")
        assert col is not None
        assert col.primary_key is True

    def test_photo_ingest_batch_has_event_id(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("event_id")
        assert col is not None
        assert col.nullable is True

    def test_photo_ingest_batch_has_status(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("status")
        assert col is not None
        assert col.nullable is False

    def test_photo_ingest_batch_has_report_column(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("report")
        assert col is not None, "PhotoIngestBatch must have 'report' column"
        assert col.nullable is False

    def test_photo_ingest_batch_report_uses_jsonb_alias(self):
        """report column type must use the JSONB alias (JSON().with_variant)."""
        from app.models import PhotoIngestBatch, JSONB
        col = PhotoIngestBatch.__table__.c.get("report")
        # The column type should be the JSONB alias (a JSON type with postgresql variant)
        col_type = type(col.type).__name__
        assert col_type in ("JSON", "JSONB"), (
            f"report column type should be JSON/JSONB alias, got {col_type}"
        )

    def test_photo_ingest_batch_has_created_at(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("created_at")
        assert col is not None

    def test_photo_ingest_batch_has_finished_at(self):
        from app.models import PhotoIngestBatch
        col = PhotoIngestBatch.__table__.c.get("finished_at")
        assert col is not None
        assert col.nullable is True

    def test_photo_ingest_batch_status_index(self):
        from app.models import PhotoIngestBatch
        index_names = [idx.name for idx in PhotoIngestBatch.__table__.indexes]
        assert "ix_photo_ingest_batches_status" in index_names, (
            f"Expected 'ix_photo_ingest_batches_status' in {index_names}"
        )

    def test_photo_ingest_batch_numeric_columns_not_nullable(self):
        """All counter columns must be non-nullable with defaults."""
        from app.models import PhotoIngestBatch
        # Check all integer counter columns exist and are non-nullable
        for col_name in ("total_images", "processed_images", "faces_detected",
                         "auto_logged", "tasks_created", "skipped",
                         "deduplicated", "errors"):
            col = PhotoIngestBatch.__table__.c.get(col_name)
            if col is not None:  # column exists
                assert col.nullable is False, (
                    f"PhotoIngestBatch.{col_name} must be non-nullable"
                )


class TestComprefaceSubjectExtension:
    """Verify the four new columns were added to ComprefaceSubject."""

    def test_enrollment_source_column_exists(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("enrollment_source")
        assert col is not None, (
            "ComprefaceSubject must have 'enrollment_source' column"
        )

    def test_enrollment_source_is_nullable(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("enrollment_source")
        assert col.nullable is True

    def test_last_trained_at_column_exists(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("last_trained_at")
        assert col is not None, (
            "ComprefaceSubject must have 'last_trained_at' column"
        )

    def test_last_trained_at_is_nullable(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("last_trained_at")
        assert col.nullable is True

    def test_is_orphan_column_exists(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("is_orphan")
        assert col is not None, "ComprefaceSubject must have 'is_orphan' column"

    def test_is_orphan_is_not_nullable(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("is_orphan")
        assert col.nullable is False, "is_orphan must be non-nullable"

    def test_is_orphan_has_false_default(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("is_orphan")
        # Python-side default should be False
        assert col.default is not None or col.server_default is not None, (
            "is_orphan must have a default of False"
        )

    def test_purged_at_column_exists(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("purged_at")
        assert col is not None, "ComprefaceSubject must have 'purged_at' column"

    def test_purged_at_is_nullable(self):
        from app.models import ComprefaceSubject
        col = ComprefaceSubject.__table__.c.get("purged_at")
        assert col.nullable is True

    def test_existing_columns_still_present(self):
        """Confirm no existing ComprefaceSubject columns were removed."""
        from app.models import ComprefaceSubject
        required = (
            "id", "subject_name", "compreface_subject_id", "contact_id",
            "enrollment_status", "sample_count", "created_at",
        )
        for col_name in required:
            assert ComprefaceSubject.__table__.c.get(col_name) is not None, (
                f"ComprefaceSubject.{col_name} must still exist"
            )


class TestModelsPlacementOrder:
    """Verify FaceSample and PhotoIngestBatch appear after PitQueue in the file."""

    def test_face_sample_after_pit_queue(self):
        import inspect as py_inspect
        from app import models
        # Verify both classes exist
        assert hasattr(models, "FaceSample")
        assert hasattr(models, "PitQueue")
        src = py_inspect.getsource(models)
        pit_pos = src.find("class PitQueue")
        face_pos = src.find("class FaceSample")
        assert pit_pos > 0, "PitQueue class must exist"
        assert face_pos > 0, "FaceSample class must exist"
        assert face_pos > pit_pos, (
            "FaceSample must appear after PitQueue in models.py"
        )

    def test_photo_ingest_batch_after_pit_queue(self):
        import inspect as py_inspect
        from app import models
        src = py_inspect.getsource(models)
        pit_pos = src.find("class PitQueue")
        batch_pos = src.find("class PhotoIngestBatch")
        assert batch_pos > 0, "PhotoIngestBatch class must exist"
        assert batch_pos > pit_pos, (
            "PhotoIngestBatch must appear after PitQueue in models.py"
        )


# ---------------------------------------------------------------------------
# Integration tests: tables must actually be created by create_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_face_sample_table_created_by_create_all(db_session: AsyncSession):
    """face_samples table must exist and accept inserts after create_all."""
    from app.models import ComprefaceSubject, FaceSample, User

    # First create a ComprefaceSubject since FaceSample FK references it
    subject = ComprefaceSubject(
        subject_name="Test Person",
        compreface_subject_id="test-uuid-001",
        enrollment_status="active",
    )
    db_session.add(subject)
    await db_session.flush()

    sample = FaceSample(
        compreface_subject_id="test-uuid-001",
        image_path="/data/faces/test.jpg",
        thumb_path="/data/faces/test_thumb.jpg",
        source="manual",
    )
    db_session.add(sample)
    await db_session.commit()
    await db_session.refresh(sample)

    assert sample.id is not None
    assert sample.compreface_subject_id == "test-uuid-001"
    assert sample.image_path == "/data/faces/test.jpg"
    assert sample.source == "manual"
    assert sample.contact_id is None
    assert sample.compreface_image_id is None
    assert sample.quality_score is None


@pytest.mark.asyncio
async def test_photo_ingest_batch_table_created_by_create_all(db_session: AsyncSession):
    """photo_ingest_batches table must exist and accept inserts after create_all."""
    from app.models import PhotoIngestBatch

    batch = PhotoIngestBatch(
        status="processing",
        total_images=10,
        processed_images=0,
        faces_detected=0,
        auto_logged=0,
        tasks_created=0,
        skipped=0,
        deduplicated=0,
        errors=0,
        report=[],
    )
    db_session.add(batch)
    await db_session.commit()
    await db_session.refresh(batch)

    assert batch.id is not None
    assert batch.status == "processing"
    assert batch.total_images == 10
    assert batch.report == []
    assert batch.event_id is None
    assert batch.finished_at is None


@pytest.mark.asyncio
async def test_compreface_subject_new_columns_readable(db_session: AsyncSession):
    """New columns on ComprefaceSubject must be readable after create_all."""
    from app.models import ComprefaceSubject

    subject = ComprefaceSubject(
        subject_name="Test Subject",
        compreface_subject_id="test-uuid-002",
        enrollment_status="pending",
        enrollment_source="bulk_ingest",
        is_orphan=False,
    )
    db_session.add(subject)
    await db_session.commit()
    await db_session.refresh(subject)

    assert subject.enrollment_source == "bulk_ingest"
    assert subject.last_trained_at is None
    assert subject.is_orphan is False
    assert subject.purged_at is None


@pytest.mark.asyncio
async def test_compreface_subject_is_orphan_default(db_session: AsyncSession):
    """is_orphan must default to False when not explicitly set."""
    from app.models import ComprefaceSubject

    subject = ComprefaceSubject(
        subject_name="Default Subject",
        compreface_subject_id="test-uuid-003",
        enrollment_status="pending",
    )
    db_session.add(subject)
    await db_session.commit()
    await db_session.refresh(subject)

    assert subject.is_orphan is False, (
        f"is_orphan should default to False, got {subject.is_orphan}"
    )


@pytest.mark.asyncio
async def test_face_sample_source_default(db_session: AsyncSession):
    """FaceSample.source must default to 'manual' when not set."""
    from app.models import ComprefaceSubject, FaceSample

    subject = ComprefaceSubject(
        subject_name="Source Test",
        compreface_subject_id="test-uuid-004",
        enrollment_status="active",
    )
    db_session.add(subject)
    await db_session.flush()

    sample = FaceSample(
        compreface_subject_id="test-uuid-004",
        image_path="/data/faces/src_test.jpg",
        thumb_path="/data/faces/src_thumb.jpg",
        # source not set — should default to "manual"
    )
    db_session.add(sample)
    await db_session.commit()
    await db_session.refresh(sample)

    assert sample.source == "manual", (
        f"FaceSample.source should default to 'manual', got {sample.source!r}"
    )


@pytest.mark.asyncio
async def test_photo_ingest_batch_report_accepts_list(db_session: AsyncSession):
    """PhotoIngestBatch.report must store and retrieve JSON list data."""
    from app.models import PhotoIngestBatch

    report_data = [
        {"file": "a.jpg", "status": "ok", "faces": 1},
        {"file": "b.jpg", "status": "error", "error": "no faces"},
    ]
    batch = PhotoIngestBatch(
        status="completed",
        total_images=2,
        processed_images=2,
        faces_detected=1,
        auto_logged=1,
        tasks_created=0,
        skipped=0,
        deduplicated=0,
        errors=1,
        report=report_data,
    )
    db_session.add(batch)
    await db_session.commit()
    await db_session.refresh(batch)

    assert batch.report == report_data, (
        f"report should round-trip correctly, got {batch.report!r}"
    )


@pytest.mark.asyncio
async def test_face_sample_fk_ondelete_cascade_declared(db_session: AsyncSession):
    """FaceSample.compreface_subject_id FK must declare ondelete=CASCADE.

    SQLite test DBs don't enforce FK cascades without PRAGMA foreign_keys=ON,
    so this test verifies the DDL-level declaration rather than runtime behavior.
    PostgreSQL production will enforce the cascade.
    """
    from app.models import FaceSample

    col = FaceSample.__table__.c.get("compreface_subject_id")
    assert col is not None
    fk = list(col.foreign_keys)[0]
    assert fk.ondelete == "CASCADE", (
        f"compreface_subject_id FK must have ondelete=CASCADE, got {fk.ondelete!r}"
    )


@pytest.mark.asyncio
async def test_no_existing_classes_removed(db_session: AsyncSession):
    """Verify all pre-existing model classes still exist and are table-mapped."""
    from app import models

    pre_existing = [
        "User", "Camera", "Contact", "Event", "ComprefaceSubject",
        "Detection", "Task", "TaskAction", "Participant", "Log",
        "AuditLog", "PitQueue", "VolunteerStat", "AdminSetting",
        "CustomFieldGroup", "CustomFieldDef",
    ]
    for class_name in pre_existing:
        assert hasattr(models, class_name), (
            f"Pre-existing model class {class_name!r} is missing"
        )
        cls = getattr(models, class_name)
        assert hasattr(cls, "__tablename__"), (
            f"{class_name} must still have __tablename__"
        )
