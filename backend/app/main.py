from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import dynamic_settings, legacy_settings
from app.database import engine
from app.dependencies import check_setup_complete
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.rate_limit import limiter
from app.routers import (
    analytics,
    attendance,
    audit,
    auth,
    cameras,
    custom_fields,
    events,
    health,
    leaderboard,
    logs,
    members,
    pit,
    settings as settings_router,
    setup,
    storage as storage_router,
    tasks,
    uploads as uploads_router,
)
from app.sse import broadcaster


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.connect():
            pass
        # Initialize dynamic settings from database
        from app.database import async_session
        from app.config import dynamic_settings
        async with async_session() as db:
            await dynamic_settings.initialize(db)

        # Fail-fast: if setup is complete the JWT secret must be present and strong
        if dynamic_settings.is_setup_complete():
            secret = dynamic_settings.get_jwt_secret()
            if not secret or len(secret) < 32:
                raise RuntimeError(
                    "JWT secret is missing or too short (< 32 chars). "
                    "Set a valid jwt_secret via admin settings."
                )
    except RuntimeError:
        raise
    except Exception:
        # Pre-setup boot is allowed (no DB/settings yet), but log so a real
        # DB/settings-init failure is visible rather than silently swallowed.
        import logging
        logging.getLogger(__name__).warning(
            "Startup settings init failed (continuing — expected only before setup).",
            exc_info=True,
        )
    yield
    from app.utils.token_denylist import aclose_redis
    await aclose_redis()
    await engine.dispose()


# Disable interactive API docs in production to avoid exposing the full API surface.
_is_prod = legacy_settings.ENVIRONMENT == "production"
app = FastAPI(
    title="Project Seraphim API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if _is_prod else "/docs",
    redoc_url=None if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)

# ── Rate limiting ──────────────────────────────────────────────────────────────
# Attach the shared limiter so SlowAPI can find it on every request, and
# register the 429 handler so clients get a clean JSON error response.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# ──────────────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[legacy_settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SecurityHeadersMiddleware)

# Setup endpoints (no auth required, setup-complete check disabled)
app.include_router(setup.router)

# Auth endpoints (no setup check needed for login)
app.include_router(auth.router)

# All other endpoints require setup complete
app.include_router(health.router)
app.include_router(tasks.router, dependencies=[Depends(check_setup_complete)])
app.include_router(leaderboard.router, dependencies=[Depends(check_setup_complete)])
app.include_router(logs.router, dependencies=[Depends(check_setup_complete)])
app.include_router(settings_router.router, dependencies=[Depends(check_setup_complete)])
app.include_router(cameras.router, dependencies=[Depends(check_setup_complete)])
app.include_router(events.router, dependencies=[Depends(check_setup_complete)])
app.include_router(members.router, dependencies=[Depends(check_setup_complete)])
app.include_router(pit.router, dependencies=[Depends(check_setup_complete)])
app.include_router(attendance.router, dependencies=[Depends(check_setup_complete)])
app.include_router(audit.router, dependencies=[Depends(check_setup_complete)])
app.include_router(uploads_router.router, dependencies=[Depends(check_setup_complete)])
app.include_router(analytics.router, dependencies=[Depends(check_setup_complete)])
app.include_router(custom_fields.router, dependencies=[Depends(check_setup_complete)])
app.include_router(storage_router.router)


@app.get("/tasks/feed")
async def task_feed(request: Request, _t: str | None = None):
    """SSE endpoint. Authenticates via Bearer token, query param, or HttpOnly refresh cookie."""
    from app.utils.auth import verify_token

    secret = dynamic_settings.get_jwt_secret()
    payload = None

    # 1. Bearer header — access tokens only (block type="refresh", S1/Design B)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = verify_token(auth_header.split(" ", 1)[1], secret, reject_type="refresh")

    # 2. Query-string token (used by EventSource which can't set headers) — access tokens only
    if payload is None and _t:
        payload = verify_token(_t, secret, reject_type="refresh")

    # 3. HttpOnly refresh cookie (same-origin, browser EventSource fallback) — any valid token
    if payload is None:
        refresh_tok = request.cookies.get("refresh_token")
        if refresh_tok:
            payload = verify_token(refresh_tok, secret)

    if payload is None or not all(k in payload for k in ("sub", "email", "role")):
        from fastapi.responses import Response as _Resp
        return _Resp(status_code=401)

    async def event_generator():
        import asyncio as _asyncio
        from app.config import dynamic_settings as _ds

        heartbeat = _ds.get_int("sse_heartbeat_seconds", 15)
        # Sentinel placed by the feeder when broadcaster.subscribe() exits so the
        # outer loop detects the end-of-stream rather than stalling on keepalives.
        _EOF = object()
        queue: _asyncio.Queue = _asyncio.Queue()

        async def _feeder():
            # broadcaster.subscribe() owns its own pubsub lifecycle (unsubscribe +
            # close in its finally block), so we just cancel this task to clean up.
            _cancelled = False
            try:
                async for message in broadcaster.subscribe():
                    await queue.put(message)
            except _asyncio.CancelledError:
                _cancelled = True
                raise
            finally:
                # Only signal EOF on normal exit or Redis error — not on cancellation.
                # When cancelled, the outer generator is already tearing down and will
                # never read from the queue again.
                if not _cancelled:
                    await queue.put(_EOF)

        feeder = _asyncio.ensure_future(_feeder())
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await _asyncio.wait_for(
                        queue.get(), timeout=heartbeat
                    )
                    if data is _EOF:
                        # Broadcaster exited (Redis error or clean shutdown); stop.
                        break
                    yield f"data: {data}\n\n"
                except _asyncio.TimeoutError:
                    # SSE comment frame — browsers ignore it; keeps the Cloudflare
                    # named-tunnel origin connection alive (100s idle timeout).
                    yield ": keepalive\n\n"
        except _asyncio.CancelledError:
            # Re-raise: Starlette relies on CancelledError propagation to detect
            # client disconnect and stop the generator.
            raise
        finally:
            feeder.cancel()
            await _asyncio.gather(feeder, return_exceptions=True)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
