import json as _json

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.middleware.cooldown import check_cooldown
from app.models import Contact, Detection, Task
from app.schemas import PaginatedTaskResponse, TaskResponse
from app.services.face_storage import to_storage_url
from app.services.task_service import TaskService
from app.sse import broadcaster

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _resolve_name(raw_name: str | None, db: AsyncSession) -> str | None:
    """Resolve 'member:{id}' synthetic names to display names."""
    if not raw_name or not raw_name.startswith("member:"):
        return raw_name
    try:
        contact_id = int(raw_name.split(":", 1)[1])
        member = await db.get(Contact, contact_id)
        if member:
            return f"{member.first_name} {member.last_name}".strip()
    except (ValueError, IndexError):
        pass
    return raw_name


@router.get("", response_model=PaginatedTaskResponse)
async def list_tasks(
    page: int = 1,
    page_size: int = 20,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Paginated task list."""
    if page < 1:
        page = 1
    if page_size < 1 or page_size > 100:
        page_size = 20

    offset = (page - 1) * page_size

    total_result = await db.execute(
        select(func.count(Task.id)).where(Task.status == status)
    )
    total = total_result.scalar() or 0

    result = await db.execute(
        select(Task, Detection)
        .join(Detection, Task.detection_id == Detection.id)
        .where(Task.status == status)
        .order_by(Detection.timestamp.desc())
        .offset(offset)
        .limit(page_size)
    )
    
    items = []
    for task, detection in result.all():
        resolved_name = await _resolve_name(detection.matched_name, db)
        items.append(TaskResponse(
            id=task.id,
            detection_id=task.detection_id,
            status=task.status,
            required_approvals=task.required_approvals,
            current_approvals=task.current_approvals,
            skip_count=task.skip_count,
            skip_reasons=task.skip_reasons or [],
            tier=detection.tier,
            confidence=float(detection.confidence) if detection.confidence else None,
            matched_name=resolved_name,
            face_thumbnail_path=to_storage_url(detection.image_path),
            camera_name=f"Camera {detection.camera_id}",
            detected_at=detection.timestamp,
            expiry_date=task.expiry_date,
        ))

    return PaginatedTaskResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/next", response_model=TaskResponse)
async def get_next_task(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Get next pending task for current volunteer."""
    service = TaskService(db)
    task = await service.get_next_task(int(user["sub"]))
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No pending tasks available"
        )
    
    detection = await db.get(Detection, task.detection_id)
    resolved_name = await _resolve_name(detection.matched_name if detection else None, db)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=resolved_name,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/confirm", response_model=TaskResponse)
async def confirm_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Confirm suggested match."""
    remaining = await check_cooldown(int(user["sub"]))
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cooldown active. Wait {remaining:.1f}s",
        )
    service = TaskService(db)
    task = await service.confirm_task(task_id, int(user["sub"]))
    await broadcaster.publish(_json.dumps(
        {"type": "task_update", "task_id": task.id, "status": task.status},
        separators=(",", ":"),
    ))
    
    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/edit", response_model=TaskResponse)
async def edit_task(
    task_id: int,
    member_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Edit task — select different member."""
    remaining = await check_cooldown(int(user["sub"]))
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cooldown active. Wait {remaining:.1f}s",
        )
    service = TaskService(db)
    task = await service.edit_task(task_id, int(user["sub"]), member_id)
    await broadcaster.publish(_json.dumps(
        {"type": "task_update", "task_id": task.id, "status": task.status},
        separators=(",", ":"),
    ))
    
    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/add", response_model=TaskResponse)
async def add_task(
    task_id: int,
    member_id: int = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Add unidentified face to member."""
    remaining = await check_cooldown(int(user["sub"]))
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cooldown active. Wait {remaining:.1f}s",
        )
    service = TaskService(db)
    task = await service.add_task(task_id, int(user["sub"]), member_id)
    await broadcaster.publish(_json.dumps(
        {"type": "task_update", "task_id": task.id, "status": task.status},
        separators=(",", ":"),
    ))
    
    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/skip", response_model=TaskResponse)
async def skip_task(
    task_id: int,
    reason: str = Body("", embed=True),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Skip task — return to queue with reason."""
    remaining = await check_cooldown(int(user["sub"]))
    if remaining:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cooldown active. Wait {remaining:.1f}s",
        )
    service = TaskService(db)
    task = await service.skip_task(task_id, int(user["sub"]), reason)
    await broadcaster.publish(_json.dumps(
        {"type": "task_update", "task_id": task.id, "status": task.status},
        separators=(",", ":"),
    ))
    
    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )


@router.post("/{task_id}/override", response_model=TaskResponse)
async def admin_override_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Admin override on dual-approval tasks."""
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    
    service = TaskService(db)
    task = await service.admin_override(task_id, int(user["sub"]))
    await broadcaster.publish(_json.dumps(
        {"type": "task_update", "task_id": task.id, "status": task.status},
        separators=(",", ":"),
    ))
    
    detection = await db.get(Detection, task.detection_id)
    return TaskResponse(
        id=task.id,
        detection_id=task.detection_id,
        status=task.status,
        required_approvals=task.required_approvals,
        current_approvals=task.current_approvals,
        skip_count=task.skip_count,
        skip_reasons=task.skip_reasons or [],
        tier=detection.tier if detection else None,
        confidence=float(detection.confidence) if detection and detection.confidence else None,
        matched_name=detection.matched_name if detection else None,
        face_thumbnail_path=to_storage_url(detection.image_path) if detection else None,
        camera_name=f"Camera {detection.camera_id}" if detection else None,
        detected_at=detection.timestamp if detection else None,
        expiry_date=task.expiry_date,
    )
