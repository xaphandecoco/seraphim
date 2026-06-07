"""JTI denylist backed by Redis.

Under REDIS_URL=memory:// (tests / CI) redis.asyncio.from_url raises ValueError
because the raw redis client only understands redis://, rediss://, unix://.
We follow the same fail-open pattern as middleware/cooldown.py: if Redis is
unavailable, the denylist is a no-op. Rotation (new token + cookie reset) still
works without Redis; only the revocation guarantee degrades.

Naming convention: module is ``token_denylist``, public helpers are
``deny_jti`` and ``is_jti_denied`` — patch targets in tests must use
``app.routers.auth.deny_jti`` / ``app.routers.auth.is_jti_denied``.

Singleton design: a module-level ``_client`` is lazily created on the first
call and reused for all subsequent calls (no new connection per call).
Per-call ``aclose()`` is deliberately NOT used — closing the client
permanently kills the connection pool, causing silent degradation in
production.  Use ``aclose_redis()`` only from a lifespan shutdown hook.
"""

import logging
import time

logger = logging.getLogger(__name__)

_KEY_PREFIX = "jti_denylist:"

# Lazily-initialised singleton Redis client (None until first successful connect).
_client = None


def _redis_key(jti: str) -> str:
    return f"{_KEY_PREFIX}{jti}"


async def _get_client():
    """Return the singleton Redis client, creating it on the first call.

    Returns None on any error (fail-open).  Under REDIS_URL=memory:// the
    from_url call raises ValueError (unknown scheme) and _client stays None
    — all denylist operations become no-ops.
    """
    global _client
    if _client is not None:
        return _client
    try:
        import redis.asyncio as aioredis
        from app.config import legacy_settings

        url = legacy_settings.REDIS_URL or "redis://redis:6379/0"
        # redis-py raises ValueError for unknown schemes (e.g. memory://)
        _client = aioredis.from_url(url, decode_responses=True)
        return _client
    except Exception as exc:
        logger.warning("token_denylist: could not connect to Redis: %s", exc)
        return None


async def deny_jti(jti: str, exp: int) -> None:
    """Add jti to denylist with TTL = exp - now. No-op if TTL <= 0 or Redis unavailable."""
    ttl = exp - int(time.time())
    if ttl <= 0:
        return
    try:
        r = await _get_client()
        if r:
            await r.set(_redis_key(jti), "1", ex=ttl)
    except Exception as exc:
        logger.warning("token_denylist.deny_jti failed: %s", exc)


async def is_jti_denied(jti: str) -> bool:
    """Return True if jti is in denylist. Fails open (returns False) if Redis unavailable."""
    try:
        r = await _get_client()
        if r is None:
            return False
        return bool(await r.exists(_redis_key(jti)))
    except Exception as exc:
        logger.warning("token_denylist.is_jti_denied failed (fail-open): %s", exc)
        return False


async def aclose_redis() -> None:
    """Close the singleton Redis client (call from FastAPI lifespan shutdown).

    Resets _client to None so the next call to _get_client() creates a fresh
    connection.  Safe to call even if _client was never initialised.
    """
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:
            logger.warning("token_denylist.aclose_redis failed: %s", exc)
        finally:
            _client = None
