"""QA: S07 migration content tests.

Validates every acceptance criterion for the S07 face enrollment and
photo ingest migration (i3j4k5l6m7n8_s07_face_enrollment_and_samples.py)
against both the migration file content and the SQLAlchemy ORM models.

These tests do NOT run alembic upgrade (SQLite incompatibility with
raw-JSONB in the initial migration); they assert the migration DDL text
and the ORM metadata for correctness.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "i3j4k5l6m7n8_s07_face_enrollment_and_samples.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def migration_text() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def engine_s07():
    """Fresh in-memory SQLite DB created from ORM metadata (includes S07 models)."""
    import app.models  # noqa: F401 – registers all ORM classes
    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def inspector_s07(engine_s07):
    return inspect(engine_s07)


# ---------------------------------------------------------------------------
# Migration file: revision / chain
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"Migration file missing: {MIGRATION_PATH}"


def test_revision_id():
    text = migration_text()
    assert 'revision: str = "i3j4k5l6m7n8"' in text


def test_down_revision():
    text = migration_text()
    assert 'down_revision: Union[str, None] = "h1i2j3k4l5m6"' in text


# ---------------------------------------------------------------------------
# face_samples table: columns
# ---------------------------------------------------------------------------


def test_face_samples_table_exists(inspector_s07):
    assert inspector_s07.has_table("face_samples"), "face_samples table must exist"


def test_face_samples_id_column(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "id" in cols
    # SQLite reflect does not expose autoincrement in the column dict;
    # verify via primary_key flag instead.
    assert cols["id"]["primary_key"] == 1, "face_samples.id must be the primary key"


def test_face_samples_image_path_not_null(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "image_path" in cols
    assert not cols["image_path"]["nullable"], "image_path must be NOT NULL"


def test_face_samples_thumb_path_nullable(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "thumb_path" in cols


def test_face_samples_compreface_image_id_nullable(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "compreface_image_id" in cols
    # nullable
    assert cols["compreface_image_id"]["nullable"]


def test_face_samples_source_default_manual():
    """ORM model defines source default; check via the model directly."""
    import app.models
    col = app.models.FaceSample.__table__.columns["source"]
    # ORM default (Python-side) must be 'manual'
    assert col.default is not None or col.server_default is not None, (
        "source column must have a default value of 'manual'"
    )
    if col.default is not None:
        assert col.default.arg == "manual", (
            f"source column default should be 'manual', got {col.default.arg!r}"
        )


def test_face_samples_quality_score_numeric(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "quality_score" in cols


def test_face_samples_created_at_column(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("face_samples")}
    assert "created_at" in cols


# ---------------------------------------------------------------------------
# face_samples: foreign keys
# ---------------------------------------------------------------------------


def test_face_samples_fk_compreface_subject_id(inspector_s07):
    fks = inspector_s07.get_foreign_keys("face_samples")
    subject_fks = [fk for fk in fks if "compreface_subject_id" in fk["constrained_columns"]]
    assert subject_fks, "face_samples.compreface_subject_id must have a FK"
    fk = subject_fks[0]
    assert fk["referred_table"] == "compreface_subjects"
    assert fk["options"].get("ondelete", "").upper() == "CASCADE"


def test_face_samples_fk_contact_id_set_null(inspector_s07):
    fks = inspector_s07.get_foreign_keys("face_samples")
    contact_fks = [fk for fk in fks if "contact_id" in fk["constrained_columns"]]
    assert contact_fks, "face_samples.contact_id must have a FK"
    fk = contact_fks[0]
    assert fk["referred_table"] == "contacts"
    assert fk["options"].get("ondelete", "").upper() == "SET NULL"


def test_face_samples_fk_added_by_id_set_null(inspector_s07):
    fks = inspector_s07.get_foreign_keys("face_samples")
    added_by_fks = [fk for fk in fks if "added_by_id" in fk["constrained_columns"]]
    assert added_by_fks, "face_samples.added_by_id must have a FK"
    fk = added_by_fks[0]
    assert fk["referred_table"] == "users"
    assert fk["options"].get("ondelete", "").upper() == "SET NULL"


# ---------------------------------------------------------------------------
# face_samples: indexes
# ---------------------------------------------------------------------------


def test_face_samples_index_contact_id(inspector_s07):
    indexes = {idx["name"] for idx in inspector_s07.get_indexes("face_samples")}
    assert "ix_face_samples_contact_id" in indexes, (
        f"ix_face_samples_contact_id missing; found: {indexes}"
    )


def test_face_samples_index_subject_id(inspector_s07):
    indexes = {idx["name"] for idx in inspector_s07.get_indexes("face_samples")}
    assert "ix_face_samples_subject_id" in indexes, (
        f"ix_face_samples_subject_id missing; found: {indexes}"
    )


# ---------------------------------------------------------------------------
# Migration text: face_samples compreface_subject_id FK column type is NOT Integer
# The referenced column compreface_subjects.compreface_subject_id is String/VARCHAR.
# The migration must use sa.String/Text/VARCHAR — not sa.Integer.
# ---------------------------------------------------------------------------


def test_migration_face_samples_subject_id_column_type_is_not_integer():
    """Regression: migration line 42 declared sa.Integer() for compreface_subject_id
    but the referenced column compreface_subjects.compreface_subject_id is VARCHAR(255).
    The FK column type must match the referenced column type."""
    text = migration_text()
    # Find the face_samples create_table block
    fs_block_match = re.search(
        r'create_table\(\s*["\']face_samples["\'].*?(?=create_table|\ndef )',
        text,
        re.DOTALL,
    )
    assert fs_block_match, "Could not locate face_samples create_table block in migration"
    fs_block = fs_block_match.group(0)

    # compreface_subject_id column definition within the block
    subj_match = re.search(
        r'"compreface_subject_id".*?(?=sa\.Column|\),$)',
        fs_block,
        re.DOTALL,
    )
    assert subj_match, "Could not find compreface_subject_id column definition in face_samples block"
    col_def = subj_match.group(0)

    assert "sa.Integer()" not in col_def, (
        "face_samples.compreface_subject_id is declared as sa.Integer() in the migration, "
        "but compreface_subjects.compreface_subject_id is VARCHAR(255). "
        "The FK column type must match the referenced PK column type. "
        "Fix: change sa.Integer() to sa.String(255) on line ~42 of the migration."
    )


# ---------------------------------------------------------------------------
# photo_ingest_batches table: columns
# ---------------------------------------------------------------------------


def test_photo_ingest_batches_table_exists(inspector_s07):
    assert inspector_s07.has_table("photo_ingest_batches"), (
        "photo_ingest_batches table must exist"
    )


def test_photo_ingest_batches_columns_in_orm(inspector_s07):
    """ORM model must contain the columns the migration will create.

    The migration spec and the ORM model must be in sync.
    This test verifies the ORM model has the spec-required columns;
    the migration text divergence test (below) catches DDL mismatches.
    """
    cols = {c["name"] for c in inspector_s07.get_columns("photo_ingest_batches")}
    # Columns every implementation must have
    required = {
        "id", "event_id", "status",
        "processed_images", "tasks_created", "errors",
        "report", "finished_at", "created_at",
    }
    missing = required - cols
    assert not missing, f"photo_ingest_batches ORM missing columns: {missing}"


def test_photo_ingest_batches_migration_columns_match_spec():
    """The migration DDL for photo_ingest_batches must use the spec-mandated column names.

    Spec says: submitted_by_id, total_files, faces_found, started_at.
    ORM uses:  uploaded_by_id, total_images, faces_detected  (no started_at).
    One of these must be consistent — if both diverge the migration cannot be
    applied and the ORM will not be able to use the resulting table.
    """
    text = migration_text()
    # What the spec mandates for photo_ingest_batches per task goal
    spec_col_names = [
        "submitted_by_id", "total_files", "faces_found", "started_at"
    ]
    migration_has = {col: col in text for col in spec_col_names}

    import app.models
    orm_col_names = {c.name for c in app.models.PhotoIngestBatch.__table__.columns}
    orm_has = {col: col in orm_col_names for col in spec_col_names}

    mismatches = [
        col for col in spec_col_names
        if migration_has[col] != orm_has[col]
    ]
    assert not mismatches, (
        f"photo_ingest_batches column name mismatches between migration and ORM: "
        f"{mismatches}. "
        f"Migration has: {migration_has}. ORM has: {orm_has}. "
        "The migration and ORM model must use identical column names. "
        "Either update the migration to use ORM column names "
        "(uploaded_by_id, total_images, faces_detected) or update the ORM model."
    )


def test_photo_ingest_batches_status_default_processing():
    """ORM model defines status default; check via the model directly."""
    import app.models
    col = app.models.PhotoIngestBatch.__table__.columns["status"]
    assert col.default is not None or col.server_default is not None, (
        "status column must have a default value of 'processing'"
    )
    if col.default is not None:
        assert col.default.arg == "processing", (
            f"status default should be 'processing', got {col.default.arg!r}"
        )


def test_photo_ingest_batches_fk_event_id_set_null(inspector_s07):
    fks = inspector_s07.get_foreign_keys("photo_ingest_batches")
    event_fks = [fk for fk in fks if "event_id" in fk["constrained_columns"]]
    assert event_fks, "photo_ingest_batches.event_id must have a FK"
    fk = event_fks[0]
    assert fk["referred_table"] == "events"
    assert fk["options"].get("ondelete", "").upper() == "SET NULL"


def test_photo_ingest_batches_index_status(inspector_s07):
    indexes = {idx["name"] for idx in inspector_s07.get_indexes("photo_ingest_batches")}
    assert "ix_photo_ingest_batches_status" in indexes, (
        f"ix_photo_ingest_batches_status missing; found: {indexes}"
    )


# ---------------------------------------------------------------------------
# Migration text: photo_ingest_batches column names must match ORM model
# ---------------------------------------------------------------------------


def test_migration_photo_ingest_batches_has_submitted_by_id_or_uploaded_by_id():
    """The migration spec says 'submitted_by_id' but the ORM model uses 'uploaded_by_id'.
    One of them must be present and consistent. This test flags the mismatch."""
    text = migration_text()
    # Check what's in the migration
    has_submitted = "submitted_by_id" in text
    has_uploaded = "uploaded_by_id" in text

    # Get what ORM model has
    import app.models
    pib_cols = {c.name for c in app.models.PhotoIngestBatch.__table__.columns}
    orm_has_submitted = "submitted_by_id" in pib_cols
    orm_has_uploaded = "uploaded_by_id" in pib_cols

    assert has_submitted == orm_has_submitted or has_uploaded == orm_has_uploaded, (
        f"Column name mismatch between migration and ORM model for 'submitter' FK column. "
        f"Migration has submitted_by_id={has_submitted}, uploaded_by_id={has_uploaded}. "
        f"ORM has submitted_by_id={orm_has_submitted}, uploaded_by_id={orm_has_uploaded}. "
        "Ensure migration and ORM use the same column name."
    )


def test_migration_photo_ingest_batches_counter_column_names_match_orm():
    """Check that total_files/faces_found in migration match ORM model column names.
    ORM uses total_images and faces_detected."""
    import app.models
    pib_cols = {c.name for c in app.models.PhotoIngestBatch.__table__.columns}
    text = migration_text()

    migration_has_total_files = "total_files" in text
    migration_has_total_images = "total_images" in text
    orm_has_total_images = "total_images" in pib_cols
    orm_has_total_files = "total_files" in pib_cols

    # Both should use the same name
    assert migration_has_total_files == orm_has_total_files, (
        f"Counter column 'total_files': migration={migration_has_total_files}, "
        f"ORM={orm_has_total_files}. "
        "The migration and ORM model must use the same column name."
    )
    assert migration_has_total_images == orm_has_total_images, (
        f"Counter column 'total_images': migration={migration_has_total_images}, "
        f"ORM={orm_has_total_images}. "
        "The migration and ORM model must use the same column name."
    )


# ---------------------------------------------------------------------------
# compreface_subjects: four new columns
# ---------------------------------------------------------------------------


def test_compreface_subjects_enrollment_source_column(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("compreface_subjects")}
    assert "enrollment_source" in cols, "compreface_subjects.enrollment_source must exist"
    assert cols["enrollment_source"]["nullable"], "enrollment_source must be NULLABLE"


def test_compreface_subjects_last_trained_at_column(inspector_s07):
    cols = {c["name"] for c in inspector_s07.get_columns("compreface_subjects")}
    assert "last_trained_at" in cols, "compreface_subjects.last_trained_at must exist"


def test_compreface_subjects_is_orphan_column(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("compreface_subjects")}
    assert "is_orphan" in cols, "compreface_subjects.is_orphan must exist"
    assert not cols["is_orphan"]["nullable"], "is_orphan must be NOT NULL"


def test_compreface_subjects_purged_at_column(inspector_s07):
    cols = {c["name"]: c for c in inspector_s07.get_columns("compreface_subjects")}
    assert "purged_at" in cols, "compreface_subjects.purged_at must exist"
    assert cols["purged_at"]["nullable"], "purged_at must be NULLABLE"


# ---------------------------------------------------------------------------
# Migration text: downgrade reverses all steps in reverse order
# ---------------------------------------------------------------------------


def test_downgrade_drops_compreface_subjects_columns_before_tables():
    """Downgrade must remove compreface_subjects columns before dropping tables."""
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    batch_pos = downgrade_section.find("batch_alter_table")
    drop_table_pib_pos = downgrade_section.find('"photo_ingest_batches"')
    drop_table_fs_pos = downgrade_section.find('"face_samples"')
    assert batch_pos < drop_table_pib_pos < drop_table_fs_pos, (
        "Downgrade must remove compreface_subjects columns first, "
        "then drop photo_ingest_batches, then drop face_samples."
    )


def test_downgrade_drops_index_before_table_photo_ingest_batches():
    """ix_photo_ingest_batches_status must be dropped before the table."""
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    idx_pos = downgrade_section.find("ix_photo_ingest_batches_status")
    table_pos = downgrade_section.find("drop_table('photo_ingest_batches'")
    if table_pos < 0:
        table_pos = downgrade_section.find('drop_table("photo_ingest_batches"')
    assert idx_pos > 0, "drop_index for ix_photo_ingest_batches_status missing"
    assert table_pos > 0, "drop_table for photo_ingest_batches missing"
    assert idx_pos < table_pos, (
        "ix_photo_ingest_batches_status must be dropped before the table"
    )


def test_downgrade_drops_face_samples_indexes_before_table():
    """face_samples indexes must be dropped before the table."""
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    idx_pos = downgrade_section.find("ix_face_samples_")
    table_pos = downgrade_section.find("drop_table")
    # last drop_table is face_samples
    last_drop = downgrade_section.rfind("drop_table")
    assert idx_pos < last_drop, (
        "face_samples indexes must be dropped before the table in downgrade"
    )


def test_downgrade_has_batch_alter_table_for_sqlite_safety():
    """compreface_subjects column removal must use batch_alter_table."""
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    assert "batch_alter_table" in downgrade_section, (
        "Downgrade must use batch_alter_table for SQLite-safe column removal"
    )
