import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import dynamic_settings, legacy_settings
from app.database import async_session
import cv2
import numpy as np

from app.models import ComprefaceSubject, Detection, Task
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
