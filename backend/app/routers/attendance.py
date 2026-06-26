from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.models import CommunityReport, Event, Participant, utc_now
from app.schemas import NameListIntakeRequest, NameListIntakeResponse, NameMatchResultItem, MatchCandidateSchema, ParticipantRecord
from app.services import audit as audit_svc
from app.services.bulk_service import _dialect
from app.services.name_match import match_name_batch

router = APIRouter(prefix="/attendance", tags=["attendance"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helper — factored out so S22-F06 community-report POST can call
# it directly without an HTTP round-trip (spec sec4.5 step5).
# ---------------------------------------------------------------------------


async def process_name_list(
    db: AsyncSession,
    event_id: int,
    names: list[str],
    source: str,
    community_report_id: Optional[int],
    caller_id: int,
) -> NameListIntakeResponse:
    """Match a list of names, insert matched contacts as Participant rows,
    and return a NameListIntakeResponse.

    This function commits the session itself (matches bulk_service convention).
    It does NOT raise HTTP exceptions — callers do validation before calling.
    """
    total = len(names)
    large_batch = total > 50

    # ---- 1. Run name matching -----------------------------------------------
    # For <= 50 names: full pipeline including synchronous Claude.
    # For > 50 names: alias + deterministic only; Claude dispatched async below.
    match_results = await match_name_batch(
        names=names,
        db=db,
        source=source,
        event_id=event_id,
        community_report_id=community_report_id,
    )
    # Note: match_name_batch auto_enqueues unmatched/ambiguous via _enqueue_review
    # internally (auto_enqueue=True by default in match_name).

    # ---- 2. Collect matched contact_ids -------------------------------------
    matched_contact_ids: list[int] = []
    result_items: list[NameMatchResultItem] = []

    for mr in match_results:
        if mr["outcome"] == "SINGLE" and mr["contact_id"] is not None:
            matched_contact_ids.append(mr["contact_id"])

        # Build NameMatchResultItem for the response
        candidates = [
            MatchCandidateSchema(
                contact_id=c["contact_id"],
                display_name=f"{c['first_name']} {c['last_name']}".strip(),
                score=c["score"],
                match_tier=_outcome_to_tier(c["score"]),
            )
            for c in mr.get("candidates", [])
        ]
        item_status: str
        if mr["outcome"] == "SINGLE":
            item_status = "matched"
        elif mr["outcome"] == "AMBIGUOUS":
            item_status = "review"
        else:
            item_status = "unmatched"

        display_name: Optional[str] = None
        if mr["contact_id"] is not None and candidates:
            display_name = candidates[0].display_name

        result_items.append(
            NameMatchResultItem(
                input_name=mr["raw_name"],
                status=item_status,
                matched_contact_id=mr.get("contact_id"),
                matched_contact_name=display_name,
                candidates=candidates,
            )
        )

    # ---- 3. Deduplicate matched contact_ids (keep unique) -------------------
    unique_contact_ids = list(dict.fromkeys(matched_contact_ids))

    # ---- 4. Count pre-insert existing rows for skipped_existing tracking ----
    skipped_existing = 0
    inserted = 0

    if unique_contact_ids:
        pre_count_result = await db.execute(
            select(func.count()).where(
                Participant.event_id == event_id,
                Participant.contact_id.in_(unique_contact_ids),
            )
        )
        pre_existing: int = pre_count_result.scalar_one()

        # ---- 5. Bulk INSERT...ON CONFLICT DO NOTHING (S05 dual-dialect pattern) ----
        now = utc_now()
        rows = [
            {
                "event_id": event_id,
                "contact_id": cid,
                "status": "attended",
                "source": source,
                "registered_by_id": caller_id,
                "created_at": now,
            }
            for cid in unique_contact_ids
        ]

        if _dialect() == "postgresql":
            stmt = pg_insert(Participant).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])
        else:
            stmt = sqlite_insert(Participant).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["event_id", "contact_id"])

        res = await db.execute(stmt)
        raw_rowcount = res.rowcount if res.rowcount is not None else -1

        if 0 <= raw_rowcount <= len(unique_contact_ids):
            inserted = raw_rowcount
        else:
            # Fallback: count post-insert rows and subtract pre-existing
            post_count_result = await db.execute(
                select(func.count()).where(
                    Participant.event_id == event_id,
                    Participant.contact_id.in_(unique_contact_ids),
                )
            )
            post_existing: int = post_count_result.scalar_one()
            inserted = post_existing - pre_existing

        skipped_existing = len(unique_contact_ids) - inserted

    # Count review queue entries
    review_queue_count = sum(
        1 for mr in match_results
        if mr["outcome"] in ("AMBIGUOUS", "UNMATCHED")
    )

    # ---- 6. Audit -----------------------------------------------------------
    try:
        await audit_svc.record(
            db,
            actor_id=caller_id,
            action="attendance.name_list_intake",
            entity="event",
            entity_id=event_id,
            before=None,
            after={
                "matched": inserted,
                "queued": review_queue_count,
                "skipped": skipped_existing,
                "total": total,
                "source": source,
                "community_report_id": community_report_id,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    # ---- 7. For large batches: dispatch Claude async after response ---------
    claude_pending: Optional[bool] = None
    if large_batch:
        claude_pending = True
        # Gather items still needing Claude resolution
        ambiguous_results = [mr for mr in match_results if mr["outcome"] == "AMBIGUOUS"]
        if ambiguous_results:
            asyncio.create_task(
                _async_claude_pass(ambiguous_results, db, event_id, community_report_id)
            )

    return NameListIntakeResponse(
        event_id=event_id,
        total=total,
        matched=inserted,
        skipped_existing=skipped_existing,
        review_queue=review_queue_count,
        claude_pending=claude_pending,
        results=result_items,
    )


async def _async_claude_pass(
    ambiguous_results: list,
    db: AsyncSession,
    event_id: int,
    community_report_id: Optional[int],
) -> None:
    """Background task: run Claude on ambiguous matches from a large batch.

    Best-effort — errors are logged and swallowed.
    """
    try:
        from app.services.name_match import lookup_claude
        from app.database import async_session

        async with async_session() as bg_db:
            for mr in ambiguous_results:
                try:
                    raw_name = mr["raw_name"]
                    normalized = mr["normalized"]
                    candidates = mr.get("candidates", [])
                    if not candidates:
                        continue
                    claude_hit = await lookup_claude(raw_name, normalized, candidates, bg_db)
                    if claude_hit is not None and claude_hit["contact_id"]:
                        # Update the existing review queue row with Claude's pick
                        from app.models import NameMatchReviewQueue
                        if mr.get("review_queue_id"):
                            row = await bg_db.get(NameMatchReviewQueue, mr["review_queue_id"])
                            if row and row.status == "pending":
                                row.candidate_contact_id = claude_hit["contact_id"]
                                row.score = claude_hit["score"]
                                await bg_db.commit()
                except Exception as exc:
                    logger.warning("_async_claude_pass: error for %r: %s", mr.get("raw_name"), exc)
    except Exception as exc:
        logger.warning("_async_claude_pass: session error: %s", exc)


def _outcome_to_tier(score: float) -> str:
    """Map a Jaro-Winkler score to a match_tier label."""
    if score >= 0.95:
        return "exact"
    if score >= 0.85:
        return "high"
    if score >= 0.70:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# POST /attendance/name-list
# ---------------------------------------------------------------------------


@router.post(
    "/name-list",
    response_model=NameListIntakeResponse,
    status_code=http_status.HTTP_200_OK,
)
async def name_list_intake(
    req: NameListIntakeRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Accept a list of attendee names, match them to contacts, and record attendance.

    - Validates the event exists and is_active (404 / 400).
    - Optional community_report_id: validates it exists and its status is
      'submitted' or 'pending' (400).
    - Clamps len(names) <= 500; over 500 returns 422.
    - For <= 50 names: runs full match_name_batch including synchronous Claude.
    - For > 50 names: runs alias + deterministic match synchronously, dispatches
      Claude via asyncio.create_task, returns claude_pending=true.
    - Inserts matched contacts as Participant rows (source=source, status='attended')
      using the S05 ON CONFLICT(event_id, contact_id) DO NOTHING pattern.
    - Emits audit_svc.record(action='attendance.name_list_intake').
    - Returns NameListIntakeResponse where matched + skipped_existing + review_queue == total.
    - Idempotent: submitting the same name twice yields matched=1 on first call,
      matched=0 / skipped_existing=1 on second.
    """
    caller_id: int = int(user["sub"])

    # ---- Validate names length (Pydantic enforces max_length=500; belt+suspenders) ----
    if len(req.names) > 500:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="names list may not exceed 500 entries.",
        )

    if not req.names:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="names list must contain at least one entry.",
        )

    # ---- Validate event ----
    event = await db.get(Event, req.event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Event {req.event_id} not found.",
        )
    if not event.is_active:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Event {req.event_id} is not active.",
        )

    # ---- Validate community_report_id (optional) ----
    if req.community_report_id is not None:
        cr = await db.get(CommunityReport, req.community_report_id)
        if cr is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"CommunityReport {req.community_report_id} not found.",
            )
        if cr.status not in ("submitted", "pending"):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"CommunityReport {req.community_report_id} has status "
                    f"'{cr.status}'; expected 'submitted' or 'pending'."
                ),
            )

    return await process_name_list(
        db=db,
        event_id=req.event_id,
        names=req.names,
        source=req.source,
        community_report_id=req.community_report_id,
        caller_id=caller_id,
    )


# ---------------------------------------------------------------------------
# GET /attendance
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ParticipantRecord])
async def list_attendance(
    event_id: Optional[int] = None,
    date: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List participant records with optional filters (bounded; newest first)."""
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    query = select(Participant).order_by(Participant.created_at.desc())

    if event_id:
        query = query.where(Participant.event_id == event_id)
    if status:
        query = query.where(Participant.status == status)
    if date:
        try:
            # Naive UTC to match the naive DateTime columns
            start = datetime.strptime(date, "%Y-%m-%d")
            end = start.replace(hour=23, minute=59, second=59)
            query = query.where(Participant.created_at >= start, Participant.created_at <= end)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()
    return records
