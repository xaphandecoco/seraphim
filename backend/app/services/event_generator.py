"""Event generator service — cadence constants + generation logic.

Ownership: S04-F06.

PHT = UTC+8.  All datetimes stored as naive UTC (utc_now convention).
UTC start_at is computed by subtracting the PHT offset:
  8 AM PHT  → date + timedelta(hours=0)
  10 AM PHT → date + timedelta(hours=2)
  3 PM PHT  → date + timedelta(hours=7)
  9 PM PHT  → date + timedelta(hours=13)

Idempotency is enforced by query-before-insert (spec §4.3) — NOT a DB unique
constraint.  Before inserting, we check existence on
(occurrence_date, event_type, session_time, recurring_series_id); existing
rows are counted as 'skipped'.

Function signatures are FROZEN — S16 imports SUNDAY_CRON, POWERHOUSE_CRON,
generate_sunday_events, generate_powerhouse_event from this module.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, EventSeries, Participant, utc_now

# ---------------------------------------------------------------------------
# Cadence constants (spec §4.2)
# ---------------------------------------------------------------------------

# Cron trigger for Sunday Service generation (runs Friday 6 PM UTC so that
# events are ready before the Sunday service).
SUNDAY_CRON: Dict[str, Any] = {"day_of_week": "fri", "hour": 18, "minute": 0}

# Cron trigger for Powerhouse generation (runs Wednesday 8 AM UTC).
POWERHOUSE_CRON: Dict[str, Any] = {"day_of_week": "wed", "hour": 8, "minute": 0}

# End-of-week recompute — Saturday midnight UTC (beginning of Saturday).
EOW_RECOMPUTE_CRON: Dict[str, Any] = {"day_of_week": "sat", "hour": 0, "minute": 0}

# End-of-month recompute — last day of every month at 23:00 UTC.
EOM_RECOMPUTE_CRON: Dict[str, Any] = {"day": "last", "hour": 23, "minute": 0}

# Notifier crons — notification sweeps at these UTC times daily.
NOTIFIER_CRONS: List[Dict[str, Any]] = [
    {"hour": 0, "minute": 0},   # midnight UTC
    {"hour": 6, "minute": 0},   # 6 AM UTC (2 PM PHT)
    {"hour": 12, "minute": 0},  # noon UTC (8 PM PHT)
    {"hour": 18, "minute": 0},  # 6 PM PHT / 10 PM PHT... (6 PM UTC)
]

# ---------------------------------------------------------------------------
# Participant counts
# ---------------------------------------------------------------------------

# PHT session time label → (hours_offset_from_midnight_utc, session_time_label)
_SUNDAY_SESSIONS = [
    (0, "8AM"),    # 8 AM PHT  = 00:00 UTC
    (2, "10AM"),   # 10 AM PHT = 02:00 UTC
    (7, "3PM"),    # 3 PM PHT  = 07:00 UTC
]


async def get_event_participant_counts(
    event_id: int,
    db: AsyncSession,
) -> Dict[str, int]:
    """Return unique_count and total_count for participants of an event.

    Uses async SQLAlchemy COUNT queries:
      unique_count = COUNT(DISTINCT contact_id)
      total_count  = COUNT(*)
    on participants WHERE event_id = <event_id>.

    Returns {unique_count: 0, total_count: 0} when no participants exist.
    """
    unique_count_result = await db.execute(
        select(func.count(distinct(Participant.contact_id))).where(
            Participant.event_id == event_id
        )
    )
    unique_count = unique_count_result.scalar_one() or 0

    total_count_result = await db.execute(
        select(func.count()).where(Participant.event_id == event_id)
    )
    total_count = total_count_result.scalar_one() or 0

    return {"unique_count": unique_count, "total_count": total_count}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_naive_date(target_date: Optional[date]) -> date:
    """Resolve target_date to today (UTC) when None."""
    if target_date is None:
        return datetime.utcnow().date()
    if isinstance(target_date, datetime):
        return target_date.date()
    return target_date


async def _check_existing_event(
    db: AsyncSession,
    occurrence_date: date,
    event_type: str,
    session_time: Optional[str],
    recurring_series_id: int,
) -> bool:
    """Return True if a matching event already exists (idempotency check)."""
    q = select(func.count()).where(
        Event.occurrence_date == occurrence_date,
        Event.event_type == event_type,
        Event.session_time == session_time,
        Event.recurring_series_id == recurring_series_id,
    )
    result = await db.execute(q)
    return (result.scalar_one() or 0) > 0


# ---------------------------------------------------------------------------
# Sunday Service generation
# ---------------------------------------------------------------------------


async def generate_sunday_events(
    series_id: int,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> List[Event]:
    """Generate the three Sunday Service events for target_date.

    Sessions (naive UTC start_at):
      8 AM PHT  → date + 0 h  (00:00 UTC)
      10 AM PHT → date + 2 h  (02:00 UTC)
      3 PM PHT  → date + 7 h  (07:00 UTC)

    Idempotent: skips any session that already has a matching row on
    (occurrence_date, event_type, session_time, recurring_series_id).

    Returns the list of newly created Event ORM objects (not committed yet).
    """
    series = await db.get(EventSeries, series_id)
    if series is None:
        raise ValueError(f"EventSeries {series_id} not found.")

    resolved_date = _to_naive_date(target_date)
    created_events: List[Event] = []

    for hours_offset, session_label in _SUNDAY_SESSIONS:
        already_exists = await _check_existing_event(
            db,
            occurrence_date=resolved_date,
            event_type=series.event_type,
            session_time=session_label,
            recurring_series_id=series_id,
        )
        if already_exists:
            continue

        start_at = datetime(
            resolved_date.year,
            resolved_date.month,
            resolved_date.day,
        ) + timedelta(hours=hours_offset)

        event = Event(
            title=f"{series.title} — {session_label}",
            event_type=series.event_type,
            session_time=session_label,
            occurrence_date=resolved_date,
            recurring_series_id=series_id,
            start_at=start_at,
            is_active=True,
            location=series.default_location,
            created_at=utc_now(),
        )
        db.add(event)
        created_events.append(event)

    await db.flush()
    return created_events


# ---------------------------------------------------------------------------
# Powerhouse generation
# ---------------------------------------------------------------------------


async def generate_powerhouse_event(
    series_id: int,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> Optional[Event]:
    """Generate a single Powerhouse event for target_date.

    Session time: 9 PM PHT = 13:00 UTC (date + 13 h).

    Idempotent: returns None when an event for the same
    (occurrence_date, event_type, session_time, recurring_series_id)
    already exists.

    Returns the newly created Event ORM object (not committed yet), or None.
    """
    series = await db.get(EventSeries, series_id)
    if series is None:
        raise ValueError(f"EventSeries {series_id} not found.")

    resolved_date = _to_naive_date(target_date)
    session_label = "9PM"

    already_exists = await _check_existing_event(
        db,
        occurrence_date=resolved_date,
        event_type=series.event_type,
        session_time=session_label,
        recurring_series_id=series_id,
    )
    if already_exists:
        return None

    start_at = datetime(
        resolved_date.year,
        resolved_date.month,
        resolved_date.day,
    ) + timedelta(hours=13)

    event = Event(
        title=f"{series.title} — {session_label}",
        event_type=series.event_type,
        session_time=session_label,
        occurrence_date=resolved_date,
        recurring_series_id=series_id,
        start_at=start_at,
        is_active=True,
        location=series.default_location,
        created_at=utc_now(),
    )
    db.add(event)
    await db.flush()
    return event


# ---------------------------------------------------------------------------
# Dispatcher — generate_series_occurrence
# ---------------------------------------------------------------------------


async def generate_series_occurrence(
    series_id: int,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Generate event occurrence(s) for a series on target_date.

    Dispatches based on series.event_type:
      "SundayService"  → generate_sunday_events (up to 3 events)
      "Powerhouse"     → generate_powerhouse_event (1 event)
      other            → raises ValueError

    Returns:
      {
        "created": <int>,   # number of new events inserted
        "skipped": <int>,   # number of existing events found (idempotency)
        "events":  [Event]  # list of newly created ORM objects
      }
    """
    series = await db.get(EventSeries, series_id)
    if series is None:
        raise ValueError(f"EventSeries {series_id} not found.")

    resolved_date = _to_naive_date(target_date)
    event_type = series.event_type

    if event_type == "Sunday Celebration":
        new_events = await generate_sunday_events(series_id, db, resolved_date)
        # skipped = total possible sessions − newly created
        skipped = len(_SUNDAY_SESSIONS) - len(new_events)
        return {
            "created": len(new_events),
            "skipped": skipped,
            "events": new_events,
        }

    if event_type == "Powerhouse":
        new_event = await generate_powerhouse_event(series_id, db, resolved_date)
        if new_event is not None:
            return {"created": 1, "skipped": 0, "events": [new_event]}
        else:
            return {"created": 0, "skipped": 1, "events": []}

    raise ValueError(
        f"Unsupported event_type '{event_type}' for series {series_id}. "
        "Supported types: Sunday Celebration, Powerhouse."
    )
