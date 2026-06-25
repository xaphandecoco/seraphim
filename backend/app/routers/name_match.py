"""Review-queue router for name-matching pipeline (S22-F05).

Prefix: /name-match

Endpoints:
  GET  /review-queue                  — paginated list, filters status/source/event_id
  POST /review-queue/{id}/resolve     — accept + optional teach_alias
  POST /review-queue/{id}/unmatch     — reject, set status='rejected'
  POST /review-queue/{id}/skip        — leave pending (idempotent)
  POST /review-queue/reprocess-aliases — admin, re-run pipeline on pending rows

Circular-import rule: routers must NEVER import other routers.
Services import from models/db/config; routers import from services.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import (
    Contact,
    Event,
    NameMatchReviewQueue,
    Participant,
    utc_now,
)
from app.services import audit as audit_svc
from app.services.name_match import (
    lookup_alias,
    lookup_claude,
    lookup_deterministic,
    normalize_name,
    teach_alias_from_resolution,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/name-match", tags=["name-match"])


# ---------------------------------------------------------------------------
# Dialect helper — mirrors bulk_service._dialect()
# ---------------------------------------------------------------------------


def _dialect() -> str:
    from app.database import engine as _eng
    try:
        return _eng.dialect.name
    except Exception:
        return "sqlite"


# ---------------------------------------------------------------------------
# Participant ON CONFLICT DO NOTHING — dual-dialect helper
# ---------------------------------------------------------------------------


async def _insert_participant_if_event(
    db: AsyncSession,
    contact_id: int,
    event_id: int,
    actor_id: Optional[int],
) -> None:
    """Insert a Participant row for (event_id, contact_id) if not already present.

    Uses dual-dialect ON CONFLICT DO NOTHING to match the S05/F04 pattern.
    No exception is raised on conflict.
    """
    now = utc_now()
    values = {
        "event_id": event_id,
        "contact_id": contact_id,
        "status": "attended",
        "source": "name_list",
        "registered_by_id": actor_id,
        "created_at": now,
    }

    if _dialect() == "postgresql":
        stmt = pg_insert(Participant).values([values])
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])
    else:
        stmt = sqlite_insert(Participant).values([values])
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])

    await db.execute(stmt)


# ---------------------------------------------------------------------------
# Pydantic request / response bodies for this router
# ---------------------------------------------------------------------------


class ResolveRequest(BaseModel):
    contact_id: int
    teach_alias: Optional[bool] = False
    alias_text: Optional[str] = None


class UnmatchRequest(BaseModel):
    reason: Optional[str] = None


class ReviewQueueRow(BaseModel):
    """Serialized review-queue row returned by the list endpoint."""

    id: int
    raw_name: str
    status: str
    score: Optional[float] = None
    source: Optional[str] = None
    event_id: Optional[int] = None
    event_title: Optional[str] = None
    community_report_id: Optional[int] = None
    candidate_contact_id: Optional[int] = None
    resolved_contact_id: Optional[int] = None
    resolved_contact_name: Optional[str] = None
    resolved_by_id: Optional[int] = None
    resolved_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PaginatedReviewQueueResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[ReviewQueueRow]


class ReprocessResult(BaseModel):
    processed: int
    auto_matched: int
    still_pending: int


# ---------------------------------------------------------------------------
# GET /review-queue
# ---------------------------------------------------------------------------


@router.get("/review-queue", response_model=PaginatedReviewQueueResponse)
async def list_review_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    event_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Paginated review queue.

    Filters: status (pending|accepted|rejected|skipped|matched), source, event_id.
    Joins event.title and resolved contact name.
    Newest-first. page_size clamped to <= 100.
    """
    page_size = min(page_size, 100)
    offset = (page - 1) * page_size

    base_query = select(NameMatchReviewQueue)

    if status is not None:
        base_query = base_query.where(NameMatchReviewQueue.status == status)
    if event_id is not None:
        base_query = base_query.where(NameMatchReviewQueue.event_id == event_id)

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    # Fetch page
    rows_query = (
        base_query
        .order_by(NameMatchReviewQueue.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(rows_query)).scalars().all()

    # Collect foreign-key IDs for batch lookups
    event_ids = {r.event_id for r in rows if r.event_id is not None}
    contact_ids = {r.contact_id for r in rows if r.contact_id is not None}

    # Batch load events
    events_by_id: dict[int, Event] = {}
    if event_ids:
        ev_rows = (
            await db.execute(select(Event).where(Event.id.in_(event_ids)))
        ).scalars().all()
        events_by_id = {ev.id: ev for ev in ev_rows}

    # Batch load resolved contacts
    contacts_by_id: dict[int, Contact] = {}
    if contact_ids:
        ct_rows = (
            await db.execute(select(Contact).where(Contact.id.in_(contact_ids)))
        ).scalars().all()
        contacts_by_id = {ct.id: ct for ct in ct_rows}

    items: List[ReviewQueueRow] = []
    for row in rows:
        event_title: Optional[str] = None
        if row.event_id is not None and row.event_id in events_by_id:
            event_title = events_by_id[row.event_id].title

        resolved_contact_name: Optional[str] = None
        if row.contact_id is not None and row.contact_id in contacts_by_id:
            ct = contacts_by_id[row.contact_id]
            resolved_contact_name = f"{ct.first_name} {ct.last_name}".strip()

        items.append(ReviewQueueRow(
            id=row.id,
            raw_name=row.raw_name,
            status=row.status,
            score=float(row.score) if row.score is not None else None,
            event_id=row.event_id,
            event_title=event_title,
            community_report_id=row.community_report_id,
            candidate_contact_id=row.candidate_contact_id,
            resolved_contact_id=row.contact_id,
            resolved_contact_name=resolved_contact_name,
            resolved_by_id=row.resolved_by_id,
            resolved_at=row.resolved_at.isoformat() if row.resolved_at else None,
            created_at=row.created_at.isoformat() if row.created_at else None,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        ))

    return PaginatedReviewQueueResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# POST /review-queue/reprocess-aliases  (declared BEFORE /{id} routes)
# ---------------------------------------------------------------------------


@router.post(
    "/review-queue/reprocess-aliases",
    response_model=ReprocessResult,
    dependencies=[Depends(require_admin)],
)
async def reprocess_aliases(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Re-run alias + claude pipeline on all pending review-queue rows.

    Admin-only. Idempotent — safe to call repeatedly (cron 0 0 * * 1).
    High-confidence single matches are auto-resolved:
      - status set to 'matched'
      - resolved_by_id = None  (system resolution)
      - Participant row inserted ON CONFLICT DO NOTHING if event_id present

    Returns {processed, auto_matched, still_pending}.
    """
    # Fetch all pending rows
    pending_rows = (
        await db.execute(
            select(NameMatchReviewQueue).where(
                NameMatchReviewQueue.status == "pending"
            )
        )
    ).scalars().all()

    processed = 0
    auto_matched = 0

    for row in pending_rows:
        processed += 1
        try:
            normalized = normalize_name(row.raw_name)

            # Stage 1: alias lookup
            alias_hit = await lookup_alias(normalized, db)
            matched_contact_id: Optional[int] = None

            if alias_hit is not None:
                matched_contact_id = alias_hit["contact_id"]
            else:
                # Stage 2: fuzzy deterministic
                candidates = await lookup_deterministic(normalized, db)

                if len(candidates) == 1:
                    matched_contact_id = candidates[0]["contact_id"]
                elif len(candidates) > 1:
                    # Stage 3: Claude tiebreaker
                    claude_hit = await lookup_claude(row.raw_name, normalized, candidates, db)
                    if claude_hit is not None:
                        matched_contact_id = claude_hit["contact_id"]

            if matched_contact_id is not None:
                # Validate contact still exists and is not deleted
                contact = await db.get(Contact, matched_contact_id)
                if contact is None or contact.is_deleted:
                    continue  # skip — contact gone

                now = utc_now()
                row.status = "matched"
                row.contact_id = matched_contact_id
                row.resolved_by_id = None  # system resolution
                row.resolved_at = now
                row.updated_at = now

                # Insert participant row if event_id is set
                if row.event_id is not None:
                    await _insert_participant_if_event(
                        db, matched_contact_id, row.event_id, actor_id=None
                    )

                await db.flush()
                auto_matched += 1

        except Exception as exc:
            logger.warning(
                "reprocess_aliases: error on review_queue id=%d: %s", row.id, exc
            )
            try:
                await db.rollback()
            except Exception:
                pass
            continue

    # Audit and commit
    try:
        await audit_svc.record(
            db,
            actor_id=int(user["sub"]),
            action="name_match.reprocess_aliases",
            entity="name_match_review_queue",
            entity_id=None,
            before=None,
            after={
                "processed": processed,
                "auto_matched": auto_matched,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    # Count still pending after commit
    still_pending: int = (
        await db.execute(
            select(func.count()).where(NameMatchReviewQueue.status == "pending")
        )
    ).scalar_one()

    return ReprocessResult(
        processed=processed,
        auto_matched=auto_matched,
        still_pending=still_pending,
    )


# ---------------------------------------------------------------------------
# POST /review-queue/{id}/resolve
# ---------------------------------------------------------------------------


@router.post(
    "/review-queue/{item_id}/resolve",
    status_code=http_status.HTTP_200_OK,
)
async def resolve_review_item(
    item_id: int,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Resolve a review-queue item by accepting a contact match.

    - 404 if item not found.
    - Validates contact exists and is not deleted (400).
    - 409 if already matched or unmatched (rejected).
    - Sets contact_id, resolved_by_id, resolved_at, status='matched'.
    - Inserts Participant ON CONFLICT DO NOTHING if event_id is present.
    - Optional: if teach_alias=true, upserts a NameAlias via
      teach_alias_from_resolution (ON CONFLICT(alias_text) DO UPDATE).
    - Emits audit log.
    """
    actor_id = int(user["sub"])

    # 404 check
    row = await db.get(NameMatchReviewQueue, item_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Review queue item {item_id} not found.",
        )

    # 409 if already terminal
    if row.status in ("matched", "rejected"):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Review queue item {item_id} already has status '{row.status}'.",
        )

    # Validate contact exists and is not deleted
    contact = await db.get(Contact, body.contact_id)
    if contact is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} not found.",
        )
    if contact.is_deleted:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} is deleted and cannot be used for resolution.",
        )

    now = utc_now()
    before = {
        "status": row.status,
        "contact_id": row.contact_id,
        "resolved_by_id": row.resolved_by_id,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }

    # Mutate the review queue row
    row.contact_id = body.contact_id
    row.resolved_by_id = actor_id
    row.resolved_at = now
    row.status = "matched"
    row.updated_at = now

    await db.flush()

    # Insert participant row if event_id present
    if row.event_id is not None:
        await _insert_participant_if_event(
            db, body.contact_id, row.event_id, actor_id=actor_id
        )

    # Optional alias teach
    if body.teach_alias:
        try:
            await teach_alias_from_resolution(
                db=db,
                review_queue_id=item_id,
                contact_id=body.contact_id,
                alias_text=body.alias_text or None,
                actor_id=actor_id,
            )
        except Exception as exc:
            logger.warning(
                "resolve_review_item: teach_alias failed for item %d: %s", item_id, exc
            )

    # Audit
    after = {
        "status": "matched",
        "contact_id": body.contact_id,
        "resolved_by_id": actor_id,
        "teach_alias": body.teach_alias,
    }
    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="name_match.resolved",
            entity="name_match_review_queue",
            entity_id=item_id,
            before=before,
            after=after,
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return {
        "id": item_id,
        "status": "matched",
        "contact_id": body.contact_id,
    }


# ---------------------------------------------------------------------------
# POST /review-queue/{id}/unmatch
# ---------------------------------------------------------------------------


@router.post(
    "/review-queue/{item_id}/unmatch",
    status_code=http_status.HTTP_200_OK,
)
async def unmatch_review_item(
    item_id: int,
    body: UnmatchRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Reject a review-queue item (mark as unmatched / rejected).

    - 404 if item not found.
    - 409 if already matched or rejected.
    - Sets status='rejected', resolved_by_id, resolved_at.
    - Optional reason stored in audit log.
    """
    actor_id = int(user["sub"])

    row = await db.get(NameMatchReviewQueue, item_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Review queue item {item_id} not found.",
        )

    if row.status in ("matched", "rejected"):
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Review queue item {item_id} already has status '{row.status}'.",
        )

    now = utc_now()
    before = {
        "status": row.status,
        "resolved_by_id": row.resolved_by_id,
    }

    row.status = "rejected"
    row.resolved_by_id = actor_id
    row.resolved_at = now
    row.updated_at = now

    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="name_match.unmatched",
            entity="name_match_review_queue",
            entity_id=item_id,
            before=before,
            after={
                "status": "rejected",
                "resolved_by_id": actor_id,
                "reason": body.reason,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return {"id": item_id, "status": "rejected"}


# ---------------------------------------------------------------------------
# POST /review-queue/{id}/skip
# ---------------------------------------------------------------------------


@router.post(
    "/review-queue/{item_id}/skip",
    status_code=http_status.HTTP_200_OK,
)
async def skip_review_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Skip a review-queue item — leaves it in 'pending' status.

    Idempotent: calling skip on an already-pending or already-skipped item
    returns 200 without changing anything.

    Returns {id, status: 'pending'}.
    """
    row = await db.get(NameMatchReviewQueue, item_id)
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Review queue item {item_id} not found.",
        )

    # No-op for already-terminal states — return current status
    return {"id": item_id, "status": row.status}
