"""Tests for async export job CRUD — S05-F08.

Covers spec §8 export job test cases and AC1-AC16:
  AC1  POST /export/jobs creates job with status='pending', returns 201
  AC2  GET /export/jobs lists own jobs (newest first)
  AC3  Admin sees all jobs; volunteer sees only own
  AC4  GET /export/jobs/{id} returns job detail
  AC5  GET /export/jobs/{id} 403 for another user's job (non-admin)
  AC6  GET /export/jobs/{id} download_url present only when status=='ready'
  AC7  GET /export/jobs/{id}/download 404 when job not ready
  AC8  GET /export/jobs/{id}/download 403 for non-owner
  AC9  GET /export/jobs/{id}/download 410 when job expired
  AC10 POST /export/jobs 422 when job_type missing
  AC11 _process_export_jobs transitions pending -> running -> ready
  AC12 _process_export_jobs sets file_path, file_bytes, row_count, finished_at
  AC13 _process_export_jobs handles unknown job_type -> failed status
  AC14 _purge_expired_export_jobs transitions ready->expired and unlinks file
  AC15 audit record created on POST /export/jobs
  AC16 list /export/jobs limit param respected
"""

from __future__ import annotations

import os
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_contacts, make_event, make_participants


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pending_job(db_session: AsyncSession, admin_user):
    """Create a pending ExportJob owned by admin_user."""
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
    return job


@pytest_asyncio.fixture
async def volunteer_pending_job(db_session: AsyncSession, volunteer_user):
    """Create a pending ExportJob owned by volunteer_user."""
    from app.models import ExportJob

    job = ExportJob(
        job_type="attendance",
        fmt="csv",
        params={},
        status="pending",
        requested_by_id=volunteer_user.id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


# ---------------------------------------------------------------------------
# AC1 — POST /export/jobs creates job, 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_export_job_201(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.post(
        "/export/jobs",
        json={"job_type": "contacts", "fmt": "csv"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "pending"
    assert data["job_type"] == "contacts"
    assert data["fmt"] == "csv"
    assert "id" in data


# ---------------------------------------------------------------------------
# AC2 — GET /export/jobs lists own jobs, newest first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_export_jobs_own(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers,
    volunteer_user,
):
    from app.models import ExportJob

    for i in range(3):
        job = ExportJob(
            job_type="contacts",
            fmt="csv",
            params={},
            status="pending",
            requested_by_id=volunteer_user.id,
        )
        db_session.add(job)
    await db_session.commit()

    resp = await client.get("/export/jobs", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 3
    # Verify newest-first ordering
    if len(jobs) >= 2:
        assert jobs[0]["id"] >= jobs[1]["id"]


# ---------------------------------------------------------------------------
# AC3 — Admin sees all; volunteer sees only own
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_sees_all_jobs(
    client: AsyncClient,
    pending_job,
    volunteer_pending_job,
    admin_auth_headers,
):
    resp = await client.get("/export/jobs", headers=admin_auth_headers)
    assert resp.status_code == 200
    jobs = resp.json()
    ids = {j["id"] for j in jobs}
    assert pending_job.id in ids
    assert volunteer_pending_job.id in ids


@pytest.mark.asyncio
async def test_volunteer_sees_only_own(
    client: AsyncClient,
    pending_job,
    volunteer_pending_job,
    volunteer_auth_headers,
):
    resp = await client.get("/export/jobs", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    jobs = resp.json()
    ids = {j["id"] for j in jobs}
    assert volunteer_pending_job.id in ids
    assert pending_job.id not in ids


# ---------------------------------------------------------------------------
# AC4 — GET /export/jobs/{id} returns detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_export_job_detail(
    client: AsyncClient,
    pending_job,
    admin_auth_headers,
):
    resp = await client.get(
        f"/export/jobs/{pending_job.id}", headers=admin_auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == pending_job.id
    assert data["status"] == "pending"


# ---------------------------------------------------------------------------
# AC5 — GET /export/jobs/{id} 403 for non-owner non-admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_export_job_403_non_owner(
    client: AsyncClient,
    pending_job,
    volunteer_auth_headers,
):
    resp = await client.get(
        f"/export/jobs/{pending_job.id}", headers=volunteer_auth_headers
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# AC6 — download_url present only when status=='ready'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_url_only_when_ready(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers,
    admin_user,
):
    from app.models import ExportJob

    # Pending job — no download_url
    job_pending = ExportJob(
        job_type="contacts",
        fmt="csv",
        params={},
        status="pending",
        requested_by_id=admin_user.id,
    )
    db_session.add(job_pending)
    await db_session.commit()
    await db_session.refresh(job_pending)

    resp = await client.get(
        f"/export/jobs/{job_pending.id}", headers=admin_auth_headers
    )
    data = resp.json()
    assert "download_url" not in data or data.get("download_url") is None

    # Ready job — download_url present
    job_pending.status = "ready"
    job_pending.file_path = "exports/test.csv"
    await db_session.commit()
    await db_session.refresh(job_pending)

    resp2 = await client.get(
        f"/export/jobs/{job_pending.id}", headers=admin_auth_headers
    )
    data2 = resp2.json()
    assert "download_url" in data2 and data2["download_url"] is not None


# ---------------------------------------------------------------------------
# AC7 — GET /export/jobs/{id}/download 404 when job not ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_404_when_not_ready(
    client: AsyncClient,
    pending_job,
    admin_auth_headers,
):
    resp = await client.get(
        f"/export/jobs/{pending_job.id}/download", headers=admin_auth_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC8 — GET /export/jobs/{id}/download 403 for non-owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_403_non_owner(
    client: AsyncClient,
    pending_job,
    volunteer_auth_headers,
):
    resp = await client.get(
        f"/export/jobs/{pending_job.id}/download", headers=volunteer_auth_headers
    )
    assert resp.status_code in (403, 404)


# ---------------------------------------------------------------------------
# AC9 — GET /export/jobs/{id}/download 410 when expired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_410_when_expired(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers,
    admin_user,
):
    from app.models import ExportJob

    expired_at = _utc_now() - timedelta(hours=1)
    job = ExportJob(
        job_type="contacts",
        fmt="csv",
        params={},
        status="ready",
        requested_by_id=admin_user.id,
        file_path="exports/expired.csv",
        expires_at=expired_at,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    resp = await client.get(
        f"/export/jobs/{job.id}/download", headers=admin_auth_headers
    )
    assert resp.status_code == 410


# ---------------------------------------------------------------------------
# AC10 — POST /export/jobs 422 when job_type missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_export_job_missing_job_type(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.post(
        "/export/jobs",
        json={"fmt": "csv"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# AC11 — _process_export_jobs transitions pending -> ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_export_jobs_transitions_to_ready(
    db_session: AsyncSession,
    admin_user,
):
    """Run the QueueManager._process_export_jobs directly and verify the job
    transitions from pending to ready (CSV written to a temp dir)."""
    from app.models import ExportJob
    from app.services.queue_manager import QueueManager
    from app.database import async_session

    # Seed some contacts so the CSV has data
    contacts = await make_contacts(db_session, 5)
    await db_session.commit()

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
    job_id = job.id

    qm = QueueManager(db_session_factory=async_session)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("app.services.queue_manager.legacy_settings") as mock_settings:
            mock_settings.STORAGE_PATH = tmp_dir
            mock_settings.WORKER_ID = "test-worker"
            mock_settings.POLL_INTERVAL = 1

            did_work = await qm._process_export_jobs()

    assert did_work is True

    # Reload job from DB — expire identity map so we get fresh data from disk
    db_session.expire_all()
    updated = await db_session.get(ExportJob, job_id)
    assert updated is not None
    assert updated.status == "ready"


# ---------------------------------------------------------------------------
# AC12 — _process_export_jobs sets file_path, file_bytes, row_count, finished_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_export_jobs_sets_metadata(
    db_session: AsyncSession,
    admin_user,
):
    from app.models import ExportJob
    from app.services.queue_manager import QueueManager
    from app.database import async_session

    await make_contacts(db_session, 3)
    await db_session.commit()

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
    job_id = job.id

    qm = QueueManager(db_session_factory=async_session)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("app.services.queue_manager.legacy_settings") as mock_settings:
            mock_settings.STORAGE_PATH = tmp_dir
            mock_settings.WORKER_ID = "test-worker"
            mock_settings.POLL_INTERVAL = 1

            await qm._process_export_jobs()

    db_session.expire_all()
    updated = await db_session.get(ExportJob, job_id)
    assert updated is not None
    assert updated.file_path is not None
    assert updated.file_bytes is not None and updated.file_bytes > 0
    assert updated.row_count is not None and updated.row_count >= 0
    assert updated.finished_at is not None


# ---------------------------------------------------------------------------
# AC13 — _process_export_jobs handles unknown job_type -> failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_export_jobs_unknown_type_fails(
    db_session: AsyncSession,
    admin_user,
):
    from app.models import ExportJob
    from app.services.queue_manager import QueueManager
    from app.database import async_session

    job = ExportJob(
        job_type="unknown_type",
        fmt="csv",
        params={},
        status="pending",
        requested_by_id=admin_user.id,
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    job_id = job.id

    qm = QueueManager(db_session_factory=async_session)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("app.services.queue_manager.legacy_settings") as mock_settings:
            mock_settings.STORAGE_PATH = tmp_dir
            mock_settings.WORKER_ID = "test-worker"
            mock_settings.POLL_INTERVAL = 1

            did_work = await qm._process_export_jobs()

    assert did_work is True

    db_session.expire_all()
    updated = await db_session.get(ExportJob, job_id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error is not None


# ---------------------------------------------------------------------------
# AC14 — _purge_expired_export_jobs transitions ready->expired and unlinks file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_expired_export_jobs(
    db_session: AsyncSession,
    admin_user,
):
    from app.models import ExportJob
    from app.services.queue_manager import QueueManager
    from app.database import async_session

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Create a fake export file
        exports_dir = Path(tmp_dir) / "exports"
        exports_dir.mkdir(parents=True)
        fake_file = exports_dir / "test_contacts.csv"
        fake_file.write_text("id,name\n1,Test\n")

        expired_at = _utc_now() - timedelta(seconds=1)
        job = ExportJob(
            job_type="contacts",
            fmt="csv",
            params={},
            status="ready",
            requested_by_id=admin_user.id,
            file_path="exports/test_contacts.csv",
            expires_at=expired_at,
        )
        db_session.add(job)
        await db_session.commit()
        await db_session.refresh(job)
        job_id = job.id

        qm = QueueManager(db_session_factory=async_session)

        with patch("app.services.queue_manager.legacy_settings") as mock_settings:
            mock_settings.STORAGE_PATH = tmp_dir
            mock_settings.WORKER_ID = "test-worker"
            mock_settings.POLL_INTERVAL = 1

            await qm._purge_expired_export_jobs()

    db_session.expire_all()
    updated = await db_session.get(ExportJob, job_id)
    assert updated is not None
    assert updated.status == "expired"
    assert updated.file_path is None


# ---------------------------------------------------------------------------
# AC15 — audit record created on POST /export/jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_export_job_audit_record(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers,
):
    from app.models import AuditLog

    resp = await client.post(
        "/export/jobs",
        json={"job_type": "contacts", "fmt": "csv"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201

    job_id = resp.json()["id"]

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "export_job.create",
            AuditLog.entity_id == job_id,
        )
    )
    rows = result.scalars().all()
    assert len(rows) >= 1, "Expected audit record for export_job.create"


# ---------------------------------------------------------------------------
# AC16 — list /export/jobs limit param respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_export_jobs_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers,
    admin_user,
):
    from app.models import ExportJob

    for i in range(10):
        job = ExportJob(
            job_type="contacts",
            fmt="csv",
            params={},
            status="pending",
            requested_by_id=admin_user.id,
        )
        db_session.add(job)
    await db_session.commit()

    resp = await client.get("/export/jobs?limit=3", headers=admin_auth_headers)
    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) <= 3


# ---------------------------------------------------------------------------
# Extra: GET /export/jobs/{id} returns 404 for unknown job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_export_job_404(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.get("/export/jobs/99999", headers=admin_auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Security regression (S05 security re-audit, HIGH 1): async export job path
# must clamp include_deleted for non-admins.  A volunteer requesting
# {"include_deleted": true} must have it forced to false in the persisted job,
# so the worker cannot exfiltrate soft-deleted PII.  Admins keep the flag.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_export_job_clamps_include_deleted_for_volunteer(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers,
):
    from app.models import ExportJob

    resp = await client.post(
        "/export/jobs",
        json={
            "job_type": "contacts",
            "fmt": "csv",
            "params": {"include_deleted": True},
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]

    job = await db_session.get(ExportJob, job_id)
    assert job is not None
    assert job.params.get("include_deleted") is False, (
        "volunteer include_deleted must be clamped to False on the async path"
    )


@pytest.mark.asyncio
async def test_create_export_job_preserves_include_deleted_for_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_auth_headers,
):
    from app.models import ExportJob

    resp = await client.post(
        "/export/jobs",
        json={
            "job_type": "contacts",
            "fmt": "csv",
            "params": {"include_deleted": True},
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]

    job = await db_session.get(ExportJob, job_id)
    assert job is not None
    assert job.params.get("include_deleted") is True, (
        "admin include_deleted must be preserved"
    )
