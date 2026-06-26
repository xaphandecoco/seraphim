"""APScheduler job registration for Project Seraphim S23.

This module is wired into the app lifespan only when HAS_SCHEDULER = True
in app/main.py.  That flag is False by default and will be flipped to True
once S16 lands and apscheduler is added to requirements.txt.

Importing apscheduler happens LAZILY inside register_s23_jobs() so that
importing this module never breaks app boot when apscheduler is absent.
"""

from __future__ import annotations


def register_s23_jobs(scheduler) -> None:  # type: ignore[type-arg]
    """Register the S23 member-status recompute cron jobs on *scheduler*.

    Jobs registered
    ---------------
    - EOW  (end-of-week):  Monday 00:00 UTC
    - EOM  (end-of-month): 1st of each month 00:00 UTC

    Both jobs run ``recompute_all`` in their own database session so they
    are independent of any request-scoped session.
    """
    from apscheduler.triggers.cron import CronTrigger  # lazy — not in requirements yet

    from app.database import async_session
    from app.services.member_status_service import recompute_all

    async def _run_recompute() -> None:
        async with async_session() as db:
            await recompute_all(db)

    scheduler.add_job(
        _run_recompute,
        CronTrigger(day_of_week="mon", hour=0, minute=0),
        id="member_status_recompute_eow",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_recompute,
        CronTrigger(day=1, hour=0, minute=0),
        id="member_status_recompute_eom",
        replace_existing=True,
    )
