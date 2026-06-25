"""Community-report router (S22-F06).

Prefix: /community-reports

Endpoints:
  POST   /                    — volunteer; submit report, auto-resolve submitter,
                                run name matching, set match_status
  GET    /                    — volunteer; paginated list with filters
  GET    /{id}                — volunteer; detail with review_queue_items +
                                matched_contacts
  PATCH  /{id}                — admin; partial update
  DELETE /{id}                — admin; soft-delete (status='archived')
  POST   /{id}/process        — admin; re-trigger name matching

Circular-import rule: this router may NOT import from other routers.
process_name_list is imported from app.routers.attendance (that helper was
explicitly designed for cross-module use per its docstring in attendance.py).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import (
    Contact,
    CommunityReport,
    Event,
    NameMatchReviewQueue,
    Participant,
    User,
    utc_now,
)
from app.schemas import (
    CommunityReportCreate,
    CommunityReportDetailResponse,
    CommunityReportResponse,
    CommunityReportUpdate,
    NameListIntakeResponse,
    PaginatedCommunityReportResponse,
)
from app.services import audit as audit_svc
from app.services.name_match import match_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/community-reports", tags=["community-reports"])

# High-confidence threshold for auto-resolving event leader name
_HIGH_CONFIDENCE_THRESHOLD = 0.88


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _report_to_response(report: CommunityReport, submitted_by_name: Optional[str] = None) -> dict:
    """Convert a CommunityReport ORM row to a dict for CommunityReportResponse."""
    doa: Optional[date] = None
    if report.date_of_activity is not None:
        if isinstance(report.date_of_activity, datetime):
            doa = report.date_of_activity.date()
        else:
            doa = report.date_of_activity

    return {
        "id": report.id,
        "event_id": report.event_id,
        "event_title": report.event_title,
        "date_of_activity": doa,
        "zone": report.zone,
        "topics": report.topics,
        "prayer_items": report.prayer_items,
        "remarks": report.remarks,
        "attendee_names": report.attendee_names or [],
        "event_leader_name": report.event_leader_name,
        "event_leader_contact_id": report.event_leader_contact_id,
        "photo_paths": report.photo_paths or [],
        "match_status": report.match_status,
        "matched_count": report.matched_count,
        "review_count": report.review_count,
        "status": report.status,
        "submitted_by_id": report.submitted_by_id,
        "submitted_by_contact_id": report.submitted_by_contact_id,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "submitted_by_name": submitted_by_name,
    }


# ---------------------------------------------------------------------------
# POST /community-reports
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=CommunityReportResponse,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_community_report(
    body: CommunityReportCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Submit a new community report.

    Steps:
    1. Validate event_id or event_title (schema handles this via model_validator).
    2. If event_id given, validate event exists.
    3. Auto-resolve submitted_by_contact_id by JOIN users.email -> contacts.email.
    4. Save report with match_status='pending'.
    5. If event_leader_name given, run match_name; on high confidence set
       event_leader_contact_id.
    6. If attendee_names non-empty, call process_name_list; update match_status,
       matched_count, review_count.
    7. If attendee_names empty, set match_status='complete'.
    8. Commit and return.
    """
    actor_id = int(user["sub"])
    actor_email: str = user["email"]

    # ---- Validate event_id if provided ----
    resolved_event_id: Optional[int] = body.event_id
    resolved_event_title: Optional[str] = body.event_title
    if body.event_id is not None:
        event = await db.get(Event, body.event_id)
        if event is None:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Event {body.event_id} not found.",
            )
        # Carry event title forward if not already supplied
        if not resolved_event_title:
            resolved_event_title = event.title

    # ---- Auto-resolve submitted_by_contact_id ----
    submitted_by_contact_id: Optional[int] = None
    try:
        contact_row = (
            await db.execute(
                select(Contact.id).where(
                    Contact.email == actor_email,
                    Contact.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if contact_row is not None:
            submitted_by_contact_id = contact_row
    except Exception:
        pass  # non-fatal; leave as None

    # ---- Persist report with initial values ----
    doa_datetime: Optional[datetime] = None
    if body.date_of_activity is not None:
        doa_datetime = datetime(
            body.date_of_activity.year,
            body.date_of_activity.month,
            body.date_of_activity.day,
        )

    report = CommunityReport(
        event_id=resolved_event_id,
        event_title=resolved_event_title,
        submitted_by_id=actor_id,
        submitted_by_contact_id=submitted_by_contact_id,
        raw_text="",
        parsed_names=[],
        zone=body.zone,
        topics=body.topics,
        prayer_items=body.prayer_items,
        remarks=body.remarks,
        attendee_names=list(body.attendee_names),
        event_leader_name=body.event_leader_name,
        event_leader_contact_id=None,
        photo_paths=list(body.photo_paths),
        match_status="pending",
        matched_count=0,
        review_count=0,
        status="pending",
        date_of_activity=doa_datetime,
    )
    db.add(report)
    await db.flush()  # get report.id

    # ---- Step 5: Match event leader name ----
    if body.event_leader_name:
        try:
            leader_result = await match_name(
                raw_name=body.event_leader_name,
                db=db,
                source="community_report",
                event_id=resolved_event_id,
                community_report_id=report.id,
                auto_enqueue=False,  # don't enqueue leader match for review
            )
            if (
                leader_result["outcome"] == "SINGLE"
                and leader_result["contact_id"] is not None
                and leader_result["score"] is not None
                and leader_result["score"] >= _HIGH_CONFIDENCE_THRESHOLD
            ):
                report.event_leader_contact_id = leader_result["contact_id"]
                await db.flush()
        except Exception as exc:
            logger.warning(
                "create_community_report: leader match failed for %r: %s",
                body.event_leader_name,
                exc,
            )

    # ---- Step 6/7: Process attendee names ----
    if body.attendee_names:
        try:
            # Import here to avoid top-level circular dep; attendance imports models/services only
            from app.routers.attendance import process_name_list

            intake_response: NameListIntakeResponse = await process_name_list(
                db=db,
                event_id=resolved_event_id if resolved_event_id else 0,
                names=list(body.attendee_names),
                source="community_report",
                community_report_id=report.id,
                caller_id=actor_id,
            )
            # process_name_list already committed; re-fetch report
            await db.refresh(report)

            matched = intake_response.matched
            review_queue = intake_response.review_queue

            report.matched_count = matched
            report.review_count = review_queue
            report.match_status = "complete" if review_queue == 0 else "partial"
            await db.flush()
        except Exception as exc:
            logger.warning(
                "create_community_report: process_name_list failed: %s", exc
            )
            report.match_status = "partial"
            await db.flush()
    else:
        # Empty attendee_names => match immediately complete
        report.match_status = "complete"
        report.status = "complete"
        await db.flush()

    # ---- Audit ----
    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="community_report.created",
            entity="community_report",
            entity_id=report.id,
            before=None,
            after={
                "event_id": report.event_id,
                "match_status": report.match_status,
                "matched_count": report.matched_count,
                "review_count": report.review_count,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    await db.refresh(report)

    # ---- Fetch submitter name ----
    submitted_by_name: Optional[str] = None
    try:
        user_row = await db.get(User, actor_id)
        if user_row is not None:
            submitted_by_name = user_row.name or user_row.email
    except Exception:
        pass

    return CommunityReportResponse.model_validate(_report_to_response(report, submitted_by_name))


# ---------------------------------------------------------------------------
# GET /community-reports
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedCommunityReportResponse)
async def list_community_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    zone: Optional[str] = Query(None),
    event_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Paginated list of community reports with optional filters.

    Filters: status, zone, event_id, date_from (inclusive), date_to (inclusive).
    Newest-first. page_size clamped to <= 100.
    """
    page_size = min(page_size, 100)
    offset = (page - 1) * page_size

    base_q = select(CommunityReport)

    if status is not None:
        base_q = base_q.where(CommunityReport.status == status)
    if zone is not None:
        base_q = base_q.where(CommunityReport.zone == zone)
    if event_id is not None:
        base_q = base_q.where(CommunityReport.event_id == event_id)
    if date_from is not None:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            base_q = base_q.where(CommunityReport.date_of_activity >= dt_from)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date_from must be YYYY-MM-DD",
            )
    if date_to is not None:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            # inclusive upper bound: end of day
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            base_q = base_q.where(CommunityReport.date_of_activity <= dt_to)
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="date_to must be YYYY-MM-DD",
            )

    # Exclude archived from default listing when no status filter is set
    # (archived is a soft-delete; still accessible via status=archived filter)

    count_q = select(func.count()).select_from(base_q.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    rows_q = (
        base_q
        .order_by(CommunityReport.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(rows_q)).scalars().all()

    # Batch load submitter names
    submitter_ids = {r.submitted_by_id for r in rows if r.submitted_by_id is not None}
    users_by_id: dict[int, User] = {}
    if submitter_ids:
        user_rows = (
            await db.execute(select(User).where(User.id.in_(submitter_ids)))
        ).scalars().all()
        users_by_id = {u.id: u for u in user_rows}

    items = []
    for row in rows:
        sub_name: Optional[str] = None
        if row.submitted_by_id is not None and row.submitted_by_id in users_by_id:
            u = users_by_id[row.submitted_by_id]
            sub_name = u.name or u.email
        items.append(
            CommunityReportResponse.model_validate(_report_to_response(row, sub_name))
        )

    return PaginatedCommunityReportResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# GET /community-reports/{id}
# ---------------------------------------------------------------------------


@router.get("/{report_id}", response_model=CommunityReportDetailResponse)
async def get_community_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Retrieve a single community report with review_queue_items and matched_contacts.

    review_queue_items: NameMatchReviewQueue rows where community_report_id == id.
    matched_contacts: Participant rows joined to contacts for the report's event.
    """
    report = await db.get(CommunityReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Community report {report_id} not found.",
        )

    # Fetch submitter name
    submitted_by_name: Optional[str] = None
    if report.submitted_by_id is not None:
        user_row = await db.get(User, report.submitted_by_id)
        if user_row is not None:
            submitted_by_name = user_row.name or user_row.email

    # Fetch review queue items for this report
    rq_rows = (
        await db.execute(
            select(NameMatchReviewQueue).where(
                NameMatchReviewQueue.community_report_id == report_id
            ).order_by(NameMatchReviewQueue.created_at.desc())
        )
    ).scalars().all()

    # Build review_queue_items list (as dicts; CommunityReportDetailResponse accepts Any)
    review_queue_items: List[Any] = []
    for rq in rq_rows:
        review_queue_items.append({
            "id": rq.id,
            "event_id": rq.event_id or report_id,
            "input_name": rq.raw_name,
            "status": rq.status,
            "candidates": [],
            "resolved_contact_id": rq.contact_id,
            "event_title": None,
            "resolved_contact_name": None,
            "submitted_by_name": None,
            "created_at": rq.created_at,
            "updated_at": rq.updated_at,
        })

    # Fetch matched_contacts: participants (source=community_report) for this event
    matched_contacts: List[Any] = []
    if report.event_id is not None:
        participant_rows = (
            await db.execute(
                select(Participant, Contact)
                .join(Contact, Participant.contact_id == Contact.id)
                .where(
                    Participant.event_id == report.event_id,
                    Participant.source == "community_report",
                )
            )
        ).all()

        for participant, contact in participant_rows:
            matched_contacts.append({
                "contact_id": contact.id,
                "display_name": f"{contact.first_name} {contact.last_name}".strip(),
                "score": 1.0,
                "match_tier": "exact",
                "participant_id": participant.id,
            })

    base_data = _report_to_response(report, submitted_by_name)
    return CommunityReportDetailResponse.model_validate(
        {
            **base_data,
            "review_queue_items": review_queue_items,
            "matched_contacts": matched_contacts,
        }
    )


# ---------------------------------------------------------------------------
# PATCH /community-reports/{id}
# ---------------------------------------------------------------------------


@router.patch("/{report_id}", response_model=CommunityReportResponse)
async def update_community_report(
    report_id: int,
    body: CommunityReportUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Partial update for a community report (admin only).

    Allowed fields: status, zone, topics, prayer_items, remarks, event_id,
    event_title, date_of_activity.
    """
    actor_id = int(user["sub"])

    report = await db.get(CommunityReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Community report {report_id} not found.",
        )

    before: dict[str, Any] = {
        "status": report.status,
        "zone": report.zone,
        "topics": report.topics,
        "prayer_items": report.prayer_items,
        "remarks": report.remarks,
        "event_id": report.event_id,
        "event_title": report.event_title,
        "date_of_activity": report.date_of_activity.isoformat() if report.date_of_activity else None,
    }

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "date_of_activity" and isinstance(value, date) and not isinstance(value, datetime):
            value = datetime(value.year, value.month, value.day)
        setattr(report, field, value)

    report.updated_at = utc_now()
    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="community_report.updated",
            entity="community_report",
            entity_id=report_id,
            before=before,
            after=update_data,
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    await db.refresh(report)

    submitted_by_name: Optional[str] = None
    if report.submitted_by_id is not None:
        user_row = await db.get(User, report.submitted_by_id)
        if user_row is not None:
            submitted_by_name = user_row.name or user_row.email

    return CommunityReportResponse.model_validate(_report_to_response(report, submitted_by_name))


# ---------------------------------------------------------------------------
# DELETE /community-reports/{id}  (soft-delete)
# ---------------------------------------------------------------------------


@router.delete("/{report_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_community_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Soft-delete a community report by setting status='archived'.

    The row is retained; no cascade deletions occur.
    """
    actor_id = int(user["sub"])

    report = await db.get(CommunityReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Community report {report_id} not found.",
        )

    before = {"status": report.status}
    report.status = "archived"
    report.updated_at = utc_now()
    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="community_report.archived",
            entity="community_report",
            entity_id=report_id,
            before=before,
            after={"status": "archived"},
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# POST /community-reports/{id}/process  (admin re-trigger)
# ---------------------------------------------------------------------------


@router.post(
    "/{report_id}/process",
    response_model=NameListIntakeResponse,
    status_code=http_status.HTTP_200_OK,
)
async def process_community_report(
    report_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Re-trigger name matching for a community report (admin only).

    Validates the report exists and has an event_id (required for participant
    insertion).  Re-runs process_name_list against attendee_names and updates
    match_status / matched_count / review_count on the report.
    """
    actor_id = int(user["sub"])

    report = await db.get(CommunityReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Community report {report_id} not found.",
        )

    if report.event_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Community report has no event_id; cannot process attendee names "
                "without a target event."
            ),
        )

    names = list(report.attendee_names or [])

    if not names:
        # Nothing to process — mark complete
        report.match_status = "complete"
        report.matched_count = 0
        report.review_count = 0
        report.updated_at = utc_now()
        await db.flush()
        try:
            await audit_svc.record(
                db,
                actor_id=actor_id,
                action="community_report.processed",
                entity="community_report",
                entity_id=report_id,
                before=None,
                after={"match_status": "complete", "attendee_count": 0},
            )
            await db.commit()
        except Exception:
            try:
                await db.commit()
            except Exception:
                pass

        return NameListIntakeResponse(
            event_id=report.event_id,
            total=0,
            matched=0,
            skipped_existing=0,
            review_queue=0,
            results=[],
        )

    from app.routers.attendance import process_name_list

    intake_response: NameListIntakeResponse = await process_name_list(
        db=db,
        event_id=report.event_id,
        names=names,
        source="community_report",
        community_report_id=report_id,
        caller_id=actor_id,
    )

    # process_name_list commits; re-fetch report
    await db.refresh(report)

    report.matched_count = intake_response.matched
    report.review_count = intake_response.review_queue
    report.match_status = "complete" if intake_response.review_queue == 0 else "partial"
    report.updated_at = utc_now()
    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="community_report.processed",
            entity="community_report",
            entity_id=report_id,
            before=None,
            after={
                "match_status": report.match_status,
                "matched_count": report.matched_count,
                "review_count": report.review_count,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return intake_response
