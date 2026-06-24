import json
import os
from pathlib import Path

import httpx
import redis.asyncio as redis_lib
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import get_db, engine
from app.models import AdminSetting, Camera, User
from app.schemas import (
    SetupRequest,
    SetupStatusResponse,
    ConnectionTestRequest,
    ConnectionTestResponse,
    ServiceTestRequest,
    ServiceTestResponse,
)
from app.utils.auth import hash_password

router = APIRouter(prefix="/setup", tags=["setup"])

BOOTSTRAP_PATH = Path(os.environ.get("BOOTSTRAP_CONFIG_PATH", "/app/config/bootstrap.json"))


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status():
    """Check if initial setup has been completed."""
    setup_complete = BOOTSTRAP_PATH.exists()
    return SetupStatusResponse(setup_complete=setup_complete)


@router.post("/test-connection", response_model=ConnectionTestResponse)
async def test_connection(req: ConnectionTestRequest):
    """Test Postgres and Redis connections using provided URLs."""
    if BOOTSTRAP_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Setup already completed. This endpoint is permanently locked.",
        )

    db_ok = False
    db_msg = ""
    redis_ok = False
    redis_msg = ""

    if req.database_url:
        try:
            test_engine = create_async_engine(req.database_url, future=True)
            async with test_engine.connect() as conn:
                await conn.execute(select(1))
            db_ok = True
            db_msg = "Connected successfully"
            await test_engine.dispose()
        except Exception:
            db_msg = "Connection failed. Check credentials and host."
    else:
        db_msg = "No URL provided"

    if req.redis_url:
        try:
            r = redis_lib.from_url(req.redis_url)
            await r.ping()
            redis_ok = True
            redis_msg = "Connected successfully"
            await r.close()
        except Exception:
            redis_msg = "Connection failed. Check credentials and host."
    else:
        redis_msg = "No URL provided"

    return ConnectionTestResponse(
        database_ok=db_ok,
        database_message=db_msg,
        redis_ok=redis_ok,
        redis_message=redis_msg,
    )


@router.post("/test-services", response_model=ServiceTestResponse)
async def test_services(req: ServiceTestRequest):
    """Test Compreface and CiviCRM connectivity."""
    if BOOTSTRAP_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Setup already completed. This endpoint is permanently locked.",
        )

    compreface_ok = False
    compreface_msg = ""

    if req.compreface_url:
        try:
            # Health probe only — /api/v1/health requires no API key.
            # The compreface_detect_api_key / compreface_recognize_api_key fields on
            # ServiceTestRequest are accepted but intentionally unused here; key
            # correctness is validated at first inference time, not during setup.
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{req.compreface_url}/api/v1/health")
                if resp.status_code == 200:
                    compreface_ok = True
                    compreface_msg = "Connected successfully"
                else:
                    compreface_msg = "Service returned an unexpected status"
        except Exception:
            compreface_msg = "Connection failed. Check URL and network."
    else:
        compreface_msg = "No URL provided"

    return ServiceTestResponse(
        compreface_ok=compreface_ok,
        compreface_message=compreface_msg,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_setup(
    req: SetupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Initial setup wizard. Locks permanently after first success."""
    if BOOTSTRAP_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Setup already completed. This endpoint is permanently locked.",
        )

    # Validate database connection
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Database connection failed. Check DATABASE_URL.",
        )

    # Pydantic schema already validates password strength (12+ chars, uppercase,
    # lowercase, number, special character). This block is a fallback defense.
    if len(req.admin_password) < 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Admin password must be at least 12 characters",
        )

    # Write bootstrap config
    BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = {"DATABASE_URL": req.database_url}
    BOOTSTRAP_PATH.write_text(json.dumps(bootstrap, indent=2))

    # Create admin_settings
    settings_data = {
        "database_url": req.database_url,
        "redis_url": req.redis_url,
        "compreface_url": req.compreface_url,
        # Legacy single key (kept for backward-compat fallback in ComprefaceClient)
        "compreface_api_key": req.compreface_api_key,
        # Per-service keys (Detection / Recognition).  Empty string if not provided;
        # ComprefaceClient falls back to compreface_api_key when either is blank.
        "compreface_detect_api_key": req.compreface_detect_api_key or "",
        "compreface_recognize_api_key": req.compreface_recognize_api_key or "",
        "jwt_secret": req.jwt_secret or os.urandom(32).hex(),
        "setup_complete": True,
        "similarity_threshold_high": 0.98,
        "similarity_threshold_medium": 0.91,
        "queue_hard_limit": 500,
        "queue_resume_limit": 400,
        "dedup_window_seconds": 30,
        "task_expiry_days": 31,
        "face_retention_days": 90,
        "allowed_domain": "lightnc.org",
        "enable_google_oauth": False,
        "access_token_expire_minutes": 15,
        "refresh_token_expire_days": 7,
        "active_event_id": None,
    }

    for key, value in settings_data.items():
        setting = AdminSetting(
            key=key,
            value={"value": value},
            category="general",
            sensitive=key in (
                "database_url",
                "redis_url",
                "compreface_api_key",
                "compreface_detect_api_key",
                "compreface_recognize_api_key",
                "jwt_secret",
            ),
            requires_restart=key in ("database_url", "redis_url"),
        )
        db.add(setting)

    # Create admin user
    admin = User(
        email=req.admin_email.lower(),
        password_hash=hash_password(req.admin_password),
        auth_provider="local",
        role="admin",
        is_active=True,
    )
    db.add(admin)

    # Create initial cameras if provided.  req.cameras is List[CameraCreateRequest]
    # so rtsp:// validation and field defaults have already been enforced by Pydantic.
    for cam in req.cameras:
        camera = Camera(
            name=cam.name,
            rtsp_url=cam.rtsp_url,
            zone_label=cam.zone_label,
            fps=cam.fps,
            enable_health_check=cam.enable_health_check,
            status="streaming",
        )
        db.add(camera)

    await db.commit()

    # Reload in-memory settings so the app is immediately usable without a restart
    from app.config import dynamic_settings
    await dynamic_settings.reload(db)

    return {
        "message": "Setup completed successfully",
        "admin_email": req.admin_email,
        "setup_locked": True,
    }
