from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import CiviCRMEvent
from app.schemas import EventResponse
from app.services.civicrm import CiviCRMClient

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventResponse])
async def list_events(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List all CiviCRM events ordered by start date."""
    result = await db.execute(
        select(CiviCRMEvent).order_by(CiviCRMEvent.start_date.desc())
    )
    return result.scalars().all()


@router.post("/sync")
async def sync_events(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Force sync events from CiviCRM."""
    try:
        client = CiviCRMClient()
        events = await client.sync_events(start_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        synced = 0
        for event_data in events:
            event_id = int(event_data.get("id", 0))
            if not event_id:
                continue
            existing = await db.get(CiviCRMEvent, event_id)
            if existing:
                existing.title = event_data.get("title", existing.title)
                if event_data.get("start_date"):
                    existing.start_date = event_data["start_date"]
                if event_data.get("end_date"):
                    existing.end_date = event_data["end_date"]
            else:
                new_event = CiviCRMEvent(
                    event_id=event_id,
                    title=event_data.get("title", "Untitled"),
                    start_date=event_data.get("start_date"),
                    end_date=event_data.get("end_date"),
                )
                db.add(new_event)
            synced += 1
        await db.commit()
        await client.close()
        return {"message": f"Synced {synced} events", "synced_count": synced}
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"CiviCRM not configured: {exc}"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CiviCRM sync failed: {exc}"
        )
