# S12 — Activities (Assignable Tasks)
**Phase:** D — Power features · **Depends on:** S03 (Native Contact CRUD + Detail Profile — provides the renamed `contacts` table, `Contact` model, contact-detail page shell with the activities slot placeholder, and the contact-picker primitive) · **Effort:** M · **Status:** Not started

> **Naming convention:** The canonical data model calls this entity **`activities`** to avoid collision with the existing face-recognition **`tasks`** table (`backend/app/models.py:131 Task`). Throughout this spec, "Activity" = the new CRM assignable-task entity; "Task" = the existing detection-review entity (`tasks` table). They are entirely unrelated and MUST NOT share routers, models, schemas, stores, or query keys. The end-user UI label for the new entity is "Tasks" (nav label: "Tasks", page heading: "My Tasks"), but every code symbol uses `activity`/`Activity`/`activities`.

---

## 1. Goal & rationale

Light North Caloocan staff currently track follow-up work (call a new friend, visit an absentee, consolidate a contact, send a message) outside the system — in CiviCRM Activities or in chat. This sprint ports the **assignable-activity half** of CiviCRM's Activity/Case feature so Seraphim becomes the system of record for *work to be done about a contact*.

An **Activity** is a typed, dated, assignable unit of work:
- A **type** (e.g. Call, Visit, Follow-up, Email, Note), a **subject**, free-text **details**, an **activity date** (when it happened / is scheduled), an optional **due date**, a **status** (`scheduled` / `in_progress` / `completed` / `cancelled`), and a **priority** (`low` / `normal` / `high` / `urgent`).
- **Assigned to a system user** (`assignee_user_id → users`) — per the locked decision (decisions.md Round 4), assignees are admin/volunteer/viewer logins only, never contacts.
- **Targets a contact** (`target_contact_id → contacts`, nullable for internal/admin chores not tied to a person).
- Surfaced in two places: (a) an **Activities panel on the contact profile** (S03 detail page, which ships a labelled slot for it) and (b) a personal **"My Tasks"** page plus an admin all-activities view.
- **Due-date reminders** are produced by writing rows into the `outbox` table (`event_type='activity.due_reminder'`). Because the outbox *consumer* (Google Chat / email delivery, S17/S18) and the APScheduler cron host (S16) arrive later, S12 ships the **producer side only as a tested stub**: the scan + outbox-insert logic is fully implemented and unit-tested but wired behind a module-level callable that S16 registers on the scheduler. Delivery channel will be Google Chat (single central channel — locked decision, decisions.md Round 8); the payload contains enough context for S17/S18 to build the notification.

**Out of scope (deferred):** multi-step **Cases** (sequences of activities with case roles, timelines, case types) are explicitly a later phase per decisions.md Round 4. S12 ships flat standalone activities only.

---

## 2. Scope

### In scope
- New `activities` table + Alembic migration (all canonical columns from the master data model plus implementation-necessary additions documented in §3).
- New `Activity` SQLAlchemy model in `backend/app/models.py`.
- Pydantic schemas for activity create / update / list / response in `backend/app/schemas.py`.
- `backend/app/routers/activities.py` — full CRUD + list/filter + status transitions, registered in `main.py`.
- `backend/app/services/activity_service.py` — business rules: status-transition matrix (role-aware), assignment validation, due-reminder scan, audit writes, outbox inserts.
- Audit writes via **S02's `app.services.audit`** (per CN-03). S12 **must NOT** create its own `audit_service.py` or `AuditLog` model — it imports `record(...)` from `app.services.audit` (table + model owned by S01; helper owned by S02). See §3.2 and §10.
- A **read-only assignee-picker** endpoint that lists assignable system users (safe projection — no `password_hash` or reset tokens) without requiring the full user-management API.
- `audit_log` writes on create / status change / reassignment / delete.
- Due-date reminder **producer stub**: `ActivityService.scan_due_reminders()` that inserts `outbox` rows; module-level `run_due_reminder_job()` callable with a TODO pointer to S16/S17.
- `outbox` table created-if-absent (idempotent guard — S17 owns it; S12 produces rows early with the canonical shape).
- **No `audit_log` table creation** (per CN-03): the table + `AuditLog` model are owned by **S01**, the `record(...)` helper by **S02**. Any `has_table('audit_log')` guard S12 ships is **defensive-only** and a no-op once S01 lands.
- Frontend: new `/activities` route ("My Tasks" + admin all-view), `ActivitiesPanel` embedded in the S03 contact-detail page, `ActivityFormModal`, `ActivityCard`, `activitiesApi` service, TanStack Query hooks, TypeScript types, and a BottomNav entry.
- Role gating: viewers can read activities (all read endpoints); volunteers can create/edit/complete/cancel; admins can additionally reassign and hard-delete.
- `require_viewer` dependency introduced in `backend/app/dependencies.py` (admits admin | volunteer | viewer) — the minimal forward-compatible hook S15 will reuse or supersede.
- Backend pytest + frontend vitest tests.

### Out of scope (explicit)
- **Cases** (multi-step workflows, case types, case timelines) — later phase.
- **Actual delivery** of due-date reminders (Google Chat send / email) — S17/S18 consume the outbox; S12 only produces rows.
- **Registering the APScheduler cron job + `job_runs` logging** — S16 owns the scheduler host; S12 exposes the callable.
- **Activity custom fields** via the S02 dynamic custom-field engine (the engine supports `entity='Activity'` but wiring `custom_data` onto activities is deferred — see §10 Q5).
- Recurring activities / activity templates.
- File attachments on activities.
- Activity-level SSE live updates (My Tasks refetches via TanStack Query `refetchInterval`).
- Historical CiviCRM Activities import (not in the cutover dataset — see §6 and §10 Q7).

---

## 3. Data model changes

### 3.1 New table: `activities`

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | NO | — | app-minted |
| `activity_type` | String(50) | NO | — | validated against `ACTIVITY_TYPES` constant; seeds: `call`, `visit`, `follow_up`, `meeting`, `email`, `note`, `other` |
| `subject` | String(255) | NO | — | short title |
| `details` | Text | YES | NULL | long-form body |
| `activity_date` | DateTime (naive UTC) | NO | `utc_now()` | when it happened / is scheduled |
| `due_date` | DateTime (naive UTC) | YES | NULL | drives reminders; nullable |
| `status` | String(20) | NO | `'scheduled'` | `scheduled` \| `in_progress` \| `completed` \| `cancelled` |
| `priority` | String(10) | NO | `'normal'` | `low` \| `normal` \| `high` \| `urgent` |
| `assignee_user_id` | Integer FK → `users.id` ON DELETE SET NULL | YES | NULL | nullable: user deletion preserves activity history |
| `target_contact_id` | Integer FK → `contacts.id` ON DELETE CASCADE | YES | NULL | nullable for internal chores not tied to a contact; cascades when contact is hard-deleted |
| `created_by_id` | Integer FK → `users.id` ON DELETE SET NULL | YES | NULL | author; SET NULL on user deletion |
| `completed_at` | DateTime (naive UTC) | YES | NULL | stamped when `status → 'completed'`; cleared on reopen; used for rollup reports + reminder suppression |
| `reminder_sent_at` | DateTime (naive UTC) | YES | NULL | idempotency guard: set once an `outbox` row has been inserted; prevents duplicate reminders; cleared when `due_date` is edited |
| `created_at` | DateTime (naive UTC) | NO | `utc_now()` | |
| `updated_at` | DateTime (naive UTC) | NO | `utc_now()` | `onupdate=utc_now` |

> **Master-doc note:** The canonical column list in the prompt is `id, activity_type, subject, details, activity_date, due_date, status, priority, assignee_user_id, target_contact_id, created_by_id, created_at`. This spec **adds** `completed_at`, `reminder_sent_at`, and `updated_at` as operationally required. These three additions must be reconciled into the master data model (§10 Q1).

**Indexes** (hot/growing columns per AGENTS.md database rules):

| Index name | Columns | Purpose |
|---|---|---|
| `ix_activities_assignee_status` | `(assignee_user_id, status)` | Powers "My Tasks" filtered by assignee + open status |
| `ix_activities_target_contact_id` | `(target_contact_id)` | Powers the contact-profile Activities panel |
| `ix_activities_due_date` | `(due_date)` | Powers reminder scan: `due_date <= now AND reminder_sent_at IS NULL` |
| `ix_activities_status` | `(status)` | Admin "All" view filtering by status |

### 3.2 `audit_log` table (consumed — owned by S01/S02, per CN-03)

**Ruling CN-03:** the `audit_log` table + `AuditLog` SQLAlchemy model are owned by **S01**; the `app/services/audit.py::record(...)` write helper is owned by **S02**. S12 **MUST NOT** create a separate `audit_service.py` or `AuditLog` model — it imports from `app.services.audit`. The `has_table('audit_log')` guard described in §3.4 is **defensive-only** and becomes a no-op once S01 lands; it exists solely so an out-of-order local migration run does not fail. The columns below document the canonical shape S12 consumes (authoritative definition lives in S01):

`audit_log` columns:

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | Integer PK | NO | |
| `actor_id` | Integer FK → `users.id` ON DELETE SET NULL | YES | who performed the action |
| `action` | String(50) | NO | e.g. `activity.create`, `activity.update`, `activity.status_change`, `activity.reassign`, `activity.delete` |
| `entity` | String(50) | NO | `'activity'` in S12; `'contact'` in S11/S03; extensible |
| `entity_id` | Integer | NO | the primary key of the affected row |
| `before` | `JSON().with_variant(JSONB, 'postgresql')` | YES | snapshot before (NULL on create) |
| `after` | `JSON().with_variant(JSONB, 'postgresql')` | YES | snapshot after (NULL on delete) |
| `at` | DateTime (naive UTC) | NO | `utc_now()` |

Index: `ix_audit_log_entity` on `(entity, entity_id)`.

### 3.3 `outbox` table (producer-only dependency)

S12 does NOT own `outbox` — S17 does — but S12 inserts rows. To avoid a hard ordering dependency, S12's migration creates `outbox` **only if absent** (idempotent guard). Canonical shape:

| Column | Type | Null | Default |
|---|---|---|---|
| `id` | Integer PK | NO | — |
| `event_type` | String(100) | NO | — |
| `payload` | `JSON().with_variant(JSONB, 'postgresql')` | YES | NULL |
| `status` | String(20) | NO | `'pending'` |
| `attempts` | Integer | NO | 0 |
| `last_error` | Text | YES | NULL |
| `run_at` | DateTime (naive UTC) | YES | NULL |
| `created_at` | DateTime (naive UTC) | NO | `utc_now()` |

Index: `ix_outbox_status_run_at` on `(status, run_at)` (powers S17's consumer poll).

S12 only ever INSERTs rows with `event_type='activity.due_reminder'`, `status='pending'`.

### 3.4 Alembic migration plan

**File:** `backend/alembic/versions/<rev>_add_activities.py`
Follow the existing hex-prefix naming convention (e.g. `f4b5c6d7e8a9_add_activities.py`). Set `down_revision` to whatever S03 (or the last applied sprint) leaves as head at implementation time — **never hardcode a fixed revision here**; resolve with `alembic heads` before authoring.

**Forward (`upgrade`):**
```python
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    # 1. Create activities table
    op.create_table(
        'activities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('activity_type', sa.String(50), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('activity_date', sa.DateTime(), nullable=False),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='scheduled'),
        sa.Column('priority', sa.String(10), nullable=False, server_default='normal'),
        sa.Column('assignee_user_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_contact_id', sa.Integer(),
                  sa.ForeignKey('contacts.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_by_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('reminder_sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_activities_assignee_status', 'activities',
                    ['assignee_user_id', 'status'])
    op.create_index('ix_activities_target_contact_id', 'activities',
                    ['target_contact_id'])
    op.create_index('ix_activities_due_date', 'activities', ['due_date'])
    op.create_index('ix_activities_status', 'activities', ['status'])

    # 2. audit_log — DEFENSIVE-ONLY guard (per CN-03: table owned by S01, no-op once S01 lands)
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table('audit_log'):
        op.create_table(
            'audit_log',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('actor_id', sa.Integer(),
                      sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
            sa.Column('action', sa.String(50), nullable=False),
            sa.Column('entity', sa.String(50), nullable=False),
            sa.Column('entity_id', sa.Integer(), nullable=False),
            sa.Column('before',
                      sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                      nullable=True),
            sa.Column('after',
                      sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                      nullable=True),
            sa.Column('at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_audit_log_entity', 'audit_log', ['entity', 'entity_id'])

    # 3. outbox — idempotent guard (owned by S17)
    if not insp.has_table('outbox'):
        op.create_table(
            'outbox',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('payload',
                      sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                      nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('run_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
        )
        op.create_index('ix_outbox_status_run_at', 'outbox', ['status', 'run_at'])
```

**Data backfill:** None (new tables only).

**Downgrade (`downgrade`):**
Drop `activities` and its four indexes. For `audit_log`/`outbox`: only drop if S12 was their sole creator — because this is difficult to detect reliably at downgrade time, the safe policy is to **not** drop `audit_log` or `outbox` on downgrade (other sprints depend on them). Add a comment:
```python
# NOTE: audit_log and outbox are shared infrastructure (S11/S15/S17).
# They are NOT dropped here to avoid breaking sprints that were deployed after S12.
# Drop them manually only if S12 was their sole creator and all dependents are rolled back.
op.drop_index('ix_activities_status', table_name='activities')
op.drop_index('ix_activities_due_date', table_name='activities')
op.drop_index('ix_activities_target_contact_id', table_name='activities')
op.drop_index('ix_activities_assignee_status', table_name='activities')
op.drop_table('activities')
```

**CI requirement:** `alembic upgrade head` must be clean and idempotent on Postgres (AGENTS.md:124); the `has_table` guards ensure a fresh `upgrade head` from scratch creates all three tables, and re-running succeeds without error. The `create_all` SQLite path (conftest.py:101) also creates all model tables; model definitions must be present and correct.

---

## 4. Backend

### 4.1 Endpoints

Prefix `/activities` — no `/api` prefix in FastAPI (nginx strips it, AGENTS.md:46). All routes registered with `dependencies=[Depends(check_setup_complete)]` in `main.py`.

Role dependencies (from `backend/app/dependencies.py`):
- `require_viewer` (new in this sprint) — admits `admin | volunteer | viewer`; used on all read endpoints.
- `require_volunteer` (existing) — admits `admin | volunteer`; used on create/edit/complete/cancel.
- `require_admin` (existing) — admits `admin` only; used on reassign and hard-delete.

| METHOD | Path | Role | Request schema | Response schema | Notes |
|---|---|---|---|---|---|
| GET | `/activities` | `require_viewer` | query params (see below) | `PaginatedActivityResponse` | Filtered list. Params: `page` (≥1, default 1), `page_size` (1–100, default 20; clamped), `assignee_user_id` (int or literal `"me"` → resolves to caller id), `target_contact_id` (int), `status` (csv: `scheduled,in_progress`), `priority` (str), `overdue` (bool: `due_date < now AND status IN ('scheduled','in_progress')`), `sort` (`due_date`|`activity_date`|`priority`|`created_at`; default `due_date` asc nulls-last). Joins `users` twice (assignee + creator) + `contacts` (target name) in a single SELECT. |
| GET | `/activities/mine` | `require_viewer` | same paging/sort params | `PaginatedActivityResponse` | Convenience = `/activities?assignee_user_id=me&status=scheduled,in_progress`. Powers "My Tasks". |
| GET | `/activities/{id}` | `require_viewer` | — | `ActivityDetailResponse` | 404 if missing. Resolved assignee/creator/target display fields. |
| POST | `/activities` | `require_volunteer` | `ActivityCreate` | `ActivityDetailResponse` | Creates. Validates `activity_type` ∈ `ACTIVITY_TYPES`, `assignee_user_id` exists + is active (or null), `target_contact_id` exists + not soft-deleted (or null), `due_date >= activity_date` if both provided (422 with clear detail if violated). `created_by_id` = caller. Writes `audit_log` `activity.create`. Returns 201. |
| PATCH | `/activities/{id}` | `require_volunteer` | `ActivityUpdate` (partial) | `ActivityDetailResponse` | Edit subject/details/type/dates/priority/status. Status changes routed through transition matrix (see §4.2). On `→ completed` stamps `completed_at`; on reopen clears it. Editing `due_date` clears `reminder_sent_at`. Writes `audit_log` `activity.update` (+ `activity.status_change` if status changed). |
| POST | `/activities/{id}/reassign` | `require_admin` | `{ "assignee_user_id": int \| null }` | `ActivityDetailResponse` | Reassigns to a different system user (admin only). Validates target user is active. Writes `audit_log` `activity.reassign`. |
| DELETE | `/activities/{id}` | `require_admin` | — | 204 | Hard delete. Writes `audit_log` `activity.delete` with `before` snapshot. Volunteers cancel via `status='cancelled'`; only admins hard-delete. |
| GET | `/activities/meta/types` | `require_viewer` | — | `ActivityMetaResponse` | Returns `{ "types": [...], "statuses": [...], "priorities": [...] }`. Server-driven so the frontend dropdowns stay current without deploys. |
| GET | `/activities/assignees` | `require_volunteer` | — | `list[AssigneeOption]` | Active system users safe projection: `id, name, email, role`. Never exposes `password_hash`, reset token, or other sensitive fields. Used to populate the assignee dropdown in the create/edit form. |

**Status-transition rules** (enforced in `ActivityService.update`):

Valid forward transitions (any role with `require_volunteer`):
- `scheduled → in_progress`
- `in_progress → completed` (stamps `completed_at`)
- `scheduled → cancelled`
- `in_progress → cancelled`

Admin-only transitions:
- `completed → scheduled` (reopen; clears `completed_at`)
- `cancelled → scheduled` (reopen)

Volunteer attempting a terminal reopen → 422 with `detail: "Only admins may reopen a completed or cancelled activity"`.
Any other invalid transition → 422 with `detail: "Invalid status transition from {current} to {requested}"`.

### 4.2 Services / workers / business rules

**`backend/app/services/activity_service.py`** — `class ActivityService(db: AsyncSession)`. Mirrors the pattern in `task_service.py`. Methods:

**`async def create(self, data: ActivityCreate, actor_id: int) -> Activity`**
1. Validate `activity_type` ∈ `ACTIVITY_TYPES` → 422 if not.
2. If `assignee_user_id` provided: load `User`; if missing or `is_active=False` → 422 naming the field.
3. If `target_contact_id` provided: load `Contact`; if missing or `is_deleted=True` → 422.
4. If both `due_date` and `activity_date` provided and `due_date < activity_date` → 422.
5. Set `created_by_id = actor_id`.
6. `db.add(activity)`, `await db.flush()`.
7. `await record(db, actor_id=actor_id, action='activity.create', entity='activity', entity_id=activity.id, before=None, after=_snapshot(activity))` — `record` imported from S02's `app.services.audit` (per CN-03; no local `write_audit`).
8. `await db.commit()`, `await db.refresh(activity)`.
9. Return activity.

**`async def update(self, activity_id: int, data: ActivityUpdate, actor: dict) -> Activity`**
1. `SELECT ... FOR UPDATE` (`with_for_update()`) to avoid concurrent edits — consistent with `task_service.py` dual-approval pattern (AGENTS.md:105).
2. 404 if not found.
3. Snapshot `before = _snapshot(activity)`.
4. Apply partial fields (only fields explicitly set in `ActivityUpdate`).
5. If `status` is being changed: run transition matrix (role-aware via `actor['role']`). 422 on invalid. Stamp/clear `completed_at` per transition.
6. If `due_date` is being changed: clear `reminder_sent_at`.
7. Set `updated_at = utc_now()`.
8. `await db.flush()`.
9. Write `audit_log` `activity.update`; if status changed, also write `activity.status_change` (two rows). `after = _snapshot(activity)`.
10. `await db.commit()`, `await db.refresh(activity)`.

**`async def reassign(self, activity_id: int, assignee_user_id: int | None, actor_id: int) -> Activity`**
Validates new assignee is active (if not null). Writes `audit_log activity.reassign`.

**`async def delete(self, activity_id: int, actor_id: int) -> None`**
Snapshot before delete. Hard-delete. Writes `audit_log activity.delete` with `before` snapshot, `after=None`.

**`async def list(self, *, filters: ActivityFilters, page: int, page_size: int, sort: str, caller_id: int) -> tuple[list[ActivityRow], int]`**
- Single `select(Activity, assignee_alias, creator_alias, Contact)` with `outerjoin` on assignee (User aliased as `assignee_alias`), creator (User aliased as `creator_alias`), Contact.
- Apply filters: resolve `assignee_user_id='me'` → `caller_id`; `overdue` → `Activity.due_date < now AND Activity.status.in_(['scheduled','in_progress'])`.
- Clamp `page_size = min(page_size, 100)`.
- Sort: for `due_date` asc → `order_by(Activity.due_date.is_(None), Activity.due_date.asc())` (NULL last). For `priority` → numeric map `low=0/normal=1/high=2/urgent=3`.
- Separate `count()` query with same WHERE filters for total. Return `(rows, total)`. Zero N+1 (display names from joined rows).

**`async def scan_due_reminders(self, *, now: datetime | None = None) -> int`** — the reminder producer (stub):
1. `now = now or datetime.now(timezone.utc).replace(tzinfo=None)` (naive UTC per AGENTS.md:102).
2. SELECT activities where `due_date IS NOT NULL AND due_date <= now AND status IN ('scheduled','in_progress') AND reminder_sent_at IS NULL AND assignee_user_id IS NOT NULL`.
3. For each activity: `db.add(Outbox(event_type='activity.due_reminder', status='pending', run_at=utc_now(), payload={"activity_id": a.id, "assignee_user_id": a.assignee_user_id, "subject": a.subject, "due_date": a.due_date.isoformat(), "target_contact_id": a.target_contact_id}))`.
4. Set `activity.reminder_sent_at = now` (idempotency guard).
5. `await db.commit()`.
6. Return count of rows processed.

Second invocation on same data → 0 rows (all have `reminder_sent_at` set).
Activities without `assignee_user_id` or with `status` not in `('scheduled','in_progress')` → skipped.
Completed / cancelled activities → skipped.

**Module-level callable** (wired by S16):
```python
async def run_due_reminder_job() -> None:
    """
    Entry point for APScheduler.
    TODO(S16): register on APScheduler cron (every 15 min) + log to job_runs.
    TODO(S17): outbox consumer delivers activity.due_reminder via Google Chat / email.
    """
    from app.database import async_session
    async with async_session() as db:
        svc = ActivityService(db)
        count = await svc.scan_due_reminders()
        # TODO(S16): write job_runs row with count in detail
```

**Audit helper — import from S02 (per CN-03):** S12 does **not** define its own helper or `AuditLog` model. Import `record(...)` from `app.services.audit` (owned by S02):
```python
from app.services.audit import record  # S02-owned helper; do NOT fork an audit_service.py

# usage at each write site (create / update / status_change / reassign / delete):
await record(
    db,
    actor_id=actor_id,
    action="activity.create",
    entity="activity",
    entity_id=activity.id,
    before=None,
    after=_snapshot(activity),
)
```
Match the call to S02's published `record(...)` signature at implementation time; do not introduce a parallel `write_audit`/`AuditLog` definition.

**Snapshot helper** in `activity_service.py`:
```python
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
```

**Allowed-enum constants** (module-level in `activity_service.py`):
```python
ACTIVITY_TYPES = ["call", "visit", "follow_up", "meeting", "email", "note", "other"]
ACTIVITY_STATUSES = ["scheduled", "in_progress", "completed", "cancelled"]
ACTIVITY_PRIORITIES = ["low", "normal", "high", "urgent"]
```

**Performance notes:**
- `GET /activities` is a single join query + count with indexes. No N+1. Safe at the church's volume (<<10k activities).
- Contact-profile panel uses `ix_activities_target_contact_id`. Reminder scan uses `ix_activities_due_date`.
- `page_size` is always clamped ≤ 100 (AGENTS.md list-endpoint rule).

### 4.3 File-by-file change list (backend)

| Op | File | What changes |
|---|---|---|
| CREATE | `backend/app/services/activity_service.py` | `ActivityService` class, `ACTIVITY_TYPES/STATUSES/PRIORITIES` constants, `_snapshot()`, `run_due_reminder_job()` |
| ~~CREATE~~ | ~~`backend/app/services/audit_service.py`~~ | **REMOVED per CN-03** — do NOT create; import `record(...)` from S02's `app.services.audit` instead |
| CREATE | `backend/app/routers/activities.py` | `APIRouter(prefix="/activities", tags=["activities"])`, all 8 endpoints from §4.1 |
| CREATE | `backend/alembic/versions/<rev>_add_activities.py` | Migration per §3.4 |
| MODIFY | `backend/app/models.py` | Add `class Activity(Base)` per §3.1 (with `JSONB` alias from line 22 and `utc_now` from line 27). **Do NOT add `class AuditLog(Base)` (per CN-03 — owned by S01).** Add `class Outbox(Base)` only if not yet defined by S17 — define exactly once |
| MODIFY | `backend/app/schemas.py` | Add `ActivityCreate`, `ActivityUpdate`, `ActivityResponse`, `ActivityDetailResponse`, `PaginatedActivityResponse`, `AssigneeOption`, `ActivityMetaResponse`, `ActivityFilters`; follow `ConfigDict(from_attributes=True)` pattern at schemas.py:121 |
| MODIFY | `backend/app/dependencies.py` | Add `require_viewer` after line 64: admits `admin | volunteer | viewer`; follows existing pattern for `require_admin`/`require_volunteer` |
| MODIFY | `backend/app/main.py` | `from app.routers import activities`; `app.include_router(activities.router, dependencies=[Depends(check_setup_complete)])` — insert after the `tasks.router` line (~line 106) |
| MODIFY | `backend/tests/conftest.py` | Add `viewer_user` + `viewer_auth_headers` fixtures, `another_user` + `another_user_token` fixture (for reassign tests), `sample_contact` fixture (delegates to S03's contacts fixture shape — `Contact` with `first_name`, `last_name`, `is_deleted=False`) |

No files are deleted in this sprint (it is additive; CiviCRM excision is S01).

---

## 5. Frontend

### 5.1 Pages / routes / components

**New route:** `/activities` → `ActivitiesPage` — registered in `App.tsx` as `<ProtectedRoute>` (all authenticated roles including viewer; viewers get read-only UI). Add import and route after the `/attendees` route (~line 57 in `App.tsx`).

**`ActivitiesPage`** (`frontend/src/pages/ActivitiesPage.tsx`):
- **Tabs / segment control:** "My Tasks" (default; calls `GET /activities/mine`) and "All" (admin/volunteer; calls `GET /activities` with full filters). Viewer sees both tabs but no mutation controls.
- **Filter bar:** Status chips (Open = `scheduled,in_progress` / Completed / Cancelled / All), Priority filter dropdown, Overdue-only toggle. Assignee filter (admin/volunteer: dropdown from `/activities/assignees`). Target-contact search (optional; reuses S03 contact-picker primitive).
- **List of `ActivityCard` components** sorted by due date (overdue cards highlighted in amber/red). Paged via TanStack Query (standard offset pagination).
- **FAB / "New Task" button** (volunteer+; hidden for viewer) → opens `ActivityFormModal` in create mode.
- **Card quick-actions** (volunteer+): "Start" (`→ in_progress`), "Complete" (`→ completed`), "..." overflow menu with Edit, Reassign (admin only), Cancel, Delete (admin only). All destructive confirms use `ConfirmDialog` (`components/ui/ConfirmDialog.tsx`).

**`ActivitiesPanel`** (`frontend/src/components/activities/ActivitiesPanel.tsx`):
- Embedded in the S03 `ContactDetailPage.tsx` in the activities slot (labeled `{/* SLOT: activities (S12) */}`).
- Fetches `GET /activities?target_contact_id={contactId}` (open + recent).
- Renders `ActivityCard` list, empty state ("No tasks for this contact yet"), loading state.
- "New Task for this contact" button (volunteer+) opens `ActivityFormModal` with `targetContactId` pre-filled.

**`ActivityFormModal`** (`frontend/src/components/activities/ActivityFormModal.tsx`):
- Create and edit modes (prop `activity?: Activity`).
- Fields: Type (select from `/activities/meta/types`), Subject (text), Details (textarea), Activity Date (date-time), Due Date (date-time, optional), Priority (select), Assignee (select from `/activities/assignees`, optional), Target Contact (contact picker, optional; pre-filled when launched from a contact profile).
- Uses Tailwind design tokens: `bg-card`, `text-foreground`, `border-border`, `bg-background`.
- Error surfaces via `sonner` toast: `toast.error(err.response?.data?.detail || 'Something went wrong')`.
- Destructive confirms via `ConfirmDialog`.
- Mobile: renders as a bottom sheet (fixed bottom-0, rounded-t-2xl). Desktop (≥`md`): centered modal.

**`ActivityCard`** (`frontend/src/components/activities/ActivityCard.tsx`):
Presentational card. Shows: subject (truncated), type badge (e.g. "Call"), priority dot (color-coded low=gray / normal=blue / high=amber / urgent=red), target-contact name + link (→ `/contacts/{id}`), assignee name/avatar, due date with overdue styling (`text-red-500` + clock icon if past due + status ≠ completed), status chip, quick-action buttons/menu per role.

**BottomNav** (`frontend/src/components/layout/BottomNav.tsx`):
Add "Tasks" as a main-tab entry using `ListTodo` icon (from `lucide-react`, line 5 in `BottomNav.tsx` already imports various lucide icons). Given the existing 5 main tabs (`Tasks`, `Audit`, `Ranking`, `Events`, `Members`), add "Activities" (user-facing label "Tasks") in the main set. The exact tab displacement is an owner UX call (§10 Q6); default: move "Ranking" to the admin "More" sheet and add "Tasks" (activities) to the main set. Add a small overdue-badge variant (count of caller's overdue activities) using `PendingBadge` pattern from `BottomNav.tsx:7-15`.

### 5.2 State management

**TanStack Query keys** (all prefixed `['activities',...]` to avoid collision with existing task-queue keys `['tasks',...]`):

| Key | Used by |
|---|---|
| `['activities', 'list', filters]` | All/filtered list (admin view) |
| `['activities', 'mine', filters]` | My Tasks |
| `['activities', 'detail', id]` | Single activity |
| `['activities', 'byContact', contactId]` | ActivitiesPanel |
| `['activities', 'meta']` | Enum types/statuses/priorities — long `staleTime` (10 min) |
| `['activities', 'assignees']` | Assignee dropdown — long `staleTime` (5 min) |
| `['activities', 'overdueBadge']` | BottomNav badge count — `refetchInterval: 60_000` |

**Mutations** (`create`, `update`, `delete`, `reassign`, status quick-actions) invalidate:
- `['activities', 'list']`, `['activities', 'mine']`, `['activities', 'detail', id]`
- `['activities', 'byContact', targetContactId]` when the mutation involves a specific contact.

**Zustand:** No new store needed. Role comes from `useAuthStore` (`authStore.ts`): `isAdmin` (existing) + a derived `isViewer` computed as `store.user?.role === 'viewer'`. Add `isViewer: boolean` to `AuthState` interface and set it in `login` / `setUser` / `setToken` (minimal forward-compat with S15 which will formalize the role).

### 5.3 Role gating and UX states

**Viewer (`role='viewer'`):**
- All read endpoints succeed; all list/detail pages visible.
- Create / Edit / Reassign / Delete controls hidden via `{!isViewer && <button>...}`.
- No "New Task" FAB, no "New Task for this contact" button.

**Volunteer:**
- Create / Edit / Complete / Cancel.
- Reassign and Delete controls hidden.

**Admin:**
- Full access including Reassign and Delete.

**UX states per AGENTS.md and existing component patterns:**

| State | Component | Detail |
|---|---|---|
| Loading | `LoadingState` from `components/ui/StateViews.tsx:4` | Spinner + message |
| Empty (My Tasks) | `EmptyState` from `StateViews.tsx:13` | Icon: `ListTodo`, "No tasks assigned to you", CTA "New Task" (hidden for viewer) |
| Empty (panel) | `EmptyState` | "No tasks for this contact yet", CTA "New Task" (hidden for viewer) |
| Error | `ErrorState` from `StateViews.tsx:31` | "Something went wrong" + retry |

**Mobile-first (375px baseline):**
- Activity cards: full-width, stacked, quick-actions in "..." overflow menu.
- Form: bottom-sheet modal with `rounded-t-2xl` panel.

**Desktop (≥`md` breakpoint):**
- Cards remain; optionally render a compact table on admin "All" view via `DataTable` from S03 (imported `components/ui/DataTable.tsx`). Cards are acceptable for S12 initial delivery.
- Form: centered modal (`sm:max-w-lg` dialog).

**Dark mode:** all components use Tailwind token classes (`bg-card`, `text-foreground`, `bg-background`, `border-border`); `dark:` variants auto-apply via the `darkMode: 'class'` config (`tailwind.config.js:2`).

### 5.4 File-by-file change list (frontend)

| Op | File | What changes |
|---|---|---|
| CREATE | `frontend/src/pages/ActivitiesPage.tsx` | "My Tasks" / "All" tabs + filters + list + FAB |
| CREATE | `frontend/src/components/activities/ActivitiesPanel.tsx` | Embedded panel for contact profile |
| CREATE | `frontend/src/components/activities/ActivityFormModal.tsx` | Create/edit modal |
| CREATE | `frontend/src/components/activities/ActivityCard.tsx` | Presentational card (used by page + panel) |
| CREATE | `frontend/src/services/activitiesApi.ts` | Typed axios calls on the shared `api` client from `services/api.ts` |
| CREATE | `frontend/src/hooks/useActivities.ts` | TanStack Query hooks: `useActivities`, `useMyActivities`, `useActivity`, `useActivitiesByContact`, `useActivityMeta`, `useAssignees`, `useCreateActivity`, `useUpdateActivity`, `useReassignActivity`, `useDeleteActivity` |
| CREATE | `frontend/src/types/activity.ts` | `Activity`, `ActivityStatus`, `ActivityPriority`, `ActivityCreate`, `ActivityUpdate`, `AssigneeOption`, `ActivityMeta`, `PaginatedActivities` TS types |
| MODIFY | `frontend/src/App.tsx` | Add `import { ActivitiesPage }` + `<Route path="/activities" element={<ProtectedRoute><ActivitiesPage /></ProtectedRoute>} />` |
| MODIFY | `frontend/src/components/layout/BottomNav.tsx` | Add "Tasks" entry (`ListTodo` icon, path `/activities`) + optional overdue badge; adjust existing tab layout per §5.1 |
| MODIFY | S03 `ContactDetailPage.tsx` (exact path owned by S03) | Mount `<ActivitiesPanel contactId={id} />` in the labelled activities slot — S03 ships `{/* SLOT: activities (S12) */}` placeholder |
| MODIFY | `frontend/src/store/authStore.ts` | Add `isViewer: boolean` to `AuthState`; derive in `login`, `setUser`, `setToken` (forward-compat with S15) |

---

## 6. Migration / data

No data migration. All three tables are new. The S06 ETL (CiviCRM cutover) does **not** import CiviCRM Activities: the re-export covers contacts, events, attendance, and custom fields only (decisions.md Round 4 — multi-step Cases deferred; standalone CiviCRM Activities not in scope for the owner's cutover dataset). Historical CiviCRM activities can be imported as a later backlog item if the owner requests it (§10 Q7).

---

## 7. Acceptance criteria

1. `alembic upgrade head` on a fresh SQLite DB **and** a fresh Postgres DB creates `activities` with all columns from §3.1 and all four indexes, creates `audit_log` and `outbox` if absent, and succeeds on re-run (idempotent `has_table` guards). `alembic downgrade -1` drops `activities` and its indexes only.
2. `POST /activities` as a volunteer with a valid body returns 201; the response has `created_by_id == caller_id`, `status == 'scheduled'`, `priority == 'normal'` (when omitted), and an `audit_log` row exists with `action='activity.create'`, `entity='activity'`, `before IS NULL`, `after` containing the activity's `id` and `subject`.
3. `POST /activities` with a non-existent `assignee_user_id` returns 422 with `detail` mentioning "assignee". With a soft-deleted or non-existent `target_contact_id` returns 422. With `due_date < activity_date` returns 422.
4. `GET /activities/mine` as user X returns only activities where `assignee_user_id == X` and `status IN ('scheduled', 'in_progress')`, paginated (default `page_size=20`), sorted `due_date` ascending with NULLs last. `page_size=500` is clamped to 100.
5. `GET /activities?target_contact_id={id}` returns only activities for that contact; used by the `ActivitiesPanel`.
6. `PATCH /activities/{id}` with `status='completed'` sets `completed_at` to a non-null datetime. `PATCH` back to `status='scheduled'` by an **admin** clears `completed_at`. A **volunteer** attempting to reopen a `completed` or `cancelled` activity receives 422/403. An invalid transition (e.g. `cancelled → in_progress`) receives 422.
7. `PATCH /activities/{id}` editing `due_date` sets `reminder_sent_at=null` regardless of prior value.
8. `POST /activities/{id}/reassign` returns 200 for admin, 403 for volunteer, 403 for viewer; on success writes `audit_log action='activity.reassign'` with both `before` and `after` snapshots.
9. `DELETE /activities/{id}` returns 204 for admin, 403 for volunteer, 403 for viewer; the row is removed; an `audit_log` row with `action='activity.delete'` and a non-null `before` snapshot exists.
10. All read endpoints (`GET /activities`, `/activities/mine`, `/activities/{id}`, `/activities/meta/types`, `/activities/assignees`) return 200 for a **viewer** token. All write endpoints (`POST`, `PATCH`, `POST /reassign`, `DELETE`) return 403 for a viewer token.
11. `GET /activities/assignees` response objects contain only `id`, `name`, `email`, `role` — no `password_hash`, `password_reset_token`, or `password_reset_expires_at` keys.
12. `ActivityService.scan_due_reminders()` called against a fixture with one overdue, unreminded, assigned activity inserts exactly **one** `outbox` row (`event_type='activity.due_reminder'`, `status='pending'`, `payload` containing `activity_id`), sets `reminder_sent_at` on that activity, and a second call inserts **zero** additional rows (idempotent). Activities with `assignee_user_id=None` or `status='completed'` are skipped.
13. `run_due_reminder_job()` is importable and callable from tests with no scheduler present; it opens its own DB session and returns without error.
14. `npm run build` and `npm run lint` pass. The `/activities` route renders "My Tasks". A viewer sees no create/edit/delete controls. A volunteer can create and complete an activity. An admin can reassign and delete. The S03 contact-detail page shows the `ActivitiesPanel`.
15. No FastAPI endpoint uses the `/api` prefix. All list endpoints clamp `page_size` to ≤ 100.

---

## 8. Test plan

### Backend (pytest)

Environment: `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`

New file: `backend/tests/test_activities.py`

**CRUD & validation:**
- `test_create_activity_as_volunteer_returns_201_and_audit` — asserts 201, `created_by_id` == caller, status `scheduled`, priority `normal`; queries `audit_log` for `action='activity.create'`, `before IS NULL`, `after` non-null.
- `test_create_activity_invalid_assignee_422` — non-existent `assignee_user_id` → 422, detail mentions assignee.
- `test_create_activity_inactive_assignee_422` — deactivated user as assignee → 422.
- `test_create_activity_invalid_contact_422` — non-existent `target_contact_id` → 422.
- `test_create_activity_soft_deleted_contact_422` — `is_deleted=True` contact → 422.
- `test_create_activity_due_before_activity_date_422` — `due_date < activity_date` → 422.
- `test_create_defaults_status_and_priority` — omitting status and priority → `scheduled` and `normal`.
- `test_update_activity_subject_and_details` — volunteer can PATCH subject/details; `updated_at` advances.
- `test_update_activity_status_scheduled_to_in_progress` — transition ok; audit row with `activity.status_change`.
- `test_update_activity_status_in_progress_to_completed` — `completed_at` set; audit row.
- `test_admin_reopen_completed_activity` — admin PATCH `status=scheduled` → `completed_at` cleared; audit.
- `test_volunteer_reopen_completed_forbidden` — volunteer PATCH `status=scheduled` on completed → 422.
- `test_invalid_transition_422` — e.g. `cancelled → in_progress` → 422.
- `test_patch_due_date_clears_reminder_sent_at` — pre-set `reminder_sent_at`; PATCH `due_date`; assert `reminder_sent_at IS NULL`.

**List & filters:**
- `test_list_mine_filters_by_assignee_and_open_status` — seed activities for two users; `/activities/mine` returns only caller's `scheduled`/`in_progress`.
- `test_list_overdue_filter` — `?overdue=true` returns only past-due open activities.
- `test_list_sort_due_date_nulls_last` — activities with and without due_date; assert ordering.
- `test_list_pagination_clamps_page_size` — `?page_size=500` → response `page_size == 100`.
- `test_list_by_target_contact` — `?target_contact_id=X` returns only that contact's activities.

**RBAC:**
- `test_reassign_admin_ok_writes_audit` — admin reassign → 200, new `assignee_user_id`, audit `activity.reassign`.
- `test_reassign_volunteer_403` — volunteer → 403.
- `test_reassign_viewer_403` — viewer token → 403.
- `test_delete_admin_204_writes_audit_before` — admin → 204; audit row with non-null `before`.
- `test_delete_volunteer_403` — 403.
- `test_delete_viewer_403` — 403.
- `test_viewer_can_read_all_endpoints` — parametrize over `GET /activities`, `/activities/mine`, `/activities/{id}`, `/activities/meta/types`, `/activities/assignees`; all 200.
- `test_viewer_write_endpoints_403` — parametrize over `POST /activities`, `PATCH /activities/{id}`, `POST /activities/{id}/reassign`, `DELETE /activities/{id}`; all 403.

**Assignees:**
- `test_assignees_endpoint_omits_sensitive_fields` — response dicts have no `password_hash`, `password_reset_token` keys.
- `test_assignees_returns_only_active_users` — deactivated user not returned.

**Meta:**
- `test_meta_types_returns_expected_enums` — response includes `types`, `statuses`, `priorities`; `"call"` in `types`.

**Reminder producer:**
- `test_scan_due_reminders_inserts_outbox_and_is_idempotent` — seed one overdue assigned activity; first call returns 1, inserts 1 `outbox` row (`event_type='activity.due_reminder'`, `status='pending'`), sets `reminder_sent_at`; second call returns 0 and inserts 0 more rows.
- `test_scan_due_reminders_skips_completed_and_no_assignee` — completed activity and unassigned activity not included.
- `test_scan_due_reminders_skips_future_due_date` — `due_date > now` not included.
- `test_scan_due_reminders_skips_already_reminded` — `reminder_sent_at` already set → skipped.
- `test_run_due_reminder_job_callable` — import and `await run_due_reminder_job()` succeeds without error; a seeded overdue activity produces an outbox row.

**`conftest.py` additions:**
- `viewer_user` fixture — `User(role='viewer', is_active=True)`.
- `viewer_auth_headers` fixture — token for viewer user.
- `another_user` fixture — second volunteer user (for reassign tests).
- `sample_contact` fixture — `Contact(first_name='Test', last_name='Contact', is_deleted=False)` using the post-S01 `Contact` model.

### Frontend (vitest)

New directory: `frontend/src/components/activities/__tests__/`

- `ActivityCard.test.tsx` — renders subject, type badge, priority indicator, due-date; shows overdue styling (`text-red-500`) when past due; hides Reassign/Delete for non-admin; hides all mutation controls (Edit, Complete, Delete) for viewer role (`isViewer=true`).
- `ActivityFormModal.test.tsx` — type and assignee selects populate from mocked query data; submit fires `createActivity` mutation with correct payload; on error `toast.error` receives server `detail`; `target_contact_id` is pre-filled when `targetContactId` prop provided.
- `ActivitiesPanel.test.tsx` — fetches `byContact` key, renders `EmptyState` when no activities, "New Task" button calls `ActivityFormModal` with pre-filled `targetContactId`, button hidden for viewer.
- `authStore.test.ts` — `login(user, token)` sets `isViewer=true` when `user.role='viewer'`; `isAdmin=false`.
- `npm run build` (TypeScript typecheck) and `npm run lint` must pass in CI with no new type errors.

---

## 9. Rollout / rollback / risks

**Rollout:** Fully additive. No existing tables modified (activities, audit_log if new, outbox if new). Deploy migration → backend → frontend as a unit. The reminder producer stub (`run_due_reminder_job`) is callable but not scheduled until S16, so no behavioral change on deploy.

**Rollback:** `alembic downgrade -1` drops `activities` and its four indexes. `audit_log` and `outbox` are kept (per §3.4 note) — they are inert without the activities table. Frontend route `/activities` returns 404 on API calls → renders `ErrorState` — inert, no data loss. No destructive change to existing tables.

**Risks:**

| Risk | Mitigation |
|---|---|
| Symbol collision `Activity` / `Task` | Strict naming rule: all new code uses `Activity`/`activities`. Code review must check for accidental `Task` imports in `activities.py`/`activity_service.py`. |
| Shared-table race: `audit_log` / `outbox` defined in multiple sprints | `has_table` migration guards + single model-class-definition rule. Reconcile in master doc (§10 Q2). |
| `contacts` FK depends on S01 renaming the table | S12 depends on S03 which depends on S01; `contacts.id` FK in the migration is safe by the time S12 runs. |
| `require_viewer` introduced before S15 formalizes RBAC | The dependency is minimal and forward-compatible. S15 must keep or supersede it consistently (§10 Q3). |
| Reminder duplicates on concurrent invocations | N/A on the single-Unraid-instance decision. The `reminder_sent_at` guard prevents duplicates even if the job overlaps with itself. |
| BottomNav 5-slot overflow | Owner UX decision required for which tab moves to "More" (§10 Q6). Default proposed in §5.1; implement per owner input before merge. |

---

## 10. Open questions & pending owner artifacts

1. **Master-doc reconciliation — extra columns:** `activities` adds `completed_at`, `reminder_sent_at`, `updated_at` beyond the canonical column list. The master data model in the prompt should adopt these (operationally required for completion rollups, reminder idempotency, and audit). **Action: master reconciler adds these three columns to the canonical `activities` definition.**

2. **Shared-table ownership (`audit_log`, `outbox`):** Both tables are needed by S11 (merge audit), S12 (activity audit + reminder outbox), S15 (RBAC audit), and S17 (outbox consumer). The master plan must designate one sprint as the creator of each (recommendation: `audit_log` created in whichever of S11/S12 ships first, with `has_table` guards; `outbox` owned by S17 with S12's `has_table` guard for early-arrival cases). `AuditLog` and `Outbox` model classes must be defined exactly once in `models.py`. **Action: master assigns ownership; this spec's guards make any ordering safe.**

3. **`require_viewer` before S15:** S12 introduces `require_viewer` as a forward-compatible shim. S15 must keep this dependency or supersede it in a non-breaking way. Confirm that the `viewer` JWT `role` claim flows correctly from `auth.py:107` through refresh (`auth.py:212-218`) without changes — yes, `role` is already a JWT claim today; no auth changes needed. **Action: S15 spec confirms compatibility.**

4. **Activity types configurability:** S12 hardcodes `ACTIVITY_TYPES` (served via `/activities/meta/types`). Owner question: should types be admin-configurable at runtime (via `admin_settings` or S02 custom-field `entity='Activity'`)? The `/meta/types` endpoint makes a later server-side switch non-breaking for the frontend. **Decision needed; deferred to post-S12 backlog.**

5. **Activity custom fields:** The S02 engine supports `custom_field_group.entity='Activity'` but S12 ships no `custom_data` column on `activities`. Confirm whether activities need dynamic custom fields for the church's use case; if yes, a follow-up sprint adds `activities.custom_data JSONB` and wires the S02 renderer. **Decision needed; deferred.**

6. **BottomNav 5-slot displacement:** The existing main nav has 5 slots: Tasks (detection-review), Audit, Ranking, Events, Members. Adding "Tasks" (activities) requires moving one item to the admin "More" sheet or adding a 6th slot. Proposal: move "Ranking" to "More" for non-admin users. **Owner UX decision required before frontend merge.**

7. **Historical CiviCRM Activities import:** Not in the S06 cutover dataset per decisions.md Round 4. Confirm the owner does not need legacy CiviCRM activity history migrated. If required, scope a separate ETL item after S06 (contacts + events + attendance only). **Pending owner confirmation.**

8. **Google Chat channel ID for activity reminders:** S12's outbox payload contains `activity_id`, `assignee_user_id`, `subject`, `due_date`, `target_contact_id`. The delivery policy (Google Chat single channel, timing, format) is finalized in S17/S18 when the notification engine lands. No S12 blocker — the payload carries all needed context. **Pending S17/S18 owner input.**

9. **S03 contact-detail page symbol names:** The `ActivitiesPanel` mount point (`<ActivitiesPanel contactId={id} />`) must match S03's actual component filename and slot label. S03 spec §2 says the detail page ships `{/* SLOT: activities (S12) */}`. If S03 renames or restructures that slot, update the mount in S03's PR, not retroactively. **Coordinate with S03 implementer.**
