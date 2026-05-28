from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import Log
from app.schemas import LogResponse, PaginatedLogResponse

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("", response_model=PaginatedLogResponse)
async def get_logs(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    camera_id: Optional[int] = None,
    event_id: Optional[int] = None,
    volunteer_id: Optional[int] = None,
    action: Optional[str] = None,
    tier: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Filtered log viewer with pagination."""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 200:
        page_size = 50

    offset = (page - 1) * page_size

    query = select(Log)

    if start_date:
        query = query.where(Log.timestamp >= start_date)
    if end_date:
        query = query.where(Log.timestamp <= end_date)
    if tier:
        query = query.where(Log.tier == tier)
    if camera_id is not None:
        query = query.where(Log.camera_id == camera_id)
    if event_id is not None:
        query = query.where(Log.event_id == event_id)
    if volunteer_id is not None:
        query = query.where(Log.volunteer_id == volunteer_id)
    if action is not None:
        query = query.where(Log.action == action)

    # Get total count
    count_query = select(func.count(Log.id)).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(Log.timestamp.desc()).offset(offset).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return PaginatedLogResponse(
        items=[LogResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )
