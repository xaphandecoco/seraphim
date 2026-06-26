"""BiometricPurgeService — RTBF hard-delete for a contact's biometric data.

Ownership: S08 (Backend Wave 2).

State machine
-------------
Three states drive idempotency and retry logic:

  FRESH       purge_detail is None (or has no _cf_subject_id key)
              → run full purge sequence

  PARTIAL     purge_detail has _cf_subject_id key, purged_at IS NULL
              → local erasure already done; retry CF delete + remaining files only

  COMPLETE    purge_detail.compreface_deleted=True AND purged_at IS NOT NULL
              → return stored result immediately

Purge sequence (FRESH path):
1.  Lock consent + subject rows (SELECT FOR UPDATE).
2.  Pre-load ALL detections with compreface_subject_id==cf_subject_id (not just enrolled).
    Also load face_samples for the contact.
3.  Erase enrolled photo files + thumbs from disk:
    (a) Glob enrolled/{subject_id}/sample_* (catch-all)
    (b) Also unlink every face_sample.image_path / thumb_path from DB (authoritative).
4.  Anonymize ALL detection crops linked to subject:
    set image_path="", deleted_at=utc_now(), compreface_subject_id=NULL, matched_name=NULL.
5.  DB-delete face_samples rows.
6.  Call ComprefaceClient.delete_subject — HTTP 404 treated as success.
7.  Stamp compreface_subjects: enrollment_status="purged", compreface_subject_id=NULL,
    sample_count=0.
8.  Stamp consent:
    - FULLY CLEAN (no errors AND CF deleted): set purged_at=utc_now(), audit "biometric_purged"
    - PARTIAL: leave purged_at=NULL, store purge_detail with _cf_subject_id, audit "biometric_purge_failed"
9.  Commit.

Error strings in purge_detail are SANITIZED (no absolute paths or exception detail);
full paths and exception messages go to the logger only.

RETAINED: contacts row, all participant rows, the consent row (stamped).
ERASED:   face_samples rows, enrolled image files, ALL detection crop files linked to subject.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BiometricConsent,
    ComprefaceSubject,
    Detection,
    FaceSample,
    utc_now,
)
from app.services import audit
from app.services.face_cleanup import _delete_image_files

logger = logging.getLogger(__name__)

# Sentinel key stored in purge_detail to track the CF subject id across retries.
_CF_ID_KEY = "_cf_subject_id_at_purge"


class BiometricPurgeService:
    """Execute RTBF purge for a single contact."""

    def __init__(self, storage_base: Path) -> None:
        self.storage_base = storage_base

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def purge(
        self,
        db: AsyncSession,
        contact_id: int,
        actor_id: Optional[int],
    ) -> dict[str, Any]:
        """Run the RTBF purge for *contact_id*.

        Returns {"status": "purged"|"already_purged", "purge_detail": {...}}.
        Commits internally; callers must NOT wrap in their own commit.
        """
        # ----- Lock rows --------------------------------------------------
        consent_result = await db.execute(
            select(BiometricConsent)
            .where(BiometricConsent.contact_id == contact_id)
            .with_for_update()
        )
        consent: Optional[BiometricConsent] = consent_result.scalar_one_or_none()

        subject_result = await db.execute(
            select(ComprefaceSubject)
            .where(ComprefaceSubject.contact_id == contact_id)
            .with_for_update()
        )
        subject: Optional[ComprefaceSubject] = subject_result.scalar_one_or_none()

        # ----- Determine state --------------------------------------------
        existing_detail: dict[str, Any] = {}
        if consent is not None and consent.purge_detail:
            existing_detail = dict(consent.purge_detail)

        is_complete = (
            consent is not None
            and consent.purged_at is not None
            and existing_detail.get("compreface_deleted", False)
        )
        is_partial = (
            not is_complete
            and _CF_ID_KEY in existing_detail  # previous attempt recorded
        )

        if is_complete:
            return {"status": "already_purged", "purge_detail": existing_detail}

        if is_partial:
            return await self._retry_partial(db, consent, existing_detail, actor_id)

        # ----- Fresh purge ------------------------------------------------
        return await self._fresh_purge(db, consent, subject, contact_id, actor_id)

    # ------------------------------------------------------------------
    # Partial-failure retry
    # ------------------------------------------------------------------

    async def _retry_partial(
        self,
        db: AsyncSession,
        consent: Optional[BiometricConsent],
        detail: dict[str, Any],
        actor_id: Optional[int],
    ) -> dict[str, Any]:
        """Re-attempt CF delete (+ stray disk files) after a prior partial failure.

        Local DB erasure (samples, detection crops) is already done.
        Uses ONLY this retry attempt's errors to determine fully_clean; historical
        errors from prior attempts are preserved in the detail blob but do not
        block stamping purged_at if this retry succeeds cleanly.
        """
        cf_subject_id: Optional[str] = detail.get(_CF_ID_KEY)
        # Track only THIS retry's errors for the fully_clean check
        retry_errors: list[str] = []

        # Re-scan enrolled dir for any files that failed to delete last time
        extra_files = 0
        if cf_subject_id:
            extra_files, scan_errors = self._erase_enrolled_dir(cf_subject_id)
            for e in scan_errors:
                logger.error("biometric_purge retry file error: %s", e)
            if scan_errors:
                retry_errors.append("enrolled_file_delete_error")

        cf_deleted, cf_err = await self._delete_cf_subject(cf_subject_id)
        if not cf_deleted and cf_err:
            logger.error("biometric_purge retry CF error: %s", cf_err)
            retry_errors.append("compreface_delete_error")

        # Merge: preserve prior error history, add retry errors, update live fields
        all_errors = list(detail.get("errors", [])) + retry_errors
        detail = {
            **detail,
            "compreface_deleted": cf_deleted,
            "errors": all_errors,
        }
        if extra_files:
            detail["files_deleted"] = detail.get("files_deleted", 0) + extra_files

        now = utc_now()
        # fully_clean is based on THIS retry's result only
        fully_clean = cf_deleted and not retry_errors

        entity_id: Optional[int] = consent.id if consent else None

        if consent is not None:
            consent.purge_detail = detail
            consent.updated_at = now
            if fully_clean:
                consent.purged_at = now

        await db.flush()
        action = "biometric_purged" if fully_clean else "biometric_purge_failed"
        await audit.record(
            db,
            actor_id=actor_id,
            action=action,
            entity="biometric_consent",
            entity_id=entity_id,
            before=None,
            after=detail,
        )
        await db.commit()

        status = "purged" if fully_clean else "partial_failure"
        return {"status": status, "purge_detail": detail}

    # ------------------------------------------------------------------
    # Fresh purge
    # ------------------------------------------------------------------

    async def _fresh_purge(
        self,
        db: AsyncSession,
        consent: Optional[BiometricConsent],
        subject: Optional[ComprefaceSubject],
        contact_id: int,
        actor_id: Optional[int],
    ) -> dict[str, Any]:
        cf_subject_id: Optional[str] = subject.compreface_subject_id if subject else None

        errors: list[str] = []
        files_deleted = 0
        samples_deleted = 0
        detections_cleared = 0

        # ----- 3a. Erase enrolled dir (glob) ------------------------------
        if cf_subject_id:
            count, glob_errors = self._erase_enrolled_dir(cf_subject_id)
            files_deleted += count
            for e in glob_errors:
                logger.error("biometric_purge enrolled-dir error: %s", e)
                errors.append("enrolled_file_delete_error")

        # ----- Pre-load all DB objects before any modification ------------
        # (SQLAlchemy autoflush fires on SELECT after in-memory mutation)

        samples_result = await db.execute(
            select(FaceSample).where(FaceSample.contact_id == contact_id)
        )
        face_samples = list(samples_result.scalars().all())

        # ALL detections linked to this CF subject (enrolled + recognition crops)
        linked_detections: list[Detection] = []
        if cf_subject_id:
            det_result = await db.execute(
                select(Detection).where(
                    Detection.compreface_subject_id == cf_subject_id,
                    Detection.deleted_at.is_(None),
                )
            )
            linked_detections = list(det_result.scalars().all())

        # ----- Mutate (no further SELECTs until flush/commit) -------------
        with db.no_autoflush:
            # 3b. Unlink enrolled files via authoritative DB paths
            for fs in face_samples:
                for rel_path in (fs.image_path, fs.thumb_path):
                    if rel_path:
                        count, errs = _delete_image_files(self.storage_base, rel_path)
                        files_deleted += count
                        for e in errs:
                            logger.error("biometric_purge sample file error: %s", e)
                            errors.append("sample_file_delete_error")

            # 4. Anonymize ALL linked detection crops
            for det in linked_detections:
                if det.image_path:
                    count, errs = _delete_image_files(self.storage_base, det.image_path)
                    files_deleted += count
                    for e in errs:
                        logger.error("biometric_purge detection crop error: %s", e)
                        errors.append("detection_crop_delete_error")
                # Clear path (empty string satisfies NOT NULL in SQLite test env;
                # S08 migration makes this nullable in postgres — see BLOCKERS.md)
                det.image_path = ""  # type: ignore[assignment]
                det.deleted_at = utc_now()
                # Break linkage to purged person
                det.compreface_subject_id = None
                if hasattr(det, "matched_name"):
                    det.matched_name = None
                detections_cleared += 1

            # 5. DB-delete face_samples rows
            for fs in face_samples:
                await db.delete(fs)
                samples_deleted += 1

        await db.flush()

        # 6. CompreFace delete (after local flush to avoid holding locks)
        cf_deleted, cf_err = await self._delete_cf_subject(cf_subject_id)
        if not cf_deleted and cf_err:
            logger.error("biometric_purge CF delete error: %s", cf_err)
            errors.append("compreface_delete_error")

        # 7. Stamp subject
        if subject is not None:
            subject.enrollment_status = "purged"
            subject.compreface_subject_id = None
            subject.sample_count = 0

        # 8. Stamp / create consent
        fully_clean = cf_deleted and not errors
        now = utc_now()
        purge_detail: dict[str, Any] = {
            "files_deleted": files_deleted,
            "samples_deleted": samples_deleted,
            "detections_cleared": detections_cleared,
            "compreface_deleted": cf_deleted,
            "errors": errors,
            # Stored for retry logic — NOT exposed to callers as a meaningful field.
            _CF_ID_KEY: cf_subject_id,
        }

        entity_id: Optional[int] = None
        if consent is not None:
            if fully_clean:
                consent.purged_at = now
            consent.purge_detail = purge_detail
            consent.updated_at = now
            entity_id = consent.id
        else:
            # No consent row yet — create a minimal stub
            new_consent = BiometricConsent(
                contact_id=contact_id,
                consent_given=False,
                purged_at=now if fully_clean else None,
                purge_detail=purge_detail,
            )
            db.add(new_consent)
            await db.flush()
            entity_id = new_consent.id

        # 9. Audit
        action = "biometric_purged" if fully_clean else "biometric_purge_failed"
        await audit.record(
            db,
            actor_id=actor_id,
            action=action,
            entity="biometric_consent",
            entity_id=entity_id,
            before=None,
            after=purge_detail,
        )

        await db.commit()

        status = "purged" if fully_clean else "partial_failure"
        return {"status": status, "purge_detail": purge_detail}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _erase_enrolled_dir(
        self, cf_subject_id: str
    ) -> tuple[int, list[str]]:
        """Glob-erase enrolled/{cf_subject_id}/sample_* from disk.

        Returns (count_deleted, sanitized_errors).
        Full OSError messages go to the logger only.
        """
        enrolled_dir = self.storage_base / "enrolled" / cf_subject_id
        count = 0
        errors: list[str] = []
        if not enrolled_dir.exists():
            return 0, []
        for f in list(enrolled_dir.glob("sample_*")):
            try:
                f.unlink()
                count += 1
            except OSError as exc:
                logger.error("biometric_purge: could not delete %s: %s", f, exc)
                errors.append("enrolled_file_delete_error")
        return count, errors

    async def _delete_cf_subject(
        self, cf_subject_id: Optional[str]
    ) -> tuple[bool, Optional[str]]:
        """Call CompreFace delete_subject; HTTP 404 is treated as success.

        Returns (deleted: bool, sanitized_error: str | None).
        Full exception detail goes to the logger; error string is sanitized.
        """
        if not cf_subject_id:
            return True, None  # Nothing to delete.

        try:
            from app.services.compreface import ComprefaceClient

            client = ComprefaceClient()
            try:
                base_url = client.base_url
                api_key = client.recognize_api_key
                url = f"{base_url}/api/v1/recognition/subjects/{cf_subject_id}"
                resp = await client._client.delete(
                    url, headers={"x-api-key": api_key}
                )
                if resp.status_code in (200, 204, 404):
                    return True, None
                logger.error(
                    "biometric_purge: delete_subject HTTP %s for subject %s",
                    resp.status_code,
                    cf_subject_id,
                )
                return False, "compreface_delete_error"
            finally:
                await client.close()
        except Exception as exc:
            logger.error(
                "biometric_purge: delete_subject raised %s for subject %s",
                type(exc).__name__,
                cf_subject_id,
                exc_info=True,
            )
            return False, "compreface_delete_error"
