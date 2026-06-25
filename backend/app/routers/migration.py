"""Migration router — CiviCRM import batch inspection (S30).

Prefix: /migration

Endpoints:
  GET /batches                    — admin; paginated list with entity/mode/status filters
  GET /batches/{batch_id}         — admin; detail with pending_review_count
  GET /batches/{batch_id}/rows    — admin; paginated row results with optional outcome filter
  GET /batches/{batch_id}/report.csv — admin; streaming CSV download
  GET /summary                    — admin; latest completed batch per entity + total pending_reviews

Circular-import rule: this router may NOT import from other routers.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import ImportBatch, ImportRowResult, NameMatchReviewQueue
from app.schemas import (
    ImportBatchDetailResponse,
    ImportBatchListResponse,
    ImportBatchOut,
    ImportRowResultListResponse,
    ImportRowResultOut,
    MigrationSummaryResponse,
)
from app.services.migration.streaming_report import stream_batch_csv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/migration", tags=["migration"])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dialect_name(db: AsyncSession) -> str:
    """Return the dialect name ('postgresql' or 'sqlite') from the session bind."""
    try:
        return db.bind.dialect.name  # type: ignore[union-attr]
    except Exception:
        pass
    try:
        from app.services.bulk_service import _dialect
        return _dialect()
    except Exception:
        return "sqlite"


def _batch_to_out(batch: ImportBatch) -> ImportBatchOut:
    """Map ImportBatch ORM fields directly to ImportBatchOut schema."""
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
    """Map ImportRowResult ORM fields to ImportRowResultOut schema."""
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


async def _pending_review_count(batch_id: int, db: AsyncSession) -> int:
    """Count NameMatchReviewQueue rows with status='pending' for the given batch_id.

    Uses a dialect branch:
    - PostgreSQL: uses the JSONB .astext operator (raw_payload['batch_id'].astext)
    - SQLite: uses func.json_extract(raw_payload, '$.batch_id')
    """
    dialect = _dialect_name(db)

    try:
        if dialect == "postgresql":
            stmt = select(func.count()).where(
                NameMatchReviewQueue.status == "pending",
                NameMatchReviewQueue.raw_payload["batch_id"].astext == str(batch_id),
            )
        else:
            # SQLite: raw_payload stores JSON; batch_id value is an int
            stmt = select(func.count()).where(
                NameMatchReviewQueue.status == "pending",
                func.json_extract(NameMatchReviewQueue.raw_payload, "$.batch_id") == batch_id,
            )
        return (await db.execute(stmt)).scalar_one()
    except Exception as exc:
        logger.warning("_pending_review_count: failed for batch %d: %s", batch_id, exc)
        return 0


# ---------------------------------------------------------------------------
# GET /migration/batches
# ---------------------------------------------------------------------------


@router.get("/batches", response_model=ImportBatchListResponse)
async def list_batches(
    entity: Optional[str] = Query(None, description="Filter by entity type (contact/event/participant/link)"),
    mode: Optional[str] = Query(None, description="Filter by mode (create/update/upsert)"),
    status: Optional[str] = Query(None, description="Filter by status (running/done/error/partial)"),
    limit: int = Query(50, ge=1, le=100, description="Max results (clamped to 100)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> ImportBatchListResponse:
    """Paginated list of import batches, newest-first.

    Filters: entity, mode, status.  limit clamped to <=100.  Ordered by
    started_at descending.
    """
    limit = min(limit, 100)

    base_q = select(ImportBatch)

    if entity is not None:
        base_q = base_q.where(ImportBatch.entity == entity)
    if mode is not None:
        base_q = base_q.where(ImportBatch.mode == mode)
    if status is not None:
        base_q = base_q.where(ImportBatch.status == status)

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    rows_q = (
        base_q
        .order_by(ImportBatch.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    batches = (await db.execute(rows_q)).scalars().all()

    return ImportBatchListResponse(
        items=[_batch_to_out(b) for b in batches],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /migration/batches/{batch_id}
# ---------------------------------------------------------------------------


@router.get("/batches/{batch_id}", response_model=ImportBatchDetailResponse)
async def get_batch(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> ImportBatchDetailResponse:
    """Retrieve a single import batch by ID with pending_review_count.

    pending_review_count is the count of NameMatchReviewQueue rows with
    status='pending' whose raw_payload->>'batch_id' matches this batch.
    Returns 404 if the batch does not exist.
    """
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Import batch {batch_id} not found.",
        )

    pending_count = await _pending_review_count(batch_id, db)
    base = _batch_to_out(batch)

    return ImportBatchDetailResponse(
        **base.model_dump(),
        pending_review_count=pending_count,
    )


# ---------------------------------------------------------------------------
# GET /migration/batches/{batch_id}/rows
# ---------------------------------------------------------------------------


@router.get("/batches/{batch_id}/rows", response_model=ImportRowResultListResponse)
async def list_batch_rows(
    batch_id: int,
    outcome: Optional[str] = Query(None, description="Filter by outcome (created/updated/skipped/error/review)"),
    limit: int = Query(50, ge=1, le=200, description="Max results (clamped to 200)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> ImportRowResultListResponse:
    """Paginated list of row-level results for a batch, ordered by row_number.

    Optionally filter by outcome.  limit clamped to <=200.
    Returns 404 if the batch does not exist.
    """
    limit = min(limit, 200)

    # Validate batch exists
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Import batch {batch_id} not found.",
        )

    base_q = select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)

    if outcome is not None:
        base_q = base_q.where(ImportRowResult.outcome == outcome)

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    rows_q = (
        base_q
        .order_by(ImportRowResult.row_number)
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(rows_q)).scalars().all()

    return ImportRowResultListResponse(
        items=[_row_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /migration/batches/{batch_id}/report.csv
# ---------------------------------------------------------------------------


@router.get("/batches/{batch_id}/report.csv")
async def download_batch_report(
    batch_id: int,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> StreamingResponse:
    """Stream the full row-level result set for a batch as a CSV download.

    Returns 404 if the batch does not exist.
    Content-Disposition: attachment; filename='batch_{id}_report.csv'
    """
    # Validate batch exists before streaming
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Import batch {batch_id} not found.",
        )

    return StreamingResponse(
        stream_batch_csv(batch_id, db),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=\"batch_{batch_id}_report.csv\""
        },
    )


# ---------------------------------------------------------------------------
# GET /migration/summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=MigrationSummaryResponse)
async def get_summary(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_admin),
) -> MigrationSummaryResponse:
    """Return the latest completed batch per entity type + total pending reviews.

    Queries one batch per entity (contacts/events/participants/links) ordered by
    started_at desc, filtered to status='completed' (or 'done' — the ORM uses
    'done' per the model docstring; we check both for forward-compat).
    Also returns total pending_reviews across all NameMatchReviewQueue rows.
    """
    completed_statuses = ("done", "completed")

    async def _latest_for_entity(entity: str) -> Optional[ImportBatchOut]:
        stmt = (
            select(ImportBatch)
            .where(
                ImportBatch.entity == entity,
                ImportBatch.status.in_(completed_statuses),
            )
            .order_by(ImportBatch.started_at.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalars().first()
        if row is None:
            return None
        return _batch_to_out(row)

    contacts_batch = await _latest_for_entity("contact")
    events_batch = await _latest_for_entity("event")
    participants_batch = await _latest_for_entity("participant")
    links_batch = await _latest_for_entity("link")

    # Total pending reviews across all batches
    pending_stmt = select(func.count()).where(
        NameMatchReviewQueue.status == "pending"
    )
    total_pending: int = (await db.execute(pending_stmt)).scalar_one()

    return MigrationSummaryResponse(
        contacts=contacts_batch,
        events=events_batch,
        participants=participants_batch,
        links=links_batch,
        pending_reviews=total_pending,
    )
