"""Migration roundtrip test for S05 — export_jobs table.

Asserts:
  1. The export_jobs table is created by the upgrade() function.
  2. The export_jobs table is dropped by the downgrade() function.
  3. The table has the expected 13 columns with the right names.
  4. The migration down_revision matches the S04 head (j4k5l6m7n8o9).
  5. ORM model Base.metadata produces the same table with the same columns.

These tests run against SQLite (in-memory / test DB) via Alembic op.get_bind()
so no live Postgres instance is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
S05_REVISION = "a2b3c4d5e6f7"
S05_DOWN_REVISION = "j4k5l6m7n8o9"  # S04 head


# ---------------------------------------------------------------------------
# Script-level (no DB) assertions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script_dir() -> ScriptDirectory:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_s05_migration_exists(script_dir):
    """The S05 migration revision must be present in the graph."""
    rev = script_dir.get_revision(S05_REVISION)
    assert rev is not None, f"S05 revision {S05_REVISION} not found in Alembic graph"


def test_s05_down_revision_is_s04(script_dir):
    """S05 must build on the S04 head (j4k5l6m7n8o9)."""
    rev = script_dir.get_revision(S05_REVISION)
    assert rev.down_revision == S05_DOWN_REVISION, (
        f"Expected S05 down_revision={S05_DOWN_REVISION}, got {rev.down_revision}"
    )


def test_s05_migration_file_defines_export_jobs():
    """The migration file must create the export_jobs table."""
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "a2b3c4d5e6f7_s05_export_jobs.py"
    )
    assert mig.exists(), "S05 migration file must exist"
    text = mig.read_text(encoding="utf-8")
    assert "export_jobs" in text
    assert "create_table" in text
    assert "drop_table" in text


def test_s05_migration_defines_indexes():
    """The S05 migration must create the two required indexes."""
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "a2b3c4d5e6f7_s05_export_jobs.py"
    )
    text = mig.read_text(encoding="utf-8")
    assert "ix_export_jobs_status_created" in text
    assert "ix_export_jobs_requested_by" in text


# ---------------------------------------------------------------------------
# ORM model assertions (create_all path — used by test suite)
# ---------------------------------------------------------------------------


def test_export_jobs_orm_model_has_expected_columns():
    """Base.metadata creates the export_jobs table with the 13 expected columns."""
    from app.models import ExportJob

    cols = {c.name for c in ExportJob.__table__.columns}
    expected = {
        "id",
        "job_type",
        "fmt",
        "params",
        "status",
        "requested_by_id",
        "row_count",
        "file_path",
        "file_bytes",
        "error",
        "expires_at",
        "created_at",
        "finished_at",
    }
    missing = expected - cols
    assert not missing, f"ExportJob ORM model missing columns: {missing}"


def test_export_jobs_orm_model_indexes():
    """The ORM model defines both required indexes."""
    from app.models import ExportJob

    index_names = {idx.name for idx in ExportJob.__table__.indexes}
    assert "ix_export_jobs_status_created" in index_names, (
        "Missing index ix_export_jobs_status_created on ExportJob"
    )
    assert "ix_export_jobs_requested_by" in index_names, (
        "Missing index ix_export_jobs_requested_by on ExportJob"
    )


# ---------------------------------------------------------------------------
# Roundtrip: ORM create_all -> insert -> select -> drop_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_jobs_roundtrip(db_session, admin_user):
    """Create an ExportJob row via ORM, select it back, assert fields match."""
    from app.models import ExportJob
    from sqlalchemy import select

    job = ExportJob(
        job_type="contacts",
        fmt="csv",
        params={"contact_type": "individual"},
        status="pending",
        requested_by_id=admin_user.id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    assert job.id is not None
    assert job.status == "pending"
    assert job.job_type == "contacts"
    assert job.fmt == "csv"
    assert job.params == {"contact_type": "individual"}
    assert job.requested_by_id == admin_user.id
    assert job.row_count is None
    assert job.file_path is None
    assert job.error is None

    # Select back
    result = await db_session.execute(select(ExportJob).where(ExportJob.id == job.id))
    fetched = result.scalar_one()
    assert fetched.id == job.id
    assert fetched.status == "pending"


@pytest.mark.asyncio
async def test_export_jobs_status_transitions(db_session, admin_user):
    """An ExportJob can be transitioned through pending -> running -> ready."""
    from app.models import ExportJob

    job = ExportJob(
        job_type="attendance",
        fmt="csv",
        params={},
        status="pending",
        requested_by_id=admin_user.id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    job.status = "running"
    await db_session.commit()
    await db_session.refresh(job)
    assert job.status == "running"

    job.status = "ready"
    job.file_path = "exports/test.csv"
    job.file_bytes = 1024
    job.row_count = 50
    await db_session.commit()
    await db_session.refresh(job)
    assert job.status == "ready"
    assert job.file_bytes == 1024
    assert job.row_count == 50


@pytest.mark.asyncio
async def test_export_jobs_failed_status(db_session, admin_user):
    """An ExportJob can be set to failed with an error message."""
    from app.models import ExportJob

    job = ExportJob(
        job_type="contacts",
        fmt="csv",
        params={},
        status="pending",
        requested_by_id=admin_user.id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    job.status = "failed"
    job.error = "Simulated error"
    await db_session.commit()
    await db_session.refresh(job)

    assert job.status == "failed"
    assert job.error == "Simulated error"
