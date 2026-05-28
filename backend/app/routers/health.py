from datetime import datetime, timezone

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings, legacy_settings
from app.database import get_db
from app.models import Task
from app.schemas import HealthCheck

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthCheck)
async def health_check(db: AsyncSession = Depends(get_db)):
    # Check DB
    try:
        await db.execute(select(1))
        db_status = True
    except Exception:
        db_status = False

    # Check Redis
    redis_status = False
    try:
        r = redis.from_url(dynamic_settings.get_redis_url())
        await r.ping()
        redis_status = True
        await r.close()
    except Exception:
        pass

    # Check Compreface
    compreface_status = False
    compreface_url = dynamic_settings.get_compreface_url()
    if compreface_url:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{compreface_url}/api/v1/health")
                compreface_status = resp.status_code == 200
        except Exception:
            pass

    overall = "ok" if db_status and redis_status else "degraded" if db_status else "down"

    return HealthCheck(
        status=overall,
        postgres=db_status,
        redis=redis_status,
        compreface=compreface_status,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/health/queue")
async def queue_status(db: AsyncSession = Depends(get_db)):
    """Return queue saturation status."""
    result = await db.execute(
        select(func.count(Task.id)).where(Task.status == "pending")
    )
    pending = result.scalar() or 0
    hard_limit = dynamic_settings.get_queue_hard_limit()
    resume_limit = dynamic_settings.get_queue_resume_limit()
    return {
        "pending_count": pending,
        "hard_limit": hard_limit,
        "resume_limit": resume_limit,
        "saturated": pending >= hard_limit,
        "paused": pending >= hard_limit,
    }
