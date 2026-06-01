from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.models import User, VolunteerStat
from app.schemas import LeaderboardEntry, LeaderboardResponse

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def _get_month_key(period: str) -> str | None:
    now = datetime.now(timezone.utc)
    if period == "this_month":
        return now.strftime("%Y-%m")
    elif period == "previous_month":
        year, month = now.year, now.month
        if month == 1:
            year -= 1
            month = 12
        else:
            month -= 1
        return f"{year}-{month:02d}"
    return None


def _display_name(user: User) -> str:
    return user.name or user.email


@router.get("", response_model=LeaderboardResponse)
async def get_leaderboard(
    period: Literal["this_month", "previous_month", "all_time"] = "this_month",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Return ranked list of volunteers by points."""
    month_key = _get_month_key(period)

    if month_key:
        result = await db.execute(
            select(VolunteerStat, User)
            .join(User, VolunteerStat.volunteer_id == User.id)
            .where(VolunteerStat.month == month_key)
            .order_by(VolunteerStat.total_points.desc())
        )
        rows = result.all()
        entries = [
            LeaderboardEntry(
                volunteer_id=str(u.id),
                volunteer_email=_display_name(u),
                total_points=stat.total_points or 0,
                tasks_completed=(stat.tasks_confirmed or 0)
                + (stat.tasks_edited or 0)
                + (stat.tasks_added or 0),
                accuracy_percent=float(stat.accuracy_score or 100.0),
            )
            for stat, u in rows
        ]
    else:
        result = await db.execute(
            select(
                VolunteerStat.volunteer_id,
                func.sum(VolunteerStat.total_points).label("total_points"),
                func.sum(VolunteerStat.tasks_confirmed).label("tasks_confirmed"),
                func.sum(VolunteerStat.tasks_edited).label("tasks_edited"),
                func.sum(VolunteerStat.tasks_added).label("tasks_added"),
            )
            .group_by(VolunteerStat.volunteer_id)
            .order_by(func.sum(VolunteerStat.total_points).desc())
        )
        rows = result.all()
        volunteer_ids = [row.volunteer_id for row in rows]
        user_result = await db.execute(
            select(User).where(User.id.in_(volunteer_ids))
        )
        user_map = {u.id: u for u in user_result.scalars().all()}

        entries = [
            LeaderboardEntry(
                volunteer_id=str(row.volunteer_id),
                volunteer_email=_display_name(user_map[row.volunteer_id]) if row.volunteer_id in user_map else "",
                total_points=row.total_points or 0,
                tasks_completed=(row.tasks_confirmed or 0)
                + (row.tasks_edited or 0)
                + (row.tasks_added or 0),
                accuracy_percent=100.0,
            )
            for row in rows
        ]

    return LeaderboardResponse(period=period, entries=entries)
