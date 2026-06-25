"""FR-transition verification and action endpoints (T03).

Prefix:  /admin/fr-transition
Auth:    Depends(require_admin) on every route

Endpoints
---------
GET  /status            — full transition health summary (FRTransitionStatus)
POST /remap             — trigger subject→contact remap (RemapReport)
POST /consent-backfill  — backfill BiometricConsent rows (ConsentBackfillReport)

Design notes
------------
* smoke_test never calls live CompreFace (recon risk #5).  In test env or
  when CompreFace config is absent the section reports {status:'skip'}.
  A prod 'skip' with detail='no-active-subjects' is normal when onboarding.
* subjects_orphaned counts rows where contact_id IS NULL OR is_orphan=True so
  it matches what the remap service considers orphaned after a pass.
* enroll_without_consent delegates to the T05 helper
  get_enroll_without_consent(db) — returns 0 if the helper is not yet wired.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings, legacy_settings
from app.database import get_db
from app.dependencies import require_admin
from app.schemas_fr_transition import (
    ConsentSection,
    FRTransitionStatus,
    OrphanSubjectPage,
    OrphanSubjectRow,
    RemapSection,
    SmokeTestSection,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/fr-transition", tags=["fr-transition"])

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _remap_section(db: AsyncSession) -> RemapSection:
    """Compute remap counts by querying ComprefaceSubject directly."""
    from app.models import ComprefaceSubject

    total: int = (
        await db.scalar(select(func.count()).select_from(ComprefaceSubject))
    ) or 0

    # Subjects with a live contact link and not flagged as orphan
    remapped: int = (
        await db.scalar(
            select(func.count())
            .select_from(ComprefaceSubject)
            .where(
                ComprefaceSubject.contact_id.is_not(None),
                ComprefaceSubject.is_orphan.is_(False),
            )
        )
    ) or 0

    # Orphaned = contact_id IS NULL OR is_orphan flag set, excluding purged (retired)
    orphaned: int = (
        await db.scalar(
            select(func.count())
            .select_from(ComprefaceSubject)
            .where(
                (
                    (ComprefaceSubject.contact_id.is_(None))
                    | (ComprefaceSubject.is_orphan.is_(True))
                )
                & (ComprefaceSubject.enrollment_status != "purged")
            )
        )
    ) or 0

    return RemapSection(
        subjects_total=total,
        subjects_remapped=remapped,
        subjects_orphaned=orphaned,
    )


async def _participants_count(db: AsyncSession) -> int:
    """Total rows in the participants table."""
    from app.models import Participant

    return (
        await db.scalar(select(func.count()).select_from(Participant))
    ) or 0


async def _consent_section(db: AsyncSession) -> ConsentSection:
    """Compute BiometricConsent coverage counts.

    Falls back gracefully to zeros if BiometricConsent does not exist yet
    (pre-P01 schema state).
    """
    from app.models import ComprefaceSubject

    # Active subjects = enrollment_status='active' AND contact_id IS NOT NULL
    active_subjects_total: int = (
        await db.scalar(
            select(func.count())
            .select_from(ComprefaceSubject)
            .where(
                ComprefaceSubject.enrollment_status == "active",
                ComprefaceSubject.contact_id.is_not(None),
            )
        )
    ) or 0

    # Counts that depend on BiometricConsent model
    consent_rows_created = 0
    subjects_missing_consent = 0
    enroll_without_consent: bool = False

    try:
        from app.models import BiometricConsent  # noqa: PLC0415 — conditional import

        consent_rows_created = (
            await db.scalar(select(func.count()).select_from(BiometricConsent))
        ) or 0

        # Active subjects whose contact_id has NO BiometricConsent row
        consented_contact_ids_subq = (
            select(BiometricConsent.contact_id).scalar_subquery()
        )
        subjects_missing_consent = (
            await db.scalar(
                select(func.count())
                .select_from(ComprefaceSubject)
                .where(
                    ComprefaceSubject.enrollment_status == "active",
                    ComprefaceSubject.contact_id.is_not(None),
                    ComprefaceSubject.contact_id.not_in(consented_contact_ids_subq),
                )
            )
        ) or 0

        # T05 helper — read the AdminSetting; returns False if key absent
        from app.services.fr_transition import get_enroll_without_consent  # noqa: PLC0415

        enroll_without_consent = await get_enroll_without_consent(db)

    except (ImportError, AttributeError):
        # BiometricConsent model not yet present (pre-P01); return zeros/False
        pass

    return ConsentSection(
        active_subjects_total=active_subjects_total,
        consent_rows_created=consent_rows_created,
        subjects_missing_consent=subjects_missing_consent,
        enroll_without_consent=enroll_without_consent,
    )


def _smoke_test_section() -> SmokeTestSection:
    """Evaluate smoke-test gate without ever calling live CompreFace.

    Rules (in priority order):
    1. ENVIRONMENT == 'test'  → skip (test-env)
    2. CompreFace URL/key absent in dynamic_settings → skip (compreface-not-configured)
    3. Otherwise → pass (config-present)
       NOTE: a 'skip' in prod with detail='no-active-subjects' is a normal
       bootstrapping state and does NOT indicate a problem.
    """
    if legacy_settings.ENVIRONMENT == "test":
        return SmokeTestSection(status="skip", detail="test-env")

    cf_url = dynamic_settings.get_compreface_url()
    cf_key = dynamic_settings.get_compreface_api_key()
    if not cf_url or not cf_key:
        return SmokeTestSection(status="skip", detail="compreface-not-configured")

    # Config is present; we report pass without making any live HTTP calls.
    return SmokeTestSection(status="pass", detail="config-present")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=FRTransitionStatus,
    summary="FR-transition health summary",
)
async def get_fr_transition_status(
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> FRTransitionStatus:
    """Return a four-section status snapshot of the FR-transition state.

    Sections: remap, participants_count, consent, smoke_test.
    Never calls live CompreFace.
    """
    remap = await _remap_section(db)
    p_count = await _participants_count(db)
    consent = await _consent_section(db)
    smoke = _smoke_test_section()

    return FRTransitionStatus(
        remap=remap,
        participants_count=p_count,
        consent=consent,
        smoke_test=smoke,
    )


@router.post(
    "/remap",
    summary="Trigger subject→contact remap",
    status_code=status.HTTP_200_OK,
)
async def post_remap(
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> Any:
    """Run remap_subjects and return the RemapReport TypedDict.

    Delegates entirely to the T02 service.
    """
    try:
        from app.services.fr_transition import remap_subjects  # noqa: PLC0415
    except (ImportError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="FR-transition service not yet available.",
        ) from exc

    report = await remap_subjects(db)
    return report


@router.post(
    "/consent-backfill",
    summary="Backfill BiometricConsent rows for active subjects",
    status_code=status.HTTP_200_OK,
)
async def post_consent_backfill(
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> Any:
    """Run backfill_consent and return the ConsentBackfillReport TypedDict.

    Delegates entirely to the T02 service.
    """
    try:
        from app.services.fr_transition import backfill_consent  # noqa: PLC0415
    except (ImportError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="FR-transition service not yet available.",
        ) from exc

    report = await backfill_consent(db)
    return report


@router.get(
    "/orphans",
    response_model=OrphanSubjectPage,
    summary="List orphaned ComprefaceSubject rows (paginated)",
)
async def list_orphans(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> OrphanSubjectPage:
    """Return paginated ComprefaceSubject rows where contact_id IS NULL or is_orphan=True.

    These represent subjects that are no longer linked to any contact and need
    either relinking (PATCH /relink) or retiring (PATCH /retire).
    """
    from app.models import ComprefaceSubject  # noqa: PLC0415

    # Orphans = no contact link OR flagged as orphan, but NOT already purged/retired
    orphan_filter = (
        (ComprefaceSubject.contact_id.is_(None)) | (ComprefaceSubject.is_orphan.is_(True))
    ) & (ComprefaceSubject.enrollment_status != "purged")

    total: int = (
        await db.scalar(
            select(func.count())
            .select_from(ComprefaceSubject)
            .where(orphan_filter)
        )
    ) or 0

    offset = (page - 1) * page_size
    result = await db.execute(
        select(ComprefaceSubject)
        .where(orphan_filter)
        .order_by(ComprefaceSubject.id)
        .offset(offset)
        .limit(page_size)
    )
    rows = result.scalars().all()

    return OrphanSubjectPage(
        items=[OrphanSubjectRow.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/orphans/{subject_id}/relink",
    summary="Relink an orphaned subject to a contact",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def relink_orphan(
    subject_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> None:
    """Reassign the orphaned subject to a new contact.

    Body: {"contact_id": <int>}

    Uses EnrollmentService.reassign_subject_contact which takes the CompreFace
    UUID string (not the integer PK).  We first fetch the subject by integer id,
    then pass subject.compreface_subject_id to the service.
    """
    from app.models import ComprefaceSubject  # noqa: PLC0415
    from app.services.enrollment import EnrollmentService  # noqa: PLC0415

    contact_id: int | None = body.get("contact_id")
    if contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="contact_id is required",
        )

    subject = await db.get(ComprefaceSubject, subject_id)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subject {subject_id} not found",
        )

    svc = EnrollmentService()
    await svc.reassign_subject_contact(db, subject.compreface_subject_id, contact_id)

    # Clear orphan flag now that it is relinked
    subject = await db.get(ComprefaceSubject, subject_id)
    if subject is not None:
        subject.is_orphan = False
        await db.commit()


@router.patch(
    "/orphans/{subject_id}/retire",
    summary="Retire an orphaned subject (mark as purged)",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def retire_orphan(
    subject_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> None:
    """Set is_orphan=True, enrollment_status='purged', purged_at=utc_now() and commit.

    Does not call CompreFace.  The row remains in the database for audit purposes
    but will no longer appear in the orphan list after this call because it is
    filtered by the remap service as already-handled.
    """
    from app.models import ComprefaceSubject, utc_now  # noqa: PLC0415

    subject = await db.get(ComprefaceSubject, subject_id)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subject {subject_id} not found",
        )

    subject.is_orphan = True
    subject.enrollment_status = "purged"
    subject.purged_at = utc_now()
    await db.commit()
