from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import AdminSetting, Event
from app.schemas import EventResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventResponse])
async def list_events(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List all events ordered by start date descending."""
    result = await db.execute(
        select(Event).order_by(Event.start_at.desc())
    )
    return result.scalars().all()


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
