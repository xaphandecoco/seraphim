"""Custom Fields CRUD router — S02.

Prefix: /custom-fields
Tag: custom-fields

All endpoints require setup-complete (injected by main.py include_router).

Auth rules (per spec §4.1 + NOTES):
  GET  /schema                        → require_volunteer
  GET  /groups, GET /defs             → require_volunteer
  POST /groups                        → require_admin
  PATCH/DELETE /groups/{id}           → require_admin
  POST /defs                          → require_admin
  PATCH/DELETE /defs/{id}             → require_admin
  POST /validate                      → require_volunteer

Immutability rules (per spec §4.3 + NOTES):
  group.name, group.entity — immutable after create (reject 422 in PATCH)
  def.name, def.data_type  — immutable after create (reject 422 in PATCH)

409 detail format: {"message": str, "affected_contacts": int}

Audit action names:
  custom_group.create / custom_group.update /
  custom_group.soft_delete / custom_group.hard_delete
  custom_field.create / custom_field.update /
  custom_field.soft_delete / custom_field.hard_delete
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import CustomFieldDef, CustomFieldGroup
from app.schemas import (
    CustomDataValidateRequest,
    CustomDataValidateResponse,
    CustomFieldDefCreate,
    CustomFieldDefPatchResponse,
    CustomFieldDefResponse,
    CustomFieldDefUpdate,
    CustomFieldGroupCreate,
    CustomFieldGroupResponse,
    CustomFieldGroupUpdate,
    CustomFieldSchemaResponse,
)
from app.services import audit as audit_svc
from app.services import custom_fields as cf_svc

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


# ---------------------------------------------------------------------------
# Schema endpoint (volunteer+admin)
# ---------------------------------------------------------------------------


@router.get("/schema", response_model=CustomFieldSchemaResponse)
async def get_schema(
    entity: str = Query("contact"),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> CustomFieldSchemaResponse:
    """Return active groups and their active fields for an entity, weight-ordered."""
    # get_active_schema returns list[dict[str, Any]] with keys "group" and "defs"
    schema_entries = await cf_svc.get_active_schema(db, entity)

    group_responses: list[CustomFieldGroupResponse] = []
    for entry in schema_entries:
        g = entry["group"]
        defs = entry["defs"]
        group_responses.append(
            CustomFieldGroupResponse(
                id=g.id,
                name=g.name,
                label=g.label,
                entity=g.entity,
                weight=g.weight,
                is_active=g.is_active,
                fields=[_def_to_response(d) for d in defs],
            )
        )

    return CustomFieldSchemaResponse(entity=entity, groups=group_responses)


# ---------------------------------------------------------------------------
# Group endpoints (admin only)
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=list[CustomFieldGroupResponse])
async def list_groups(
    entity: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> list[CustomFieldGroupResponse]:
    """List groups with their nested field defs."""
    stmt = select(CustomFieldGroup).order_by(CustomFieldGroup.weight, CustomFieldGroup.id)
    if entity is not None:
        stmt = stmt.where(CustomFieldGroup.entity == entity)
    if not include_inactive:
        stmt = stmt.where(CustomFieldGroup.is_active.is_(True))
    result = await db.execute(stmt)
    groups = list(result.scalars().all())

    # For each group, fetch active defs (use include_inactive flag consistently)
    group_responses: list[CustomFieldGroupResponse] = []
    for g in groups:
        def_stmt = (
            select(CustomFieldDef)
            .where(CustomFieldDef.group_id == g.id)
            .order_by(CustomFieldDef.weight, CustomFieldDef.id)
        )
        if not include_inactive:
            def_stmt = def_stmt.where(CustomFieldDef.is_active.is_(True))
        defs_result = await db.execute(def_stmt)
        defs = list(defs_result.scalars().all())
        group_responses.append(
            CustomFieldGroupResponse(
                id=g.id,
                name=g.name,
                label=g.label,
                entity=g.entity,
                weight=g.weight,
                is_active=g.is_active,
                fields=[_def_to_response(d) for d in defs],
            )
        )

    return group_responses


@router.post("/groups", response_model=CustomFieldGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: CustomFieldGroupCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> CustomFieldGroupResponse:
    """Create a new custom field group. name+entity must be unique."""
    # Check for name+entity collision
    existing = await db.execute(
        select(CustomFieldGroup).where(
            CustomFieldGroup.entity == body.entity,
            CustomFieldGroup.name == body.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Group '{body.name}' already exists for entity '{body.entity}'.",
                "affected_contacts": 0,
            },
        )

    group = CustomFieldGroup(
        name=body.name,
        label=body.label,
        entity=body.entity,
        weight=body.weight,
        is_active=True,
    )
    db.add(group)
    await db.flush()  # Get the id before audit

    actor_id = int(user["sub"])
    await audit_svc.record(
        db,
        actor_id=actor_id,
        action="custom_group.create",
        entity="custom_field_group",
        entity_id=group.id,
        before=None,
        after={"name": group.name, "label": group.label, "entity": group.entity, "weight": group.weight},
    )
    await db.commit()
    await db.refresh(group)

    return CustomFieldGroupResponse(
        id=group.id,
        name=group.name,
        label=group.label,
        entity=group.entity,
        weight=group.weight,
        is_active=group.is_active,
        fields=[],
    )


@router.patch("/groups/{group_id}", response_model=CustomFieldGroupResponse)
async def update_group(
    group_id: int,
    body: CustomFieldGroupUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> CustomFieldGroupResponse:
    """Partial update of group label, weight, or is_active. name and entity are immutable."""
    result = await db.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    before_snapshot = {
        "label": group.label,
        "weight": group.weight,
        "is_active": group.is_active,
    }

    if body.label is not None:
        group.label = body.label
    if body.weight is not None:
        group.weight = body.weight
    if body.is_active is not None:
        group.is_active = body.is_active

    await db.flush()
    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="custom_group.update",
        entity="custom_field_group",
        entity_id=group.id,
        before=before_snapshot,
        after={"label": group.label, "weight": group.weight, "is_active": group.is_active},
    )
    await db.commit()
    await db.refresh(group)

    # Fetch active defs for response
    defs_result = await db.execute(
        select(CustomFieldDef)
        .where(CustomFieldDef.group_id == group.id, CustomFieldDef.is_active.is_(True))
        .order_by(CustomFieldDef.weight, CustomFieldDef.id)
    )
    defs = list(defs_result.scalars().all())

    return CustomFieldGroupResponse(
        id=group.id,
        name=group.name,
        label=group.label,
        entity=group.entity,
        weight=group.weight,
        is_active=group.is_active,
        fields=[_def_to_response(d) for d in defs],
    )


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    hard: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> Response:
    """Soft-delete (default) or hard-delete a group.

    Hard-delete is blocked if any contact holds data for any child field.
    Soft-delete sets is_active=False on the group and all child defs.
    """
    result = await db.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    actor_id = int(user["sub"])

    if hard:
        # Count contacts with data in any child field
        defs_result = await db.execute(
            select(CustomFieldDef).where(CustomFieldDef.group_id == group_id)
        )
        child_defs = list(defs_result.scalars().all())
        total_affected = 0
        for d in child_defs:
            total_affected += await cf_svc.count_contacts_with_field_data(db, d.name)

        if total_affected > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Contacts have data in this group's fields.", "affected_contacts": total_affected},
            )

        # Hard delete — cascade removes defs
        await db.delete(group)
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="custom_group.hard_delete",
            entity="custom_field_group",
            entity_id=group_id,
            before={"name": group.name},
            after=None,
        )
    else:
        # Soft delete: deactivate group and all child defs
        group.is_active = False
        child_stmt = select(CustomFieldDef).where(CustomFieldDef.group_id == group_id)
        child_result = await db.execute(child_stmt)
        for d in child_result.scalars().all():
            d.is_active = False

        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="custom_group.soft_delete",
            entity="custom_field_group",
            entity_id=group_id,
            before={"name": group.name, "is_active": True},
            after={"name": group.name, "is_active": False},
        )

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Def endpoints (admin only)
# ---------------------------------------------------------------------------


@router.get("/defs", response_model=list[CustomFieldDefResponse])
async def list_defs(
    group_id: Optional[int] = Query(None),
    entity: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> list[CustomFieldDefResponse]:
    """List field defs, optionally filtered by group_id or entity."""
    stmt = (
        select(CustomFieldDef)
        .join(CustomFieldGroup, CustomFieldDef.group_id == CustomFieldGroup.id)
        .order_by(CustomFieldDef.weight, CustomFieldDef.id)
    )
    if group_id is not None:
        stmt = stmt.where(CustomFieldDef.group_id == group_id)
    if entity is not None:
        stmt = stmt.where(CustomFieldGroup.entity == entity)
    if not include_inactive:
        stmt = stmt.where(CustomFieldDef.is_active.is_(True))

    result = await db.execute(stmt)
    defs = list(result.scalars().all())
    return [_def_to_response(d) for d in defs]


@router.post("/defs", response_model=CustomFieldDefResponse, status_code=status.HTTP_201_CREATED)
async def create_def(
    body: CustomFieldDefCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> CustomFieldDefResponse:
    """Create a new field def. name must be unique within the entity scope."""
    # Verify group exists
    group_result = await db.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.id == body.group_id)
    )
    group = group_result.scalar_one_or_none()
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Group {body.group_id} not found.",
        )

    # Enforce entity-scoped name uniqueness
    await cf_svc.assert_entity_field_name_unique(db, group.entity, body.name)

    # Force is_multi=True for multiselect data_type
    is_multi = body.is_multi or (body.data_type == "multiselect")

    field_def = CustomFieldDef(
        group_id=body.group_id,
        name=body.name,
        label=body.label,
        data_type=body.data_type,
        options=[o.model_dump() for o in body.options],
        is_required=body.is_required,
        is_multi=is_multi,
        weight=body.weight,
        is_active=True,
        help_text=body.help_text,
    )
    db.add(field_def)
    await db.flush()

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="custom_field.create",
        entity="custom_field_def",
        entity_id=field_def.id,
        before=None,
        after={"name": field_def.name, "data_type": field_def.data_type, "group_id": field_def.group_id},
    )
    await db.commit()
    await db.refresh(field_def)
    return _def_to_response(field_def)


@router.patch("/defs/{def_id}", response_model=CustomFieldDefPatchResponse)
async def update_def(
    def_id: int,
    body: CustomFieldDefUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> CustomFieldDefPatchResponse:
    """Partial update. name and data_type are immutable after create."""
    result = await db.execute(
        select(CustomFieldDef).where(CustomFieldDef.id == def_id)
    )
    field_def = result.scalar_one_or_none()
    if field_def is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    before_snapshot = {
        "label": field_def.label,
        "options": field_def.options,
        "is_required": field_def.is_required,
        "is_multi": field_def.is_multi,
        "weight": field_def.weight,
        "is_active": field_def.is_active,
        "help_text": field_def.help_text,
    }

    # Compute affected_contacts for removed options
    affected_contacts = 0
    if body.options is not None:
        new_values = {o.value for o in body.options}
        old_values: set[str] = set()
        for opt in field_def.options or []:
            v = opt["value"] if isinstance(opt, dict) else opt.value
            old_values.add(v)
        removed_values = old_values - new_values
        if removed_values:
            affected_contacts = await cf_svc.count_contacts_with_field_data(db, field_def.name)

    if body.label is not None:
        field_def.label = body.label
    if body.options is not None:
        field_def.options = [o.model_dump() for o in body.options]
    if body.is_required is not None:
        field_def.is_required = body.is_required
    if body.is_multi is not None:
        field_def.is_multi = body.is_multi
    if body.weight is not None:
        field_def.weight = body.weight
    if body.is_active is not None:
        field_def.is_active = body.is_active
    if body.help_text is not None:
        field_def.help_text = body.help_text

    await db.flush()
    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="custom_field.update",
        entity="custom_field_def",
        entity_id=field_def.id,
        before=before_snapshot,
        after={
            "label": field_def.label,
            "options": field_def.options,
            "is_required": field_def.is_required,
            "is_multi": field_def.is_multi,
            "weight": field_def.weight,
            "is_active": field_def.is_active,
            "help_text": field_def.help_text,
        },
    )
    await db.commit()
    await db.refresh(field_def)

    return CustomFieldDefPatchResponse(
        id=field_def.id,
        group_id=field_def.group_id,
        name=field_def.name,
        label=field_def.label,
        data_type=field_def.data_type,
        options=_parse_options(field_def.options),
        is_required=field_def.is_required,
        is_multi=field_def.is_multi,
        weight=field_def.weight,
        is_active=field_def.is_active,
        help_text=field_def.help_text,
        affected_contacts=affected_contacts,
    )


@router.delete("/defs/{def_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_def(
    def_id: int,
    hard: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> Response:
    """Soft-delete (default) or hard-delete a field def."""
    result = await db.execute(
        select(CustomFieldDef).where(CustomFieldDef.id == def_id)
    )
    field_def = result.scalar_one_or_none()
    if field_def is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    actor_id = int(user["sub"])

    if hard:
        affected = await cf_svc.count_contacts_with_field_data(db, field_def.name)
        if affected > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Contacts have data for this field.", "affected_contacts": affected},
            )
        await db.delete(field_def)
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="custom_field.hard_delete",
            entity="custom_field_def",
            entity_id=def_id,
            before={"name": field_def.name},
            after=None,
        )
    else:
        field_def.is_active = False
        await db.flush()
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="custom_field.soft_delete",
            entity="custom_field_def",
            entity_id=def_id,
            before={"name": field_def.name, "is_active": True},
            after={"name": field_def.name, "is_active": False},
        )

    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Validate endpoint (volunteer+admin)
# ---------------------------------------------------------------------------


@router.post("/validate", response_model=CustomDataValidateResponse)
async def validate_custom_data(
    body: CustomDataValidateRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> CustomDataValidateResponse:
    """Stateless validate+coerce passthrough. Used by S06 dry-run and admin preview."""
    normalized = await cf_svc.validate_and_coerce(db, body.entity, body.custom_data)
    return CustomDataValidateResponse(custom_data=normalized, normalized=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_options(raw: list) -> list:
    """Convert stored options (list of dicts or OptionItem) to OptionItem list."""
    from app.schemas import OptionItem
    result = []
    for opt in (raw or []):
        if isinstance(opt, dict):
            result.append(OptionItem(value=opt["value"], label=opt["label"]))
        else:
            result.append(opt)
    return result


def _def_to_response(d: CustomFieldDef) -> CustomFieldDefResponse:
    return CustomFieldDefResponse(
        id=d.id,
        group_id=d.group_id,
        name=d.name,
        label=d.label,
        data_type=d.data_type,
        options=_parse_options(d.options),
        is_required=d.is_required,
        is_multi=d.is_multi,
        weight=d.weight,
        is_active=d.is_active,
        help_text=d.help_text,
    )
