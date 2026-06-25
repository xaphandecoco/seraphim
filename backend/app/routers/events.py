from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import AdminSetting, Contact, Event, Participant
from app.schemas import (
    EventCreate,
    EventDetailResponse,
    EventResponse,
    EventUpdate,
    PaginatedEventResponse,
    ParticipantCounts,
    ParticipantListResponse,
    ParticipantManualAdd,
    ParticipantStatusUpdate,
)
from app.services import audit as audit_svc

router = APIRouter(prefix="/events", tags=["events"])


# ---------------------------------------------------------------------------
# Route ORDER is load-bearing: /active-event-id and /set-active must come
# BEFORE the /{id} routes, or FastAPI will try to parse "active-event-id"
# and "set-active" as integer path parameters and shadow these endpoints.
# ---------------------------------------------------------------------------


@router.get("/active-event-id")
async def get_active_event_id(user=Depends(require_volunteer)):
    """Return the currently active event ID (or null if none set)."""
    return {"active_event_id": dynamic_settings.get_active_event_id()}


@router.post("/set-active")
async def set_active_event(
    event_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Set or clear the active event. Detections will be tagged with this event_id."""
    result = await db.execute(
        select(AdminSetting).where(AdminSetting.key == "active_event_id")
    )
    setting = result.scalar_one_or_none()

    if event_id is not None:
        # Verify the event exists
        event = await db.get(Event, event_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event {event_id} not found.",
            )

    if setting:
        setting.value = {"value": event_id}
    else:
        setting = AdminSetting(
            key="active_event_id",
            value={"value": event_id},
            category="general",
            description="Currently active event for camera detections",
        )
        db.add(setting)

    await db.commit()
    await dynamic_settings.reload(db)

    if event_id:
        event = await db.get(Event, event_id)
        return {
            "active_event_id": event_id,
            "event_title": event.title if event else None,
            "message": f"Active event set to '{event.title}'" if event else f"Active event set to {event_id}",
        }
    return {"active_event_id": None, "message": "Active event cleared"}


# ---------------------------------------------------------------------------
# Collection endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedEventResponse)
async def list_events(
    event_type: Optional[str] = Query(None, alias="type"),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    series_id: Optional[int] = None,
    is_active: Optional[bool] = Query(True),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List events with optional filters and pagination."""
    q = select(Event)

    if event_type is not None:
        q = q.where(Event.event_type == event_type)
    if date_from is not None:
        q = q.where(Event.start_at >= date_from)
    if date_to is not None:
        q = q.where(Event.start_at <= date_to)
    if series_id is not None:
        q = q.where(Event.recurring_series_id == series_id)

    # Default: show only active events; ?is_active=false shows inactive
    if is_active is not None:
        q = q.where(Event.is_active == is_active)

    # Count total
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    q = q.order_by(Event.start_at.desc()).offset(offset).limit(page_size)
    items = (await db.execute(q)).scalars().all()

    return PaginatedEventResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=list(items),
    )


@router.post("", response_model=EventDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Create a new event. Only admins may create events."""
    # Validate session_time: only allowed for Sunday Celebration event type
    if body.session_time is not None and body.event_type != "Sunday Celebration":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="session_time is only allowed when event_type is 'Sunday Celebration'",
        )

    # Auto-set occurrence_date from start_at when omitted
    occurrence_date = body.occurrence_date
    if occurrence_date is None and body.start_at is not None:
        occurrence_date = body.start_at.date()

    ev = Event(
        title=body.title,
        start_at=body.start_at,
        end_at=body.end_at,
        external_id=body.external_id,
        event_type=body.event_type or "other",
        session_time=body.session_time,
        occurrence_date=occurrence_date,
        location=body.location,
        recurring_series_id=body.recurring_series_id,
        is_active=body.is_active,
    )
    db.add(ev)
    await db.flush()  # get ev.id before audit

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="event.create",
        entity="event",
        entity_id=ev.id,
        before=None,
        after={
            "title": ev.title,
            "event_type": ev.event_type,
            "session_time": ev.session_time,
            "start_at": ev.start_at.isoformat() if ev.start_at else None,
            "end_at": ev.end_at.isoformat() if ev.end_at else None,
            "occurrence_date": ev.occurrence_date.isoformat() if ev.occurrence_date else None,
            "location": ev.location,
            "is_active": ev.is_active,
        },
    )
    await db.commit()
    await db.refresh(ev)

    # Get participant counts (lazy import — event_generator may not exist yet)
    counts = await _get_participant_counts(db, ev.id)

    return EventDetailResponse.model_validate(ev, from_attributes=True).model_copy(
        update={"participant_counts": counts}
    )


# ---------------------------------------------------------------------------
# Single-event endpoints — must come AFTER /active-event-id and /set-active
# ---------------------------------------------------------------------------


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Retrieve a single event by ID including participant counts."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    counts = await _get_participant_counts(db, ev.id)

    return EventDetailResponse.model_validate(ev, from_attributes=True).model_copy(
        update={"participant_counts": counts}
    )


@router.patch("/{event_id}", response_model=EventResponse)
async def update_event(
    event_id: int,
    body: EventUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Update event fields. Only admins may update events."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    before = {
        "title": ev.title,
        "event_type": ev.event_type,
        "session_time": ev.session_time,
        "start_at": ev.start_at.isoformat() if ev.start_at else None,
        "is_active": ev.is_active,
    }

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(ev, field, value)

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="event.update",
        entity="event",
        entity_id=ev.id,
        before=before,
        after=update_data,
    )
    await db.commit()
    await db.refresh(ev)
    return ev


@router.delete("/{event_id}", status_code=status.HTTP_200_OK)
async def delete_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Soft-delete an event (set is_active=False). Only admins may delete events."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    # Check if this is the currently active event
    active_event_id = dynamic_settings.get_active_event_id()
    is_current_active = active_event_id is not None and active_event_id == event_id

    ev.is_active = False

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="event.delete",
        entity="event",
        entity_id=ev.id,
        before={"is_active": True},
        after={"is_active": False},
    )
    await db.commit()

    response: dict = {"id": event_id, "is_active": False}
    if is_current_active:
        response["warning"] = (
            f"Event {event_id} was the active event. "
            "Detections will no longer be tagged until a new active event is set."
        )
    return response


# ---------------------------------------------------------------------------
# Participant sub-resources
# ---------------------------------------------------------------------------


@router.get("/{event_id}/participants", response_model=ParticipantListResponse)
async def list_participants(
    event_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List participants for an event."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    q = select(Participant).where(Participant.event_id == event_id)
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    q = q.offset(offset).limit(page_size)
    participants = (await db.execute(q)).scalars().all()

    # Resolve contact display names
    items = []
    for p in participants:
        contact = await db.get(Contact, p.contact_id)
        display_name = None
        if contact:
            display_name = (
                contact.nickname
                or f"{contact.first_name} {contact.last_name}".strip()
            )
        items.append(
            {
                "participant_id": p.id,
                "contact_id": p.contact_id,
                "contact_display_name": display_name,
                "status": p.status,
                "source": p.source,
                "role": p.role,
                "created_at": p.created_at,
            }
        )

    return ParticipantListResponse(total=total, page=page, page_size=page_size, items=items)


@router.post(
    "/{event_id}/participants",
    status_code=status.HTTP_201_CREATED,
)
async def add_participant(
    event_id: int,
    body: ParticipantManualAdd,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Manually add a participant to an event. source is always 'manual'."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    contact = await db.get(Contact, body.contact_id)
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {body.contact_id} not found.",
        )

    participant = Participant(
        event_id=event_id,
        contact_id=body.contact_id,
        status=body.status,
        role=body.role,
        source="manual",
    )
    db.add(participant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Contact {body.contact_id} is already a participant in event {event_id}.",
        )

    await db.commit()
    await db.refresh(participant)

    return {
        "participant_id": participant.id,
        "event_id": event_id,
        "contact_id": participant.contact_id,
        "status": participant.status,
        "source": participant.source,
        "role": participant.role,
        "created_at": participant.created_at,
    }


@router.patch("/{event_id}/participants/{participant_id}", status_code=status.HTTP_200_OK)
async def update_participant_status(
    event_id: int,
    participant_id: int,
    body: ParticipantStatusUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Update a participant's status (and optionally role)."""
    ev = await db.get(Event, event_id)
    if not ev:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    participant = await db.get(Participant, participant_id)
    if not participant or participant.event_id != event_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Participant {participant_id} not found in event {event_id}.",
        )

    participant.status = body.status
    if body.role is not None:
        participant.role = body.role

    await db.commit()
    await db.refresh(participant)

    return {
        "participant_id": participant.id,
        "event_id": event_id,
        "contact_id": participant.contact_id,
        "status": participant.status,
        "source": participant.source,
        "role": participant.role,
        "created_at": participant.created_at,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_participant_counts(db: AsyncSession, event_id: int) -> ParticipantCounts:
    """Get participant counts for an event.

    Uses event_generator.get_event_participant_counts when available (F06).
    Falls back to a direct DB query when the service has not yet been created.
    """
    try:
        from app.services.event_generator import get_event_participant_counts  # type: ignore[import]
        counts_dict = await get_event_participant_counts(event_id, db)
        return ParticipantCounts(
            unique_count=counts_dict.get("unique_count", 0),
            total_count=counts_dict.get("total_count", 0),
        )
    except ImportError:
        pass

    # Fallback: count directly from the participants table
    total_count = (
        await db.execute(
            select(func.count()).where(Participant.event_id == event_id)
        )
    ).scalar_one()

    # unique_count: distinct contacts (a contact may have at most one row due to
    # the unique constraint, but counting distinct is the canonical form)
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
