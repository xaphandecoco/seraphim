"""Biometric Consent & RTBF router — S08.

Prefix:  /biometric
No /api prefix — nginx strips it.
All mutation endpoints commit after the service call.

Endpoints
---------
GET  /biometric/contacts/{id}/consent          any authenticated role → 200, never 404
POST /biometric/contacts/{id}/consent          require_volunteer → 409 if purged
PATCH /biometric/contacts/{id}/consent         require_volunteer; retention_until admin-only
POST /biometric/contacts/{id}/consent/revoke   require_volunteer
POST /biometric/contacts/{id}/deletion-request require_volunteer; immediate+admin → sync purge
POST /biometric/contacts/{id}/purge            require_admin → PurgeResultResponse
GET  /biometric/retention/report               require_admin; pagination
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin, require_volunteer
from app.models import BiometricConsent, Contact, utc_now
from app.schemas import (
    ConsentRecordRequest,
    ConsentResponse,
    ConsentUpdateRequest,
    DeletionRequest,
    PurgeDetail,
    PurgeResultResponse,
    RetentionReportItem,
    RetentionReportResponse,
)
from app.services import biometric_consent as consent_svc
from app.services.biometric_purge import BiometricPurgeService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/biometric", tags=["biometric"])


def _make_purge_service() -> BiometricPurgeService:
    from app.config import legacy_settings

    base = os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)
    return BiometricPurgeService(Path(base))


def _purge_detail_from_dict(d: dict) -> PurgeDetail:
    return PurgeDetail(
        files_deleted=d.get("files_deleted", 0),
        samples_deleted=d.get("samples_deleted", 0),
        detections_cleared=d.get("detections_cleared", 0),
        compreface_deleted=d.get("compreface_deleted", False),
        errors=d.get("errors", []),
    )


# ---------------------------------------------------------------------------
# GET /biometric/contacts/{id}/consent
# ---------------------------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def get_consent_status(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> ConsentResponse:
    """Return synthesized consent status — never returns 404."""
    data = await consent_svc.get_status(db, contact_id)
    return ConsentResponse(**data)


# ---------------------------------------------------------------------------
# POST /biometric/contacts/{id}/consent
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def record_consent(
    contact_id: int,
    body: ConsentRecordRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> ConsentResponse:
    """Record or update consent grant. 409 if data has been purged."""
    actor_id: int = int(user["sub"])
    await consent_svc.record(
        db,
        contact_id=contact_id,
        actor_id=actor_id,
        basis_note=body.basis_note,
        retention_years=body.retention_years,
    )
    await db.commit()
    data = await consent_svc.get_status(db, contact_id)
    return ConsentResponse(**data)


# ---------------------------------------------------------------------------
# PATCH /biometric/contacts/{id}/consent
# ---------------------------------------------------------------------------


@router.patch(
    "/contacts/{contact_id}/consent",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def update_consent(
    contact_id: int,
    body: ConsentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> ConsentResponse:
    """Update consent metadata. retention_until requires admin role."""
    actor_id: int = int(user["sub"])
    is_admin: bool = user["role"] == "admin"

    # Enforce: only admins may set retention_until
    if body.retention_until is not None and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins may modify retention_until",
        )

    await consent_svc.update(
        db,
        contact_id=contact_id,
        actor_id=actor_id,
        basis_note=body.basis_note,
        retention_until=body.retention_until,
        is_admin=is_admin,
    )
    await db.commit()
    data = await consent_svc.get_status(db, contact_id)
    return ConsentResponse(**data)


# ---------------------------------------------------------------------------
# POST /biometric/contacts/{id}/consent/revoke
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/consent/revoke",
    response_model=ConsentResponse,
    status_code=status.HTTP_200_OK,
)
async def revoke_consent(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> ConsentResponse:
    """Mark consent revoked (no purge)."""
    actor_id: int = int(user["sub"])
    await consent_svc.revoke(db, contact_id=contact_id, actor_id=actor_id)
    await db.commit()
    data = await consent_svc.get_status(db, contact_id)
    return ConsentResponse(**data)


# ---------------------------------------------------------------------------
# POST /biometric/contacts/{id}/deletion-request
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/deletion-request",
    response_model=PurgeResultResponse,
    status_code=status.HTTP_200_OK,
)
async def request_deletion(
    contact_id: int,
    body: DeletionRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> PurgeResultResponse:
    """Request data erasure. immediate=True AND admin role → synchronous purge."""
    actor_id: int = int(user["sub"])
    is_admin: bool = user["role"] == "admin"

    if body.immediate and is_admin:
        # Synchronous purge
        purge_svc = _make_purge_service()
        result = await purge_svc.purge(db, contact_id=contact_id, actor_id=actor_id)
        return PurgeResultResponse(
            status=result["status"],
            purge_detail=_purge_detail_from_dict(result["purge_detail"]),
        )

    # Non-immediate: queue the request
    await consent_svc.request_deletion(db, contact_id=contact_id, actor_id=actor_id)
    await db.commit()
    return PurgeResultResponse(
        status="deletion_requested",
        purge_detail=PurgeDetail(),
    )


# ---------------------------------------------------------------------------
# POST /biometric/contacts/{id}/purge
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/purge",
    response_model=PurgeResultResponse,
    status_code=status.HTTP_200_OK,
)
async def purge_contact(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> PurgeResultResponse:
    """Hard-delete all biometric data for a contact (admin only)."""
    actor_id: int = int(user["sub"])
    purge_svc = _make_purge_service()
    result = await purge_svc.purge(db, contact_id=contact_id, actor_id=actor_id)
    return PurgeResultResponse(
        status=result["status"],
        purge_detail=_purge_detail_from_dict(result["purge_detail"]),
    )


# ---------------------------------------------------------------------------
# GET /biometric/retention/report
# ---------------------------------------------------------------------------


@router.get(
    "/retention/report",
    response_model=RetentionReportResponse,
    status_code=status.HTTP_200_OK,
)
async def retention_report(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    within_days: Optional[int] = Query(default=None, ge=1),
    include_purged: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> RetentionReportResponse:
    """Paginated retention report sorted by retention_until ASC NULLS LAST."""
    from datetime import timedelta, timezone
    from sqlalchemy import case, nulls_last

    now = utc_now()

    # Build the base query joining contacts for contact_name
    query = (
        select(
            BiometricConsent,
            Contact.first_name,
            Contact.last_name,
        )
        .join(Contact, Contact.id == BiometricConsent.contact_id)
    )

    if not include_purged:
        query = query.where(BiometricConsent.purged_at.is_(None))

    if within_days is not None:
        cutoff = now + timedelta(days=within_days)
        query = query.where(
            BiometricConsent.retention_until.is_not(None),
            BiometricConsent.retention_until <= cutoff,
        )

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total: int = total_result.scalar_one() or 0

    # Paginate with NULLS LAST ordering
    offset = (page - 1) * page_size
    items_result = await db.execute(
        query
        .order_by(nulls_last(asc(BiometricConsent.retention_until)))
        .offset(offset)
        .limit(page_size)
    )
    rows = items_result.all()

    def _row_status(consent: BiometricConsent) -> str:
        if consent.purged_at is not None:
            return "purged"
        if consent.consent_given:
            return "given"
        if consent.consented_at is not None:
            return "revoked"
        return "pending"

    items = [
        RetentionReportItem(
            contact_id=consent.contact_id,
            contact_name=f"{first} {last}".strip(),
            consent_given=consent.consent_given,
            retention_until=consent.retention_until,
            deletion_requested_at=consent.deletion_requested_at,
            purged_at=consent.purged_at,
            status=_row_status(consent),
        )
        for consent, first, last in rows
    ]

    return RetentionReportResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
