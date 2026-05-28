from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import dynamic_settings, legacy_settings
from app.database import engine
from app.dependencies import check_setup_complete, get_current_user
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.rate_limit import limiter
from app.routers import (
    attendance,
    audit,
    auth,
    cameras,
    events,
    health,
    leaderboard,
    logs,
    members,
    pit,
    settings as settings_router,
    setup,
    tasks,
    uploads as uploads_router,
)
from app.sse import broadcaster


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.connect() as conn:
            pass
        # Initialize dynamic settings from database
        from app.database import async_session
        from app.config import dynamic_settings
        async with async_session() as db:
            await dynamic_settings.initialize(db)
    except Exception:
        pass
    yield
    await engine.dispose()


app = FastAPI(
    title="Project Seraphim API",
    version="0.1.0",
    lifespan=lifespan,
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


@app.get("/tasks/feed")
async def task_feed(current_user=Depends(get_current_user)):
    async def event_generator():
        async for data in broadcaster.subscribe():
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
