"""Enrollment router — face sample CRUD + recognition history for contacts.

Two APIRouters are defined:
  router         prefix=/contacts  tags=['enrollment']
  backfill_router prefix=/enrollment tags=['backfill']

Both are imported and registered in main.py.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, require_admin, require_volunteer
from app.models import ComprefaceSubject, Contact, FaceSample, Participant, utc_now
from app.schemas import (
    BackfillRequest,
    BackfillResponse,
    FacePanelResponse,
    FaceSampleResponse,
    RecognitionHistoryItem,
    RecognitionHistoryResponse,
    RetrainResponse,
)
from app.services.compreface import ComprefaceClient
from app.services.enrollment import EnrollmentService
from app.services.face_storage import FaceStorage, to_storage_url

logger = logging.getLogger(__name__)


def _make_storage() -> FaceStorage:
    from app.config import legacy_settings
    base = os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)
    return FaceStorage(base_path=base)


def _sample_to_response(sample: FaceSample) -> FaceSampleResponse:
    return FaceSampleResponse(
        id=sample.id,
        compreface_subject_id=sample.compreface_subject_id,
        image_path=sample.image_path,
        thumb_path=sample.thumb_path,
        thumb_url=to_storage_url(sample.thumb_path) if sample.thumb_path else None,
        source=sample.source,
        compreface_image_id=sample.compreface_image_id,
        created_at=sample.created_at,
    )


# ---------------------------------------------------------------------------
# Contact-scoped enrollment router  (prefix /contacts)
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/contacts", tags=["enrollment"])


@router.post(
    "/{contact_id}/faces",
    response_model=FaceSampleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_face_sample(
    contact_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> FaceSampleResponse:
    """Upload a face image for a contact and enroll it in CompreFace.

    Returns 201 FaceSampleResponse on success.
    Error mapping:
      ValueError / quality-gate failure → 422
      FileNotFoundError → 404
      CompreFace returned None → 502
      purged_at non-null → 409
    """
    # Accept image/jpeg, image/png, image/webp; reject others
    content_type = (file.content_type or "").lower()
    if not any(t in content_type for t in ("jpeg", "jpg", "png", "webp", "image")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported content type '{file.content_type}'; upload a JPEG or PNG image",
        )

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Empty file upload",
        )

    # Decode to numpy ndarray for quality gate
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    face_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if face_img is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not decode image — ensure the upload is a valid JPEG or PNG",
        )

    client = ComprefaceClient()
    storage = _make_storage()
    svc = EnrollmentService(client=client, storage=storage)
    try:
        # EnrollmentService raises HTTPException directly for all error conditions;
        # the finally block ensures the httpx client is always closed.
        sample = await svc.enroll_contact_face(
            session=db,
            contact_id=contact_id,
            face_crop=face_img,
            source="manual",
        )
    finally:
        await client.close()

    return _sample_to_response(sample)


@router.get(
    "/{contact_id}/faces",
    response_model=FacePanelResponse,
)
async def get_face_panel(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> FacePanelResponse:
    """Return all enrolled face samples for a contact.

    Thumbnail URLs are /storage/-prefixed via to_storage_url().
    """
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    result = await db.execute(
        select(ComprefaceSubject).where(ComprefaceSubject.contact_id == contact_id)
    )
    subject: Optional[ComprefaceSubject] = result.scalar_one_or_none()

    samples: list[FaceSampleResponse] = []
    if subject is not None:
        samples_result = await db.execute(
            select(FaceSample)
            .where(FaceSample.compreface_subject_id == subject.compreface_subject_id)
            .order_by(FaceSample.created_at.asc())
        )
        samples = [_sample_to_response(s) for s in samples_result.scalars().all()]

    return FacePanelResponse(
        contact_id=contact_id,
        compreface_subject_id=subject.compreface_subject_id if subject else None,
        sample_count=subject.sample_count if subject else 0,
        enrollment_status=subject.enrollment_status if subject else "pending",
        last_trained_at=subject.last_trained_at if subject else None,
        is_orphan=subject.is_orphan if subject else False,
        purged_at=subject.purged_at if subject else None,
        samples=samples,
    )


@router.delete(
    "/{contact_id}/faces/{sample_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_face_sample(
    contact_id: int,
    sample_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> None:
    """Remove a single face sample row and best-effort delete from CompreFace.

    - Validates the sample belongs to the given contact.
    - Deletes FaceSample row from DB.
    - Calls client.delete_example best-effort (non-fatal on failure).
    - Recomputes ComprefaceSubject.sample_count via EnrollmentService._count_samples.
    """
    sample = await db.get(FaceSample, sample_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FaceSample {sample_id} not found",
        )

    # Validate ownership: the sample's subject must belong to contact_id
    result = await db.execute(
        select(ComprefaceSubject).where(
            ComprefaceSubject.compreface_subject_id == sample.compreface_subject_id,
            ComprefaceSubject.contact_id == contact_id,
        )
    )
    subject = result.scalar_one_or_none()
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FaceSample {sample_id} does not belong to contact {contact_id}",
        )

    if subject.purged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subject for contact {contact_id} has been purged",
        )

    cf_image_id = sample.compreface_image_id
    compreface_subject_id = sample.compreface_subject_id

    await db.delete(sample)
    await db.flush()

    # Recompute sample_count
    count_result = await db.execute(
        select(func.count()).where(
            FaceSample.compreface_subject_id == compreface_subject_id
        )
    )
    subject.sample_count = count_result.scalar_one() or 0

    await db.commit()

    # Best-effort CompreFace delete (non-fatal)
    if cf_image_id:
        client = ComprefaceClient()
        try:
            deleted = await client.delete_example(cf_image_id)
            if not deleted:
                logger.warning(
                    "delete_face_sample: CompreFace delete_example returned False "
                    "for image_id=%s sample_id=%s contact_id=%s",
                    cf_image_id,
                    sample_id,
                    contact_id,
                )
        except Exception:
            logger.warning(
                "delete_face_sample: non-fatal error deleting CompreFace image_id=%s",
                cf_image_id,
                exc_info=True,
            )
        finally:
            await client.close()


@router.post(
    "/{contact_id}/faces/retrain",
    response_model=RetrainResponse,
    status_code=status.HTTP_200_OK,
)
async def retrain_contact_faces(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_volunteer),
) -> RetrainResponse:
    """Re-push all existing sample images to CompreFace and update last_trained_at.

    Steps:
    1. Locate ComprefaceSubject for contact_id (404 if missing, 409 if purged).
    2. Load all FaceSample rows.
    3. Read each full image from disk; skip (with warning) if missing.
    4. call client.add_example for each; skip on None return.
    5. Set subject.last_trained_at = utc_now(), commit.
    """
    result = await db.execute(
        select(ComprefaceSubject).where(ComprefaceSubject.contact_id == contact_id)
    )
    subject = result.scalar_one_or_none()
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No CompreFace subject found for contact {contact_id}",
        )
    if subject.purged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Subject for contact {contact_id} has been purged",
        )

    samples_result = await db.execute(
        select(FaceSample).where(
            FaceSample.compreface_subject_id == subject.compreface_subject_id
        )
    )
    samples = list(samples_result.scalars().all())

    storage = _make_storage()
    pushed = 0
    client = ComprefaceClient()
    try:
        for sample in samples:
            full_abs = storage.base_path / sample.image_path
            try:
                img_bytes = full_abs.read_bytes()
            except FileNotFoundError:
                logger.warning(
                    "retrain_contact_faces: disk image missing for sample_id=%s path=%s "
                    "contact_id=%s",
                    sample.id,
                    sample.image_path,
                    contact_id,
                )
                continue

            image_id = await client.add_example(subject.compreface_subject_id, img_bytes)
            if image_id is None:
                logger.warning(
                    "retrain_contact_faces: add_example returned None for sample_id=%s "
                    "contact_id=%s",
                    sample.id,
                    contact_id,
                )
                continue

            sample.compreface_image_id = image_id
            pushed += 1
    finally:
        await client.close()

    subject.last_trained_at = utc_now()
    await db.commit()
    await db.refresh(subject)

    return RetrainResponse(
        compreface_subject_id=subject.compreface_subject_id,
        samples_pushed=pushed,
        last_trained_at=subject.last_trained_at,
    )


@router.get(
    "/{contact_id}/recognition-history",
    response_model=RecognitionHistoryResponse,
)
async def get_recognition_history(
    contact_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
) -> RecognitionHistoryResponse:
    """Return paginated Participant rows with source='face' for a contact, newest first."""
    contact = await db.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    count_result = await db.execute(
        select(func.count()).where(
            Participant.contact_id == contact_id,
            Participant.source == "face",
        )
    )
    total = count_result.scalar_one() or 0

    offset = (page - 1) * page_size
    items_result = await db.execute(
        select(Participant)
        .where(
            Participant.contact_id == contact_id,
            Participant.source == "face",
        )
        .order_by(Participant.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = [
        RecognitionHistoryItem(
            id=p.id,
            contact_id=p.contact_id,
            event_id=p.event_id,
            status=p.status,
            source=p.source,
            detection_id=p.detection_id,
            created_at=p.created_at,
        )
        for p in items_result.scalars().all()
    ]

    return RecognitionHistoryResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# Backfill router  (prefix /enrollment)
# ---------------------------------------------------------------------------
backfill_router = APIRouter(prefix="/enrollment", tags=["backfill"])


@backfill_router.post(
    "/backfill",
    response_model=BackfillResponse,
    status_code=status.HTTP_200_OK,
)
async def backfill_enrollment(
    body: BackfillRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
) -> BackfillResponse:
    """Re-push all on-disk enrolled samples to CompreFace.

    Scans STORAGE_PATH/enrolled/ for subject directories, reads each
    sample_*_full.jpg, and calls CompreFace add_example for each.
    Skips subjects that have purged_at set.

    dry_run=True counts samples without pushing anything.
    """
    from app.config import legacy_settings
    base_path = pathlib.Path(
        os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)
    )
    enrolled_root = base_path / "enrolled"

    subjects_scanned = 0
    samples_found = 0
    samples_pushed = 0
    errors = 0

    client = ComprefaceClient()
    try:
        if not enrolled_root.exists():
            return BackfillResponse(
                subjects_scanned=0,
                samples_found=0,
                samples_pushed=0,
                errors=0,
                dry_run=body.dry_run,
            )

        for subject_dir in sorted(enrolled_root.iterdir()):
            if not subject_dir.is_dir():
                continue

            compreface_subject_id = subject_dir.name
            subjects_scanned += 1

            # Skip purged subjects
            result = await db.execute(
                select(ComprefaceSubject).where(
                    ComprefaceSubject.compreface_subject_id == compreface_subject_id
                )
            )
            subject = result.scalar_one_or_none()
            if subject is not None and subject.purged_at is not None:
                logger.info(
                    "backfill: skipping purged subject %s", compreface_subject_id
                )
                continue

            full_images = sorted(subject_dir.glob("sample_*_full.jpg"))
            samples_found += len(full_images)

            if body.dry_run:
                continue

            for img_path in full_images:
                try:
                    img_bytes = img_path.read_bytes()
                    image_id = await client.add_example(compreface_subject_id, img_bytes)
                    if image_id is None:
                        logger.warning(
                            "backfill: add_example returned None for %s / %s",
                            compreface_subject_id,
                            img_path.name,
                        )
                        errors += 1
                        continue

                    # Upsert FaceSample row if a matching path doesn't already exist
                    thumb_name = img_path.name.replace("_full.jpg", "_thumb.jpg")
                    full_rel = str(img_path.relative_to(base_path))
                    thumb_rel = str((img_path.parent / thumb_name).relative_to(base_path))

                    existing = await db.execute(
                        select(FaceSample).where(
                            FaceSample.compreface_subject_id == compreface_subject_id,
                            FaceSample.image_path == full_rel,
                        )
                    )
                    existing_sample = existing.scalar_one_or_none()
                    if existing_sample is None:
                        db.add(
                            FaceSample(
                                compreface_subject_id=compreface_subject_id,
                                image_path=full_rel,
                                thumb_path=thumb_rel,
                                source="backfill",
                                compreface_image_id=image_id,
                            )
                        )
                    else:
                        existing_sample.compreface_image_id = image_id

                    samples_pushed += 1
                except Exception:
                    errors += 1
                    logger.warning(
                        "backfill: error processing %s / %s",
                        compreface_subject_id,
                        img_path.name,
                        exc_info=True,
                    )

            # Recompute sample_count for the subject
            if subject is not None and not body.dry_run:
                cnt_result = await db.execute(
                    select(func.count()).where(
                        FaceSample.compreface_subject_id == compreface_subject_id
                    )
                )
                subject.sample_count = cnt_result.scalar_one() or 0

        if not body.dry_run:
            await db.commit()

    finally:
        await client.close()

    return BackfillResponse(
        subjects_scanned=subjects_scanned,
        samples_found=samples_found,
        samples_pushed=samples_pushed,
        errors=errors,
        dry_run=body.dry_run,
    )
