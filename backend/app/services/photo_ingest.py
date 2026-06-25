"""PhotoIngestService — bulk-photo batch processing pipeline.

Processes a PhotoIngestBatch by running each uploaded image through the
existing process_face_crop pipeline (detect → quality gate → recognize →
dedup → DB insert → SSE broadcast).

Design decisions:
- PhotoIngestService owns its own ComprefaceClient and FaceStorage instances
  constructed in __init__; it does NOT import _UploadContext from uploads.py
  (private class).
- A lightweight _IngestContext provides the .compreface, .storage, .dedup
  attributes that process_face_crop expects from ctx.
- db_session_factory follows the asynccontextmanager pattern from uploads.py.
- Report list capped at MAX_REPORT_ENTRIES (500); counters continue past cap.
- Per-image decode failures and CompreFace detect failures increment
  batch.errors without aborting the loop.
- batch.processed_images is committed after each file for live polling.
- Unhandled exceptions set status='failed', commit that update, then re-raise.
"""

import logging
from contextlib import asynccontextmanager  # used by _build_db_session_factory inner factory
from datetime import datetime, timezone
from typing import Callable, Optional

import cv2
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import legacy_settings
from app.database import async_session
from app.models import PhotoIngestBatch
from app.services.compreface import ComprefaceClient
from app.services.dedup import DedupCache
from app.services.face_pipeline import process_face_crop
from app.services.face_storage import FaceStorage

logger = logging.getLogger(__name__)

MAX_REPORT_ENTRIES = 500
MAX_MEGAPIXELS = 25


def _utc_now() -> datetime:
    """Return timezone-naive UTC datetime (matches models.utc_now convention)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _IngestContext:
    """Lightweight pipeline context for PhotoIngestService.

    Provides the .compreface, .storage, .dedup attributes that
    process_face_crop expects from its ctx argument.
    """

    def __init__(self, compreface: ComprefaceClient, storage: FaceStorage) -> None:
        self.compreface = compreface
        self.storage = storage
        self.dedup = DedupCache()


def _build_db_session_factory() -> Callable:
    """Return the asynccontextmanager function for process_face_crop.

    process_face_crop receives a callable and calls it as:
        async with db_session_factory() as session: ...

    We return _make_db_session_factory directly, which is an asynccontextmanager
    decorator applied to a generator — exactly the pattern in uploads.py lines 33-37.
    """
    @asynccontextmanager
    async def factory():
        async with async_session() as session:
            yield session
    return factory


class PhotoIngestService:
    """Process a PhotoIngestBatch by running each file through the face pipeline."""

    def __init__(self) -> None:
        storage_path = (
            legacy_settings.STORAGE_PATH
            if hasattr(legacy_settings, "STORAGE_PATH")
            else "data"
        )
        self._compreface = ComprefaceClient()
        self._storage = FaceStorage(storage_path)
        self._ctx = _IngestContext(self._compreface, self._storage)
        self._db_session_factory = _build_db_session_factory()

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._compreface.close()

    async def process_batch(
        self,
        batch_id: int,
        files: list[tuple[str, bytes]],
        event_id: Optional[int],
        session: AsyncSession,
    ) -> None:
        """Process all (filename, raw_bytes) pairs in the batch.

        Acceptance-criteria mapping:
        1. Sets batch.status='completed' and batch.finished_at on success.
        2. Sets status='failed' on unhandled exception, commits, re-raises.
        3. Per-image decode failure increments batch.errors without aborting.
        4. batch.processed_images flushed after each file for live polling.
        5. report list capped at 500 entries; counters continue past cap.
        6. process_face_crop called with existing signature unchanged.
        7. CompreFace detect failure logs and increments errors without aborting.
        """
        batch: Optional[PhotoIngestBatch] = await session.get(PhotoIngestBatch, batch_id)
        if batch is None:
            raise ValueError(f"PhotoIngestBatch {batch_id} not found")

        # Initialise the report list from the current DB value.
        # ORM default is list; after creation from the router it should already be [].
        if batch.report is None:
            batch.report = []

        try:
            for filename, raw_bytes in files:
                await self._process_one_image(
                    batch=batch,
                    filename=filename,
                    raw_bytes=raw_bytes,
                    event_id=event_id,
                    session=session,
                )

            # All files processed — mark completed.
            batch.status = "completed"
            batch.finished_at = _utc_now()
            await session.commit()

        except Exception:
            # Unhandled exception: mark failed and commit that status, then re-raise.
            logger.exception(
                "PhotoIngestBatch %s failed with unhandled exception", batch_id
            )
            try:
                batch.status = "failed"
                batch.finished_at = _utc_now()
                await session.commit()
            except Exception:
                logger.exception(
                    "Could not commit failed status for batch %s", batch_id
                )
            raise

    async def _process_one_image(
        self,
        batch: PhotoIngestBatch,
        filename: str,
        raw_bytes: bytes,
        event_id: Optional[int],
        session: AsyncSession,
    ) -> None:
        """Process a single image file, updating batch counters in place.

        On any recoverable error (bad decode, megapixel limit, CompreFace detect
        failure) this increments batch.errors, appends a report entry, and returns
        normally so the outer loop continues with the next file.

        batch.processed_images is incremented and flushed to DB at the end of this
        method regardless of success or failure.
        """
        report_entry: dict = {"filename": filename, "faces_detected": 0, "outcome": "ok"}

        # --- Step 1: Decode image ---
        nparr = np.frombuffer(raw_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning(
                "photo_ingest: cv2.imdecode returned None for file=%s batch=%s",
                filename,
                batch.id,
            )
            batch.errors += 1
            report_entry["outcome"] = "decode_error"
            report_entry["error"] = "Could not decode image"
            _append_report(batch, report_entry)
            batch.processed_images += 1
            await session.commit()
            return

        # --- Step 2: Enforce 25-megapixel limit ---
        h, w = frame.shape[:2]
        if (h * w) > MAX_MEGAPIXELS * 1_000_000:
            logger.warning(
                "photo_ingest: image exceeds %dMP limit file=%s batch=%s (h=%d w=%d)",
                MAX_MEGAPIXELS,
                filename,
                batch.id,
                h,
                w,
            )
            batch.errors += 1
            report_entry["outcome"] = "megapixel_limit"
            report_entry["error"] = f"Image exceeds {MAX_MEGAPIXELS} megapixel limit"
            _append_report(batch, report_entry)
            batch.processed_images += 1
            await session.commit()
            return

        # --- Step 3: Encode for CompreFace detect ---
        success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            logger.warning(
                "photo_ingest: cv2.imencode failed for file=%s batch=%s",
                filename,
                batch.id,
            )
            batch.errors += 1
            report_entry["outcome"] = "encode_error"
            report_entry["error"] = "Could not encode image for detection"
            _append_report(batch, report_entry)
            batch.processed_images += 1
            await session.commit()
            return

        image_bytes = encoded.tobytes()

        # --- Step 4: CompreFace detect ---
        try:
            faces = await self._compreface.detect(image_bytes)
        except Exception as exc:
            logger.exception(
                "photo_ingest: CompreFace detect failed for file=%s batch=%s: %s",
                filename,
                batch.id,
                exc,
            )
            batch.errors += 1
            report_entry["outcome"] = "detect_error"
            report_entry["error"] = "CompreFace detection failed"
            _append_report(batch, report_entry)
            batch.processed_images += 1
            await session.commit()
            return

        # --- Step 5: Process each detected face ---
        faces_detected_this_image = len(faces)
        batch.faces_detected += faces_detected_this_image
        report_entry["faces_detected"] = faces_detected_this_image

        for face_box in faces:
            img_h, img_w = frame.shape[:2]
            x = max(0, face_box.get("x", 0))
            y = max(0, face_box.get("y", 0))
            bw = min(face_box.get("w", 0), img_w - x)
            bh = min(face_box.get("h", 0), img_h - y)
            if bw <= 0 or bh <= 0:
                continue

            face_crop = frame[y : y + bh, x : x + bw]

            try:
                action_result = await process_face_crop(
                    face_crop=face_crop,
                    camera_id=None,
                    event_id=event_id,
                    ctx=self._ctx,
                    db_session_factory=self._db_session_factory,
                )
            except Exception as exc:
                logger.exception(
                    "photo_ingest: process_face_crop failed file=%s batch=%s: %s",
                    filename,
                    batch.id,
                    exc,
                )
                batch.errors += 1
                continue

            action = action_result.get("action")
            if action == "skipped":
                batch.skipped += 1
            elif action == "deduplicated":
                batch.deduplicated += 1
            elif action == "auto_logged":
                batch.auto_logged += 1
            elif action == "tasked":
                batch.tasks_created += 1

        # --- Step 6: Append report entry (capped) and commit progress ---
        # commit() flushes automatically; no explicit flush needed.
        _append_report(batch, report_entry)
        batch.processed_images += 1
        await session.commit()


def _append_report(batch: PhotoIngestBatch, entry: dict) -> None:
    """Append an entry to batch.report only if the cap has not been reached.

    Counters (processed_images, errors, faces_found, tasks_created) continue
    accumulating beyond 500 — only detail dicts stop being appended.

    SQLAlchemy does not track mutations inside a JSON list automatically
    on all dialects, so we replace the list reference to trigger dirty-tracking.
    """
    current = batch.report if isinstance(batch.report, list) else []
    if len(current) < MAX_REPORT_ENTRIES:
        new_report = list(current)
        new_report.append(entry)
        batch.report = new_report
    # If cap reached: no append; counters on the batch object continue outside.
