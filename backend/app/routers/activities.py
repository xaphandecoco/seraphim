"""Activities router — S12 assignable CRM tasks.

Prefix: /activities  (no /api prefix — nginx strips it per AGENTS.md:46)
All routes registered with dependencies=[Depends(check_setup_complete)] in main.py.

Route ORDER matters: literal paths (/mine, /meta/types, /assignees) MUST come
before the parameterized path (/{id}) so FastAPI's routing does not shadow them.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_viewer, require_volunteer
from app.schemas import (
    ActivityCreate,
    ActivityDetailResponse,
    ActivityFilters,
    ActivityMetaResponse,
    ActivityReassignRequest,
    ActivityUpdate,
    AssigneeOption,
    PaginatedActivityResponse,
)
from app.services.activity_service import (
    ACTIVITY_PRIORITIES,
    ACTIVITY_STATUSES,
    ACTIVITY_TYPES,
    ActivityService,
)

router = APIRouter(prefix="/activities", tags=["activities"])


# ---------------------------------------------------------------------------
# Literal paths FIRST (must precede /{id})
# ---------------------------------------------------------------------------


@router.get("/mine", response_model=PaginatedActivityResponse)
async def list_my_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    sort: str = Query("due_date"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_viewer),
) -> PaginatedActivityResponse:
    """Convenience endpoint: caller's open activities (scheduled + in_progress), due-date sorted."""
    caller_id = int(current_user["sub"])
    filters = ActivityFilters(
        assignee_user_id="me",
        status="scheduled,in_progress",
    )
    svc = ActivityService(db)
    items, total = await svc.list(
        filters=filters,
        page=page,
        page_size=page_size,
        sort=sort,
        caller_id=caller_id,
    )
    return PaginatedActivityResponse(
        total=total,
        page=page,
        page_size=min(page_size, 100),
        items=[ActivityDetailResponse(**item) for item in items],
    )


@router.get("/meta/types", response_model=ActivityMetaResponse)
async def get_activity_meta(
    current_user: dict = Depends(require_viewer),
) -> ActivityMetaResponse:
    """Server-driven enum lists so frontend dropdowns stay current without deploys."""
    return ActivityMetaResponse(
        types=ACTIVITY_TYPES,
        statuses=ACTIVITY_STATUSES,
        priorities=ACTIVITY_PRIORITIES,
    )


@router.get("/assignees", response_model=list[AssigneeOption])
async def list_assignees(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_viewer),
) -> list[AssigneeOption]:
    """Active system users — safe projection (id/name/email/role only, no password_hash)."""
    svc = ActivityService(db)
    users = await svc.get_assignees()
    return [
        AssigneeOption(
            id=u.id,
            name=u.name,
            email=u.email,
            role=u.role,
        )
        for u in users
    ]


# ---------------------------------------------------------------------------
# Parameterized paths
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedActivityResponse)
async def list_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1),
    assignee_user_id: Optional[str] = Query(None),
    target_contact_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    overdue: bool = Query(False),
    sort: str = Query("due_date"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_viewer),
) -> PaginatedActivityResponse:
    """Filtered, paginated list of activities; joined to resolve display names in one query."""
    caller_id = int(current_user["sub"])
    filters = ActivityFilters(
        assignee_user_id=assignee_user_id,
        target_contact_id=target_contact_id,
        status=status,
        priority=priority,
        overdue=overdue,
    )
    svc = ActivityService(db)
    items, total = await svc.list(
        filters=filters,
        page=page,
        page_size=page_size,
        sort=sort,
        caller_id=caller_id,
    )
    return PaginatedActivityResponse(
        total=total,
        page=page,
        page_size=min(page_size, 100),
        items=[ActivityDetailResponse(**item) for item in items],
    )


@router.get("/{activity_id}", response_model=ActivityDetailResponse)
async def get_activity(
    activity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_viewer),
) -> ActivityDetailResponse:
    """Fetch one activity with resolved assignee / creator / contact display names."""
    svc = ActivityService(db)
    detail = await svc.get_detail(activity_id)
    return ActivityDetailResponse(**detail)


@router.post("", response_model=ActivityDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_activity(
    data: ActivityCreate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
) -> ActivityDetailResponse:
    """Create an activity; sets created_by_id = caller; validates type/assignee/contact/dates."""
    actor_id = int(current_user["sub"])
    svc = ActivityService(db)
    detail = await svc.create(data, actor_id)
    return ActivityDetailResponse(**detail)


@router.patch("/{activity_id}", response_model=ActivityDetailResponse)
async def update_activity(
    activity_id: int,
    data: ActivityUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
) -> ActivityDetailResponse:
    """Partial update with role-aware status-transition enforcement."""
    svc = ActivityService(db)
    detail = await svc.update(activity_id, data, current_user)
    return ActivityDetailResponse(**detail)


@router.post("/{activity_id}/reassign", response_model=ActivityDetailResponse)
async def reassign_activity(
    activity_id: int,
    body: ActivityReassignRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
) -> ActivityDetailResponse:
    """Admin-only reassignment; writes audit_log activity.reassign with before/after."""
    actor_id = int(current_user["sub"])
    svc = ActivityService(db)
    detail = await svc.reassign(activity_id, body.assignee_user_id, actor_id)
    return ActivityDetailResponse(**detail)


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity(
    activity_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_admin),
) -> None:
    """Hard-delete; writes audit_log activity.delete with before-snapshot. Volunteers cancel via PATCH status='cancelled'."""
    actor_id = int(current_user["sub"])
    svc = ActivityService(db)
    await svc.delete(activity_id, actor_id)
