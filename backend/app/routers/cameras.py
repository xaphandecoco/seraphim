from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import Camera
from app.schemas import CameraCreateRequest, CameraResponse, CameraUpdateRequest

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.get("", response_model=list[CameraResponse])
async def list_cameras(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """List all cameras."""
    result = await db.execute(select(Camera).order_by(Camera.created_at.desc()))
    cameras = result.scalars().all()
    return [CameraResponse.model_validate(c) for c in cameras]


@router.post("", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
async def create_camera(
    req: CameraCreateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Create a new camera."""
    camera = Camera(
        name=req.name,
        rtsp_url=req.rtsp_url,
        zone_label=req.zone_label,
        fps=req.fps,
        enable_health_check=req.enable_health_check,
    )
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return CameraResponse.model_validate(camera)


@router.put("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: int,
    req: CameraUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Update a camera."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found"
        )

    if req.name is not None:
        camera.name = req.name
    if req.rtsp_url is not None:
        camera.rtsp_url = req.rtsp_url
    if req.zone_label is not None:
        camera.zone_label = req.zone_label
    if req.fps is not None:
        camera.fps = req.fps
    if req.enable_health_check is not None:
        camera.enable_health_check = req.enable_health_check

    await db.commit()
    await db.refresh(camera)
    return CameraResponse.model_validate(camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Delete a camera."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found"
        )

    await db.delete(camera)
    await db.commit()
    return None


@router.get("/{camera_id}/preview")
async def preview_camera(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Grab a single preview frame from the camera."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found"
        )

    try:
        from app.services.rtsp import FFmpegCapture

        capture = FFmpegCapture(camera, lambda *_: None)
        frame_data = await capture.get_preview_frame()
        if not frame_data:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to capture frame from camera",
            )
        from fastapi.responses import Response
        return Response(content=frame_data, media_type="image/jpeg")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Preview failed. Check camera connectivity.",
        )


@router.post("/{camera_id}/reconnect")
async def reconnect_camera(
    camera_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Force reconnect a camera."""
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found"
        )

    camera.status = "reconnecting"
    await db.commit()
    return {"message": f"Camera {camera_id} reconnect initiated", "camera_id": camera_id}
