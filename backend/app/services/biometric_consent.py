"""BiometricConsentService — CRUD + status synthesis for biometric_consent rows.

Ownership: S08 (Backend Wave 2).

Rules
-----
* Never commits — caller is responsible for db.commit() after the service call.
* NEVER raises 404 from get_status; returns status='none' shape when no row.
* Uses with_for_update() on record/revoke/request_deletion to prevent races.
* Delegates audit writes to app.services.audit — never constructs AuditLog directly.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BiometricConsent,
    ComprefaceSubject,
    Contact,
    FaceSample,
    utc_now,
)
from app.services import audit

logger = logging.getLogger(__name__)


def _resolve_status(consent: BiometricConsent) -> str:
    """Derive the human-readable consent status from the consent row fields.

    Logic (ordered):
    1. purged_at IS NOT NULL  → "purged"
    2. consent_given is True  → "given"
    3. recorded_at is present and consent_given is False:
         if consented_at was ever set (i.e. a prior grant existed) → "revoked"
         else                                                         → "pending"
    4. Otherwise → "none" (defensive; row without meaningful data)
    """
    if consent.purged_at is not None:
        return "purged"
    if consent.consent_given:
        return "given"
    # Row exists but consent_given is False
    if consent.consented_at is not None:
        # consented_at was previously set → it was revoked after a grant
        return "revoked"
    return "pending"


def _snapshot(consent: BiometricConsent) -> dict[str, Any]:
    """Minimal serializable snapshot of a consent row for audit before/after."""
    return {
        "consent_given": consent.consent_given,
        "consented_at": consent.consented_at.isoformat() if consent.consented_at else None,
        "basis_note": consent.basis_note,
        "retention_until": consent.retention_until.isoformat() if consent.retention_until else None,
        "deletion_requested_at": (
            consent.deletion_requested_at.isoformat()
            if consent.deletion_requested_at
            else None
        ),
        "purged_at": consent.purged_at.isoformat() if consent.purged_at else None,
    }


async def get_status(
    db: AsyncSession,
    contact_id: int,
) -> dict[str, Any]:
    """Return synthesized consent status — NEVER raises 404.

    Returns a dict matching ConsentResponse shape.  When no row exists for
    contact_id the status is 'none' and all other fields are None/0/False.
    """
    result = await db.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact_id)
    )
    consent: Optional[BiometricConsent] = result.scalar_one_or_none()

    # Count enrolled face samples for this contact
    count_result = await db.execute(
        select(func.count()).where(FaceSample.contact_id == contact_id)
    )
    enrolled_photo_count: int = count_result.scalar_one() or 0

    # Check for an active CompreFace subject
    subject_result = await db.execute(
        select(ComprefaceSubject).where(
            ComprefaceSubject.contact_id == contact_id,
            ComprefaceSubject.enrollment_status == "active",
        )
    )
    subject_active: bool = subject_result.scalar_one_or_none() is not None

    if consent is None:
        return {
            "status": "none",
            "consent_given": False,
            "consented_at": None,
            "basis_note": None,
            "retention_until": None,
            "deletion_requested_at": None,
            "purged_at": None,
            "recorded_by_id": None,
            "enrolled_photo_count": enrolled_photo_count,
            "subject_active": subject_active,
        }

    return {
        "status": _resolve_status(consent),
        "consent_given": consent.consent_given,
        "consented_at": consent.consented_at,
        "basis_note": consent.basis_note,
        "retention_until": consent.retention_until,
        "deletion_requested_at": consent.deletion_requested_at,
        "purged_at": consent.purged_at,
        "recorded_by_id": consent.recorded_by_id,
        "enrolled_photo_count": enrolled_photo_count,
        "subject_active": subject_active,
    }


async def record(
    db: AsyncSession,
    contact_id: int,
    actor_id: int,
    basis_note: Optional[str] = None,
    retention_years: Optional[int] = None,
) -> BiometricConsent:
    """Upsert a consent grant for contact_id.

    Uses SELECT … FOR UPDATE to prevent concurrent grants on the same contact.
    404 if the contact does not exist.
    409 if the row has already been purged.  The retention window is
    computed from the current timestamp so repeated calls extend it correctly.

    Caller must call db.commit() to persist.
    """
    from app.config import dynamic_settings

    # Pre-check: contact must exist (clean 404 instead of FK IntegrityError).
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    # Lock the existing row (if any) to prevent race conditions.
    result = await db.execute(
        select(BiometricConsent)
        .where(BiometricConsent.contact_id == contact_id)
        .with_for_update()
    )
    existing: Optional[BiometricConsent] = result.scalar_one_or_none()

    now = utc_now()

    if existing is not None:
        if existing.purged_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Biometric data for contact {contact_id} has been purged "
                    "and cannot be re-consented."
                ),
            )

        before = _snapshot(existing)
        years = retention_years if retention_years is not None else dynamic_settings.get_biometric_retention_years()
        retention_until = now + timedelta(days=365 * years)

        existing.consent_given = True
        existing.consented_at = now
        if basis_note is not None:
            existing.basis_note = basis_note
        existing.retention_until = retention_until
        existing.recorded_by_id = actor_id
        existing.updated_at = now

        await db.flush()
        await audit.record(
            db,
            actor_id=actor_id,
            action="consent_updated",
            entity="biometric_consent",
            entity_id=existing.id,
            before=before,
            after=_snapshot(existing),
        )
        return existing

    # New row
    years = retention_years if retention_years is not None else dynamic_settings.get_biometric_retention_years()
    retention_until = now + timedelta(days=365 * years)

    consent = BiometricConsent(
        contact_id=contact_id,
        consent_given=True,
        consented_at=now,
        basis_note=basis_note,
        recorded_at=now,
        recorded_by_id=actor_id,
        retention_until=retention_until,
        updated_at=now,
    )
    db.add(consent)
    await db.flush()
    await audit.record(
        db,
        actor_id=actor_id,
        action="consent_recorded",
        entity="biometric_consent",
        entity_id=consent.id,
        before=None,
        after=_snapshot(consent),
    )
    return consent


async def update(
    db: AsyncSession,
    contact_id: int,
    actor_id: int,
    basis_note: Optional[str] = None,
    retention_until: Optional[datetime] = None,
    is_admin: bool = False,
) -> BiometricConsent:
    """PATCH existing consent metadata.

    - 404 if the contact does not exist OR if no consent row exists.
    - retention_until is admin-only; caller enforces the 403 before calling.
    - basis_note may be updated by any volunteer+.
    - Caller must commit.
    """
    # Pre-check: contact must exist.
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    result = await db.execute(
        select(BiometricConsent)
        .where(BiometricConsent.contact_id == contact_id)
        .with_for_update()
    )
    consent: Optional[BiometricConsent] = result.scalar_one_or_none()
    if consent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No biometric consent record found for contact {contact_id}",
        )

    before = _snapshot(consent)

    if basis_note is not None:
        consent.basis_note = basis_note
    if retention_until is not None and is_admin:
        consent.retention_until = retention_until
    consent.updated_at = utc_now()

    await db.flush()
    await audit.record(
        db,
        actor_id=actor_id,
        action="consent_updated",
        entity="biometric_consent",
        entity_id=consent.id,
        before=before,
        after=_snapshot(consent),
    )
    return consent


async def revoke(
    db: AsyncSession,
    contact_id: int,
    actor_id: int,
) -> BiometricConsent:
    """Mark consent as revoked (consent_given=False) without purging data.

    - 404 if no row exists.
    - Caller must commit.
    """
    result = await db.execute(
        select(BiometricConsent)
        .where(BiometricConsent.contact_id == contact_id)
        .with_for_update()
    )
    consent: Optional[BiometricConsent] = result.scalar_one_or_none()
    if consent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No biometric consent record found for contact {contact_id}",
        )

    before = _snapshot(consent)
    consent.consent_given = False
    consent.updated_at = utc_now()

    await db.flush()
    await audit.record(
        db,
        actor_id=actor_id,
        action="consent_revoked",
        entity="biometric_consent",
        entity_id=consent.id,
        before=before,
        after=_snapshot(consent),
    )
    return consent


async def request_deletion(
    db: AsyncSession,
    contact_id: int,
    actor_id: int,
) -> BiometricConsent:
    """Flag a RTBF deletion request; does NOT purge immediately.

    - 404 if no row exists.
    - Caller must commit.
    """
    result = await db.execute(
        select(BiometricConsent)
        .where(BiometricConsent.contact_id == contact_id)
        .with_for_update()
    )
    consent: Optional[BiometricConsent] = result.scalar_one_or_none()
    if consent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No biometric consent record found for contact {contact_id}",
        )

    before = _snapshot(consent)
    now = utc_now()
    consent.deletion_requested_at = now
    consent.deletion_requested_by_id = actor_id
    consent.updated_at = now

    await db.flush()
    await audit.record(
        db,
        actor_id=actor_id,
        action="deletion_requested",
        entity="biometric_consent",
        entity_id=consent.id,
        before=before,
        after=_snapshot(consent),
    )
    return consent
