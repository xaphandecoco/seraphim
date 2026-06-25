"""EnrollmentService — atomic face enrollment for a Contact.

Responsibilities
----------------
* enroll_contact_face  — create/reuse ComprefaceSubject, write FaceSample,
                         call CompreFace add_subject + add_example atomically.
* purge_subject_local  — stub (raises NotImplementedError; purge logic lives in
                         a dedicated purge service so the boundary is clean).
* reassign_subject_contact — move a ComprefaceSubject to a different Contact
                             without touching CompreFace.
* _count_samples       — SELECT COUNT(*) on face_samples; never blind-increments.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, Contact, FaceSample
from app.services.compreface import ComprefaceClient
from app.services.face_storage import FaceStorage
from app.services.quality_gate import FaceQualityGate

logger = logging.getLogger(__name__)

_SourceLiteral = Literal["manual", "detection", "bulk_ingest", "backfill"]


class EnrollmentService:
    """Enroll face samples for contacts, backed by CompreFace.

    Parameters
    ----------
    client:
        A fully-constructed :class:`ComprefaceClient`.  The caller owns the
        lifetime (open/close); this service never closes the client.
    storage:
        A :class:`FaceStorage` instance rooted at the deployment's STORAGE_PATH.
    """

    def __init__(self, client: ComprefaceClient, storage: FaceStorage) -> None:
        self.client = client
        self.storage = storage

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enroll_contact_face(
        self,
        session: AsyncSession,
        contact_id: int,
        face_crop: np.ndarray,
        source: _SourceLiteral = "manual",
    ) -> FaceSample:
        """Create a face sample for *contact_id* and register it with CompreFace.

        Steps (all acceptance-criteria-ordered):
        1. Quality gate — 422 on failure with zero DB/disk writes.
        2. Resolve or create ComprefaceSubject — 409 if purged_at IS NOT NULL.
        3. Write the face crop to disk via storage.save_enrollment().
        4. Open a DB transaction:
           a. Ensure subject exists in CompreFace (add_subject idempotent).
           b. call add_example; if it returns None → delete disk file best-effort,
              raise 502 without committing.
           c. Insert FaceSample row with the returned compreface_image_id.
           d. Recompute sample_count via _count_samples (includes the new row).
        5. Commit and return the FaceSample.

        Raises
        ------
        HTTPException 404 — contact not found.
        HTTPException 409 — subject has been purged (purged_at IS NOT NULL).
        HTTPException 422 — quality gate failure.
        HTTPException 502 — CompreFace add_example returned None.
        """
        # ---- 1. Quality gate (zero side-effects on failure) ----
        ok, reason = FaceQualityGate.check(face_crop)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Face quality check failed: {reason}",
            )

        # ---- 2. Verify contact exists ----
        contact = await session.get(Contact, contact_id)
        if contact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Contact {contact_id} not found",
            )

        # ---- 3. Resolve or create ComprefaceSubject ----
        subject_id_str = f"contact_{contact_id}"
        result = await session.execute(
            select(ComprefaceSubject).where(
                ComprefaceSubject.compreface_subject_id == subject_id_str
            )
        )
        subject = result.scalar_one_or_none()

        if subject is None:
            subject = ComprefaceSubject(
                subject_name=f"{contact.first_name} {contact.last_name}",
                compreface_subject_id=subject_id_str,
                contact_id=contact_id,
                enrollment_status="pending",
                sample_count=0,
            )
            session.add(subject)
            await session.flush()  # assign PK without committing

        if subject.purged_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Subject {subject_id_str} has been purged and cannot accept new samples",
            )

        # ---- 4. Write disk file ----
        full_path, thumb_path = await self.storage.save_enrollment(
            subject_id_str, face_crop
        )

        # ---- 5. CompreFace + DB transaction ----
        try:
            # Ensure the subject exists in CompreFace (idempotent).
            await self.client.add_subject(subject_id_str)

            # Upload the example image.
            image_bytes = self._read_disk_file(full_path)
            image_id: Optional[str] = await self.client.add_example(
                subject_id_str, image_bytes
            )

            if image_id is None:
                # Rollback: delete disk file best-effort, then raise 502.
                self._delete_disk_file_best_effort(full_path, thumb_path)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="CompreFace add_example returned no image_id; enrollment aborted",
                )

            # Insert FaceSample row.
            sample = FaceSample(
                compreface_subject_id=subject_id_str,
                image_path=full_path,
                thumb_path=thumb_path,
                source=source,
                compreface_image_id=image_id,
            )
            session.add(sample)
            await session.flush()  # persist so _count_samples includes this row

            # Recompute sample_count — never blind-increment.
            subject.sample_count = await self._count_samples(session, subject_id_str)
            subject.enrollment_status = "active"

            await session.commit()
            await session.refresh(sample)
            return sample

        except HTTPException:
            await session.rollback()
            raise
        except Exception as exc:
            logger.error(
                "EnrollmentService.enroll_contact_face failed: contact_id=%s subject=%s error=%s",
                contact_id,
                subject_id_str,
                exc,
                exc_info=True,
            )
            self._delete_disk_file_best_effort(full_path, thumb_path)
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Enrollment failed due to an internal error",
            )

    async def purge_subject_local(
        self,
        session: AsyncSession,  # noqa: ARG002  (kept for future caller signature)
        compreface_subject_id: str,  # noqa: ARG002
    ) -> None:
        """Purge a subject locally.

        Not yet implemented — dedicated purge service owns this boundary.
        """
        raise NotImplementedError(
            "purge_subject_local is not implemented in EnrollmentService; "
            "use the dedicated purge service."
        )

    async def reassign_subject_contact(
        self,
        session: AsyncSession,
        compreface_subject_id: str,
        new_contact_id: int,
    ) -> ComprefaceSubject:
        """Reassign a ComprefaceSubject to a different Contact.

        Does NOT touch CompreFace — the subject_id key in CompreFace remains
        unchanged.  Only the local FK and subject_name are updated.

        Raises
        ------
        HTTPException 404 — subject or new contact not found.
        """
        result = await session.execute(
            select(ComprefaceSubject).where(
                ComprefaceSubject.compreface_subject_id == compreface_subject_id
            )
        )
        subject = result.scalar_one_or_none()
        if subject is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subject {compreface_subject_id} not found",
            )

        new_contact = await session.get(Contact, new_contact_id)
        if new_contact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Contact {new_contact_id} not found",
            )

        subject.contact_id = new_contact_id
        subject.subject_name = f"{new_contact.first_name} {new_contact.last_name}"
        await session.commit()
        await session.refresh(subject)
        return subject

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _count_samples(
        self, session: AsyncSession, compreface_subject_id: str
    ) -> int:
        """Return SELECT COUNT(*) FROM face_samples WHERE compreface_subject_id=?."""
        result = await session.execute(
            select(func.count()).where(
                FaceSample.compreface_subject_id == compreface_subject_id
            )
        )
        return result.scalar_one()

    def _read_disk_file(self, rel_path: str) -> bytes:
        """Read the full image bytes written by save_enrollment."""
        full = self.storage.base_path / rel_path
        return full.read_bytes()

    def _delete_disk_file_best_effort(
        self, full_path: str, thumb_path: str
    ) -> None:
        """Delete disk files without raising on failure (best-effort rollback)."""
        for rel in (full_path, thumb_path):
            try:
                p = Path(self.storage.base_path) / rel
                p.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not delete rollback file %s: %s", rel, exc
                )
