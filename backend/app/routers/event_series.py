"""Event Series router — CRUD + generate endpoint.

Prefix : /event-series
Tags   : event-series
Auth   : GET endpoints require_volunteer; mutating endpoints require_admin.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import Event, EventSeries, Participant
from app.schemas import (
    EventDetailResponse,
    EventSeriesCreate,
    EventSeriesResponse,
    EventSeriesUpdate,
    ParticipantCounts,
)
from app.services import audit as audit_svc

router = APIRouter(prefix="/event-series", tags=["event-series"])


# ---------------------------------------------------------------------------
# Request body schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Body for POST /event-series/{id}/generate."""

    target_date: date


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _series_to_response(series: EventSeries) -> EventSeriesResponse:
    """Map EventSeries ORM object to EventSeriesResponse.

    The ORM model uses 'title' and 'session_time'; the schema uses 'name' and
    'default_session_time'.  This helper bridges the mismatch.
    """
    return EventSeriesResponse(
        id=series.id,
        name=series.title,
        event_type=series.event_type,
        default_session_time=series.session_time,
        default_location=series.default_location,
        is_active=series.is_active,
        created_at=series.created_at,
    )


async def _get_participant_counts(db: AsyncSession, event_id: int) -> ParticipantCounts:
    """Get participant counts for an event, delegating to event_generator if available."""
    try:
        from app.services.event_generator import get_event_participant_counts  # type: ignore[import]

        raw = await get_event_participant_counts(event_id, db)
        if isinstance(raw, dict):
            return ParticipantCounts(
                unique_count=raw.get("unique_count", 0),
                total_count=raw.get("total_count", 0),
            )
        return raw
    except ImportError:
        pass

    total_count = (
        await db.execute(
            select(func.count()).where(Participant.event_id == event_id)
        )
    ).scalar_one()

    unique_count = (
        await db.execute(
            select(func.count(distinct(Participant.contact_id))).where(
                Participant.event_id == event_id
            )
        )
    ).scalar_one()

    present = (
        await db.execute(
            select(func.count()).where(
                Participant.event_id == event_id,
                Participant.status == "attended",
            )
        )
    ).scalar_one()

    absent = (
        await db.execute(
            select(func.count()).where(
                Participant.event_id == event_id,
                Participant.status == "no_show",
            )
        )
    ).scalar_one()

    return ParticipantCounts(
        unique_count=unique_count,
        total_count=total_count,
        present=present,
        absent=absent,
        unknown=max(0, total_count - present - absent),
    )


# ---------------------------------------------------------------------------
# Collection endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=List[EventSeriesResponse])
async def list_event_series(
    is_active: Optional[bool] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List all event series.  Optionally filter by ?is_active=true/false."""
    q = select(EventSeries)
    if is_active is not None:
        q = q.where(EventSeries.is_active == is_active)
    q = q.order_by(EventSeries.id)
    rows = (await db.execute(q)).scalars().all()
    return [_series_to_response(s) for s in rows]


@router.post("", response_model=EventSeriesResponse, status_code=status.HTTP_201_CREATED)
async def create_event_series(
    body: EventSeriesCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Create a new event series.  Admin only."""
    series = EventSeries(
        title=body.name,
        event_type=body.event_type,
        session_time=body.default_session_time,
        default_location=body.default_location,
        is_active=body.is_active,
    )
    db.add(series)
    await db.flush()

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="series.create",
        entity="event_series",
        entity_id=series.id,
        before=None,
        after={
            "name": series.title,
            "event_type": series.event_type,
            "default_session_time": series.session_time,
            "default_location": series.default_location,
            "is_active": series.is_active,
        },
    )
    await db.commit()
    await db.refresh(series)
    return _series_to_response(series)


# ---------------------------------------------------------------------------
# Single-series endpoints
# ---------------------------------------------------------------------------


@router.get("/{series_id}", response_model=EventSeriesResponse)
async def get_event_series(
    series_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Retrieve a single event series by ID."""
    series = await db.get(EventSeries, series_id)
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventSeries {series_id} not found.",
        )
    return _series_to_response(series)


@router.patch("/{series_id}", response_model=EventSeriesResponse)
async def update_event_series(
    series_id: int,
    body: EventSeriesUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Update an event series.  Admin only."""
    series = await db.get(EventSeries, series_id)
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventSeries {series_id} not found.",
        )

    before = {
        "name": series.title,
        "event_type": series.event_type,
        "default_session_time": series.session_time,
        "default_location": series.default_location,
        "is_active": series.is_active,
    }

    # Map schema field names to ORM column names where they differ
    field_map = {
        "name": "title",
        "default_session_time": "session_time",
    }
    update_data = body.model_dump(exclude_unset=True)
    for schema_field, value in update_data.items():
        orm_field = field_map.get(schema_field, schema_field)
        setattr(series, orm_field, value)

    after = {
        "name": series.title,
        "event_type": series.event_type,
        "default_session_time": series.session_time,
        "default_location": series.default_location,
        "is_active": series.is_active,
    }

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="series.update",
        entity="event_series",
        entity_id=series.id,
        before=before,
        after=after,
    )
    await db.commit()
    await db.refresh(series)
    return _series_to_response(series)


@router.delete("/{series_id}", status_code=status.HTTP_200_OK)
async def delete_event_series(
    series_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Soft-delete an event series (sets is_active=False).

    Linked events are NOT deleted — they remain in the database with their
    recurring_series_id intact.  Admin only.
    """
    series = await db.get(EventSeries, series_id)
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventSeries {series_id} not found.",
        )

    series.is_active = False

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="series.delete",
        entity="event_series",
        entity_id=series.id,
        before={"is_active": True},
        after={"is_active": False},
    )
    await db.commit()

    return {"id": series_id, "is_active": False}


# ---------------------------------------------------------------------------
# Generate endpoint
# ---------------------------------------------------------------------------


@router.post("/{series_id}/generate", status_code=status.HTTP_200_OK)
async def generate_series_occurrences(
    series_id: int,
    body: GenerateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Generate event occurrences for the given series and target date.

    Delegates entirely to event_generator.generate_series_occurrence — no
    business logic lives in this router.  Returns:
        {created: int, skipped: int, events: [EventDetailResponse]}
    """
    series = await db.get(EventSeries, series_id)
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventSeries {series_id} not found.",
        )

    from app.services.event_generator import generate_series_occurrence  # type: ignore[import]

    result = await generate_series_occurrence(series_id, db, body.target_date)

    # Build EventDetailResponse for each generated event
    event_responses: List[EventDetailResponse] = []
    for ev in result.get("events", []):
        if isinstance(ev, Event):
            counts = await _get_participant_counts(db, ev.id)
            event_responses.append(
                EventDetailResponse.model_validate(ev, from_attributes=True).model_copy(
                    update={"participant_counts": counts}
                )
            )
        elif isinstance(ev, dict):
            counts = await _get_participant_counts(db, ev["id"])
            event_responses.append(
                EventDetailResponse(**ev, participant_counts=counts)
            )
        else:
            # Already an EventDetailResponse (or compatible schema object)
            event_responses.append(ev)

    return {
        "created": result.get("created", 0),
        "skipped": result.get("skipped", 0),
        "events": [e.model_dump() for e in event_responses],
    }
