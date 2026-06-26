"""Search and Groups routers — S09 Advanced Search, Saved Searches & Smart Groups.

Two routers exported from this module:
  search_router  (prefix /search)
  groups_router  (prefix /groups)

Both are registered in main.py with dependencies=[Depends(check_setup_complete)].
Individual endpoints enforce Depends(require_volunteer); admin-only operations
additionally check user['role'] == 'admin'.

Route prefixes have NO /api prefix — nginx strips it upstream.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.schemas import (
    ContactListItem,
    FieldRegistryResponse,
    FieldSpecOut,
    GroupCreate,
    GroupMemberAdd,
    GroupResponse,
    GroupUpdate,
    PaginatedContactResponse,
    PopulateRequest,
    PromoteRequest,
    SavedSearchCreate,
    SavedSearchOut,
    SavedSearchUpdate,
    SearchRequest,
    ValidateRequest,
)
from app.services import contact_service
from app.services.search_fields import build_registry
from app.services.search_service import (
    InvalidCriteria,
    add_members_to_group,
    compile_criteria,
    create_group,
    create_saved_search,
    delete_group,
    delete_saved_search,
    get_group,
    get_saved_search,
    list_groups,
    list_saved_searches,
    populate_static_group,
    promote_saved_search_to_group,
    remove_member_from_group,
    resolve_group_contacts,
    run_search,
    update_group,
    update_saved_search,
    _dialect_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _contact_list_item(c: Any) -> ContactListItem:
    """Build a ContactListItem from an ORM Contact row."""
    return ContactListItem(
        id=c.id,
        display_name=contact_service._display_name(c),
        first_name=c.first_name,
        last_name=c.last_name,
        nickname=c.nickname,
        email=c.email,
        phone=c.phone,
        contact_type=c.contact_type,
        contact_subtype=c.contact_subtype,
        tier=c.tier,
        is_regular=c.is_regular,
        is_connected=c.is_connected,
        face_thumbnail_path=None,  # list views omit thumbnail
    )


def _invalid_criteria_to_422(exc: InvalidCriteria) -> HTTPException:
    """Convert InvalidCriteria to a FastAPI 422 with the identifier-safe message."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    )


# ---------------------------------------------------------------------------
# Search router
# ---------------------------------------------------------------------------

search_router = APIRouter(prefix="/search", tags=["search"])


@search_router.get("/fields", response_model=FieldRegistryResponse)
async def get_field_registry(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> FieldRegistryResponse:
    """Return the full field registry: keys, labels, types, and allowed operators."""
    registry = await build_registry(db)
    fields: list[FieldSpecOut] = []
    for spec in registry.values():
        fields.append(
            FieldSpecOut(
                key=spec.key,
                label=spec.label,
                kind=spec.kind,
                type=spec.value_type,
                ops=sorted(spec.allowed_ops),
                options=spec.options,
                nullable=spec.nullable,
            )
        )
    return FieldRegistryResponse(fields=fields)


@search_router.post("/validate", status_code=200)
async def validate_criteria(
    body: ValidateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> dict[str, str]:
    """Validate search criteria without running it.  200 = valid, 422 = invalid."""
    registry = await build_registry(db)
    dialect = _dialect_name()
    try:
        compile_criteria(body.criteria, registry, dialect)
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc
    return {"status": "valid"}


@search_router.post("", response_model=PaginatedContactResponse)
async def search_contacts(
    body: SearchRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> PaginatedContactResponse:
    """Run a criteria search against contacts with pagination."""
    # include_deleted is admin-gated
    effective_include_deleted = body.include_deleted and user["role"] == "admin"

    try:
        total, rows = await run_search(
            db,
            body.criteria,
            body.page,
            body.page_size,
            include_deleted=effective_include_deleted,
        )
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc

    items = [_contact_list_item(c) for c in rows]
    return PaginatedContactResponse(
        total=total,
        page=body.page,
        page_size=body.page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# Saved searches sub-router  (under /search/saved)
# ---------------------------------------------------------------------------


@search_router.get("/saved", response_model=List[SavedSearchOut])
async def list_saved_searches_route(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> list[SavedSearchOut]:
    """List saved searches owned by the current user."""
    owner_id = int(user["sub"])
    searches = await list_saved_searches(db, owner_id)
    return [SavedSearchOut.model_validate(s) for s in searches]


@search_router.post("/saved", response_model=SavedSearchOut, status_code=201)
async def create_saved_search_route(
    body: SavedSearchCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> SavedSearchOut:
    """Create a new saved search for the current user."""
    owner_id = int(user["sub"])
    try:
        saved = await create_saved_search(
            db,
            owner_id=owner_id,
            name=body.name,
            entity=body.entity,
            criteria=body.criteria,
            actor_id=owner_id,
        )
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc
    return SavedSearchOut.model_validate(saved)


@search_router.get("/saved/{search_id}", response_model=SavedSearchOut)
async def get_saved_search_route(
    search_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> SavedSearchOut:
    """Get a saved search by id (owner-scoped; cross-owner → 404)."""
    owner_id = int(user["sub"])
    saved = await get_saved_search(db, search_id, owner_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found")
    return SavedSearchOut.model_validate(saved)


@search_router.patch("/saved/{search_id}", response_model=SavedSearchOut)
async def update_saved_search_route(
    search_id: int,
    body: SavedSearchUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> SavedSearchOut:
    """Partial update of a saved search (owner-scoped; cross-owner → 404)."""
    owner_id = int(user["sub"])
    saved = await get_saved_search(db, search_id, owner_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found")

    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.criteria is not None:
        updates["criteria"] = body.criteria

    try:
        saved = await update_saved_search(db, saved, updates, actor_id=owner_id)
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc
    return SavedSearchOut.model_validate(saved)


@search_router.delete("/saved/{search_id}", status_code=204)
async def delete_saved_search_route(
    search_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> None:
    """Delete a saved search (owner-scoped; cross-owner → 404)."""
    owner_id = int(user["sub"])
    saved = await get_saved_search(db, search_id, owner_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found")
    await delete_saved_search(db, saved, actor_id=owner_id)


@search_router.post("/saved/{search_id}/run", response_model=PaginatedContactResponse)
async def run_saved_search_route(
    search_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> PaginatedContactResponse:
    """Execute a saved search and return paginated results (owner-scoped)."""
    owner_id = int(user["sub"])
    saved = await get_saved_search(db, search_id, owner_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found")

    try:
        total, rows = await run_search(
            db, saved.criteria, page, page_size, include_deleted=False
        )
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc

    items = [_contact_list_item(c) for c in rows]
    return PaginatedContactResponse(total=total, page=page, page_size=page_size, items=items)


@search_router.post("/saved/{search_id}/promote", response_model=GroupResponse, status_code=201)
async def promote_saved_search_route(
    search_id: int,
    body: PromoteRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> GroupResponse:
    """Promote a saved search into a smart group (owner-scoped)."""
    owner_id = int(user["sub"])
    saved = await get_saved_search(db, search_id, owner_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved search not found")

    group = await promote_saved_search_to_group(
        db, saved, body.group_name, body.entity, actor_id=owner_id
    )
    return GroupResponse(
        id=group.id,
        name=group.name,
        entity=group.entity,
        group_type=group.group_type,
        criteria=group.criteria,
        owner_id=group.owner_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
        member_count=None,
    )


# ---------------------------------------------------------------------------
# Groups router
# ---------------------------------------------------------------------------

groups_router = APIRouter(prefix="/groups", tags=["groups"])


@groups_router.get("", response_model=List[GroupResponse])
async def list_groups_route(
    group_type: Optional[str] = Query(default=None),
    entity: Optional[str] = Query(default=None),
    with_counts: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> List[GroupResponse]:
    """List groups.  Returns a BARE ARRAY of GroupResponse (not paginated)."""
    groups_data = await list_groups(db, group_type=group_type, entity=entity, with_counts=with_counts)
    result: list[GroupResponse] = []
    for item in groups_data:
        g = item["group"]
        result.append(
            GroupResponse(
                id=g.id,
                name=g.name,
                entity=g.entity,
                group_type=g.group_type,
                criteria=g.criteria,
                owner_id=g.owner_id,
                created_at=g.created_at,
                updated_at=g.updated_at,
                member_count=item["member_count"],
            )
        )
    return result


@groups_router.post("", response_model=GroupResponse, status_code=201)
async def create_group_route(
    body: GroupCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> GroupResponse:
    """Create a group (smart or static)."""
    actor_id = int(user["sub"])
    group = await create_group(
        db,
        name=body.name,
        entity=body.entity,
        group_type=body.group_type,
        criteria=body.criteria,
        owner_id=actor_id,
        actor_id=actor_id,
    )
    return GroupResponse(
        id=group.id,
        name=group.name,
        entity=group.entity,
        group_type=group.group_type,
        criteria=group.criteria,
        owner_id=group.owner_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
        member_count=None,
    )


@groups_router.get("/{group_id}", response_model=GroupResponse)
async def get_group_route(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> GroupResponse:
    """Get group by id."""
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return GroupResponse(
        id=group.id,
        name=group.name,
        entity=group.entity,
        group_type=group.group_type,
        criteria=group.criteria,
        owner_id=group.owner_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
        member_count=None,
    )


@groups_router.patch("/{group_id}", response_model=GroupResponse)
async def update_group_route(
    group_id: int,
    body: GroupUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> GroupResponse:
    """Partial update.  group_type is immutable.  Smart criteria edit admin-only."""
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    actor_id = int(user["sub"])
    is_admin = user["role"] == "admin"

    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.criteria is not None:
        updates["criteria"] = body.criteria

    group = await update_group(db, group, updates, actor_id=actor_id, is_admin=is_admin)
    return GroupResponse(
        id=group.id,
        name=group.name,
        entity=group.entity,
        group_type=group.group_type,
        criteria=group.criteria,
        owner_id=group.owner_id,
        created_at=group.created_at,
        updated_at=group.updated_at,
        member_count=None,
    )


@groups_router.delete("/{group_id}", status_code=204)
async def delete_group_route(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> None:
    """Delete a group."""
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    actor_id = int(user["sub"])
    await delete_group(db, group, actor_id=actor_id)


@groups_router.get("/{group_id}/members", response_model=PaginatedContactResponse)
async def list_group_members_route(
    group_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> PaginatedContactResponse:
    """Paginated contact list for a group.

    Static groups: frozen snapshot.
    Smart groups: live criteria evaluation.
    """
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    try:
        total, rows = await resolve_group_contacts(db, group, page, page_size)
    except InvalidCriteria as exc:
        raise _invalid_criteria_to_422(exc) from exc

    items = [_contact_list_item(c) for c in rows]
    return PaginatedContactResponse(total=total, page=page, page_size=page_size, items=items)


@groups_router.post("/{group_id}/members")
async def add_group_members_route(
    group_id: int,
    body: GroupMemberAdd,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> dict[str, int]:
    """Add contacts to a static group.  Deduplicates via on_conflict_do_nothing.

    Returns {added, skipped}.  400 on smart groups.
    """
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    actor_id = int(user["sub"])
    return await add_members_to_group(db, group, body.contact_ids, actor_id=actor_id)


@groups_router.delete("/{group_id}/members/{contact_id}", status_code=204)
async def remove_group_member_route(
    group_id: int,
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> None:
    """Remove a contact from a static group.  400 on smart groups."""
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    actor_id = int(user["sub"])
    removed = await remove_member_from_group(db, group, contact_id, actor_id=actor_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contact is not a member of this group",
        )


@groups_router.post("/{group_id}/populate")
async def populate_group_route(
    group_id: int,
    body: PopulateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
) -> dict[str, int]:
    """Populate a static group from search criteria or a saved search.

    mode='replace': clear existing members first.
    mode='append': add without removing existing.
    400 on smart groups.
    """
    group = await get_group(db, group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    actor_id = int(user["sub"])
    return await populate_static_group(
        db,
        group,
        criteria=body.criteria,
        saved_search_id=body.saved_search_id,
        mode=body.mode,
        actor_id=actor_id,
        owner_id=actor_id,  # scope saved_search lookup to requesting user (IDOR fix)
    )
