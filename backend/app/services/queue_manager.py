import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import dynamic_settings, legacy_settings
from app.database import async_session
import cv2
import numpy as np

from app.models import ComprefaceSubject, Detection, ExportJob, Task
from app.services.compreface import ComprefaceClient
from app.services.enrollment import EnrollmentService
from app.services.face_cleanup import FaceCleanupService
from app.services.face_storage import FaceStorage

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession] = None):
        self.db_session_factory = db_session_factory or async_session
        self.worker_id = legacy_settings.WORKER_ID
        self.poll_interval = legacy_settings.POLL_INTERVAL
        self._last_cleanup_run: Optional[datetime] = None

    async def run(self):
        """Main loop: poll for background jobs."""
        logger.info("Background worker %s started", self.worker_id)
        while True:
            try:
                # Reload settings each cycle so admin changes take effect without restart
                async with self.db_session_factory() as session:
                    await dynamic_settings.reload(session)

                worked = False
                worked |= await self._process_enrollment()
                worked |= await self._process_expired_tasks()
                worked |= await self._process_face_cleanup()
                worked |= await self._process_export_jobs()
                await self._purge_expired_export_jobs()
                await self._purge_expired_imports()
                if not worked:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background worker loop error")
                await asyncio.sleep(self.poll_interval)

    async def _process_enrollment(self) -> bool:
        """Process pending Compreface enrollments.

        Returns True if work was done.
        """
        async with self.db_session_factory() as session:
            result = await session.execute(
                select(ComprefaceSubject)
                .where(ComprefaceSubject.enrollment_status == "pending")
                .where(ComprefaceSubject.purged_at.is_(None))
                .limit(5)
            )
            subjects = result.scalars().all()
            if not subjects:
                return False

            client = ComprefaceClient()
            storage = FaceStorage(legacy_settings.STORAGE_PATH)
            service = EnrollmentService(client, storage)
            try:
                for subject in subjects:
                    logger.info(
                        "Enrollment: subject=%s contact_id=%s",
                        subject.compreface_subject_id, subject.contact_id
                    )

                    # Find the most recent detection for this subject
                    det_result = await session.execute(
                        select(Detection)
                        .where(Detection.compreface_subject_id == subject.compreface_subject_id)
                        .where(Detection.is_enrolled.is_(True))
                        .order_by(Detection.created_at.desc())
                        .limit(1)
                    )
                    detection = det_result.scalar_one_or_none()
                    if not detection or not detection.image_path:
                        logger.warning(
                            "No detection image found for subject %s",
                            subject.compreface_subject_id
                        )
                        continue

                    # Read full image bytes and decode to ndarray for EnrollmentService
                    image_bytes = storage.read_detection_full_image(detection.image_path)
                    if not image_bytes:
                        logger.error(
                            "Enrollment image not found for subject %s",
                            subject.compreface_subject_id
                        )
                        continue

                    face_crop = cv2.imdecode(
                        np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if face_crop is None:
                        logger.error(
                            "Failed to decode enrollment image for subject %s",
                            subject.compreface_subject_id
                        )
                        continue

                    try:
                        await service.enroll_contact_face(
                            session,
                            contact_id=subject.contact_id,
                            face_crop=face_crop,
                            source="detection",
                        )
                        logger.info(
                            "Enrolled subject %s", subject.compreface_subject_id
                        )
                    except Exception:
                        logger.exception(
                            "Enrollment failed for subject %s contact_id=%s",
                            subject.compreface_subject_id, subject.contact_id
                        )
            finally:
                await client.close()

            return True

    async def _process_face_cleanup(self) -> bool:
        """Run face image retention cleanup once per day.

        Returns True if work was done.
        """
        now = datetime.now(timezone.utc)
        if self._last_cleanup_run and (now - self._last_cleanup_run) < timedelta(hours=24):
            return False

        retention_days = dynamic_settings.get_face_retention_days()
        storage_path = Path(legacy_settings.STORAGE_PATH)
        service = FaceCleanupService(storage_path, retention_days)

        async with self.db_session_factory() as session:
            result = await service.run_cleanup(session)
            self._last_cleanup_run = now
            return result.deleted > 0 or result.errors > 0

    async def _process_expired_tasks(self) -> bool:
        """Mark expired tasks as expired.

        Returns True if work was done.
        """
        # Use naive UTC to match naive DateTime columns
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        async with self.db_session_factory() as session:
            result = await session.execute(
                select(Task)
                .where(Task.status == "pending")
                .where(Task.expiry_date < now)
                .limit(50)
            )
            expired = result.scalars().all()
            if not expired:
                return False

            for task in expired:
                task.status = "expired"
                logger.info("Task %s expired", task.id)

            await session.commit()
            return True

    # ---------------------------------------------------------------------------
    # Export job processor (S05-F09)
    # ---------------------------------------------------------------------------

    async def _collect_csv_to_file(
        self,
        job: ExportJob,
        dest_path: Path,
    ) -> int:
        """Stream CSV for *job* into *dest_path* and return the data-row count.

        Opens its own DB session so the streaming cursor is isolated from the
        outer session that holds the row-lock on the ExportJob.
        """
        from app.services.export_service import (
            stream_contacts_csv,
            stream_participants_csv,
        )

        params: dict = job.params or {}
        job_type: str = job.job_type

        async with self.db_session_factory() as read_session:
            if job_type == "attendance":
                stream = stream_participants_csv(read_session, params)
            elif job_type == "contacts":
                stream = stream_contacts_csv(read_session, params)
            else:
                raise ValueError(f"Unknown export job_type: {job_type!r}")

            row_count = 0
            # Byte-count the BOM (3 bytes) + header row for the first chunk;
            # subsequent chunks are all data rows.  We count newlines to
            # approximate data rows without parsing.
            first_chunk = True
            with dest_path.open("wb") as fh:
                async for chunk in stream:
                    fh.write(chunk)
                    if first_chunk:
                        # Strip BOM + header line from newline count
                        stripped = chunk.lstrip(b"\xef\xbb\xbf")
                        # The first chunk contains BOM + header row + optional data rows
                        lines = stripped.count(b"\n")
                        # Subtract 1 for header row
                        row_count += max(0, lines - 1)
                        first_chunk = False
                    else:
                        row_count += chunk.count(b"\n")

        return row_count

    async def _process_export_jobs(self) -> bool:
        """Pick the oldest pending ExportJob and process it.

        Returns True if work was done (a job was picked up), False otherwise.

        Uses SELECT ... FOR UPDATE SKIP LOCKED on Postgres so multiple worker
        processes don't race on the same job.  On SQLite (tests) the hint is
        ignored — that is fine.
        """
        async with self.db_session_factory() as session:
            stmt = (
                select(ExportJob)
                .where(ExportJob.status == "pending")
                .order_by(ExportJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            job: Optional[ExportJob] = result.scalar_one_or_none()
            if job is None:
                return False

            # Transition: pending -> running
            job.status = "running"
            await session.commit()
            logger.info(
                "ExportJob %s started: type=%s fmt=%s",
                job.id, job.job_type, job.fmt,
            )

        # Work is done outside the lock so the session above is closed.
        # Re-open to write final status.
        async with self.db_session_factory() as session:
            # Reload the job so this session can mutate it
            result = await session.execute(
                select(ExportJob).where(ExportJob.id == job.id)
            )
            job = result.scalar_one()

            try:
                # Build destination path
                exports_dir = Path(legacy_settings.STORAGE_PATH) / "exports"
                exports_dir.mkdir(parents=True, exist_ok=True)

                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                filename = f"{job.job_type}_{job.id}_{timestamp}.{job.fmt}"
                dest_path = exports_dir / filename

                # HIGH 2 defense-in-depth: resolve the destination path and
                # confirm it stays inside exports_dir before writing.  The
                # router now allowlists job_type and fmt at create time, but
                # jobs created before that fix or via direct DB access could
                # still carry malicious values, so we re-check here at the
                # write primitive.
                resolved_dest = dest_path.resolve()
                resolved_exports_dir = exports_dir.resolve()
                try:
                    resolved_dest.relative_to(resolved_exports_dir)
                except ValueError:
                    raise ValueError(
                        f"ExportJob {job.id}: computed path {resolved_dest} "
                        f"escapes exports directory {resolved_exports_dir}"
                    )

                row_count = await self._collect_csv_to_file(job, dest_path)

                file_bytes = dest_path.stat().st_size

                # expires_at = created_at + 24h  (created_at is naive UTC)
                created_naive = job.created_at
                expires_at = created_naive + timedelta(hours=24)

                job.status = "ready"
                job.file_path = f"exports/{filename}"
                job.file_bytes = file_bytes
                job.row_count = row_count
                job.expires_at = expires_at
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

                logger.info(
                    "ExportJob %s ready: rows=%d bytes=%d path=%s",
                    job.id, row_count, file_bytes, job.file_path,
                )

            except Exception as exc:
                error_text = str(exc)[:500]
                job.status = "failed"
                job.error = error_text
                job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                logger.exception("ExportJob %s failed: %s", job.id, error_text)

            await session.commit()

        await self._record_job_run("export_jobs")
        return True

    async def _purge_expired_export_jobs(self) -> None:
        """Transition ready/failed ExportJobs past their expires_at to 'expired'.

        Also unlinks the associated file (if any) and nulls file_path.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        async with self.db_session_factory() as session:
            result = await session.execute(
                select(ExportJob)
                .where(ExportJob.status.in_(["ready", "failed"]))
                .where(ExportJob.expires_at <= now)
                .limit(50)
            )
            jobs = result.scalars().all()
            if not jobs:
                return

            for job in jobs:
                # Unlink file if present
                if job.file_path:
                    file_abs = Path(legacy_settings.STORAGE_PATH) / job.file_path
                    try:
                        file_abs.unlink(missing_ok=True)
                    except OSError:
                        logger.warning(
                            "ExportJob %s: could not delete file %s",
                            job.id, file_abs,
                        )
                    job.file_path = None

                job.status = "expired"
                logger.info("ExportJob %s expired", job.id)

            await session.commit()

    async def _purge_expired_imports(self) -> None:
        """Transition staged ImportBatch records past their expires_at.

        Unlinks the staged file and nulls staging_file on each expired batch.
        Mirrors _purge_expired_export_jobs so the pattern is consistent.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        async with self.db_session_factory() as session:
            from app.models import ImportBatch  # noqa: PLC0415

            result = await session.execute(
                select(ImportBatch)
                .where(ImportBatch.status == "staged")
                .where(ImportBatch.expires_at <= now)
                .limit(50)
            )
            batches = result.scalars().all()
            if not batches:
                return

            from pathlib import Path as _Path  # noqa: PLC0415

            staging_root = _Path(legacy_settings.STORAGE_PATH).resolve()

            for batch in batches:
                if batch.staging_file:
                    file_abs = _Path(legacy_settings.STORAGE_PATH) / batch.staging_file
                    # Defense-in-depth: re-check path containment even though
                    # staging_file is server-generated.  Mirrors the pattern in
                    # _process_export_jobs (resolved_dest.relative_to(exports_dir)).
                    try:
                        file_abs.resolve().relative_to(staging_root)
                    except ValueError:
                        logger.warning(
                            "ImportBatch %s: staging_file %r escapes STORAGE_PATH; skipping unlink",
                            batch.id,
                            batch.staging_file,
                        )
                        batch.staging_file = None
                        batch.status = "expired"
                        continue
                    try:
                        file_abs.unlink(missing_ok=True)
                    except OSError:
                        logger.warning(
                            "ImportBatch %s: could not delete staging file %s",
                            batch.id,
                            file_abs,
                        )
                    batch.staging_file = None

                batch.status = "expired"
                logger.info("ImportBatch %s staging expired", batch.id)

            await session.commit()

    async def _record_job_run(self, job_name: str) -> None:
        """Insert a row into the S16-owned job_runs table if it exists.

        Guarded: if the table does not yet exist (S16 migration not yet
        applied) the method silently returns so this worker never crashes
        because of a missing table.
        """
        try:
            async with self.db_session_factory() as session:
                # Check table existence without inspecting dialect-specific catalogs
                await session.execute(
                    text(
                        "INSERT INTO job_runs (job_name, status, started_at) "
                        "VALUES (:job_name, :status, :started_at)"
                    ),
                    {
                        "job_name": job_name,
                        "status": "success",
                        "started_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    },
                )
                await session.commit()
        except Exception:
            # Table does not exist yet (S16 not applied) or any other error — ignore
            pass
