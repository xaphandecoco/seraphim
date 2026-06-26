"""BackfillService — reconcile compreface_subjects against CompreFace live list.

Acceptance criteria (s07-backfill-service):
- dry_run=True returns BackfillReportResponse without committing any DB changes.
- dry_run=False commits orphan flags and seeded face_samples rows.
- image_path uniqueness checked before inserting face_samples to prevent
  duplicates on re-run (idempotent).
- purged_at IS NOT NULL subjects are skipped entirely.
- Unregistered CompreFace subjects (present in CF but absent from DB) are
  logged only — no DB rows created.
- Returns BackfillReport with orphans_found, samples_seeded,
  unregistered_cf_subjects, dry_run fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, Contact, FaceSample
from app.services.compreface import ComprefaceClient
from app.services.face_storage import FaceStorage

logger = logging.getLogger(__name__)


@dataclass
class BackfillReport:
    """Plain result returned by BackfillService.run()."""

    dry_run: bool
    orphans_found: int = 0
    samples_seeded: int = 0
    unregistered_cf_subjects: int = 0
    message: str = ""


class BackfillService:
    """Reconcile compreface_subjects rows against CompreFace and on-disk crops."""

    async def run(
        self,
        session: AsyncSession,
        compreface: ComprefaceClient,
        storage: FaceStorage,
        dry_run: bool = True,
    ) -> BackfillReport:
        """Run the backfill reconciliation.

        Args:
            session: async SQLAlchemy session (caller owns lifecycle).
            compreface: live CompreFace client.
            storage: FaceStorage instance (used for disk-path resolution only).
            dry_run: when True, all changes are collected but session.rollback()
                     is called before returning — no DB mutations are persisted.

        Returns:
            BackfillReport with counters and dry_run flag.
        """
        report = BackfillReport(dry_run=dry_run)

        # ------------------------------------------------------------------
        # 1. Fetch the live CompreFace subject set.
        # ------------------------------------------------------------------
        try:
            cf_subjects = await compreface.list_subjects()
        except Exception:
            logger.exception("BackfillService: failed to list CompreFace subjects")
            raise

        # Build a set of compreface_subject_id strings present in CF right now.
        live_cf_ids: set[str] = {s.subject for s in cf_subjects}

        # ------------------------------------------------------------------
        # 2. Load all DB compreface_subjects rows.
        # ------------------------------------------------------------------
        db_result = await session.execute(select(ComprefaceSubject))
        db_subjects: list[ComprefaceSubject] = list(db_result.scalars().all())

        # Build a set of compreface_subject_id values present in DB so we can
        # identify CF subjects with no matching DB row.
        db_cf_ids: set[str] = {s.compreface_subject_id for s in db_subjects}

        # ------------------------------------------------------------------
        # 3. Identify unregistered CF subjects (CF has them, DB does not).
        # ------------------------------------------------------------------
        unregistered_ids = live_cf_ids - db_cf_ids
        report.unregistered_cf_subjects = len(unregistered_ids)
        for uid in sorted(unregistered_ids):
            logger.warning(
                "BackfillService: CompreFace subject '%s' has no DB row "
                "(external tool or manual registration) — skipping DB write",
                uid,
            )

        # ------------------------------------------------------------------
        # 4. Process each DB subject.
        # ------------------------------------------------------------------
        enrolled_base: Path = storage.base_path / "enrolled"

        for subject in db_subjects:
            # 4a. Skip purged subjects entirely.
            if subject.purged_at is not None:
                logger.debug(
                    "BackfillService: subject %s (cf_id=%s) is purged — skipping",
                    subject.id,
                    subject.compreface_subject_id,
                )
                continue

            # 4b. Mark orphan if CompreFace has no matching subject.
            if subject.compreface_subject_id not in live_cf_ids:
                if not subject.is_orphan:
                    logger.info(
                        "BackfillService: subject %s (cf_id=%s) not in CF live list "
                        "— marking is_orphan=True",
                        subject.id,
                        subject.compreface_subject_id,
                    )
                    subject.is_orphan = True
                    report.orphans_found += 1
                else:
                    # Already marked; still count it.
                    report.orphans_found += 1
                # Cannot seed samples for a subject CF has deleted.
                continue

            # 4c. Mark orphan if contact_id is NULL or the Contact is absent/deleted.
            if subject.contact_id is None:
                if not subject.is_orphan:
                    logger.info(
                        "BackfillService: subject %s (cf_id=%s) has NULL contact_id "
                        "— marking is_orphan=True",
                        subject.id,
                        subject.compreface_subject_id,
                    )
                    subject.is_orphan = True
                    report.orphans_found += 1
                else:
                    report.orphans_found += 1
                continue

            contact: Optional[Contact] = await session.get(Contact, subject.contact_id)
            if contact is None or contact.is_deleted:
                if not subject.is_orphan:
                    logger.info(
                        "BackfillService: subject %s (cf_id=%s) — contact %s is "
                        "absent or deleted — marking is_orphan=True",
                        subject.id,
                        subject.compreface_subject_id,
                        subject.contact_id,
                    )
                    subject.is_orphan = True
                    report.orphans_found += 1
                else:
                    report.orphans_found += 1
                continue

            # 4d. Seed face_samples rows from on-disk files if none exist yet.
            seeded = await self._seed_samples_from_disk(
                session=session,
                subject=subject,
                enrolled_base=enrolled_base,
            )
            report.samples_seeded += seeded

        # ------------------------------------------------------------------
        # 5. Commit or rollback depending on dry_run.
        # ------------------------------------------------------------------
        if dry_run:
            await session.rollback()
            report.message = (
                f"Dry run complete: {report.orphans_found} orphan(s) would be "
                f"flagged, {report.samples_seeded} sample row(s) would be seeded, "
                f"{report.unregistered_cf_subjects} unregistered CF subject(s) logged."
            )
            logger.info("BackfillService: dry_run=True — rolled back all changes. %s", report.message)
        else:
            await session.commit()
            report.message = (
                f"Backfill committed: {report.orphans_found} orphan(s) flagged, "
                f"{report.samples_seeded} sample row(s) seeded, "
                f"{report.unregistered_cf_subjects} unregistered CF subject(s) logged."
            )
            logger.info("BackfillService: committed. %s", report.message)

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _seed_samples_from_disk(
        self,
        session: AsyncSession,
        subject: ComprefaceSubject,
        enrolled_base: Path,
    ) -> int:
        """Seed FaceSample rows from on-disk full-size crops for a subject.

        Only seeds if the subject currently has zero face_samples rows.
        Checks image_path uniqueness before inserting to stay idempotent
        across repeated dry_run=False calls.

        Returns the number of rows inserted (or that would be inserted in
        dry_run — session.rollback is handled by the caller).
        """
        # Count existing face_samples for this subject.
        count_result = await session.execute(
            select(func.count(FaceSample.id)).where(
                FaceSample.compreface_subject_id == subject.compreface_subject_id
            )
        )
        existing_count: int = count_result.scalar() or 0
        if existing_count > 0:
            # Already has samples — nothing to backfill.
            return 0

        # Locate the on-disk directory for this subject.
        subject_dir: Path = enrolled_base / subject.compreface_subject_id
        if not subject_dir.is_dir():
            logger.debug(
                "BackfillService: no enrolled directory for subject %s at %s",
                subject.id,
                subject_dir,
            )
            return 0

        # Collect all sample_*_full.jpg files in the directory.
        full_files = sorted(subject_dir.glob("sample_*_full.jpg"))
        if not full_files:
            logger.debug(
                "BackfillService: enrolled directory for subject %s has no "
                "sample_*_full.jpg files",
                subject.id,
            )
            return 0

        # Derive relative paths from storage base (two levels up from enrolled_base).
        storage_base: Path = enrolled_base.parent

        seeded = 0
        for full_file in full_files:
            try:
                rel_full = str(full_file.relative_to(storage_base))
            except ValueError:
                logger.warning(
                    "BackfillService: file %s is outside storage base %s — skipping",
                    full_file,
                    storage_base,
                )
                continue

            # Normalize path separators to forward-slash for cross-platform
            # storage consistency (DB always stores POSIX-style paths).
            rel_full_posix = rel_full.replace("\\", "/")

            # Uniqueness check: skip if this image_path already has a FaceSample row.
            exists_result = await session.execute(
                select(func.count(FaceSample.id)).where(
                    FaceSample.image_path == rel_full_posix
                )
            )
            if (exists_result.scalar() or 0) > 0:
                logger.debug(
                    "BackfillService: face_samples row for image_path '%s' already "
                    "exists — skipping duplicate",
                    rel_full_posix,
                )
                continue

            # Derive thumbnail path (may not exist on disk; stored as NULL if absent).
            thumb_file = full_file.with_name(full_file.name.replace("_full.jpg", "_thumb.jpg"))
            rel_thumb_posix: Optional[str] = None
            if thumb_file.exists():
                try:
                    rel_thumb_posix = str(thumb_file.relative_to(storage_base)).replace("\\", "/")
                except ValueError:
                    pass

            sample = FaceSample(
                contact_id=subject.contact_id,
                compreface_subject_id=subject.compreface_subject_id,
                image_path=rel_full_posix,
                thumb_path=rel_thumb_posix if rel_thumb_posix is not None else rel_full_posix,
                source="backfill",
                compreface_image_id=None,  # cannot recover from CF — best-effort
            )
            session.add(sample)
            seeded += 1
            logger.debug(
                "BackfillService: queued FaceSample for subject %s path=%s",
                subject.id,
                rel_full_posix,
            )

        if seeded:
            # Flush so that subsequent uniqueness checks within the same run
            # see the newly-added rows even before commit/rollback.
            await session.flush()
            logger.info(
                "BackfillService: seeded %d face_sample row(s) for subject %s (cf_id=%s)",
                seeded,
                subject.id,
                subject.compreface_subject_id,
            )

        return seeded
