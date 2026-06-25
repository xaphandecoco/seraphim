"""Bulk participant operations router.

Ownership: S05-F07.
Prefix: /participants
Tags: participants

Endpoints
---------
POST /participants/bulk-add      — bulk-enrol an audience in an event
POST /participants/bulk-status   — bulk-update participant status
POST /participants/bulk-remove   — bulk-remove (soft cancel or hard delete)
POST /participants/bulk-preview  — count-only preview, no writes

All endpoints require at least volunteer-level authentication.
/bulk-remove additionally enforces an admin gate when req.hard is True.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.schemas import AudienceSelector, BulkParticipantPreview, BulkParticipantResult
from app.services import bulk_service

router = APIRouter(prefix="/participants", tags=["participants"])


# ---------------------------------------------------------------------------
# Request models (extend base schemas with event_id and hard flag)
# ---------------------------------------------------------------------------


class BulkAddRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    status: Literal["attended", "registered", "no_show", "cancelled"] = "registered"
    source: Literal["manual", "import", "bulk"] = "bulk"
    role: Optional[str] = None


class BulkStatusRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    new_status: Literal["attended", "registered", "no_show", "cancelled"]
    only_if_status: Optional[Literal["attended", "registered", "no_show", "cancelled"]] = None


class BulkRemoveRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    hard: bool = False


class BulkPreviewRequest(BaseModel):
    event_id: int
    audience: AudienceSelector


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/bulk-add", response_model=BulkParticipantResult, status_code=status.HTTP_200_OK)
async def bulk_add(
    req: BulkAddRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> BulkParticipantResult:
    """Bulk-enrol an audience in an event."""
    caller_id = int(user["sub"])
    result = await bulk_service.bulk_add_participants(db, req, caller_id=caller_id)
    return BulkParticipantResult(
        inserted=result.inserted,
        skipped=result.skipped,
    )


@router.post("/bulk-status", response_model=BulkParticipantResult, status_code=status.HTTP_200_OK)
async def bulk_status(
    req: BulkStatusRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> BulkParticipantResult:
    """Bulk-update participant status for an audience."""
    caller_id = int(user["sub"])
    result = await bulk_service.bulk_update_status(db, req, caller_id=caller_id)
    return BulkParticipantResult(
        matched=result.matched,
        updated=result.updated,
    )


@router.post("/bulk-remove", response_model=BulkParticipantResult, status_code=status.HTTP_200_OK)
async def bulk_remove(
    req: BulkRemoveRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> BulkParticipantResult:
    """Bulk-remove participants (soft cancel or hard delete).

    Hard delete requires admin role (AC6).
    """
    if req.hard and user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hard delete requires admin role",
        )
    caller_id = int(user["sub"])
    result = await bulk_service.bulk_remove(db, req, caller_id=caller_id)
    return BulkParticipantResult(
        updated=result.removed,
    )


@router.post(
    "/bulk-preview",
    response_model=BulkParticipantPreview,
    status_code=status.HTTP_200_OK,
)
async def bulk_preview(
    req: BulkPreviewRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> BulkParticipantPreview:
    """Count-only preview — no writes performed."""
    result = await bulk_service.preview(db, req)
    return BulkParticipantPreview(
        contact_count=result.requested,
        already_participating=result.already_enrolled,
    )
