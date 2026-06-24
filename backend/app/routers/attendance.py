from datetime import datetime
from typing import List, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.models import Participant
from app.schemas import ParticipantRecord

router = APIRouter(prefix="/attendance", tags=["attendance"])

logger = logging.getLogger(__name__)


@router.get("", response_model=List[ParticipantRecord])
async def list_attendance(
    event_id: Optional[int] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List participant records with optional filters (bounded; newest first)."""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = select(Participant).order_by(Participant.created_at.desc())

    if event_id:
        query = query.where(Participant.event_id == event_id)
    if status:
        query = query.where(Participant.status == status)
    if date:
        try:
            # Naive UTC to match the naive DateTime columns
            start = datetime.strptime(date, "%Y-%m-%d")
            end = start.replace(hour=23, minute=59, second=59)
            query = query.where(Participant.created_at >= start, Participant.created_at <= end)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return records
