import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import legacy_settings
from app.database import async_session, get_db
from app.dependencies import require_admin, require_volunteer
from app.models import PhotoIngestBatch
from app.schemas import FaceUploadResponse, PhotoIngestBatchResponse
from app.services.compreface import ComprefaceClient
from app.services.dedup import DedupCache
from app.services.face_pipeline import process_face_crop
from app.services.face_storage import FaceStorage
from app.services.photo_ingest import PhotoIngestService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


class _UploadContext:
    """Lightweight pipeline context for upload processing."""

    def __init__(self):
        self.compreface = ComprefaceClient()
        storage_path = legacy_settings.STORAGE_PATH if hasattr(legacy_settings, "STORAGE_PATH") else "data"
        self.storage = FaceStorage(storage_path)
        self.dedup = DedupCache()


@asynccontextmanager
async def _db_session_factory():
    """Async context manager wrapper around async_session for the pipeline."""
    async with async_session() as session:
        yield session


@router.post("/faces", response_model=FaceUploadResponse)
async def upload_faces(
    file: UploadFile = File(...),
    event_id: Optional[int] = Query(None),
    user=Depends(require_admin),
):
    """Upload an image and process all detected faces through the recognition pipeline."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be an image",
        )

    MAX_BYTES = 10 * 1024 * 1024  # 10 MB
    MAX_MEGAPIXELS = 25

    contents = await file.read(MAX_BYTES + 1)
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )
    if len(contents) > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Image exceeds 10 MB limit",
        )

    # Decode image to BGR numpy array
    nparr = np.frombuffer(contents, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image format",
        )

    h, w = frame.shape[:2]
    if (h * w) > MAX_MEGAPIXELS * 1_000_000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image exceeds {MAX_MEGAPIXELS} megapixel limit",
        )

    ctx = _UploadContext()
    try:
        # Detect all faces in the uploaded image
        success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to encode image for detection",
            )
        image_bytes = encoded.tobytes()

        try:
            faces = await ctx.compreface.detect(image_bytes)
        except Exception as exc:
            logger.exception("Compreface detection failed for upload: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Face detection service error",
            )

        if not faces:
            return FaceUploadResponse(
                faces_detected=0,
                quality_passed=0,
                quality_failed=0,
                tasks_created=0,
                auto_logged=0,
                skipped=0,
                deduplicated=0,
            )

        results = {
            "faces_detected": len(faces),
            "quality_passed": 0,
            "quality_failed": 0,
            "tasks_created": 0,
            "auto_logged": 0,
            "skipped": 0,
            "deduplicated": 0,
        }

        for face_box in faces:
            height, width = frame.shape[:2]
            x = max(0, face_box.get("x", 0))
            y = max(0, face_box.get("y", 0))
            w = min(face_box.get("w", 0), width - x)
            h = min(face_box.get("h", 0), height - y)
            if w <= 0 or h <= 0:
                continue

            face_crop = frame[y : y + h, x : x + w]
            action_result = await process_face_crop(
                face_crop=face_crop,
                camera_id=None,
                event_id=event_id,
                ctx=ctx,
                db_session_factory=_db_session_factory,
            )

            action = action_result.get("action")
            if action == "skipped":
                results["quality_failed"] += 1
                results["skipped"] += 1
            elif action == "deduplicated":
                results["deduplicated"] += 1
            elif action == "auto_logged":
                results["quality_passed"] += 1
                results["auto_logged"] += 1
            elif action == "tasked":
                results["quality_passed"] += 1
                results["tasks_created"] += 1

        return FaceUploadResponse(**results)
    finally:
        await ctx.compreface.close()


MAX_BATCH_FILES = 50
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/photos/batch", response_model=PhotoIngestBatchResponse, status_code=status.HTTP_201_CREATED)
async def create_photo_batch(
    files: List[UploadFile] = File(...),
    event_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Upload up to 50 images for async batch processing.

    Creates a PhotoIngestBatch row, launches PhotoIngestService.process_batch as
    a background task via asyncio.create_task, and immediately returns the batch
    record with status='processing'. Poll GET /uploads/photos/batch/{id} for results.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No files provided",
        )
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Maximum {MAX_BATCH_FILES} files per batch",
        )

    # Read all file contents before creating the batch row — uploads are gone
    # after the request ends, so we must materialise them now.
    file_pairs: list[tuple[str, bytes]] = []
    for upload in files:
        contents = await upload.read(MAX_FILE_BYTES + 1)
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File '{upload.filename}' exceeds 10 MB limit",
            )
        file_pairs.append((upload.filename or "unknown", contents))

    # Resolve the user ID from the token payload
    uploaded_by_id: Optional[int] = None
    if isinstance(user, dict):
        try:
            uploaded_by_id = int(user.get("sub", 0)) or None
        except (ValueError, TypeError):
            pass
    elif hasattr(user, "id"):
        uploaded_by_id = user.id

    # Create the batch record using the request-scoped session
    batch = PhotoIngestBatch(
        event_id=event_id,
        uploaded_by_id=uploaded_by_id,
        status="processing",
        total_images=len(file_pairs),
        processed_images=0,
        faces_detected=0,
        auto_logged=0,
        tasks_created=0,
        skipped=0,
        deduplicated=0,
        errors=0,
        report=[],
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    batch_id = batch.id
    captured_event_id = event_id

    async def _run_batch() -> None:
        """Background task: opens its own session and drives process_batch."""
        service = PhotoIngestService()
        try:
            async with async_session() as bg_session:
                await service.process_batch(
                    batch_id=batch_id,
                    files=file_pairs,
                    event_id=captured_event_id,
                    session=bg_session,
                )
        except Exception as exc:
            logger.exception(
                "uploads: background batch %s failed: %s", batch_id, exc
            )
        finally:
            await service.close()

    asyncio.create_task(_run_batch())
    return batch


@router.get("/photos/batch/{batch_id}", response_model=PhotoIngestBatchResponse)
async def get_photo_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Poll batch status and per-image report."""
    batch: Optional[PhotoIngestBatch] = await db.get(PhotoIngestBatch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Batch {batch_id} not found",
        )
    return batch
