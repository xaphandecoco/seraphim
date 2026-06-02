import asyncio
import logging
from typing import Optional

import numpy as np
from sqlalchemy import select

from app.config import dynamic_settings, legacy_settings
from app.database import async_session
from app.models import Camera
from app.services.compreface import ComprefaceClient
from app.services.dedup import DedupCache
from app.services.face_pipeline import process_face_crop
from app.services.face_storage import FaceStorage
from app.services.rtsp import FFmpegCapture

logger = logging.getLogger(__name__)

SETTINGS_RELOAD_INTERVAL = 60  # seconds


class PipelineContext:
    def __init__(self):
        self.compreface = ComprefaceClient()
        storage_path = legacy_settings.STORAGE_PATH if hasattr(legacy_settings, "STORAGE_PATH") else "data"
        self.storage = FaceStorage(storage_path)
        self.dedup = DedupCache()


async def _encode_frame(frame: np.ndarray) -> bytes:
    """Encode numpy BGR frame to JPEG bytes."""
    import cv2

    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not success:
        raise RuntimeError("Failed to encode frame")
    return encoded.tobytes()


async def _process_face(
    camera_id: int,
    frame: np.ndarray,
    face_box: dict,
    ctx: PipelineContext,
) -> None:
    """Run full detection pipeline for a single face."""
    # Clamp box to frame bounds
    height, width = frame.shape[:2]
    x = max(0, face_box["x"])
    y = max(0, face_box["y"])
    w = min(face_box["w"], width - x)
    h = min(face_box["h"], height - y)
    if w <= 0 or h <= 0:
        return

    face_crop = frame[y : y + h, x : x + w]
    # Stamp the currently active event (loaded from DB settings)
    event_id = dynamic_settings.get_active_event_id()
    await process_face_crop(
        face_crop=face_crop,
        camera_id=camera_id,
        event_id=event_id,
        ctx=ctx,
        db_session_factory=async_session,
    )


async def _on_frame(camera_id: int, frame: np.ndarray, ctx: PipelineContext) -> None:
    """Callback invoked for each raw frame from ffmpeg."""
    # Use Compreface detection to find all faces in frame
    try:
        image_bytes = await _encode_frame(frame)
        faces = await ctx.compreface.detect(image_bytes)
    except Exception as exc:
        logger.exception("Face detection failed for camera %s: %s", camera_id, exc)
        return

    if not faces:
        return

    # Spawn non-blocking tasks for each face
    for face_box in faces:
        asyncio.create_task(_process_face(camera_id, frame.copy(), face_box, ctx))


async def _reload_settings_loop() -> None:
    """Periodically reload dynamic settings so URL/key changes take effect."""
    while True:
        await asyncio.sleep(SETTINGS_RELOAD_INTERVAL)
        try:
            async with async_session() as session:
                await dynamic_settings.reload(session)
                logger.debug("RTSP worker: dynamic settings reloaded")
        except Exception as exc:
            logger.warning("RTSP worker: failed to reload settings: %s", exc)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Initialize dynamic settings from DB before doing any work
    logger.info("RTSP worker: initializing settings from database…")
    try:
        async with async_session() as session:
            await dynamic_settings.initialize(session)
        logger.info("RTSP worker: settings initialized (compreface_url=%r, active_event_id=%r)",
                    dynamic_settings.get_compreface_url(), dynamic_settings.get_active_event_id())
    except Exception as exc:
        logger.error("RTSP worker: could not load settings from DB: %s — proceeding with defaults", exc)

    ctx = PipelineContext()

    async with async_session() as session:
        result = await session.execute(
            select(Camera).where(Camera.enable_health_check.is_(True))
        )
        cameras = result.scalars().all()

    if not cameras:
        logger.warning("No active cameras found, waiting...")
        # Keep alive and poll for cameras every 10s
        try:
            while True:
                await asyncio.sleep(10)
                async with async_session() as session:
                    result = await session.execute(
                        select(Camera).where(Camera.enable_health_check.is_(True))
                    )
                    cameras = result.scalars().all()
                if cameras:
                    logger.info("Found %s camera(s), starting capture", len(cameras))
                    break
        except asyncio.CancelledError:
            return

    captures = []
    for camera in cameras:
        cap = FFmpegCapture(
            camera,
            lambda cid, frm, ctx=ctx: _on_frame(cid, frm, ctx)
        )
        await cap.start()
        captures.append(cap)
        logger.info("Started capture for camera %s: %s", camera.id, camera.name)

    # Keep running; reload settings periodically
    try:
        asyncio.create_task(_reload_settings_loop())
        while True:
            await asyncio.sleep(1)
            ctx.dedup.cleanup()
    finally:
        for cap in captures:
            await cap.stop()
        await ctx.compreface.close()


if __name__ == "__main__":
    asyncio.run(main())
