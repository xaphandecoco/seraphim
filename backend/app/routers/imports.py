"""Import wizard router (S10).

Prefix: /imports
Tags:   imports

All routes require_volunteer (viewer → 403, unauthenticated → 401).
Batch ownership: only the batch creator (or admin) can preview/run/read a batch
— returns 404 for cross-user requests to avoid information leakage.

Endpoints
---------
POST   /imports/upload
GET    /imports               (list, at prefix root — frontend depends on this path)
GET    /imports/presets       (MUST be declared before /{id} to avoid routing conflict)
POST   /imports/presets
PUT    /imports/presets/{preset_id}
DELETE /imports/presets/{preset_id}
GET    /imports/{id}
GET    /imports/{id}/columns
POST   /imports/{id}/preview
POST   /imports/{id}/run
GET    /imports/{id}/rows
GET    /imports/{id}/preview-rows
GET    /imports/{id}/report.csv

Circular-import rule (CN-25): must NOT import from other routers.
"""
from __future__ import annotations

import logging
import os
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import legacy_settings
from app.database import get_db
from app.dependencies import require_volunteer
from app.rate_limit import limiter
from app.models import ImportBatch, ImportMappingPreset, ImportRowResult, utc_now
from app.schemas import (
    # S10 schemas (added below in schemas.py)
    ColumnsResponse,
    ImportBatchListResponse,
    ImportBatchOut,
    ImportPresetIn,
    ImportPresetOut,
    ImportPresetListResponse,
    ImportRowResultListResponse,
    ImportRowResultOut,
    ImportUploadResponse,
    PreviewCounts,
    PreviewRequest,
    RunCounts,
    RunRequest,
)
from app.services.imports.presets import (
    create_preset,
    delete_preset,
    list_presets,
    update_preset,
)
from app.services.imports.runner import run_import, run_preview, stream_import_report_csv
from app.services.imports.staging import read_header_and_sample
from app.services.imports.suggest import suggest_mappings, targets_catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/imports", tags=["imports"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
_ALLOWED_EXTS = frozenset({".csv", ".xlsx"})
_EXT_FMT = {".csv": "csv", ".xlsx": "xlsx"}
_STAGING_DIR_NAME = "imports"
_BATCH_TTL_HOURS = 24
_ALLOWED_ENTITIES = frozenset({"contacts", "participants"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _batch_to_out(batch: ImportBatch) -> ImportBatchOut:
    return ImportBatchOut(
        id=batch.id,
        source_filename=batch.source_filename,
        entity=batch.entity,
        mode=batch.mode,
        status=batch.status,
        column_map=batch.column_map or {},
        options=batch.options or {},
        total_rows=batch.total_rows,
        created_count=batch.created_count,
        updated_count=batch.updated_count,
        skipped_count=batch.skipped_count,
        error_count=batch.error_count,
        review_count=batch.review_count,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        created_by_id=batch.created_by_id,
    )


def _row_to_out(row: ImportRowResult) -> ImportRowResultOut:
    return ImportRowResultOut(
        id=row.id,
        batch_id=row.batch_id,
        row_number=row.row_number,
        external_id=row.external_id,
        outcome=row.outcome,
        entity_id=row.entity_id,
        message=row.message,
        created_at=row.created_at,
    )


async def _get_owned_batch(
    batch_id: int,
    db: AsyncSession,
    current_user: dict,
) -> ImportBatch:
    """Fetch a batch; 404 if not found or not owned by current user (non-admin)."""
    result = await db.execute(
        select(ImportBatch).where(ImportBatch.id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if batch is None:
        raise HTTPException(status_code=404, detail="Import batch not found")
    user_id = int(current_user["sub"])
    if current_user.get("role") != "admin" and batch.created_by_id != user_id:
        # Return 404 to avoid leaking existence of other users' batches
        raise HTTPException(status_code=404, detail="Import batch not found")
    return batch


def _validate_column_map(column_map: dict, entity: str) -> None:
    """Raise 422 for duplicate targets or missing required first_name (contacts)."""
    targets = [
        spec.get("target", "ignore")
        for spec in column_map.values()
        if isinstance(spec, dict) and spec.get("target", "ignore") != "ignore"
    ]

    counts = Counter(targets)
    dupes = [t for t, c in counts.items() if c > 1]
    if dupes:
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate column mapping targets: {', '.join(sorted(dupes))}",
        )

    entity_singular = _entity_singular(entity)
    if entity_singular == "contact" and "first_name" not in targets:
        raise HTTPException(
            status_code=422,
            detail="A column mapped to 'first_name' is required for contact imports.",
        )


def _entity_singular(entity: str) -> str:
    _MAP = {"contacts": "contact", "events": "event", "participants": "participant"}
    return _MAP.get(entity, entity)


def _staging_dir() -> Path:
    return Path(legacy_settings.STORAGE_PATH) / _STAGING_DIR_NAME


def _assert_path_contained(resolved: Path, parent: Path) -> None:
    """Raise ValueError if *resolved* escapes *parent*."""
    try:
        resolved.relative_to(parent)
    except ValueError:
        raise ValueError(
            f"Computed path {resolved} escapes staging directory {parent}"
        )


async def _audit_import_run(
    db: AsyncSession,
    actor_id: Optional[int],
    batch_id: int,
    counts: dict,
) -> None:
    """Record an audit log entry for a committed import run (guarded)."""
    try:
        from app.services import audit as audit_svc  # noqa: PLC0415
        await audit_svc.record(
            db=db,
            actor_id=actor_id,
            action="import.run",
            entity="import_batch",
            entity_id=batch_id,
            before=None,
            after={"counts": counts},
        )
        await db.commit()
    except Exception:
        logger.warning("audit import.run failed for batch_id=%s", batch_id)


# ---------------------------------------------------------------------------
# POST /imports/upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=ImportUploadResponse)
@limiter.limit("20/minute")
async def upload_import_file(
    request: Request,
    file: UploadFile = File(...),
    entity: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Stage an uploaded CSV or XLSX file for import.

    Validates extension/content-type, enforces 15 MB size cap, reads headers
    and a sample of up to 10 rows, and creates an ImportBatch in 'staged' status.
    """
    # --- Validate entity ---
    if entity not in _ALLOWED_ENTITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported entity {entity!r}. Must be one of: {', '.join(sorted(_ALLOWED_ENTITIES))}.",
        )

    # --- Validate extension ---
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {ext!r}. Upload a .csv or .xlsx file.",
        )
    fmt = _EXT_FMT[ext]

    # --- Read & enforce size cap (stream exactly MAX+1 bytes) ---
    raw = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the 15 MB upload limit ({_MAX_UPLOAD_BYTES // (1024*1024)} MB).",
        )

    # --- Stage the file atomically ---
    staging_dir = _staging_dir()
    staging_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    dest_rel = f"{_STAGING_DIR_NAME}/{file_id}{ext}"
    dest_abs = Path(legacy_settings.STORAGE_PATH) / dest_rel
    tmp_abs = dest_abs.with_suffix(dest_abs.suffix + ".tmp")

    # Path-containment assertion (defence-in-depth)
    resolved_dest = dest_abs.resolve()
    resolved_dir = staging_dir.resolve()
    _assert_path_contained(resolved_dest, resolved_dir)

    try:
        tmp_abs.write_bytes(raw)
        os.replace(str(tmp_abs), str(dest_abs))
    except OSError as exc:
        try:
            tmp_abs.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to stage file: {exc}")

    # --- Read headers + sample (delete staged file on reject) ---
    try:
        result = read_header_and_sample(str(dest_abs), fmt)
    except ValueError as exc:
        try:
            dest_abs.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=str(exc))

    # --- Create ImportBatch ---
    user_id = int(current_user["sub"])
    now = _utc_now_naive()
    expires_at = now + timedelta(hours=_BATCH_TTL_HOURS)

    batch = ImportBatch(
        source_filename=filename,
        entity=entity,
        mode="wizard_preview",
        status="staged",
        column_map={},
        options={
            "fmt":       fmt,
            "encoding":  result.get("encoding"),
            "delimiter": result.get("delimiter"),
        },
        created_by_id=user_id,
        staging_file=dest_rel,
        expires_at=expires_at,
    )
    db.add(batch)
    await db.commit()
    await db.refresh(batch)

    headers = result["headers"]
    sample_rows = result.get("sample_rows", [])

    return ImportUploadResponse(
        batch_id=batch.id,
        columns=[{"name": h, "sample": [str(r.get(h, "") or "") for r in sample_rows]} for h in headers],
        sample_rows=sample_rows,
        total_rows=result["total_rows"],
        encoding=result.get("encoding"),
        delimiter=result.get("delimiter"),
        sheets=result.get("sheets"),
    )


# ---------------------------------------------------------------------------
# GET /imports   (list — MUST be before /{id})
# ---------------------------------------------------------------------------


@router.get("", response_model=ImportBatchListResponse)
async def list_import_batches(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Paginated list of import batches visible to the current user."""
    user_id = int(current_user["sub"])
    is_admin = current_user.get("role") == "admin"

    base_stmt = select(ImportBatch)
    if not is_admin:
        base_stmt = base_stmt.where(ImportBatch.created_by_id == user_id)

    count_result = await db.execute(
        select(func.count()).select_from(base_stmt.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        base_stmt.order_by(ImportBatch.started_at.desc()).limit(limit).offset(offset)
    )
    batches = result.scalars().all()

    return ImportBatchListResponse(
        items=[_batch_to_out(b) for b in batches],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /imports/presets   (MUST be before /{id})
# ---------------------------------------------------------------------------


@router.get("/presets", response_model=ImportPresetListResponse)
async def list_import_presets(
    entity: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """List mapping presets visible to the current user."""
    presets = await list_presets(entity, db, current_user)
    return ImportPresetListResponse(
        items=[ImportPresetOut.model_validate(p) for p in presets],
        total=len(presets),
    )


# ---------------------------------------------------------------------------
# POST /imports/presets
# ---------------------------------------------------------------------------


@router.post("/presets", response_model=ImportPresetOut, status_code=201)
async def create_import_preset(
    data: ImportPresetIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Create a new mapping preset. Returns 409 on (entity, name) collision."""
    preset = await create_preset(data, db, current_user)
    return ImportPresetOut.model_validate(preset)


# ---------------------------------------------------------------------------
# PUT /imports/presets/{preset_id}
# ---------------------------------------------------------------------------


@router.put("/presets/{preset_id}", response_model=ImportPresetOut)
async def update_import_preset(
    preset_id: int,
    data: ImportPresetIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Update a preset. 403 if non-owner non-admin. 409 on name collision."""
    preset = await update_preset(preset_id, data, db, current_user)
    return ImportPresetOut.model_validate(preset)


# ---------------------------------------------------------------------------
# DELETE /imports/presets/{preset_id}
# ---------------------------------------------------------------------------


@router.delete("/presets/{preset_id}", status_code=204)
async def delete_import_preset(
    preset_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Delete a preset. 403 if non-owner non-admin."""
    await delete_preset(preset_id, db, current_user)


# ---------------------------------------------------------------------------
# GET /imports/{id}
# ---------------------------------------------------------------------------


@router.get("/{batch_id}", response_model=ImportBatchOut)
async def get_import_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Fetch a single import batch (owner or admin only)."""
    batch = await _get_owned_batch(batch_id, db, current_user)
    return _batch_to_out(batch)


# ---------------------------------------------------------------------------
# GET /imports/{id}/columns
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/columns", response_model=ColumnsResponse)
async def get_import_columns(
    batch_id: int,
    sheet: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Return column headers, a sample, suggested mappings, and the targets catalog."""
    batch = await _get_owned_batch(batch_id, db, current_user)

    if not batch.staging_file:
        raise HTTPException(status_code=400, detail="No staged file for this batch.")

    staging_abs = Path(legacy_settings.STORAGE_PATH) / batch.staging_file
    if not staging_abs.exists():
        raise HTTPException(status_code=400, detail="Staged file not found; the batch may have expired.")

    fmt = (batch.options or {}).get("fmt", "xlsx")
    try:
        result = read_header_and_sample(str(staging_abs), fmt, sheet=sheet)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    headers = result["headers"]
    suggested = await suggest_mappings(headers, batch.entity, db)
    catalog = await targets_catalog(batch.entity, db)

    return ColumnsResponse(
        headers=headers,
        sample_rows=result.get("sample_rows", []),
        sheets=result.get("sheets"),
        suggested_map=suggested,
        targets=catalog,
    )


# ---------------------------------------------------------------------------
# POST /imports/{id}/preview
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/preview")
@limiter.limit("30/minute")
async def preview_import(
    request: Request,
    batch_id: int,
    body: PreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Simulate the import without writing any core rows.

    Returns counts: would_create, would_update, would_skip, ambiguous, errors.
    """
    batch = await _get_owned_batch(batch_id, db, current_user)
    _validate_column_map(body.column_map, batch.entity)

    if not batch.staging_file:
        raise HTTPException(status_code=400, detail="No staged file for this batch.")

    staging_abs = Path(legacy_settings.STORAGE_PATH) / batch.staging_file
    if not staging_abs.exists():
        raise HTTPException(status_code=400, detail="Staged file not found; the batch may have expired.")

    counts = await run_preview(
        db=db,
        batch=batch,
        column_map=body.column_map,
        match_key=body.match_key,
        conflict_policy=body.conflict_policy,
        sheet=body.sheet,
        target_event_id=body.target_event_id,
    )
    return {"counts": PreviewCounts(**counts)}


# ---------------------------------------------------------------------------
# POST /imports/{id}/run
# ---------------------------------------------------------------------------


@router.post("/{batch_id}/run")
@limiter.limit("10/minute")
async def run_import_batch(
    request: Request,
    batch_id: int,
    body: RunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Execute the actual import, writing core table rows.

    Returns counts: created, updated, skipped, review, errors, elapsed_ms, status.
    """
    batch = await _get_owned_batch(batch_id, db, current_user)
    _validate_column_map(body.column_map, batch.entity)

    if not batch.staging_file:
        raise HTTPException(status_code=400, detail="No staged file for this batch.")

    staging_abs = Path(legacy_settings.STORAGE_PATH) / batch.staging_file
    if not staging_abs.exists():
        raise HTTPException(status_code=400, detail="Staged file not found; the batch may have expired.")

    result = await run_import(
        db=db,
        batch=batch,
        column_map=body.column_map,
        match_key=body.match_key,
        conflict_policy=body.conflict_policy,
        sheet=body.sheet,
        target_event_id=body.target_event_id,
    )

    # Audit the committed run (guarded)
    user_id = int(current_user["sub"])
    await _audit_import_run(db, user_id, batch_id, {k: result[k] for k in ("created","updated","skipped","review","errors")})

    counts_out = RunCounts(
        created=result["created"],
        updated=result["updated"],
        skipped=result["skipped"],
        review=result["review"],
        errors=result["errors"],
    )
    return {
        "counts": counts_out,
        "elapsed_ms": result["elapsed_ms"],
        "status": result["status"],
    }


# ---------------------------------------------------------------------------
# GET /imports/{id}/rows
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/rows", response_model=ImportRowResultListResponse)
async def get_import_rows(
    batch_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    outcome: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Paginated list of import row results, optionally filtered by outcome."""
    await _get_owned_batch(batch_id, db, current_user)

    stmt = select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)
    if outcome:
        stmt = stmt.where(ImportRowResult.outcome == outcome)

    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(
        stmt.order_by(ImportRowResult.row_number).limit(limit).offset(offset)
    )
    rows = result.scalars().all()

    return ImportRowResultListResponse(
        items=[_row_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /imports/{id}/preview-rows
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/preview-rows", response_model=ImportRowResultListResponse)
async def get_preview_rows(
    batch_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Paginated list of all import row results (no outcome filter)."""
    await _get_owned_batch(batch_id, db, current_user)

    stmt = (
        select(ImportRowResult)
        .where(ImportRowResult.batch_id == batch_id)
        .order_by(ImportRowResult.row_number)
    )
    count_result = await db.execute(
        select(func.count()).select_from(stmt.subquery())
    )
    total = count_result.scalar_one()

    result = await db.execute(stmt.limit(limit).offset(offset))
    rows = result.scalars().all()

    return ImportRowResultListResponse(
        items=[_row_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /imports/{id}/report.csv
# ---------------------------------------------------------------------------


@router.get("/{batch_id}/report.csv")
async def download_import_report(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_volunteer),
):
    """Stream the import row results as a sanitised CSV download."""
    await _get_owned_batch(batch_id, db, current_user)

    return StreamingResponse(
        stream_import_report_csv(db, batch_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="import_{batch_id}_report.csv"',
        },
    )
