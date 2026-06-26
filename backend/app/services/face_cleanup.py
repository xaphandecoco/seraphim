import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Detection, Task

logger = logging.getLogger(__name__)


def _delete_image_files(
    storage_base: Path,
    image_path: Optional[str],
) -> tuple[int, list[str]]:
    """Delete a detection's thumb and _full.jpg sibling from disk.

    Parameters
    ----------
    storage_base:
        Absolute Path to the storage root (legacy_settings.STORAGE_PATH).
    image_path:
        Relative path (thumb) as stored in Detection.image_path.
        The full sibling is derived by replacing ``_thumb.jpg`` with
        ``_full.jpg``.

    Returns
    -------
    (count_deleted, errors)
        count_deleted: number of files successfully deleted (0, 1, or 2).
        errors: list of error message strings for any failed deletions.
    """
    if not image_path:
        return 0, []

    thumb_path = storage_base / image_path
    full_path = Path(str(thumb_path).replace("_thumb.jpg", "_full.jpg"))

    count_deleted = 0
    errors: list[str] = []
    for path in (thumb_path, full_path):
        try:
            if path.exists():
                path.unlink()
                count_deleted += 1
        except OSError as exc:
            msg = f"Failed to delete {path}: {exc}"
            logger.error(msg)
            errors.append(msg)

    return count_deleted, errors


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
            .where(Detection.is_enrolled.is_(False))
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

        # Delete thumb + full sibling via shared helper
        count_deleted, errs = _delete_image_files(self.storage_base, detection.image_path)
        if errs:
            return "error"

        # Update detection record
        detection.image_path = None
        detection.deleted_at = datetime.now(timezone.utc)

        return "deleted" if count_deleted > 0 else "missing_file"
