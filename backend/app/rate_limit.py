"""
Shared SlowAPI rate-limiter instance.

Keeping the limiter in its own module prevents circular imports between
main.py (which registers the exception handler and app.state) and the
individual routers that need to apply @limiter.limit() decorators.

Storage backend: Redis (falls back to in-memory if Redis URL is not set,
which is useful for local dev / unit tests).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import legacy_settings

# Use the Redis URL from settings so rate-limit counters are shared across
# every Uvicorn worker process.  If the URL is missing we fall back to the
# default in-memory store (safe for single-worker / test environments).
_redis_url: str = legacy_settings.REDIS_URL or "redis://redis:6379/0"

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_redis_url,
    default_limits=["100/minute"],
)
