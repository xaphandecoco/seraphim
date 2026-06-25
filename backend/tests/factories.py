"""Test factories for S05 tests.

Provides:
    is_postgres()              — bool, checks active engine dialect
    make_contacts(db, n, **kw) — create n Contact rows, flush, return list
    make_event(db, **kw)       — create one Event row, flush, return it
    make_participants(db, event, contacts, **kw) — create Participant rows, flush, return list
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def is_postgres() -> bool:
    """Return True if the currently-bound engine is PostgreSQL."""
    from app.database import engine as _app_engine

    try:
        return _app_engine.dialect.name == "postgresql"
    except Exception:
        return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def make_contacts(
    db: AsyncSession,
    n: int,
    *,
    first_name_prefix: str = "First",
    last_name_prefix: str = "Last",
    contact_type: str = "individual",
    **extra: Any,
) -> list[Any]:
    """Create *n* Contact rows, flush (but do NOT commit), and return them.

    NOTE: contact_type must be lowercase ('individual', 'household', 'organization')
    per the ORM model default and the S05 lowercase ruling.
    """
    from app.models import Contact

    contacts = []
    for i in range(n):
        c = Contact(
            first_name=f"{first_name_prefix}{i}",
            last_name=f"{last_name_prefix}{i}",
            contact_type=contact_type,
            **extra,
        )
        db.add(c)
        contacts.append(c)

    await db.flush()
    return contacts


async def make_event(
    db: AsyncSession,
    *,
    title: str = "Test Event",
    start_at: datetime | None = None,
    event_type: str = "Event",
    **extra: Any,
) -> Any:
    """Create one Event row, flush, and return it."""
    from app.models import Event

    ev = Event(
        title=title,
        start_at=start_at or _utc_now(),
        event_type=event_type,
        **extra,
    )
    db.add(ev)
    await db.flush()
    return ev


async def make_participants(
    db: AsyncSession,
    event: Any,
    contacts: list[Any],
    *,
    source: str = "manual",
    status: str = "attended",
    **extra: Any,
) -> list[Any]:
    """Create one Participant per contact (for *event*), flush, and return them.

    Skips silently on duplicate (event_id, contact_id) pairs.
    """
    from app.models import Participant

    participants = []
    for contact in contacts:
        p = Participant(
            event_id=event.id,
            contact_id=contact.id,
            source=source,
            status=status,
            **extra,
        )
        db.add(p)
        participants.append(p)

    await db.flush()
    return participants
