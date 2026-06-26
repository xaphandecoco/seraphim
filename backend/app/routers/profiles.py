"""Profiles router — S13 form-template CRUD + internal field renderer.

Prefix : /profiles   (registered in main.py WITH check_setup_complete)
Auth   : GET/POST list needs require_volunteer; mutations need require_admin
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import Profile
from app.schemas import (
    PaginatedProfileResponse,
    ProfileCreate,
    ProfileRenderResponse,
    ProfileResponse,
    ProfileUpdate,
)
from app.services.profile_service import (
    check_one_public_profile,
    render_profile,
    validate_profile_fields,
)

router = APIRouter(prefix="/profiles", tags=["profiles"])


class _RenderRequest(BaseModel):
    """Optional body for POST /profiles/{id}/render."""

    prefill: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedProfileResponse)
async def list_profiles(
    entity: Optional[str] = Query(None),
    is_public: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> PaginatedProfileResponse:
    """Return paginated profiles, optionally filtered by entity and is_public."""
    conditions = []
    if entity is not None:
        conditions.append(Profile.entity == entity)
    if is_public is not None:
        conditions.append(Profile.is_public.is_(is_public))

    count_stmt = select(func.count()).select_from(Profile)
    items_stmt = select(Profile).order_by(Profile.name)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        items_stmt = items_stmt.where(*conditions)

    total = (await db.execute(count_stmt)).scalar_one()
    offset = (page - 1) * page_size
    rows = (
        await db.execute(items_stmt.offset(offset).limit(page_size))
    ).scalars().all()

    return PaginatedProfileResponse(
        items=[ProfileResponse.model_validate(p) for p in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post("/", response_model=ProfileResponse, status_code=201)
async def create_profile(
    body: ProfileCreate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> ProfileResponse:
    """Create a profile. Validates custom_field_name values and one-public rule."""
    await validate_profile_fields(body.fields, db)

    if body.is_public:
        await check_one_public_profile(db)

    dup = (
        await db.execute(select(Profile).where(Profile.name == body.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A profile named '{body.name}' already exists.",
        )

    profile = Profile(
        name=body.name,
        entity=body.entity,
        fields=[f.model_dump() for f in body.fields],
        settings=body.settings.model_dump(),
        is_public=body.is_public,
        owner_id=int(_user["sub"]),
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return ProfileResponse.model_validate(profile)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get("/{profile_id}", response_model=ProfileResponse)
async def get_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> ProfileResponse:
    """Return a single profile by id."""
    profile = (
        await db.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found",
        )
    return ProfileResponse.model_validate(profile)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


@router.put("/{profile_id}", response_model=ProfileResponse)
async def update_profile(
    profile_id: int,
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> ProfileResponse:
    """Partial-update a profile. Re-validates one-public rule when is_public changes."""
    profile = (
        await db.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found",
        )

    if body.name is not None:
        dup = (
            await db.execute(
                select(Profile).where(
                    Profile.name == body.name, Profile.id != profile_id
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"A profile named '{body.name}' already exists.",
            )
        profile.name = body.name

    if body.fields is not None:
        await validate_profile_fields(body.fields, db)
        profile.fields = [f.model_dump() for f in body.fields]

    if body.settings is not None:
        profile.settings = body.settings.model_dump()

    if body.is_public is not None:
        if body.is_public and not profile.is_public:
            await check_one_public_profile(db, exclude_id=profile_id)
        profile.is_public = body.is_public

    await db.commit()
    await db.refresh(profile)
    return ProfileResponse.model_validate(profile)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> None:
    """Delete a profile. Returns 409 when it is the only public profile."""
    profile = (
        await db.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found",
        )

    if profile.is_public:
        public_count = (
            await db.execute(
                select(func.count())
                .select_from(Profile)
                .where(Profile.is_public.is_(True))
            )
        ).scalar_one()
        if public_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot delete the only public profile. "
                    "Set is_public=false first."
                ),
            )

    await db.delete(profile)
    await db.commit()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


@router.post("/{profile_id}/render", response_model=ProfileRenderResponse)
async def render_profile_endpoint(
    profile_id: int,
    body: Optional[_RenderRequest] = None,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> ProfileRenderResponse:
    """Resolve profile schema for the frontend form renderer.

    Accepts an optional JSON body {"prefill": {"field_id": "value", ...}}.
    """
    prefill = (body.prefill if body else None) or {}
    return await render_profile(profile_id, prefill, db)
