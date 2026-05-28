import asyncio
import logging
from typing import Callable, Optional

import numpy as np

from app.models import Camera

logger = logging.getLogger(__name__)


class FFmpegCapture:
    def __init__(self, camera: Camera, frame_callback: Callable):
        self.camera = camera
        self.frame_callback = frame_callback
        self.process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self._restart_count = 0

    async def start(self):
        """Start ffmpeg subprocess capturing RTSP at 5fps, 720p."""
        cmd = [
            "ffmpeg",
            "-rtsp_transport", "tcp",
            "-i", self.camera.rtsp_url,
            "-vf", f"fps={self.camera.fps},scale=1280:720",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1"
        ]
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        self.running = True
        self._restart_count = 0
        asyncio.create_task(self._read_frames())

    async def _read_frames(self):
        """Read raw frames from ffmpeg stdout and callback."""
        width, height = 1280, 720
        frame_size = width * height * 3
        while self.running:
            raw = await self.process.stdout.read(frame_size)
            if len(raw) != frame_size:
                # Frame incomplete — connection dropped
                await self._handle_disconnect()
                return
            frame = np.frombuffer(raw, np.uint8).reshape((height, width, 3))
            await self.frame_callback(self.camera.id, frame)

    async def _handle_disconnect(self):
        """Exponential backoff restart."""
        self._restart_count = min(self._restart_count + 1, 6)
        backoff = min(2 ** self._restart_count, 60)
        logger.warning(f"Camera {self.camera.id} disconnected, restarting in {backoff}s")
        await asyncio.sleep(backoff)
        if self.running:
            await self.restart()

    async def restart(self):
        if self.process:
            self.process.kill()
            await self.process.wait()
        await self.start()

    async def stop(self):
        self.running = False
        if self.process:
            self.process.kill()
            await self.process.wait()

    async def get_preview_frame(self) -> Optional[bytes]:
        """Grab single JPEG frame for admin preview."""
        cmd = [
            "ffmpeg", "-y", "-rtsp_transport", "tcp",
            "-i", self.camera.rtsp_url,
            "-vframes", "1",
            "-f", "image2",
            "-q:v", "2",
            "pipe:1"
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        data, _ = await proc.communicate()
        return data if data else None
