"""Server-Sent Events broadcaster backed by Redis pub/sub.

Using Redis as the message bus means events published from any container
(the RTSP worker, the queue worker, or the API itself) are received by the
API process where browsers are subscribed — solving the cross-container
silent-drop problem.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

_CHANNEL = "seraphim:sse"


class SSEBroadcaster:
    """Publish and subscribe to SSE events via Redis pub/sub."""

    def __init__(self):
        self._redis = None

    def _get_redis_url(self) -> str:
        from app.config import dynamic_settings, legacy_settings
        url = dynamic_settings.get_redis_url()
        return url or legacy_settings.REDIS_URL or "redis://redis:6379/0"

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    self._get_redis_url(),
                    decode_responses=True,
                )
            except Exception as exc:
                logger.warning("SSEBroadcaster: could not connect to Redis: %s", exc)
        return self._redis

    async def publish(self, data: str) -> None:
        """Publish a message to all SSE subscribers across all containers."""
        try:
            r = await self._get_redis()
            if r:
                await r.publish(_CHANNEL, data)
        except Exception as exc:
            logger.warning("SSEBroadcaster.publish failed: %s", exc)

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """Yield messages as they arrive via Redis pub/sub."""
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(self._get_redis_url(), decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(_CHANNEL)
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        yield message["data"]
            finally:
                await pubsub.unsubscribe(_CHANNEL)
                await pubsub.close()
                await r.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("SSEBroadcaster.subscribe error: %s", exc)
            return


broadcaster = SSEBroadcaster()
