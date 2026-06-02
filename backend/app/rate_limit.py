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
# every Uvicorn worker process.  If REDIS_URL is not configured (dev / test /
# single-process), fall back to slowapi's in-memory store rather than an
# unreachable Redis host. Production compose always sets REDIS_URL.
_redis_url: str = legacy_settings.REDIS_URL or "memory://"

# swallow_errors=True → if the Redis storage backend is unreachable, slowapi fails
# OPEN (allows the request) instead of raising 500s. This keeps login/auth working
# during a Redis outage, and lets the test suite run without a Redis server.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_redis_url,
    default_limits=["100/minute"],
    swallow_errors=True,
)
