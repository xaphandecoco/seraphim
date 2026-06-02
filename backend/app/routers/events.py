from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import AdminSetting, CiviCRMEvent
from app.schemas import EventResponse
from app.services.civicrm import CiviCRMClient

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventResponse])
async def list_events(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List all CiviCRM events ordered by start date descending."""
    result = await db.execute(
        select(CiviCRMEvent).order_by(CiviCRMEvent.start_date.desc())
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
        event = await db.get(CiviCRMEvent, event_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event {event_id} not found. Sync events from CiviCRM first.",
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
        event = await db.get(CiviCRMEvent, event_id)
        return {
            "active_event_id": event_id,
            "event_title": event.title if event else None,
            "message": f"Active event set to '{event.title}'" if event else f"Active event set to {event_id}",
        }
    return {"active_event_id": None, "message": "Active event cleared"}


@router.post("/sync")
async def sync_events(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Force sync events from CiviCRM."""
    try:
        client = CiviCRMClient()
        events = await client.sync_events(
            start_date=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        synced = 0
        for event_data in events:
            event_id = int(event_data.get("id", 0))
            if not event_id:
                continue
            existing = await db.get(CiviCRMEvent, event_id)

            # Parse date strings safely (CiviCRM returns "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD")
            def _parse_date(val: str | None) -> datetime | None:
                if not val:
                    return None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(val, fmt)
                    except ValueError:
                        continue
                return None

            start_date = _parse_date(event_data.get("start_date"))
            end_date = _parse_date(event_data.get("end_date"))

            if existing:
                existing.title = event_data.get("title", existing.title)
                if start_date:
                    existing.start_date = start_date
                if end_date:
                    existing.end_date = end_date
                existing.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                new_event = CiviCRMEvent(
                    event_id=event_id,
                    title=event_data.get("title", "Untitled"),
                    start_date=start_date,
                    end_date=end_date,
                    last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(new_event)
            synced += 1
        await db.commit()
        await client.close()
        return {"message": f"Synced {synced} events", "synced_count": synced}
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CiviCRM not configured",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CiviCRM sync failed. Check server logs.",
        )
