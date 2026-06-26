"""Consolidated APScheduler singleton for Project Seraphim S16.

Registers all 9 cron jobs:
  7 S16 jobs (sunday_generation, powerhouse_generation, 4 notifiers,
              biometric_retention)
  2 S23 recompute jobs (via register_s23_jobs)

All jobs are tracked: a JobRun row is inserted at start (status='running'),
then updated at finish (status=success/skipped/failed, duration_ms, finished_at).

Scheduler is started only when ENVIRONMENT != 'test' (wired in main.py lifespan).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level scheduler singleton (lazy)
# ---------------------------------------------------------------------------

_scheduler = None


def _get_or_create_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
    return _scheduler


# ---------------------------------------------------------------------------
# Internal sentinel for 'skipped' job outcome
# ---------------------------------------------------------------------------

class _JobSkipped(Exception):
    """Raise inside a job coro to record status='skipped'."""


# ---------------------------------------------------------------------------
# Core tracked-run helper
# ---------------------------------------------------------------------------


async def _run_tracked_job(job_name: str, coro_factory: Callable) -> None:
    """Insert a 'running' JobRun row, execute coro_factory(), update the row.

    Parameters
    ----------
    job_name:
        Logical name stored in job_runs.job_name.
    coro_factory:
        Async callable (no args) whose return value becomes the `detail` string.
        Raise _JobSkipped(reason) to record status='skipped'.
        Any other exception records status='failed'.
    """
    from app.database import async_session as AsyncSessionLocal
    from app.models import JobRun

    t0 = time.monotonic()
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id: Optional[int] = None

    # --- insert 'running' row ---
    try:
        async with AsyncSessionLocal() as db:
            jr = JobRun(
                job_name=job_name,
                status="running",
                started_at=started_at,
            )
            db.add(jr)
            await db.commit()
            await db.refresh(jr)
            run_id = jr.id
    except Exception:
        logger.exception("scheduler: could not insert job_runs start row for %s", job_name)

    # --- run the job ---
    status = "failed"
    detail: Optional[str] = None
    try:
        result = await coro_factory()
        status = "success"
        if isinstance(result, str):
            detail = result
        elif result is not None:
            detail = str(result)
    except _JobSkipped as exc:
        status = "skipped"
        detail = str(exc) or "skipped"
    except Exception as exc:
        status = "failed"
        # Sanitize: only exception class name in DB — full traceback to logs only.
        # This prevents DSNs, passwords, or secrets embedded in exception messages
        # from being persisted in job_runs.detail and returned by GET /settings/jobs.
        detail = f"{type(exc).__name__}: job execution failed"
        logger.exception("scheduler: job %s failed", job_name)

    # --- update finish row ---
    finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if run_id is not None:
        try:
            from app.database import async_session as AsyncSessionLocal2
            from app.models import JobRun as _JobRun
            async with AsyncSessionLocal2() as db:
                jr = await db.get(_JobRun, run_id)
                if jr is not None:
                    jr.status = status
                    jr.detail = detail
                    jr.finished_at = finished_at
                    jr.duration_ms = duration_ms
                    await db.commit()
        except Exception:
            logger.exception(
                "scheduler: could not update job_runs finish row for %s", job_name
            )


# ---------------------------------------------------------------------------
# Job coro factories
# ---------------------------------------------------------------------------


async def _sunday_generation_job() -> str:
    """Generate Sunday Service events for the next Sunday."""
    from app.config import dynamic_settings
    from app.database import async_session
    from app.services.event_generator import generate_sunday_events

    sid = dynamic_settings.get_sunday_event_series_id()
    if sid is None:
        raise _JobSkipped("not configured")

    async with async_session() as db:
        events = await generate_sunday_events(sid, db)
        await db.commit()
        count = len(events)
    return f"created {count} events"


async def _powerhouse_generation_job() -> str:
    """Generate the Powerhouse event for the next Wednesday."""
    from app.config import dynamic_settings
    from app.database import async_session
    from app.services.event_generator import generate_powerhouse_event

    sid = dynamic_settings.get_powerhouse_event_series_id()
    if sid is None:
        raise _JobSkipped("not configured")

    async with async_session() as db:
        event = await generate_powerhouse_event(sid, db)
        await db.commit()
        count = 1 if event is not None else 0
    return f"created {count} events"


def _make_notifier_job(job_name: str) -> Callable:
    """Factory returning an async no-arg callable that always skips (S18 pending)."""
    async def _notifier_job() -> str:
        raise _JobSkipped("awaiting S18")

    _notifier_job.__name__ = f"_notifier_{job_name}"
    return _notifier_job


async def _biometric_retention_job() -> str:
    """Run face-image retention cleanup."""
    from pathlib import Path

    from app.config import dynamic_settings, legacy_settings
    from app.database import async_session

    retention_days = dynamic_settings.get_face_retention_days()
    storage_path = Path(legacy_settings.STORAGE_PATH)
    try:
        from app.services.face_cleanup import FaceCleanupService
        service = FaceCleanupService(storage_path, retention_days)
        async with async_session() as db:
            result = await service.run_cleanup(db)
        return f"deleted={result.deleted} errors={result.errors}"
    except Exception as exc:
        raise RuntimeError(f"face_cleanup failed: {exc}") from exc


async def _recompute_eow_job() -> str:
    """End-of-week full member-status recompute."""
    from app.database import async_session
    from app.services.member_status_service import recompute_all

    async with async_session() as db:
        result = await recompute_all(db)
    return f"contacts={result.contact_count} duration_ms={result.duration_ms}"


async def _recompute_eom_job() -> str:
    """End-of-month full member-status recompute."""
    from app.database import async_session
    from app.services.member_status_service import recompute_all

    async with async_session() as db:
        result = await recompute_all(db)
    return f"contacts={result.contact_count} duration_ms={result.duration_ms}"


# ---------------------------------------------------------------------------
# Job Registry
# ---------------------------------------------------------------------------

JOB_REGISTRY: dict[str, Callable] = {
    "sunday_generation": _sunday_generation_job,
    "powerhouse_generation": _powerhouse_generation_job,
    "notifier_8am": _make_notifier_job("8am"),
    "notifier_10am": _make_notifier_job("10am"),
    "notifier_3pm": _make_notifier_job("3pm"),
    "notifier_powerhouse": _make_notifier_job("powerhouse"),
    "biometric_retention": _biometric_retention_job,
    "member_status_recompute_eow": _recompute_eow_job,
    "member_status_recompute_eom": _recompute_eom_job,
}

# ---------------------------------------------------------------------------
# S23 recompute jobs — moved here from backend/app/scheduler.py
# ---------------------------------------------------------------------------

# Cron strings for the S23 recompute jobs (UTC)
_EOW_CRON = {"day_of_week": "mon", "hour": 0, "minute": 0}   # Monday 00:00 UTC
_EOM_CRON = {"day": 1, "hour": 0, "minute": 0}                # 1st of month 00:00 UTC


def register_s23_jobs(scheduler) -> None:
    """Register the two S23 member-status recompute cron jobs.

    Jobs
    ----
    - EOW (end-of-week):  Monday 00:00 UTC
    - EOM (end-of-month): 1st of each month 00:00 UTC
    """
    from apscheduler.triggers.cron import CronTrigger

    scheduler.add_job(
        lambda: asyncio.ensure_future(
            _run_tracked_job("member_status_recompute_eow", _recompute_eow_job)
        ),
        CronTrigger(**_EOW_CRON),
        id="member_status_recompute_eow",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: asyncio.ensure_future(
            _run_tracked_job("member_status_recompute_eom", _recompute_eom_job)
        ),
        CronTrigger(**_EOM_CRON),
        id="member_status_recompute_eom",
        replace_existing=True,
    )


# ---------------------------------------------------------------------------
# S16 cron strings (UTC) — NOT imported from event_generator.py
# ---------------------------------------------------------------------------

# sunday_generation: Friday 18:00 UTC
_SUNDAY_GEN_CRON = "0 18 * * 5"
# powerhouse_generation: Wednesday 08:00 UTC
_POWERHOUSE_GEN_CRON = "0 8 * * 3"
# notifier_8am: Sunday 09:30 UTC
_NOTIFIER_8AM_CRON = "30 9 * * 0"
# notifier_10am: Sunday 12:00 UTC
_NOTIFIER_10AM_CRON = "0 12 * * 0"
# notifier_3pm: Sunday 17:00 UTC
_NOTIFIER_3PM_CRON = "0 17 * * 0"
# notifier_powerhouse: Wednesday 21:00 UTC
_NOTIFIER_POWERHOUSE_CRON = "0 21 * * 3"
# biometric_retention: daily 02:00 UTC
_BIOMETRIC_RETENTION_CRON = "0 2 * * *"

_S16_CRON_MAP: dict[str, tuple[str, Callable]] = {
    "sunday_generation": (_SUNDAY_GEN_CRON, _sunday_generation_job),
    "powerhouse_generation": (_POWERHOUSE_GEN_CRON, _powerhouse_generation_job),
    "notifier_8am": (_NOTIFIER_8AM_CRON, _make_notifier_job("8am")),
    "notifier_10am": (_NOTIFIER_10AM_CRON, _make_notifier_job("10am")),
    "notifier_3pm": (_NOTIFIER_3PM_CRON, _make_notifier_job("3pm")),
    "notifier_powerhouse": (_NOTIFIER_POWERHOUSE_CRON, _make_notifier_job("powerhouse")),
    "biometric_retention": (_BIOMETRIC_RETENTION_CRON, _biometric_retention_job),
}


# ---------------------------------------------------------------------------
# Public API: start / stop
# ---------------------------------------------------------------------------


def start_scheduler() -> None:
    """Build the scheduler, register all 9 jobs, and start it.

    Called from main.py lifespan on startup when ENVIRONMENT != 'test'.
    """
    from apscheduler.triggers.cron import CronTrigger

    scheduler = _get_or_create_scheduler()

    # Register 7 S16 non-recompute jobs
    for job_name, (cron_str, coro_factory) in _S16_CRON_MAP.items():
        _factory = coro_factory  # capture for closure

        def _make_wrapper(jname: str, factory: Callable):
            def _wrapper():
                asyncio.ensure_future(_run_tracked_job(jname, factory))
            return _wrapper

        scheduler.add_job(
            _make_wrapper(job_name, _factory),
            CronTrigger.from_crontab(cron_str),
            id=job_name,
            replace_existing=True,
        )

    # Register 2 S23 recompute jobs
    register_s23_jobs(scheduler)

    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    """Shutdown the scheduler gracefully (wait=False)."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception:
            logger.exception("Error stopping scheduler")
