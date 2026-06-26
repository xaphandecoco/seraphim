"""Import mapping preset CRUD service (S10).

Public API
----------
list_presets(entity, db, current_user)           -> list[ImportMappingPreset]
create_preset(data, db, current_user)            -> ImportMappingPreset
update_preset(preset_id, data, db, current_user) -> ImportMappingPreset
delete_preset(preset_id, db, current_user)       -> None

Visibility rules
----------------
- is_shared=True  → visible to ALL authenticated users.
- is_shared=False → visible only to the owner.

Auth rules
----------
- Non-owner non-admin update/delete → 403.
- (entity, name) collision → 409.

Circular-import rule (CN-25): must NOT import from any router module.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportMappingPreset


async def list_presets(
    entity: Optional[str],
    db: AsyncSession,
    current_user: dict,
) -> list[ImportMappingPreset]:
    """Return presets visible to *current_user* (shared + own).

    Optionally filter by *entity*.
    """
    user_id = int(current_user["sub"])
    stmt = (
        select(ImportMappingPreset)
        .where(
            or_(
                ImportMappingPreset.is_shared.is_(True),
                ImportMappingPreset.owner_id == user_id,
            )
        )
        .order_by(ImportMappingPreset.created_at.desc())
    )
    if entity:
        stmt = stmt.where(ImportMappingPreset.entity == entity)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_preset(
    data: Any,  # ImportPresetIn schema instance
    db: AsyncSession,
    current_user: dict,
) -> ImportMappingPreset:
    """Create a new mapping preset.

    Raises HTTPException 409 on (entity, name) collision.
    """
    user_id = int(current_user["sub"])
    preset = ImportMappingPreset(
        owner_id=user_id,
        entity=data.entity,
        name=data.name,
        column_map=data.column_map or {},
        options=data.options or {},
        is_shared=data.is_shared if data.is_shared is not None else False,
    )
    db.add(preset)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A preset named {data.name!r} already exists for entity {data.entity!r}.",
        )
    await db.refresh(preset)
    return preset


async def update_preset(
    preset_id: int,
    data: Any,  # ImportPresetIn schema instance
    db: AsyncSession,
    current_user: dict,
) -> ImportMappingPreset:
    """Update a preset.

    Raises:
        404 if not found.
        403 if non-owner non-admin.
        409 on (entity, name) collision.
    """
    result = await db.execute(
        select(ImportMappingPreset).where(ImportMappingPreset.id == preset_id)
    )
    preset = result.scalar_one_or_none()
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    _assert_owner_or_admin(preset, current_user)

    preset.entity = data.entity
    preset.name = data.name
    preset.column_map = data.column_map or {}
    preset.options = data.options or {}
    preset.is_shared = data.is_shared if data.is_shared is not None else preset.is_shared

    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A preset named {data.name!r} already exists for entity {data.entity!r}.",
        )

    await db.refresh(preset)
    return preset


async def delete_preset(
    preset_id: int,
    db: AsyncSession,
    current_user: dict,
) -> None:
    """Delete a preset.

    Raises:
        404 if not found.
        403 if non-owner non-admin.
    """
    result = await db.execute(
        select(ImportMappingPreset).where(ImportMappingPreset.id == preset_id)
    )
    preset = result.scalar_one_or_none()
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    _assert_owner_or_admin(preset, current_user)
    await db.delete(preset)
    await db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_owner_or_admin(
    preset: ImportMappingPreset,
    current_user: dict,
) -> None:
    """Raise 403 if the current user is not the preset's owner or an admin."""
    user_id = int(current_user["sub"])
    if current_user.get("role") == "admin":
        return
    if preset.owner_id is None or preset.owner_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to modify this preset.",
        )
