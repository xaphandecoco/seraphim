"""Activity service — business logic for S12 assignable CRM tasks.

Ownership: S12. Imports audit helper from S02 (per CN-03).
Do NOT create audit_service.py or AuditLog model here — use app.services.audit.record.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Activity, Contact, Outbox, User, utc_now
from app.schemas import ActivityCreate, ActivityFilters, ActivityUpdate
from app.services.audit import record  # S02-owned helper; do NOT fork an audit_service.py

# ---------------------------------------------------------------------------
# Module-level constants (served via /activities/meta/types endpoint)
# ---------------------------------------------------------------------------

ACTIVITY_TYPES = ["call", "visit", "follow_up", "meeting", "email", "note", "other"]
ACTIVITY_STATUSES = ["scheduled", "in_progress", "completed", "cancelled"]
ACTIVITY_PRIORITIES = ["low", "normal", "high", "urgent"]

# Valid forward transitions (any role with require_volunteer)
_FORWARD_TRANSITIONS: dict[str, set[str]] = {
    "scheduled": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
}

# Admin-only reopen transitions
_REOPEN_TRANSITIONS: dict[str, set[str]] = {
    "completed": {"scheduled"},
    "cancelled": {"scheduled"},
}

_PRIORITY_ORDER: dict[str, int] = {"low": 0, "normal": 1, "high": 2, "urgent": 3}


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------


def _snapshot(a: Activity) -> dict:
    return {
        "id": a.id,
        "activity_type": a.activity_type,
        "subject": a.subject,
        "status": a.status,
        "priority": a.priority,
        "assignee_user_id": a.assignee_user_id,
        "target_contact_id": a.target_contact_id,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "activity_date": a.activity_date.isoformat() if a.activity_date else None,
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
    }


# ---------------------------------------------------------------------------
# ActivityService
# ---------------------------------------------------------------------------


class ActivityService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---- private helpers --------------------------------------------------

    async def _apply_status_transition(
        self, activity: Activity, new_status: str, role: str
    ) -> None:
        """Enforce role-aware status-transition matrix; mutates activity in place."""
        current = activity.status
        if current == new_status:
            return  # No-op

        # Forward transition (volunteer+)
        if new_status in _FORWARD_TRANSITIONS.get(current, set()):
            if new_status == "completed":
                activity.completed_at = utc_now()
            activity.status = new_status
            return

        # Reopen (admin-only)
        if new_status in _REOPEN_TRANSITIONS.get(current, set()):
            if role != "admin":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Only admins may reopen a completed or cancelled activity",
                )
            activity.completed_at = None
            activity.status = new_status
            return

        # Invalid transition
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status transition from {current} to {new_status}",
        )

    def _row_to_dict(self, row: tuple) -> dict:
        """Convert (Activity, assignee_User, creator_User, Contact) joined row to dict."""
        activity, assignee, creator, contact = row

        target_contact_name: Optional[str] = None
        if contact is not None:
            parts = [contact.first_name or "", contact.last_name or ""]
            target_contact_name = " ".join(p for p in parts if p).strip() or None

        return {
            "id": activity.id,
            "activity_type": activity.activity_type,
            "subject": activity.subject,
            "details": activity.details,
            "activity_date": activity.activity_date,
            "due_date": activity.due_date,
            "status": activity.status,
            "priority": activity.priority,
            "assignee_user_id": activity.assignee_user_id,
            "target_contact_id": activity.target_contact_id,
            "created_by_id": activity.created_by_id,
            "completed_at": activity.completed_at,
            "reminder_sent_at": activity.reminder_sent_at,
            "created_at": activity.created_at,
            "updated_at": activity.updated_at,
            # Resolved display fields (frontend contract)
            "assignee_name": assignee.name if assignee is not None else None,
            "assignee_email": assignee.email if assignee is not None else None,
            "creator_name": creator.name if creator is not None else None,
            "target_contact_name": target_contact_name,
        }

    def _build_enriched_stmt(self):
        """Base SELECT with outerjoin on assignee / creator User and target Contact."""
        AssigneeAlias = aliased(User)
        CreatorAlias = aliased(User)
        stmt = (
            select(Activity, AssigneeAlias, CreatorAlias, Contact)
            .outerjoin(AssigneeAlias, Activity.assignee_user_id == AssigneeAlias.id)
            .outerjoin(CreatorAlias, Activity.created_by_id == CreatorAlias.id)
            .outerjoin(Contact, Activity.target_contact_id == Contact.id)
        )
        return stmt

    # ---- public API -------------------------------------------------------

    async def get_detail(self, activity_id: int) -> dict:
        """Fetch a single activity with resolved display names; raises 404 if absent."""
        stmt = self._build_enriched_stmt().where(Activity.id == activity_id)
        result = await self.db.execute(stmt)
        row = result.first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )
        return self._row_to_dict(row)

    async def create(self, data: ActivityCreate, actor_id: int) -> dict:
        """Validate and create a new Activity; writes audit log; returns enriched dict."""
        # --- Validation ---
        if data.activity_type not in ACTIVITY_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid activity_type '{data.activity_type}'. Must be one of {ACTIVITY_TYPES}",
            )
        resolved_status = data.status or "scheduled"
        if resolved_status not in ACTIVITY_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status '{resolved_status}'. Must be one of {ACTIVITY_STATUSES}",
            )
        resolved_priority = data.priority or "normal"
        if resolved_priority not in ACTIVITY_PRIORITIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid priority '{resolved_priority}'. Must be one of {ACTIVITY_PRIORITIES}",
            )
        if data.assignee_user_id is not None:
            assignee = await self.db.get(User, data.assignee_user_id)
            if assignee is None or not assignee.is_active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"assignee user {data.assignee_user_id} not found or not active",
                )
        if data.target_contact_id is not None:
            contact = await self.db.get(Contact, data.target_contact_id)
            if contact is None or contact.is_deleted:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"target_contact_id {data.target_contact_id} not found or deleted",
                )
        activity_date = data.activity_date or utc_now()
        if data.due_date is not None and data.due_date < activity_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="due_date must be >= activity_date",
            )

        # Stamp completed_at when creating directly in completed state
        completed_at = utc_now() if resolved_status == "completed" else None

        # --- Persist ---
        activity = Activity(
            activity_type=data.activity_type,
            subject=data.subject,
            details=data.details,
            activity_date=activity_date,
            due_date=data.due_date,
            status=resolved_status,
            priority=resolved_priority,
            assignee_user_id=data.assignee_user_id,
            target_contact_id=data.target_contact_id,
            created_by_id=actor_id,
            completed_at=completed_at,
        )
        self.db.add(activity)
        await self.db.flush()

        await record(
            self.db,
            actor_id,
            "activity.create",
            "activity",
            activity.id,
            None,
            _snapshot(activity),
        )

        await self.db.commit()
        return await self.get_detail(activity.id)

    async def update(
        self,
        activity_id: int,
        data: ActivityUpdate,
        actor: dict,
    ) -> dict:
        """Partial update with role-aware status transitions; writes audit log."""
        result = await self.db.execute(
            select(Activity).where(Activity.id == activity_id).with_for_update()
        )
        activity = result.scalar_one_or_none()
        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        before = _snapshot(activity)
        old_status = activity.status
        update_fields = data.model_fields_set

        # Apply partial fields (only those explicitly sent)
        if "activity_type" in update_fields and data.activity_type is not None:
            if data.activity_type not in ACTIVITY_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid activity_type '{data.activity_type}'",
                )
            activity.activity_type = data.activity_type

        if "subject" in update_fields and data.subject is not None:
            activity.subject = data.subject

        if "details" in update_fields:
            activity.details = data.details  # Allows clearing to None

        if "activity_date" in update_fields and data.activity_date is not None:
            activity.activity_date = data.activity_date

        if "due_date" in update_fields:
            activity.due_date = data.due_date  # Allows clearing to None
            activity.reminder_sent_at = None  # Clear reminder when due_date edited

        if "priority" in update_fields and data.priority is not None:
            if data.priority not in ACTIVITY_PRIORITIES:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid priority '{data.priority}'. Must be one of {ACTIVITY_PRIORITIES}",
                )
            activity.priority = data.priority

        # NOTE: assignee_user_id is intentionally NOT in ActivityUpdate.
        # Reassignment is admin-only and routes through POST /activities/{id}/reassign only.

        if "target_contact_id" in update_fields:
            if data.target_contact_id is not None:
                contact = await self.db.get(Contact, data.target_contact_id)
                if contact is None or contact.is_deleted:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"target_contact_id {data.target_contact_id} not found or deleted",
                    )
            activity.target_contact_id = data.target_contact_id

        if "status" in update_fields and data.status is not None:
            await self._apply_status_transition(activity, data.status, actor["role"])

        status_changed = activity.status != old_status

        # Cross-field validation after partial updates
        if (
            activity.due_date is not None
            and activity.due_date < activity.activity_date
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="due_date must be >= activity_date",
            )

        activity.updated_at = utc_now()
        await self.db.flush()

        actor_id = int(actor["sub"])
        after = _snapshot(activity)

        await record(
            self.db,
            actor_id,
            "activity.update",
            "activity",
            activity.id,
            before,
            after,
        )

        if status_changed:
            await record(
                self.db,
                actor_id,
                "activity.status_change",
                "activity",
                activity.id,
                {"status": old_status},
                {"status": activity.status},
            )

        await self.db.commit()
        return await self.get_detail(activity_id)

    async def reassign(
        self,
        activity_id: int,
        assignee_user_id: Optional[int],
        actor_id: int,
    ) -> dict:
        """Reassign to a different system user (or unassign with None); admin-only."""
        result = await self.db.execute(
            select(Activity).where(Activity.id == activity_id).with_for_update()
        )
        activity = result.scalar_one_or_none()
        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        if assignee_user_id is not None:
            assignee = await self.db.get(User, assignee_user_id)
            if assignee is None or not assignee.is_active:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"assignee user {assignee_user_id} not found or not active",
                )

        before = _snapshot(activity)
        activity.assignee_user_id = assignee_user_id
        activity.updated_at = utc_now()
        await self.db.flush()

        await record(
            self.db,
            actor_id,
            "activity.reassign",
            "activity",
            activity.id,
            before,
            _snapshot(activity),
        )

        await self.db.commit()
        return await self.get_detail(activity_id)

    async def delete(self, activity_id: int, actor_id: int) -> None:
        """Hard-delete; writes audit log with before-snapshot; no return value."""
        result = await self.db.execute(
            select(Activity).where(Activity.id == activity_id)
        )
        activity = result.scalar_one_or_none()
        if activity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        before = _snapshot(activity)
        await self.db.delete(activity)
        await self.db.flush()

        await record(
            self.db,
            actor_id,
            "activity.delete",
            "activity",
            activity_id,
            before,
            None,
        )

        await self.db.commit()

    async def list(
        self,
        *,
        filters: ActivityFilters,
        page: int,
        page_size: int,
        sort: str,
        caller_id: int,
    ) -> tuple[list[dict], int]:
        """Single SELECT with joins; clamps page_size ≤ 100; returns (items, total)."""
        page_size = min(page_size, 100)

        # Build WHERE conditions on Activity columns (no join columns needed for filtering)
        conditions = []

        if filters.assignee_user_id is not None:
            if str(filters.assignee_user_id) == "me":
                conditions.append(Activity.assignee_user_id == caller_id)
            else:
                try:
                    conditions.append(
                        Activity.assignee_user_id == int(filters.assignee_user_id)
                    )
                except (ValueError, TypeError):
                    pass  # Invalid value silently ignored

        if filters.target_contact_id is not None:
            conditions.append(Activity.target_contact_id == filters.target_contact_id)

        if filters.status is not None:
            statuses = [s.strip() for s in filters.status.split(",") if s.strip()]
            if statuses:
                conditions.append(Activity.status.in_(statuses))

        if filters.priority is not None:
            conditions.append(Activity.priority == filters.priority)

        if filters.overdue:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            conditions.append(Activity.due_date.isnot(None))
            conditions.append(Activity.due_date < now)
            conditions.append(Activity.status.in_(["scheduled", "in_progress"]))

        # Count query (on Activity table only — joins not needed for counting)
        count_stmt = select(func.count()).select_from(Activity)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar() or 0

        # Enriched query with joins
        stmt = self._build_enriched_stmt()
        for cond in conditions:
            stmt = stmt.where(cond)

        # Sort
        if sort == "activity_date":
            stmt = stmt.order_by(Activity.activity_date.asc())
        elif sort == "priority":
            priority_col = case(
                (Activity.priority == "low", 0),
                (Activity.priority == "normal", 1),
                (Activity.priority == "high", 2),
                (Activity.priority == "urgent", 3),
                else_=1,
            )
            stmt = stmt.order_by(priority_col.asc())
        elif sort == "created_at":
            stmt = stmt.order_by(Activity.created_at.asc())
        else:
            # Default: due_date asc, NULLs last
            stmt = stmt.order_by(Activity.due_date.is_(None), Activity.due_date.asc())

        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await self.db.execute(stmt)
        rows = result.all()
        items = [self._row_to_dict(row) for row in rows]
        return items, total

    async def get_assignees(self) -> list[User]:
        """Return all active users for the assignee-picker dropdown."""
        result = await self.db.execute(
            select(User).where(User.is_active.is_(True)).order_by(User.name)
        )
        return list(result.scalars().all())

    async def scan_due_reminders(self, *, now: Optional[datetime] = None) -> int:
        """Insert outbox rows for overdue unreminded activities; idempotent via reminder_sent_at.

        Returns count of activities processed (outbox rows inserted).
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)

        stmt = select(Activity).where(
            Activity.due_date.isnot(None),
            Activity.due_date <= now,
            Activity.status.in_(["scheduled", "in_progress"]),
            Activity.reminder_sent_at.is_(None),
            Activity.assignee_user_id.isnot(None),
        )
        result = await self.db.execute(stmt)
        activities = list(result.scalars().all())

        count = 0
        for a in activities:
            outbox_row = Outbox(
                event_type="activity.due_reminder",
                status="pending",
                run_at=utc_now(),
                payload={
                    "activity_id": a.id,
                    "assignee_user_id": a.assignee_user_id,
                    "subject": a.subject,
                    "due_date": a.due_date.isoformat(),
                    "target_contact_id": a.target_contact_id,
                },
            )
            self.db.add(outbox_row)
            a.reminder_sent_at = now
            count += 1

        await self.db.commit()
        return count


# ---------------------------------------------------------------------------
# Module-level callable — wired by S16 scheduler
# ---------------------------------------------------------------------------


async def run_due_reminder_job() -> None:
    """Entry point for APScheduler.

    TODO(S16): register on APScheduler cron (every 15 min) + log to job_runs.
    TODO(S17): outbox consumer delivers activity.due_reminder via Google Chat / email.
    """
    from app.database import async_session  # noqa: PLC0415 — deferred to avoid import cycle

    async with async_session() as db:
        svc = ActivityService(db)
        _count = await svc.scan_due_reminders()
        # TODO(S16): write job_runs row with _count in detail
