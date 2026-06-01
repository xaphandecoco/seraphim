import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from app.config import legacy_settings
from app.dependencies import get_current_user

router = APIRouter(tags=["storage"])

_STORAGE_ROOT = Path(os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)).resolve()


@router.get("/storage/{file_path:path}")
async def serve_storage_file(
    file_path: str,
    _user: dict = Depends(get_current_user),
):
    """Serve face images from the storage directory with auth enforcement."""
    # Strict path containment — prevent path traversal
    requested = (_STORAGE_ROOT / file_path).resolve()
    if not str(requested).startswith(str(_STORAGE_ROOT)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    suffix = requested.suffix.lower()
    media_type = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png" if suffix == ".png" else "application/octet-stream"
    return FileResponse(str(requested), media_type=media_type)
