"""Export router — sync streaming exports + async job CRUD + file download.

Endpoints:
  GET  /export/contacts.csv             — stream contacts as CSV (require_viewer)
  GET  /export/contacts.xlsx            — build contacts XLSX, FileResponse + cleanup
  GET  /export/participants.csv         — stream participants as CSV (require_viewer)
  GET  /export/participants.xlsx        — build participants XLSX, FileResponse + cleanup
  POST /export/jobs                     — create async ExportJob (201)
  GET  /export/jobs                     — list own jobs (admin sees all), newest first
  GET  /export/jobs/{job_id}            — detail; download_url only when status=='ready'
  GET  /export/jobs/{job_id}/download   — download completed export file
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi import status as http_status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import legacy_settings
from app.database import get_db
from app.dependencies import require_viewer, get_current_user
from app.models import Contact, ExportJob, Participant, utc_now
from app.services import audit as audit_svc
from app.services.export_service import _sanitize_cell

router = APIRouter(prefix="/export", tags=["export"])

# ---------------------------------------------------------------------------
# Storage root for job download path-containment check (mirrors storage.py)
# ---------------------------------------------------------------------------

def _get_storage_root() -> Path:
    return Path(os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)).resolve()


# ---------------------------------------------------------------------------
# §4.4 filter helpers — shared between contacts and participants exports
# ---------------------------------------------------------------------------


def _build_contact_query(
    *,
    search: Optional[str] = None,
    contact_type: Optional[str] = None,
    subtype: Optional[str] = None,
    tier: Optional[str] = None,
    is_regular: Optional[bool] = None,
    include_deleted: bool = False,
):
    """Return a SQLAlchemy select() for Contact rows with the given filters."""
    from sqlalchemy import or_

    conditions = []
    if not include_deleted:
        conditions.append(Contact.is_deleted.is_(False))

    if search and search.strip():
        q = f"%{search.strip()}%"
        conditions.append(
            or_(
                Contact.first_name.ilike(q),
                Contact.last_name.ilike(q),
                Contact.nickname.ilike(q),
                Contact.email.ilike(q),
            )
        )

    if contact_type is not None:
        conditions.append(Contact.contact_type == contact_type)
    if subtype is not None:
        conditions.append(Contact.contact_subtype == subtype)
    if tier is not None:
        conditions.append(Contact.tier == tier)
    if is_regular is not None:
        conditions.append(Contact.is_regular == is_regular)

    stmt = select(Contact).order_by(Contact.last_name, Contact.first_name)
    if conditions:
        stmt = stmt.where(*conditions)
    return stmt


def _build_participant_query(
    *,
    event_id: Optional[int] = None,
    participant_status: Optional[str] = None,
    source: Optional[str] = None,
):
    """Return a SQLAlchemy select() for Participant rows with the given filters."""
    conditions = []
    if event_id is not None:
        conditions.append(Participant.event_id == event_id)
    if participant_status is not None:
        conditions.append(Participant.status == participant_status)
    if source is not None:
        conditions.append(Participant.source == source)

    stmt = select(Participant).order_by(Participant.created_at.desc())
    if conditions:
        stmt = stmt.where(*conditions)
    return stmt


# ---------------------------------------------------------------------------
# CSV row builders
# ---------------------------------------------------------------------------

_CONTACT_HEADERS = [
    "id", "external_id", "contact_type", "contact_subtype",
    "first_name", "last_name", "nickname", "suffix", "gender",
    "birth_date", "phone", "email", "street_address",
    "tier", "is_active", "is_regular", "is_connected",
    "last_attended_at", "attendance_count", "weeks_absent",
    "created_at", "updated_at",
]

_PARTICIPANT_HEADERS = [
    "id", "contact_id", "event_id", "status", "role",
    "source", "detection_id", "registered_by_id", "created_at",
]


def _contact_row(c: Contact) -> list:
    return [
        c.id,
        c.external_id,
        c.contact_type,
        c.contact_subtype or "",
        _sanitize_cell(c.first_name or ""),
        _sanitize_cell(c.last_name or ""),
        _sanitize_cell(c.nickname or ""),
        _sanitize_cell(c.suffix or ""),
        c.gender or "",
        c.birth_date.isoformat() if c.birth_date else "",
        c.phone or "",
        _sanitize_cell(c.email or ""),
        _sanitize_cell(c.street_address or ""),
        c.tier or "",
        "" if c.is_active is None else c.is_active,
        "" if c.is_regular is None else c.is_regular,
        "" if c.is_connected is None else c.is_connected,
        c.last_attended_at.isoformat() if c.last_attended_at else "",
        c.attendance_count if c.attendance_count is not None else "",
        c.weeks_absent if c.weeks_absent is not None else "",
        c.created_at.isoformat() if c.created_at else "",
        c.updated_at.isoformat() if c.updated_at else "",
    ]


def _participant_row(p: Participant) -> list:
    return [
        p.id,
        p.contact_id,
        p.event_id,
        p.status,
        _sanitize_cell(p.role or ""),
        p.source,
        p.detection_id if p.detection_id is not None else "",
        p.registered_by_id if p.registered_by_id is not None else "",
        p.created_at.isoformat() if p.created_at else "",
    ]


# ---------------------------------------------------------------------------
# Sync streaming CSV exports
# ---------------------------------------------------------------------------


@router.get("/contacts.csv")
async def export_contacts_csv(
    search: Optional[str] = None,
    contact_type: Optional[str] = None,
    subtype: Optional[str] = None,
    tier: Optional[str] = None,
    is_regular: Optional[bool] = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_viewer),
):
    """Stream all matching contacts as a CSV file."""
    # Only admins may see deleted contacts
    effective_include_deleted = include_deleted and user["role"] == "admin"

    stmt = _build_contact_query(
        search=search,
        contact_type=contact_type,
        subtype=subtype,
        tier=tier,
        is_regular=is_regular,
        include_deleted=effective_include_deleted,
    ).execution_options(yield_per=1000)

    async def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CONTACT_HEADERS)
        yield buf.getvalue()

        stream = await db.stream(stmt)
        async for partition in stream.partitions(1000):
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in partition:
                # stream() with scalars() returns the ORM object directly;
                # with a select(Contact) we get single-element Row tuples.
                contact = row[0] if not isinstance(row, Contact) else row
                writer.writerow(_contact_row(contact))
            yield buf.getvalue()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


@router.get("/participants.csv")
async def export_participants_csv(
    event_id: Optional[int] = None,
    participant_status: Optional[str] = Query(None, alias="status"),
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Stream all matching participants as a CSV file.

    Note: the query param is named `status` but the local variable is
    `participant_status` to avoid shadowing `fastapi.status`.
    """
    stmt = _build_participant_query(
        event_id=event_id,
        participant_status=participant_status,
        source=source,
    ).execution_options(yield_per=1000)

    async def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_PARTICIPANT_HEADERS)
        yield buf.getvalue()

        stream = await db.stream(stmt)
        async for partition in stream.partitions(1000):
            buf = io.StringIO()
            writer = csv.writer(buf)
            for row in partition:
                participant = row[0] if not isinstance(row, Participant) else row
                writer.writerow(_participant_row(participant))
            yield buf.getvalue()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=participants.csv"},
    )


# ---------------------------------------------------------------------------
# XLSX exports (build in memory, write to temp file, FileResponse + cleanup)
# ---------------------------------------------------------------------------


def _build_contacts_xlsx(rows: list[Contact]) -> str:
    """Write contacts to a temporary XLSX file and return its path."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"
    ws.append(_CONTACT_HEADERS)
    for c in rows:
        ws.append(_contact_row(c))

    fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="contacts_")
    os.close(fd)
    wb.save(path)
    return path


def _build_participants_xlsx(rows: list[Participant]) -> str:
    """Write participants to a temporary XLSX file and return its path."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Participants"
    ws.append(_PARTICIPANT_HEADERS)
    for p in rows:
        ws.append(_participant_row(p))

    fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="participants_")
    os.close(fd)
    wb.save(path)
    return path


@router.get("/contacts.xlsx")
async def export_contacts_xlsx(
    search: Optional[str] = None,
    contact_type: Optional[str] = None,
    subtype: Optional[str] = None,
    tier: Optional[str] = None,
    is_regular: Optional[bool] = None,
    include_deleted: bool = False,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_viewer),
):
    """Return an XLSX file containing contacts matching the given filters."""
    effective_include_deleted = include_deleted and user["role"] == "admin"

    stmt = _build_contact_query(
        search=search,
        contact_type=contact_type,
        subtype=subtype,
        tier=tier,
        is_regular=is_regular,
        include_deleted=effective_include_deleted,
    )
    result = await db.execute(stmt)
    contacts = [row[0] if not isinstance(row, Contact) else row for row in result.all()]

    tmp_path = _build_contacts_xlsx(contacts)
    background_tasks.add_task(os.unlink, tmp_path)

    return FileResponse(
        tmp_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="contacts.xlsx",
        background=background_tasks,
    )


@router.get("/participants.xlsx")
async def export_participants_xlsx(
    event_id: Optional[int] = None,
    participant_status: Optional[str] = Query(None, alias="status"),
    source: Optional[str] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    """Return an XLSX file containing participants matching the given filters."""
    stmt = _build_participant_query(
        event_id=event_id,
        participant_status=participant_status,
        source=source,
    )
    result = await db.execute(stmt)
    participants = [
        row[0] if not isinstance(row, Participant) else row
        for row in result.all()
    ]

    tmp_path = _build_participants_xlsx(participants)
    background_tasks.add_task(os.unlink, tmp_path)

    return FileResponse(
        tmp_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="participants.xlsx",
        background=background_tasks,
    )


# ---------------------------------------------------------------------------
# Async export job CRUD
# ---------------------------------------------------------------------------


def _job_to_dict(job: ExportJob, *, include_download_url: bool = False) -> dict:
    data: dict = {
        "id": job.id,
        "job_type": job.job_type,
        "fmt": job.fmt,
        "params": job.params,
        "status": job.status,
        "requested_by_id": job.requested_by_id,
        "row_count": job.row_count,
        "file_bytes": job.file_bytes,
        "error": job.error,
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if include_download_url:
        data["download_url"] = f"/export/jobs/{job.id}/download"
    return data


@router.post("/jobs", status_code=http_status.HTTP_201_CREATED)
async def create_export_job(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Create an async export job. Returns 201 with the new job record.

    Expected body keys: job_type (str), fmt (str), params (dict, optional).
    """
    job_type = body.get("job_type", "")
    fmt = body.get("fmt", "csv")
    params = body.get("params") or {}

    if not job_type:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="job_type is required",
        )

    # HIGH 2: allowlist job_type and fmt to prevent path traversal / arbitrary
    # file write in the export worker (queue_manager.py builds the filename
    # directly from these values).
    _ALLOWED_JOB_TYPES = {"contacts", "attendance"}
    _ALLOWED_FMTS = {"csv"}
    if job_type not in _ALLOWED_JOB_TYPES:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid job_type. Allowed: {sorted(_ALLOWED_JOB_TYPES)}",
        )
    if fmt not in _ALLOWED_FMTS:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid fmt. Allowed: {sorted(_ALLOWED_FMTS)}",
        )

    # Validate params structure
    if not isinstance(params, dict):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="params must be an object",
        )

    # HIGH 1: clamp include_deleted — non-admins may never request deleted
    # records via the async job path.  Mirrors the gate on the sync paths
    # (export_contacts_csv / export_contacts_xlsx lines 191/319).
    if params.get("include_deleted") and user["role"] != "admin":
        params = {**params, "include_deleted": False}

    job = ExportJob(
        job_type=job_type,
        fmt=fmt,
        params=params,
        status="pending",
        requested_by_id=int(user["sub"]),
    )
    db.add(job)
    await db.flush()

    await audit_svc.record(
        db,
        actor_id=int(user["sub"]),
        action="export_job.create",
        entity="export_job",
        entity_id=job.id,
        before=None,
        after={"job_type": job_type, "fmt": fmt, "params": params},
    )
    await db.commit()
    await db.refresh(job)

    return _job_to_dict(job)


@router.get("/jobs")
async def list_export_jobs(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """List export jobs. Admins see all; others see only their own. Newest first.

    limit is clamped to a maximum of 100.
    """
    actor_id = int(user["sub"])
    stmt = select(ExportJob).order_by(ExportJob.created_at.desc()).limit(limit)

    if user["role"] != "admin":
        stmt = stmt.where(ExportJob.requested_by_id == actor_id)

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return [_job_to_dict(j) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_export_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Return a single export job. Owner or admin only.

    download_url is included only when status == 'ready'.
    """
    job = await db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Export job {job_id} not found.",
        )

    actor_id = int(user["sub"])
    if job.requested_by_id != actor_id and user["role"] != "admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    include_download = job.status == "ready"
    return _job_to_dict(job, include_download_url=include_download)


@router.get("/jobs/{job_id}/download")
async def download_export_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Download the file for a completed export job.

    - 403 if caller is not the owner (admin bypass not granted on download).
    - 410 if the job is expired.
    - 404 if the job is not in 'ready' status or the file no longer exists.
    - 403 on path traversal attempt.
    """
    job = await db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Export job {job_id} not found.",
        )

    # Ownership: only the requesting user may download their own job file.
    actor_id = int(user["sub"])
    if job.requested_by_id != actor_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    # Check expiry first (410 Gone takes priority over 404 not-ready).
    if job.expires_at is not None and job.expires_at < utc_now():
        raise HTTPException(
            status_code=http_status.HTTP_410_GONE,
            detail="Export file has expired.",
        )

    # Job must be in 'ready' status and have a file_path.
    if job.status != "ready" or not job.file_path:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Export file is not ready.",
        )

    # Path-containment check — prevent directory traversal.
    storage_root = _get_storage_root()
    requested = (storage_root / job.file_path).resolve()
    try:
        requested.relative_to(storage_root)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Access denied.",
        )

    if not requested.exists() or not requested.is_file():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Export file not found on disk.",
        )

    # Determine media type from file extension.
    suffix = requested.suffix.lower()
    if suffix == ".csv":
        media_type = "text/csv"
        filename = f"export_{job_id}.csv"
    elif suffix == ".xlsx":
        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        filename = f"export_{job_id}.xlsx"
    else:
        media_type = "application/octet-stream"
        filename = requested.name

    return FileResponse(str(requested), media_type=media_type, filename=filename)
