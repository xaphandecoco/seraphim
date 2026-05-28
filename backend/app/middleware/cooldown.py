import logging
from typing import Optional

import redis.asyncio as aioredis

from app.config import legacy_settings

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 3

_redis_client: Optional[aioredis.Redis] = None


async def _get_redis() -> Optional[aioredis.Redis]:
    """Lazy-initialize the async Redis client."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(
                legacy_settings.REDIS_URL,
                decode_responses=True,
            )
        except Exception as exc:
            logger.warning("Failed to create Redis client for cooldown: %s", exc)
    return _redis_client


async def check_cooldown(volunteer_id: int) -> Optional[float]:
    """Check if volunteer is in cooldown. Returns seconds remaining or None if OK."""
    r = await _get_redis()
    if r is None:
        # Fail-open: allow action if Redis is unavailable
        return None

    key = f"cooldown:volunteer:{volunteer_id}"
    try:
        # Atomically set only if key does not exist (NX) with expiry (EX)
        was_set = await r.set(key, "1", nx=True, ex=COOLDOWN_SECONDS)
        if was_set:
            # Cooldown was just started; action is allowed
            return None

        # Key already exists — retrieve remaining TTL
        ttl = await r.ttl(key)
        if ttl > 0:
            return float(ttl)

        # TTL <= 0 means key expired or has no expiry; allow action and reset
        await r.delete(key)
        await r.setex(key, COOLDOWN_SECONDS, "1")
        return None
    except Exception as exc:
        logger.warning("Redis cooldown check failed for volunteer %s: %s", volunteer_id, exc)
        # Fail-open
        return None
