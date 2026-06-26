"""Dedupe router — /dedupe prefix.

S11 Find & Merge Duplicates.
No /api prefix — nginx strips it upstream.

RBAC:
  - require_volunteer  — all read/candidate/merge endpoints
  - require_admin      — rule-set write (POST/PUT/DELETE)
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import AuditLog
from app.schemas import (
    CandidatesRequest,
    CandidatesResponse,
    CandidatePair,
    ContactLite,
    FieldConflict,
    MergeHistoryItem,
    MergePreviewRequest,
    MergePreviewResponse,
    MergeRequest,
    MergeResponse,
    PaginatedMergeHistoryResponse,
    ReassignmentStat,
    RuleSetIn,
    RuleSetOut,
)
from app.services import dedupe_service

router = APIRouter(prefix="/dedupe", tags=["dedupe"])


# ============================================================================
# Candidate detection
# ============================================================================


@router.post("/candidates", response_model=CandidatesResponse)
async def get_candidates(
    req: CandidatesRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> CandidatesResponse:
    """Return duplicate-candidate pairs scored by the active rule set."""
    result = await dedupe_service.detect_candidates(
        db,
        page=req.page,
        page_size=req.page_size,
        rule_set_id=req.rule_set_id,
    )

    items = [
        CandidatePair(
            contact_a=ContactLite(**item["contact_a"]),
            contact_b=ContactLite(**item["contact_b"]),
            score=item["score"],
            matched_fields=item["matched_fields"],
            same_name=item["same_name"],
        )
        for item in result["items"]
    ]
    return CandidatesResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ============================================================================
# Merge preview (dry-run)
# ============================================================================


@router.post("/merge/preview", response_model=MergePreviewResponse)
async def preview_merge(
    req: MergePreviewRequest,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> MergePreviewResponse:
    """Return a read-only preview of what a merge would change."""
    data = await dedupe_service.merge_preview(db, req.survivor_id, req.loser_id)

    field_conflicts = {
        k: FieldConflict(**v) for k, v in data["field_conflicts"].items()
    }
    custom_field_conflicts = {
        k: FieldConflict(**v) for k, v in data["custom_field_conflicts"].items()
    }
    reassignments = {
        k: ReassignmentStat(**v) for k, v in data["reassignments"].items()
    }
    return MergePreviewResponse(
        field_conflicts=field_conflicts,
        custom_field_conflicts=custom_field_conflicts,
        reassignments=reassignments,
        same_name=data["same_name"],
        warnings=data["warnings"],
    )


# ============================================================================
# Merge (atomic write)
# ============================================================================


@router.post("/merge", response_model=MergeResponse)
async def do_merge(
    req: MergeRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> MergeResponse:
    """Atomically merge loser contact into survivor contact."""
    actor_id: int | None = None
    try:
        actor_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        actor_id = None

    result = await dedupe_service.merge_contacts(
        db,
        survivor_id=req.survivor_id,
        loser_id=req.loser_id,
        actor_id=actor_id,
        confirm_same_name=req.confirm_same_name,
        field_choices=req.field_choices,
    )

    reassignments = {
        k: ReassignmentStat(**v) for k, v in result["reassignments"].items()
    }
    return MergeResponse(
        survivor_id=result["survivor_id"],
        reassignments=reassignments,
        warnings=result["warnings"],
    )


# ============================================================================
# Merge history
# ============================================================================


@router.get("/merge/history", response_model=PaginatedMergeHistoryResponse)
async def get_merge_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> PaginatedMergeHistoryResponse:
    """Paginate audit_log rows where action='contact_merge'."""
    total: int = (await db.execute(
        select(func.count(AuditLog.id)).where(AuditLog.action == "contact_merge")
    )).scalar() or 0

    rows = (await db.execute(
        select(AuditLog)
        .where(AuditLog.action == "contact_merge")
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).scalars().all()

    items = [MergeHistoryItem.model_validate(row) for row in rows]
    return PaginatedMergeHistoryResponse(
        items=items, total=total, page=page, page_size=page_size
    )


# ============================================================================
# Rule sets  (GET — require_volunteer; write ops — require_admin)
# ============================================================================


@router.get("/rule-sets", response_model=List[RuleSetOut])
async def list_rule_sets(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> List[RuleSetOut]:
    """Return all rule sets as a plain array (frontend expects bare array)."""
    items = await dedupe_service.list_rule_sets(db)
    return [RuleSetOut.model_validate(rs) for rs in items]


@router.get("/rule-sets/{rule_set_id}", response_model=RuleSetOut)
async def get_rule_set(
    rule_set_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_volunteer),
) -> RuleSetOut:
    rs = await dedupe_service.get_rule_set(db, rule_set_id)
    return RuleSetOut.model_validate(rs)


@router.post("/rule-sets", response_model=RuleSetOut, status_code=201)
async def create_rule_set(
    data: RuleSetIn,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> RuleSetOut:
    rs = await dedupe_service.create_rule_set(db, data)
    return RuleSetOut.model_validate(rs)


@router.put("/rule-sets/{rule_set_id}", response_model=RuleSetOut)
async def update_rule_set(
    rule_set_id: int,
    data: RuleSetIn,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> RuleSetOut:
    rs = await dedupe_service.update_rule_set(db, rule_set_id, data)
    return RuleSetOut.model_validate(rs)


@router.delete("/rule-sets/{rule_set_id}", status_code=204)
async def delete_rule_set(
    rule_set_id: int,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
) -> Response:
    await dedupe_service.delete_rule_set(db, rule_set_id)
    return Response(status_code=204)
