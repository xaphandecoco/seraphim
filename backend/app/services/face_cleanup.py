import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detection, Task

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    scanned: int = 0
    deleted: int = 0
    errors: int = 0
    protected_by_task: int = 0
    protected_by_pit: int = 0
    skipped_missing_file: int = 0


class FaceCleanupService:
    """Delete unprocessed face images older than the retention period.

    Enrolled faces (is_enrolled=True) are never deleted.
    Detections with pending tasks or in the PIT queue are protected.
    """

    def __init__(self, storage_base: Path, retention_days: int):
        self.storage_base = storage_base
        self.retention_days = max(retention_days, 1)

    async def run_cleanup(self, session: AsyncSession) -> CleanupResult:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        result = CleanupResult()

        # Find eligible detections in batches
        while True:
            batch = await self._get_eligible_batch(session, cutoff)
            if not batch:
                break

            result.scanned += len(batch)

            for detection in batch:
                cleanup_status = await self._process_detection(session, detection)
                if cleanup_status == "deleted":
                    result.deleted += 1
                elif cleanup_status == "protected_task":
                    result.protected_by_task += 1
                elif cleanup_status == "protected_pit":
                    result.protected_by_pit += 1
                elif cleanup_status == "missing_file":
                    result.skipped_missing_file += 1
                elif cleanup_status == "error":
                    result.errors += 1

            await session.commit()

        logger.info(
            "Face cleanup completed: scanned=%s deleted=%s errors=%s "
            "protected_task=%s protected_pit=%s missing_file=%s",
            result.scanned,
            result.deleted,
            result.errors,
            result.protected_by_task,
            result.protected_by_pit,
            result.skipped_missing_file,
        )
        return result

    async def _get_eligible_batch(
        self, session: AsyncSession, cutoff: datetime, limit: int = 100
    ) -> list[Detection]:
        result = await session.execute(
            select(Detection)
            .where(Detection.is_enrolled == False)
            .where(Detection.deleted_at.is_(None))
            .where(Detection.created_at < cutoff)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _process_detection(
        self, session: AsyncSession, detection: Detection
    ) -> str:
        # Check for pending task protection
        task_result = await session.execute(
            select(Task)
            .where(Task.detection_id == detection.id)
            .where(Task.status == "pending")
        )
        pending_task = task_result.scalar_one_or_none()
        if pending_task is not None:
            return "protected_task"

        # Check for PIT queue protection
        pit_result = await session.execute(
            select(Task)
            .where(Task.detection_id == detection.id)
            .where(Task.pit_status == "awaiting")
        )
        pit_task = pit_result.scalar_one_or_none()
        if pit_task is not None:
            return "protected_pit"

        if not detection.image_path:
            return "missing_file"

        thumb_path = self.storage_base / detection.image_path
        full_path = Path(str(thumb_path).replace("_thumb.jpg", "_full.jpg"))

        # Delete files
        deleted_any = False
        for path in (thumb_path, full_path):
            try:
                if path.exists():
                    path.unlink()
                    deleted_any = True
            except OSError as exc:
                logger.error("Failed to delete %s: %s", path, exc)
                return "error"

        # Update detection record
        detection.image_path = None
        detection.deleted_at = datetime.now(timezone.utc)

        return "deleted" if deleted_any else "missing_file"
