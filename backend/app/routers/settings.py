import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import get_db
from app.dependencies import require_admin
from app.models import AdminSetting, User
from app.schemas import (
    ConfigChecklistResponse,
    ConnectionTestResult,
    JobRunItem,
    JobRunsResponse,
    SettingItem,
    SettingKeyUpdateRequest,
    SettingKeyValueResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SystemStatusResponse,
)
from app.services import audit as audit_svc

router = APIRouter(prefix="/settings", tags=["settings"])

BOOTSTRAP_PATH = Path(os.environ.get("BOOTSTRAP_CONFIG_PATH", "/app/config/bootstrap.json"))

# Keys that are FULLY IMMUTABLE via the settings API (created during /setup only).
# ANY write — set or blank — is rejected with 403.
_IMMUTABLE_KEYS = {"jwt_secret", "database_url", "redis_url"}

# Keys that must not be blanked but can otherwise be updated.
_NO_BLANK_KEYS = {"setup_complete"}

# Combined for backward-compat helpers.
_PROTECTED_KEYS = _IMMUTABLE_KEYS | _NO_BLANK_KEYS

# Keys whose values are treated as sensitive (masked on read, encrypted at rest).
_SENSITIVE_KEYS = {
    "compreface_api_key",
    "compreface_detect_api_key",
    "compreface_recognize_api_key",
    "google_client_secret",
}


def _mask_sensitive(value: dict) -> dict:
    """Mask sensitive values for GET responses."""
    result = value.copy()
    if "value" in result and isinstance(result["value"], str) and len(result["value"]) > 0:
        result["value"] = "********"
    return result


# ---------------------------------------------------------------------------
# Existing bulk GET / PUT
# ---------------------------------------------------------------------------


@router.get("", response_model=SettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Read all admin settings. Sensitive values are masked."""
    result = await db.execute(select(AdminSetting))
    settings = result.scalars().all()

    items = []
    for s in settings:
        value = s.value
        if s.sensitive:
            value = _mask_sensitive(value)
        items.append(SettingItem(
            key=s.key,
            value=value,
            category=s.category,
            description=s.description,
            requires_restart=s.requires_restart,
            sensitive=s.sensitive,
        ))

    return SettingsResponse(settings=items)


@router.put("", response_model=SettingsResponse)
async def update_settings(
    req: SettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Update admin settings. Reloads in-memory cache."""
    current_user = await db.get(User, int(user["sub"]))

    for key, value in req.settings.items():
        # Fully immutable keys — reject ANY write (set or blank) via the API
        if key in _IMMUTABLE_KEYS:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Setting '{key}' is immutable and cannot be changed via the settings API",
            )

        # Reject attempts to blank write-protected keys
        if key in _NO_BLANK_KEYS and (value is None or value == "" or value == "********"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot blank protected setting '{key}'",
            )

        result = await db.execute(select(AdminSetting).where(AdminSetting.key == key))
        setting = result.scalar_one_or_none()

        if setting:
            # Preserve sensitive values if masked (user didn't change them)
            if setting.sensitive and value == "********":
                continue
            setting.value = {"value": value}
            setting.updated_by = current_user.id if current_user else None
        else:
            setting = AdminSetting(
                key=key,
                value={"value": value},
                category="general",
                updated_by=current_user.id if current_user else None,
            )
            db.add(setting)

    await db.commit()

    # Reload in-memory cache
    await dynamic_settings.reload(db)

    # Return updated settings
    result = await db.execute(select(AdminSetting))
    settings = result.scalars().all()
    items = []
    for s in settings:
        value = s.value
        if s.sensitive:
            value = _mask_sensitive(value)
        items.append(SettingItem(
            key=s.key,
            value=value,
            category=s.category,
            description=s.description,
            requires_restart=s.requires_restart,
            sensitive=s.sensitive,
        ))

    return SettingsResponse(settings=items)


@router.post("/safe-mode")
async def toggle_safe_mode(
    enabled: bool,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Toggle safe mode. When enabled, pauses all recognition."""
    result = await db.execute(select(AdminSetting).where(AdminSetting.key == "safe_mode"))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = {"value": enabled}
    else:
        setting = AdminSetting(
            key="safe_mode",
            value={"value": enabled},
            category="safety",
            description="Pause all recognition and auto-logging",
        )
        db.add(setting)
    await db.commit()
    await dynamic_settings.reload(db)
    return {"safe_mode": enabled, "message": f"Safe mode {'enabled' if enabled else 'disabled'}"}


# ---------------------------------------------------------------------------
# S16: PUT /settings/{key} — single-key update with Fernet encryption + audit
# ---------------------------------------------------------------------------


@router.put("/{key}", response_model=SettingKeyValueResponse)
async def update_setting_by_key(
    key: str,
    req: SettingKeyUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Update a single admin setting by key.

    - Sensitive values are Fernet-encrypted at rest.
    - Submitting an empty masked value (\"********\") is a no-op.
    - Unknown key → 404.
    - Protected keys cannot be blanked.
    """
    from app.services.settings_service import (
        MASKED_SENTINEL,
        encrypt_value,
        read_setting_plaintext,
    )

    value = req.value

    # Fully immutable keys — reject ANY write via the settings API
    if key in _IMMUTABLE_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Setting '{key}' is immutable and cannot be changed via the settings API",
        )

    # Reject blanking write-protected keys
    if key in _NO_BLANK_KEYS and (value is None or value == "" or value == MASKED_SENTINEL):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot blank protected setting '{key}'",
        )

    # Load existing row
    result = await db.execute(select(AdminSetting).where(AdminSetting.key == key))
    setting = result.scalar_one_or_none()

    if setting is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting '{key}' not found",
        )

    # No-op if masked sentinel re-submitted
    if value == MASKED_SENTINEL:
        plaintext = read_setting_plaintext(setting)
        return SettingKeyValueResponse(
            key=key,
            value=MASKED_SENTINEL if setting.sensitive else plaintext,
        )

    # Audit log
    before_val = read_setting_plaintext(setting)
    current_user_id = int(user["sub"]) if user else None

    # Encrypt if sensitive — fail CLOSED: raise 500 rather than storing plaintext
    is_sensitive = key in _SENSITIVE_KEYS or setting.sensitive
    if is_sensitive and value is not None:
        try:
            stored_value = encrypt_value(value)
            new_value_dict = {"value": stored_value, "encrypted": True}
        except Exception as exc:
            logger.error(
                "settings: encryption failed for key '%s' — refusing to store plaintext: %s",
                key, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to encrypt sensitive setting value. Ensure jwt_secret is configured.",
            )
    else:
        new_value_dict = {"value": value}

    before_dict = {"value": "***" if is_sensitive else before_val}
    after_dict = {"value": "***" if is_sensitive else value}

    setting.value = new_value_dict
    setting.updated_by = current_user_id
    if is_sensitive:
        setting.sensitive = True

    await db.flush()

    await audit_svc.record(
        db,
        actor_id=current_user_id,
        action="setting.update",
        entity="admin_setting",
        entity_id=None,
        before={"key": key, **before_dict},
        after={"key": key, **after_dict},
    )
    await db.commit()
    await dynamic_settings.reload(db)

    return SettingKeyValueResponse(
        key=key,
        value=MASKED_SENTINEL if is_sensitive else value,
    )


# ---------------------------------------------------------------------------
# S16: GET /settings/jobs — paginated job_runs list
# ---------------------------------------------------------------------------


@router.get("/jobs", response_model=JobRunsResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    job_name: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Paginated list of job_runs rows, ordered started_at DESC."""
    from app.services.settings_service import list_job_runs

    items, total = await list_job_runs(db, page=page, page_size=page_size, job_name=job_name)

    return JobRunsResponse(
        items=[JobRunItem(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# S16: POST /settings/jobs/{job_name}/trigger — manual job trigger
# ---------------------------------------------------------------------------


@router.post("/jobs/{job_name}/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_job(
    job_name: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Manually trigger a registered job by name.

    Returns 404 if job_name is unknown, 409 if a 'running' row exists for it.
    """
    from sqlalchemy import select as _select
    from app.models import JobRun
    from app.services.scheduler import JOB_REGISTRY, _run_tracked_job

    if job_name not in JOB_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_name}' is not registered",
        )

    # Check for running
    running_check = await db.execute(
        _select(JobRun)
        .where(JobRun.job_name == job_name)
        .where(JobRun.status == "running")
        .limit(1)
    )
    if running_check.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job '{job_name}' is already running",
        )

    coro_factory = JOB_REGISTRY[job_name]
    background_tasks.add_task(_run_tracked_job, job_name, coro_factory)

    return {"detail": f"Job '{job_name}' triggered"}


# ---------------------------------------------------------------------------
# S16: GET /settings/system-status
# ---------------------------------------------------------------------------


@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """System health overview: postgres, redis, compreface, queue depth, job states."""
    from app.services.settings_service import get_system_status as _get_system_status

    data = await _get_system_status(db)
    return SystemStatusResponse(**data)


# ---------------------------------------------------------------------------
# S16: GET /settings/config-checklist
# ---------------------------------------------------------------------------


@router.get("/config-checklist", response_model=ConfigChecklistResponse)
async def get_config_checklist(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Return the configuration checklist with required / recommended status."""
    from app.services.settings_service import get_config_checklist as _get_checklist

    data = await _get_checklist(db)
    return ConfigChecklistResponse(**data)


# ---------------------------------------------------------------------------
# S16: POST /settings/test/{service} — connection-test stubs
# ---------------------------------------------------------------------------


@router.post("/test/{service}", response_model=ConnectionTestResult)
async def test_connection(
    service: str,
    user=Depends(require_admin),
):
    """Stub connection-test endpoints for google-chat, zoom, gmail (S18 pending)."""
    _KNOWN_SERVICES = {"google-chat", "zoom", "gmail"}
    if service not in _KNOWN_SERVICES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown service '{service}'",
        )
    # S18 not yet built — all return not-configured gracefully
    return ConnectionTestResult(ok=False, detail="not configured")
