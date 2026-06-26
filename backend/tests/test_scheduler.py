"""Tests for backend/app/services/scheduler.py (S16).

All tests mock generators/services and never start the real scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _success_factory() -> str:
    return "ok"


async def _skip_factory() -> str:
    from app.services.scheduler import _JobSkipped
    raise _JobSkipped("not configured")


async def _fail_factory() -> str:
    raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# _run_tracked_job: writes start + finish job_runs rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tracked_job_writes_rows(db_session: AsyncSession):
    """_run_tracked_job writes start row then updates it to 'success'."""
    from sqlalchemy import select
    from app.models import JobRun
    from app.services.scheduler import _run_tracked_job

    class _FakeCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            pass

    with patch("app.database.async_session", return_value=_FakeCtx()):
        await _run_tracked_job("test_tracked", _success_factory)

    result = await db_session.execute(
        select(JobRun).where(JobRun.job_name == "test_tracked")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1
    latest = sorted(rows, key=lambda r: r.id)[-1]
    assert latest.job_name == "test_tracked"
    assert latest.status == "success"


@pytest.mark.asyncio
async def test_run_tracked_job_skipped_path(db_session: AsyncSession):
    """_JobSkipped → status='skipped' in job_runs row."""
    from sqlalchemy import select
    from app.models import JobRun
    from app.services.scheduler import _run_tracked_job

    class _FakeCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            pass

    with patch("app.database.async_session", return_value=_FakeCtx()):
        await _run_tracked_job("test_skipped", _skip_factory)

    result = await db_session.execute(
        select(JobRun).where(JobRun.job_name == "test_skipped")
    )
    rows = result.scalars().all()
    assert any(r.status == "skipped" for r in rows)


@pytest.mark.asyncio
async def test_run_tracked_job_failed_path(db_session: AsyncSession):
    """RuntimeError → status='failed' in job_runs row."""
    from sqlalchemy import select
    from app.models import JobRun
    from app.services.scheduler import _run_tracked_job

    class _FakeCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *args):
            pass

    with patch("app.database.async_session", return_value=_FakeCtx()):
        await _run_tracked_job("test_failed", _fail_factory)

    result = await db_session.execute(
        select(JobRun).where(JobRun.job_name == "test_failed")
    )
    rows = result.scalars().all()
    assert any(r.status == "failed" for r in rows)


# ---------------------------------------------------------------------------
# JOB_REGISTRY: exactly 9 jobs, no duplicate recompute
# ---------------------------------------------------------------------------


def test_job_registry_has_9_jobs():
    """JOB_REGISTRY must contain exactly 9 jobs."""
    from app.services.scheduler import JOB_REGISTRY

    assert len(JOB_REGISTRY) == 9, (
        f"Expected 9 jobs in JOB_REGISTRY, got {len(JOB_REGISTRY)}: {list(JOB_REGISTRY)}"
    )


def test_job_registry_no_duplicate_recompute():
    """EOW and EOM recompute job names appear exactly once each."""
    from app.services.scheduler import JOB_REGISTRY

    keys = list(JOB_REGISTRY.keys())
    assert keys.count("member_status_recompute_eow") == 1
    assert keys.count("member_status_recompute_eom") == 1


def test_job_registry_expected_names():
    """All expected job names are registered."""
    from app.services.scheduler import JOB_REGISTRY

    expected = {
        "sunday_generation",
        "powerhouse_generation",
        "notifier_8am",
        "notifier_10am",
        "notifier_3pm",
        "notifier_powerhouse",
        "biometric_retention",
        "member_status_recompute_eow",
        "member_status_recompute_eom",
    }
    assert set(JOB_REGISTRY.keys()) == expected


# ---------------------------------------------------------------------------
# Cron strings parse correctly with APScheduler
# ---------------------------------------------------------------------------


def test_s16_cron_strings_parse():
    """All S16 cron strings must be valid APScheduler CronTrigger expressions."""
    from apscheduler.triggers.cron import CronTrigger
    from app.services.scheduler import (
        _SUNDAY_GEN_CRON,
        _POWERHOUSE_GEN_CRON,
        _NOTIFIER_8AM_CRON,
        _NOTIFIER_10AM_CRON,
        _NOTIFIER_3PM_CRON,
        _NOTIFIER_POWERHOUSE_CRON,
        _BIOMETRIC_RETENTION_CRON,
    )

    for cron_str in [
        _SUNDAY_GEN_CRON,
        _POWERHOUSE_GEN_CRON,
        _NOTIFIER_8AM_CRON,
        _NOTIFIER_10AM_CRON,
        _NOTIFIER_3PM_CRON,
        _NOTIFIER_POWERHOUSE_CRON,
        _BIOMETRIC_RETENTION_CRON,
    ]:
        trigger = CronTrigger.from_crontab(cron_str)
        assert trigger is not None, f"Failed to parse cron string: {cron_str}"


def test_s23_cron_dicts_parse():
    """S23 EOW/EOM cron dicts must produce valid CronTrigger instances."""
    from apscheduler.triggers.cron import CronTrigger
    from app.services.scheduler import _EOW_CRON, _EOM_CRON

    eow_trigger = CronTrigger(**_EOW_CRON)
    assert eow_trigger is not None

    eom_trigger = CronTrigger(**_EOM_CRON)
    assert eom_trigger is not None


# ---------------------------------------------------------------------------
# Skipped paths: unset series_id → skipped; notifier → skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sunday_generation_skipped_when_not_configured():
    """_sunday_generation_job raises _JobSkipped when series_id is None."""
    from app.services.scheduler import _JobSkipped, _sunday_generation_job

    with patch("app.config.dynamic_settings.get_sunday_event_series_id", return_value=None):
        with pytest.raises(_JobSkipped):
            await _sunday_generation_job()


@pytest.mark.asyncio
async def test_powerhouse_generation_skipped_when_not_configured():
    """_powerhouse_generation_job raises _JobSkipped when series_id is None."""
    from app.services.scheduler import _JobSkipped, _powerhouse_generation_job

    with patch("app.config.dynamic_settings.get_powerhouse_event_series_id", return_value=None):
        with pytest.raises(_JobSkipped):
            await _powerhouse_generation_job()


@pytest.mark.asyncio
async def test_notifier_jobs_always_skip():
    """All notifier job factories raise _JobSkipped (S18 pending)."""
    from app.services.scheduler import JOB_REGISTRY, _JobSkipped

    notifier_names = ["notifier_8am", "notifier_10am", "notifier_3pm", "notifier_powerhouse"]
    for name in notifier_names:
        factory = JOB_REGISTRY[name]
        with pytest.raises(_JobSkipped):
            await factory()
