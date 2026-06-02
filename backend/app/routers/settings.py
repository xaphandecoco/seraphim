import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.database import get_db
from app.dependencies import require_admin
from app.models import AdminSetting, User
from app.schemas import SettingsResponse, SettingsUpdateRequest, SettingItem

router = APIRouter(prefix="/settings", tags=["settings"])

BOOTSTRAP_PATH = Path(os.environ.get("BOOTSTRAP_CONFIG_PATH", "/app/config/bootstrap.json"))

# Keys that cannot be cleared/blanked via the settings API — blanking them would
# break authentication or make the app unbootable.
_PROTECTED_KEYS = {"jwt_secret", "database_url", "redis_url", "setup_complete"}


def _mask_sensitive(value: dict) -> dict:
    """Mask sensitive values for GET responses."""
    result = value.copy()
    if "value" in result and isinstance(result["value"], str) and len(result["value"]) > 0:
        result["value"] = "********"
    return result


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

    bootstrap_updated = False

    for key, value in req.settings.items():
        # Reject attempts to blank critical keys
        if key in _PROTECTED_KEYS and (value is None or value == "" or value == "********"):
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

        # Sync database_url changes to bootstrap file
        if key == "database_url" and isinstance(value, str):
            BOOTSTRAP_PATH.parent.mkdir(parents=True, exist_ok=True)
            BOOTSTRAP_PATH.write_text(json.dumps({"DATABASE_URL": value}, indent=2))
            bootstrap_updated = True

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
