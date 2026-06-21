# S05 — Bulk Participants & Export-at-Scale
**Phase:** B — Core CRM · **Depends on:** S03 (Native Contact CRUD + `contacts` table, `ContactsPage.tsx`, `DataTable`/`Pagination`/`FormField` primitives), S04 (Native Event CRUD + `participants` table with `UNIQUE(event_id,contact_id)`, `EventDetailPage.tsx` + participant grid) · **Effort:** L · **Status:** Not started

> Conventions are non-negotiable: async/await for all I/O; type-hint every public function; no `/api` prefix in FastAPI (nginx strips it); JSON columns use `JSON().with_variant(JSONB, "postgresql")`; one Alembic migration per schema change and never edit an applied one; Tailwind design tokens only (`bg-card`/`text-foreground`/`bg-background`/`bg-primary`/`border-border`) — no hardcoded hex; toasts via `sonner` surfacing `err.response?.data?.detail`; access token in-memory (Zustand), refresh via HttpOnly cookie; tests run under `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`.

---

## 1. Goal & rationale

These are the **two daily pains** the owner named explicitly in the grilling sessions (framework.md §3, decisions.md Round 1): (1) adding or updating attendance for an entire congregation (up to ~1,440 contacts) is slow and error-prone — it must be done person-by-person or via fragile UI batches; and (2) exporting the full contact list or the full ~33,600-row attendance history either times out or buffers the whole dataset into memory and falls over.

S05 makes both operations **fast, set-based, and memory-bounded**:

- **Bulk participants** — a single HTTP call adds `attended`/`registered` participant rows for a whole audience (a static group, a saved-search result, an explicit contact-id list, or "all contacts") to one event using a **single set-based `INSERT ... SELECT ... ON CONFLICT (event_id, contact_id) DO NOTHING`**. A separate call flips the **status** of an audience's existing participant rows via one `UPDATE ... WHERE contact_id IN (subquery)`. Soft remove defaults to status → `cancelled`; hard delete (admin only) uses a single `DELETE`. Target: **< 1 s for 1,500 rows** on Postgres, verified by a load test.

- **Export at scale** — streaming CSV and streaming XLSX for **contacts** and **participants**, driven by a server-side cursor + Python async generator so the file is **never fully buffered in RAM**. Verified at **33,000 rows**. For very large or slow exports an optional **background-job mode** writes the file to storage and returns an authenticated, one-time download link, reusing the existing worker host (`services/queue_manager.py`).

This sprint operates against the `contacts`, `events`, and `participants` tables minted in S01/S03/S04. It deliberately concentrates set-write logic in one new service (`bulk_service.py`) and streaming-read logic in one new service (`export_service.py`) so S06 (ETL preview/import), S09 (saved searches / smart groups), S10 (import wizard), and S14 (reporting export) can import from them without duplication.

**Why S03/S04 are blocking:** `contacts`, `events`, and `participants` (with the `UNIQUE(event_id, contact_id)` constraint that makes `ON CONFLICT` safe) must already exist and be the authoritative tables. S05 adds only *bulk write* and *streaming read* paths over them plus the `export_jobs` table for the async option.

**Analytics.py legacy note:** `backend/app/routers/analytics.py` currently contains `export_attendance_csv` (analytics.py:118-154) and `export_logs_csv` (analytics.py:157-184) that buffer `result.all()` into an `io.StringIO()` and emit a `push_status` column (analytics.py:131,144,165,173). After S01 drops `push_status` both exports are broken at runtime. S05 replaces the attendance export with streaming `/export/participants.csv` and rewrites the logs export to remove the dead column. These must be addressed in this sprint — they cannot be left for later.

---

## 2. Scope

### In scope
- Set-based **bulk add participants** to a single event for a target audience (all-contacts / static-group / saved-search (guarded on S09) / explicit contact-id list), using a single `INSERT ... SELECT ... ON CONFLICT (event_id, contact_id) DO NOTHING`. Returns `requested` / `inserted` / `skipped` / `elapsed_ms` counts.
- Set-based **bulk update participant status** (and optionally `role`) for a target audience on a single event, using a single `UPDATE ... WHERE event_id=:e AND contact_id IN (<audience subquery>)`. Optional `only_if_status` narrowing. Returns `matched` / `updated` / `elapsed_ms`.
- Set-based **bulk cancel/remove participants** — soft (status → `cancelled`, the safe default, volunteer-gated) or hard (`DELETE`, admin-only flag).
- A **target-audience resolver** (`services/audience.py`) shared by all bulk ops and the export filter layer: resolves `{ all | group_id | saved_search_id | contact_ids[] }` into a SQLAlchemy `Select` subquery of contact ids; the subquery is composed server-side and never materialised into a Python list for the `all`/`group`/`search` cases.
- A **preview endpoint** that resolves the audience and returns counts-only (audience size / would-insert / would-update / would-skip) with no writes — drives the confirm dialog.
- **Streaming CSV export** for `contacts` and `participants` (server-side cursor + async generator + chunked 1,000-row partitions via `db.stream(stmt.execution_options(yield_per=1000))`; never `.all()`).
- **Streaming XLSX export** for `contacts` and `participants` using `xlsxwriter` in `constant_memory=True` mode writing to a temp file, then returned via `FileResponse` with a `BackgroundTask` to delete the temp file after send.
- **Background-job export mode** (`export_jobs` table): enqueue → worker generates to `STORAGE_PATH/exports/` → client polls status → authenticated download with path-containment check and 24h TTL.
- Contact export columns: all 13 core scalar columns **plus** one column per active `custom_field_def` (with multi-select rendered as `"; "`-joined and `contact_reference` rendered as display names) **plus** the six derived snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) — nullable until S23 recomputes them.
- Participant export columns: `participant_id`, `contact_id`, `contact_first_name`, `contact_last_name`, `event_id`, `event_title`, `event_type`, `session_time`, `occurrence_date`, `status`, `role`, `source`, `created_at`.
- Frontend: **Bulk-Add/Status/Remove panel** on the event detail participant grid + **Export menu** on the contacts list and participant/attendance list pages. Progress/polling UI for async jobs.
- A **load test** asserting < 1 s for 1,500 bulk inserts (Postgres); an **export memory-bounded streaming test** at 33k rows.
- `require_viewer` dependency shim (forward-compatible with S15 viewer role) for export auth gating.
- Deletion/rewriting of the two broken legacy analytics.py exports.
- One `AuditLog` row per successful bulk write (guarded: try/except if `audit_log` pre-dates S01).

### Out of scope
- Import (CSV/XLSX *in*) → S06 (migration ETL) and S10 (import wizard). S05 is bulk write-by-reference and streaming read-out only.
- Building saved-search/group *definitions* → S09. S05 *consumes* existing `groups`/`group_members`/`saved_searches` rows but does not create them; smart-group/saved-search audience modes return a guarded 400 until S09's criteria compiler is importable.
- Per-row inline participant edit → S04 built that in the participant grid.
- Reporting aggregates / charts → S14.
- Email delivery of export files → download-link only.
- CiviCRM push, outbox, or any dead-letter machinery — deleted in S01; not reintroduced here.
- Computing derived snapshot columns (tier, is_active, etc.) — those belong to S23's nightly recompute job. S05 only *exports* whatever values are currently in the snapshot columns (NULL if S23 hasn't run yet).

---

## 3. Data model changes

S05 adds exactly **one** new table (`export_jobs`). All bulk-write paths require **no schema change** — they operate on `participants` exactly as established by S04 (the `UNIQUE(event_id, contact_id)` constraint is the linchpin of `ON CONFLICT`).

### Pre-condition check
Before implementing the `ON CONFLICT` clause, confirm:
- S04's migration created the unique constraint; the canonical name is `uq_participants_event_contact`. If S04 used a different name, use that name in `index_elements`.
- `participants.source` is a free `String(20)` (the canonical source list is `face|manual|zoom|name_list|community_report|import|bulk`; adding `"bulk"` here needs no schema change).
- `participants.role` is a nullable `String` (not a DB enum).

### New table: `export_jobs`

| Column | Type | Null | Default / constraint | Notes |
|---|---|---|---|---|
| `id` | Integer PK | NOT NULL | autoincrement | app-minted |
| `job_type` | String(30) | NOT NULL | — | `contacts` \| `participants` |
| `fmt` | String(8) | NOT NULL | — | `csv` \| `xlsx` |
| `params` | JSONB | NOT NULL | `'{}'` server default | Filter/scope echo (event_id, status, group_id, columns list, etc.); `JSON().with_variant(JSONB,"postgresql")` |
| `status` | String(20) | NOT NULL | `'pending'` | `pending` \| `running` \| `ready` \| `failed` \| `expired` |
| `requested_by_id` | Integer FK→`users.id` `ON DELETE SET NULL` | NULL | — | Actor who requested |
| `row_count` | Integer | NULL | — | Filled on completion |
| `file_path` | Text | NULL | — | Relative to `STORAGE_PATH` (e.g. `exports/contacts_20260621_ab12.csv`) |
| `file_bytes` | Integer | NULL | — | File size in bytes; surfaced in UI |
| `error` | Text | NULL | — | Set on failure (truncated to 500 chars) |
| `expires_at` | DateTime (naive UTC) | NULL | — | `created_at + 24h`; expired rows purged by worker |
| `created_at` | DateTime (naive UTC) | NOT NULL | `utc_now` | |
| `finished_at` | DateTime (naive UTC) | NULL | — | Set when terminal |

Indexes:
- `ix_export_jobs_status_created` on `(status, created_at)` — worker picks `pending` oldest-first; purge sweep finds `ready`/`failed` past `expires_at`.
- `ix_export_jobs_requested_by` on `(requested_by_id)` — "my recent exports" list.

ORM model — add to `backend/app/models.py` after `AdminSetting`, using the module-level `JSONB` alias already defined at `models.py:22` and `utc_now` at `models.py:28`:

```python
class ExportJob(Base):
    __tablename__ = "export_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)   # contacts | participants
    fmt: Mapped[str] = mapped_column(String(8), nullable=False)         # csv | xlsx
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    requested_by_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL")
    )
    row_count: Mapped[Optional[int]] = mapped_column(Integer)
    file_path: Mapped[Optional[str]] = mapped_column(Text)
    file_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
```

### Alembic migration

New revision file `backend/alembic/versions/<hash>_s05_export_jobs.py`. `down_revision` = the head commit produced by S04.

```python
import sqlalchemy as sa
from alembic import op
try:
    from sqlalchemy.dialects import postgresql
    _HAS_PG = True
except ImportError:
    _HAS_PG = False


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        json_type = postgresql.JSONB(astext_type=sa.Text())
    else:
        json_type = sa.JSON()

    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_type", sa.String(30), nullable=False),
        sa.Column("fmt", sa.String(8), nullable=False),
        sa.Column("params", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("file_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_export_jobs_status_created", "export_jobs", ["status", "created_at"])
    op.create_index("ix_export_jobs_requested_by", "export_jobs", ["requested_by_id"])


def downgrade() -> None:
    op.drop_index("ix_export_jobs_requested_by", table_name="export_jobs")
    op.drop_index("ix_export_jobs_status_created", table_name="export_jobs")
    op.drop_table("export_jobs")
```

Notes:
- The JSONB dialect branch is required so `alembic upgrade head` is clean+idempotent on Postgres (CI asserts this) and the migration also runs on SQLite (tests).
- No data backfill. `export_jobs` starts empty.
- Downgrade drops indexes then table. Files orphaned under `STORAGE_PATH/exports/` on downgrade are left for manual cleanup (dev-only scenario; the TTL purge handles them going forward).

---

## 4. Backend

All new routes are auth-gated and registered **without** an `/api` prefix. Both new routers must be added to `backend/app/main.py` with `dependencies=[Depends(check_setup_complete)]`.

### 4.1 Endpoints

| METHOD | Path | Min role | Request | Response | Notes |
|---|---|---|---|---|---|
| POST | `/participants/bulk-add` | volunteer | `BulkParticipantAddRequest` | `BulkParticipantResult` | Set-based `INSERT…SELECT…ON CONFLICT DO NOTHING`. |
| POST | `/participants/bulk-status` | volunteer | `BulkParticipantStatusRequest` | `BulkParticipantResult` | Single `UPDATE` over audience ∩ event. Optional `only_if_status`. |
| POST | `/participants/bulk-remove` | volunteer (soft) / admin (hard) | `BulkParticipantRemoveRequest` | `BulkParticipantResult` | `hard=false` → `cancelled`; `hard=true` → physical `DELETE`. |
| POST | `/participants/bulk-preview` | volunteer | `BulkParticipantPreviewRequest` | `BulkParticipantPreview` | Counts only, no writes. Drives confirm dialog. |
| GET | `/export/contacts.csv` | viewer+ | query params (§4.4) | `text/csv` stream | Streaming cursor. UTF-8 BOM on first chunk. |
| GET | `/export/contacts.xlsx` | viewer+ | query params | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Constant-memory xlsxwriter. `FileResponse` + `BackgroundTask` unlink. |
| GET | `/export/participants.csv` | viewer+ | query params | `text/csv` stream | Streaming cursor. Synchronous. |
| GET | `/export/participants.xlsx` | viewer+ | query params | XLSX | Constant-memory xlsxwriter. Synchronous. |
| POST | `/export/jobs` | viewer+ | `ExportJobCreate` | `ExportJobResponse` (201) | Enqueue async export. Returns `id`, `status="pending"`. |
| GET | `/export/jobs` | viewer+ | `?limit&offset` | `List[ExportJobResponse]` | Own jobs (admin sees all); newest first. |
| GET | `/export/jobs/{id}` | viewer+ (owner or admin) | — | `ExportJobResponse` | Poll status; `download_url` populated only when `status=="ready"`. |
| GET | `/export/jobs/{id}/download` | viewer+ (owner or admin) | — | file stream | Path-containment check; 410 if expired; 404 if file missing. |

**Router placement:** if S04 already registered a `/participants` prefix router in `main.py`, S05's four bulk handlers must be added to the same registered router file — do not create a second router with the same prefix. Verify S04's router registration in `main.py` before creating `bulk_participants.py`. The export endpoints always go in a new `routers/export.py` with `prefix="/export"`.

### 4.2 Dependency shim — `require_viewer` (until S15)

`backend/app/dependencies.py` today exports only `require_admin` and `require_volunteer` (volunteer accepts admin|volunteer). S15 will add the `viewer` role. To keep S05 forward-compatible:

```python
async def require_viewer(current_user: dict = Depends(get_current_user)) -> dict:
    # S15 shim: viewer role is real after S15. Until then any authenticated user passes.
    # After S15: role="viewer" is minted and get_current_user validates it.
    if current_user.get("role") not in ("admin", "volunteer", "viewer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Read access required")
    return current_user
```

Before S15, no user has `role="viewer"`, so `require_viewer` == "any authenticated user." After S15, `viewer` users are real and the check is correct. Mark the function with `# S15 shim` comment.

### 4.3 Pydantic schemas (add to `backend/app/schemas.py`)

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

# ─── Audience selector ─────────────────────────────────────────────────────

class AudienceSelector(BaseModel):
    mode: Literal["all", "group", "saved_search", "ids"]
    group_id: Optional[int] = None
    saved_search_id: Optional[int] = None
    # Cap at 50,000 — covers 1,440 contacts many times over; prevents abuse.
    contact_ids: Optional[list[int]] = Field(default=None, max_length=50_000)
    include_deleted: bool = False  # default excludes is_deleted contacts

    @model_validator(mode="after")
    def _validate_mode(self) -> "AudienceSelector":
        if self.mode == "group" and self.group_id is None:
            raise ValueError("group_id required when mode='group'")
        if self.mode == "saved_search" and self.saved_search_id is None:
            raise ValueError("saved_search_id required when mode='saved_search'")
        if self.mode == "ids" and not self.contact_ids:
            raise ValueError("contact_ids required when mode='ids'")
        return self

# ─── Bulk participants ──────────────────────────────────────────────────────

class BulkParticipantAddRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    status: Literal["attended", "registered", "no_show", "cancelled"] = "attended"
    role: Optional[str] = None
    # Canonical source list: face|manual|zoom|name_list|community_report|import|bulk
    source: Literal["manual", "import", "bulk"] = "bulk"

class BulkParticipantStatusRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    new_status: Literal["attended", "registered", "no_show", "cancelled"]
    new_role: Optional[str] = None
    only_if_status: Optional[Literal["attended", "registered", "no_show", "cancelled"]] = None

class BulkParticipantRemoveRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    hard: bool = False  # True = physical DELETE (admin only); False = status→cancelled

class BulkParticipantPreviewRequest(BaseModel):
    event_id: int
    audience: AudienceSelector
    operation: Literal["add", "status", "remove"]
    new_status: Optional[Literal["attended", "registered", "no_show", "cancelled"]] = None
    only_if_status: Optional[Literal["attended", "registered", "no_show", "cancelled"]] = None

class BulkParticipantResult(BaseModel):
    event_id: int
    requested: int      # audience size after resolution (unknown ids dropped)
    inserted: int = 0   # rows actually inserted (ON CONFLICT excluded)
    skipped: int = 0    # already present for add; not-matched for status/remove
    matched: int = 0    # rows WHERE clause touched for status / remove
    updated: int = 0    # rows actually changed (== matched for hard remove)
    elapsed_ms: int     # wall-clock time for the DB statement

class BulkParticipantPreview(BaseModel):
    event_id: int
    audience_size: int
    already_participants: int
    would_insert: int = 0
    would_update: int = 0
    would_cancel: int = 0  # for remove preview

# ─── Export jobs ───────────────────────────────────────────────────────────

class ExportJobCreate(BaseModel):
    job_type: Literal["contacts", "participants"]
    fmt: Literal["csv", "xlsx"]
    params: dict = Field(default_factory=dict)  # echoes the sync export query params

class ExportJobResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    job_type: str
    fmt: str
    status: str          # pending | running | ready | failed | expired
    row_count: Optional[int] = None
    file_bytes: Optional[int] = None
    error: Optional[str] = None
    download_url: Optional[str] = None  # populated only when status=="ready"
    expires_at: Optional[datetime] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
```

### 4.4 Export query parameters (sync endpoints)

**`/export/contacts.csv` and `/export/contacts.xlsx`:**
- `contact_type: str | None` — filter by `contacts.contact_type`
- `contact_subtype: str | None`
- `group_id: int | None` — static group membership filter (S09 for smart groups)
- `saved_search_id: int | None` — criteria-backed (returns 400 if S09 unavailable)
- `q: str | None` — name/email/nickname/phone free-text substring
- `include_deleted: bool = False`
- `columns: str | None` — comma-separated column names; default = all core + active custom fields + derived snapshots

**`/export/participants.csv` and `/export/participants.xlsx`:**
- `event_id: int | None`
- `status: str | None` — `attended|registered|no_show|cancelled`
- `source: str | None` — `face|manual|zoom|name_list|community_report|import|bulk`
- `event_type: str | None` — e.g. `Sunday Celebration`
- `session_time: str | None` — e.g. `8AM` (matches `events.session_time`)
- `date_from: str | None` — ISO date, filters on `events.occurrence_date` or `events.start_at`
- `date_to: str | None`
- `group_id: int | None` — restricts to contacts in this group
- `columns: str | None`

### 4.5 Services and business logic

#### `backend/app/services/audience.py` (new)

```python
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status as http_status
from app.schemas import AudienceSelector


async def resolve_audience(
    db: AsyncSession,
    audience: AudienceSelector,
) -> "Select":
    """
    Return a SQLAlchemy Select of contact ids (a subquery, never materialised
    into a Python list for the all/group/search cases).
    """
    from app.models import Contact, GroupMember, Group  # S03/S09 models

    base = select(Contact.id)
    if not audience.include_deleted:
        base = base.where(Contact.is_deleted == False)

    if audience.mode == "all":
        return base

    if audience.mode == "ids":
        # Validate against real contacts; drop unknowns silently.
        valid = await db.execute(
            base.where(Contact.id.in_(audience.contact_ids or []))
        )
        valid_ids = [r[0] for r in valid.all()]
        return select(Contact.id).where(Contact.id.in_(valid_ids))

    if audience.mode == "group":
        group = await db.get(Group, audience.group_id)
        if not group:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                                detail=f"Group {audience.group_id} not found")
        if group.type == "static":
            return base.join(GroupMember, GroupMember.contact_id == Contact.id).where(
                GroupMember.group_id == audience.group_id
            )
        else:
            return _compile_criteria(base, group.criteria)

    if audience.mode == "saved_search":
        from app.models import SavedSearch
        ss = await db.get(SavedSearch, audience.saved_search_id)
        if not ss:
            raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                                detail=f"Saved search {audience.saved_search_id} not found")
        return _compile_criteria(base, ss.criteria)

    raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST,
                        detail=f"Unknown audience mode: {audience.mode}")


def _compile_criteria(base: "Select", criteria: dict) -> "Select":
    """
    Delegate to S09's criteria compiler. Guards with 400 if S09 is not yet shipped.
    S09 owns `app.services.criteria_compiler.compile_contact_criteria`.
    """
    try:
        from app.services.criteria_compiler import compile_contact_criteria
    except ImportError:
        raise HTTPException(
            status_code=400,
            detail=(
                "Smart groups and saved searches require the S09 criteria compiler "
                "(not yet available). Use mode='all', 'group' (static), or 'ids'."
            )
        )
    return compile_contact_criteria(base, criteria)


async def count_audience(db: AsyncSession, audience: AudienceSelector) -> int:
    """Return the resolved audience size (a COUNT(*) over the subquery)."""
    subq = await resolve_audience(db, audience)
    result = await db.execute(select(func.count()).select_from(subq.subquery()))
    return result.scalar_one()
```

#### `backend/app/services/bulk_service.py` (new)

All public functions are `async`, type-hinted, measured with `time.perf_counter()`. Zero per-row Python iteration for any operation.

**`bulk_add_participants(db, req, caller_id) -> BulkParticipantResult`**

```python
import time
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Contact, Event, Participant, utc_now
from app.schemas import BulkParticipantAddRequest, BulkParticipantResult
from app.services.audience import resolve_audience, count_audience


async def bulk_add_participants(
    db: AsyncSession,
    req: BulkParticipantAddRequest,
    caller_id: int,
) -> BulkParticipantResult:
    # 1. Validate event.
    event = await db.get(Event, req.event_id)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")

    # 2. Resolve audience subquery (server-side; never materialised for all/group/search).
    audience_subq = await resolve_audience(db, req.audience)
    requested = await count_audience(db, req.audience)

    if requested == 0:
        return BulkParticipantResult(event_id=req.event_id, requested=0,
                                     inserted=0, skipped=0, elapsed_ms=0)

    t0 = time.perf_counter()

    # 3. Set-based INSERT ... SELECT ... ON CONFLICT (event_id, contact_id) DO NOTHING
    #    One statement; zero Python iteration over rows.
    sel = select(
        audience_subq.subquery().c.id.label("contact_id"),
        literal(req.event_id).label("event_id"),
        literal(req.status).label("status"),
        literal(req.role).label("role"),
        literal(req.source).label("source"),
        literal(caller_id).label("registered_by_id"),
        func.now().label("created_at"),
    )

    # Dialect-specific ON CONFLICT DO NOTHING
    from sqlalchemy import inspect as sa_inspect
    dialect_name = db.get_bind().dialect.name if hasattr(db, "get_bind") else "sqlite"
    # For SQLAlchemy 2.0 async sessions use the engine:
    try:
        dialect_name = db.sync_session.get_bind().dialect.name
    except Exception:
        dialect_name = "postgresql"  # prod default

    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(Participant).from_select(
            ["contact_id", "event_id", "status", "role", "source",
             "registered_by_id", "created_at"],
            sel,
        ).on_conflict_do_nothing(index_elements=["event_id", "contact_id"])
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(Participant).from_select(
            ["contact_id", "event_id", "status", "role", "source",
             "registered_by_id", "created_at"],
            sel,
        ).on_conflict_do_nothing(index_elements=["event_id", "contact_id"])

    res = await db.execute(stmt)
    await db.commit()

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # rowcount after ON CONFLICT DO NOTHING:
    # - asyncpg (Postgres): returns count of actually inserted rows.
    # - aiosqlite (SQLite): may return -1 or total rows attempted.
    # Fallback: re-count audience members who are now participants in this event.
    raw_rc = res.rowcount if res.rowcount is not None else -1
    if 0 <= raw_rc <= requested:
        inserted = raw_rc
    else:
        count_res = await db.execute(
            select(func.count()).select_from(
                select(Participant.id)
                .where(Participant.event_id == req.event_id)
                .where(Participant.contact_id.in_(
                    select(audience_subq.subquery().c.id)
                ))
                .subquery()
            )
        )
        inserted = count_res.scalar_one()

    skipped = requested - inserted

    await _write_audit(db, caller_id, "participant.bulk_add", "participants", req.event_id,
                       {"inserted": inserted, "skipped": skipped,
                        "requested": requested, "status": req.status,
                        "audience_mode": req.audience.mode})

    return BulkParticipantResult(
        event_id=req.event_id, requested=requested,
        inserted=inserted, skipped=skipped, elapsed_ms=elapsed_ms,
    )
```

**`bulk_update_status(db, req, caller_id) -> BulkParticipantResult`**

```python
from sqlalchemy import update as sa_update


async def bulk_update_status(db, req, caller_id):
    event = await db.get(Event, req.event_id)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")

    audience_subq = await resolve_audience(db, req.audience)
    requested = await count_audience(db, req.audience)

    t0 = time.perf_counter()
    values = {"status": req.new_status}
    if req.new_role is not None:
        values["role"] = req.new_role

    stmt = (
        sa_update(Participant)
        .where(Participant.event_id == req.event_id)
        .where(Participant.contact_id.in_(select(audience_subq.subquery().c.id)))
        .values(**values)
    )
    if req.only_if_status is not None:
        stmt = stmt.where(Participant.status == req.only_if_status)

    res = await db.execute(stmt)
    await db.commit()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    matched = updated = res.rowcount if res.rowcount is not None else 0

    await _write_audit(db, caller_id, "participant.bulk_status", "participants", req.event_id,
                       {"matched": matched, "new_status": req.new_status,
                        "only_if_status": req.only_if_status,
                        "audience_mode": req.audience.mode})

    return BulkParticipantResult(event_id=req.event_id, requested=requested,
                                  matched=matched, updated=updated, elapsed_ms=elapsed_ms)
```

**`bulk_remove(db, req, caller_id) -> BulkParticipantResult`**

Soft (default): delegates to `bulk_update_status` with `new_status="cancelled"`.  
Hard (admin-gated at router): a single `DELETE` statement.

```python
async def bulk_remove(db, req, caller_id):
    if not req.hard:
        from app.schemas import BulkParticipantStatusRequest
        status_req = BulkParticipantStatusRequest(
            event_id=req.event_id, audience=req.audience, new_status="cancelled"
        )
        return await bulk_update_status(db, status_req, caller_id)

    event = await db.get(Event, req.event_id)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")
    audience_subq = await resolve_audience(db, req.audience)
    requested = await count_audience(db, req.audience)

    t0 = time.perf_counter()
    from sqlalchemy import delete as sa_delete
    stmt = (
        sa_delete(Participant)
        .where(Participant.event_id == req.event_id)
        .where(Participant.contact_id.in_(select(audience_subq.subquery().c.id)))
    )
    res = await db.execute(stmt)
    await db.commit()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    deleted = res.rowcount if res.rowcount is not None else 0

    await _write_audit(db, caller_id, "participant.bulk_remove_hard", "participants",
                       req.event_id,
                       {"deleted": deleted, "requested": requested,
                        "audience_mode": req.audience.mode})

    return BulkParticipantResult(event_id=req.event_id, requested=requested,
                                  matched=deleted, updated=deleted, elapsed_ms=elapsed_ms)
```

**`preview(db, req) -> BulkParticipantPreview`** — resolve audience, count audience size, count existing `participants` rows for `(event_id, audience contact ids)`. No writes.

```python
async def preview(db, req):
    event = await db.get(Event, req.event_id)
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Event not found")
    audience_subq = await resolve_audience(db, req.audience)
    audience_size = await count_audience(db, req.audience)

    already_res = await db.execute(
        select(func.count()).where(
            Participant.event_id == req.event_id,
            Participant.contact_id.in_(select(audience_subq.subquery().c.id)),
        )
    )
    already_n = already_res.scalar_one()
    would_insert = max(0, audience_size - already_n)

    from app.schemas import BulkParticipantPreview
    return BulkParticipantPreview(
        event_id=req.event_id,
        audience_size=audience_size,
        already_participants=already_n,
        would_insert=would_insert,
        would_update=already_n if req.operation == "status" else 0,
        would_cancel=already_n if req.operation == "remove" else 0,
    )
```

**`_write_audit(db, actor_id, action, entity, entity_id, after)`** — best-effort, guarded:

```python
async def _write_audit(db, actor_id, action, entity, entity_id, after):
    try:
        from app.models import AuditLog, utc_now as _now
        db.add(AuditLog(actor_id=actor_id, action=action, entity=entity,
                        entity_id=entity_id, before=None, after=after,
                        at=_now()))
        await db.commit()
    except Exception:
        pass  # Audit is best-effort; bulk op already committed above.
```

#### `backend/app/services/export_service.py` (new)

The contract: **never call `.all()`** on a large result set. Use `await db.stream(stmt.execution_options(yield_per=1000))` + `async for partition in result.partitions(1000)`.

**Column model** — `_contact_columns(db)` returns an ordered `list[tuple[str, Callable]]` of `(header, accessor)` pairs:

Core columns (13): `id`, `external_id`, `contact_type`, `contact_subtype`, `first_name`, `last_name`, `suffix`, `gender`, `birth_date`, `phone`, `email`, `street_address`, `created_at`.

Derived snapshot columns (7, nullable until S23 runs): `last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`.

Custom field columns: one per active `custom_field_def` ordered by `(group_id, weight)`. Header = `def.label`. Accessor reads `row.custom_data.get(def.name)` rendered as:
- `text / textarea / select / date / number / checkbox` → `str(value)` or `""`
- `multiselect` or `is_multi=True` → `"; ".join(value)` if value is a list
- `contact_reference` → resolve each target id to `first_name + " " + last_name`; join with `"; "` if multi. Batch-load target contacts per-partition (dict cache within the generator) to avoid N+1.

**CSV streaming generator:**

```python
import csv, io
from typing import AsyncIterator


async def stream_contacts_csv(
    db: AsyncSession,
    filters: dict,
) -> AsyncIterator[bytes]:
    cols = await _contact_columns(db)
    header_buf = io.StringIO()
    csv.writer(header_buf).writerow([h for h, _ in cols])
    yield b"\xef\xbb\xbf" + header_buf.getvalue().encode("utf-8")  # BOM on first chunk

    stmt = _build_contact_query(filters).execution_options(yield_per=1000)
    result = await db.stream(stmt)
    async for partition in result.partitions(1000):
        buf = io.StringIO()
        w = csv.writer(buf)
        for row in partition:
            w.writerow([_render(acc, row) for _, acc in cols])
        yield buf.getvalue().encode("utf-8")
```

Wrap in `StreamingResponse(generator, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="contacts_<ts>.csv"'})`.

**XLSX constant-memory writer:** `xlsxwriter` `constant_memory=True` writes one row at a time to a temp file on disk (no RAM accumulation):

```python
import xlsxwriter, tempfile, os


async def build_contacts_xlsx(
    db: AsyncSession,
    filters: dict,
) -> tuple[str, int]:
    """Returns (tmp_file_path, row_count). Caller must delete the temp file."""
    cols = await _contact_columns(db)
    fd, path = tempfile.mkstemp(suffix=".xlsx", prefix="seraphim_export_")
    os.close(fd)
    wb = xlsxwriter.Workbook(path, {"constant_memory": True, "in_memory": False})
    ws = wb.add_worksheet("Contacts")
    for c, (h, _) in enumerate(cols):
        ws.write(0, c, h)
    n = 0
    stmt = _build_contact_query(filters).execution_options(yield_per=1000)
    result = await db.stream(stmt)
    async for partition in result.partitions(1000):
        for row in partition:
            n += 1
            for c, (_, acc) in enumerate(cols):
                ws.write(n, c, _render(acc, row))
    wb.close()
    return path, n
```

XLSX sync endpoint returns `FileResponse(path, filename="contacts_<ts>.xlsx", background=BackgroundTask(os.unlink, path))` — the background task deletes the temp file after the response is fully sent.

Mirror `stream_participants_csv` / `build_participants_xlsx` with the participant column set defined below.

**Participant column set** (`_participant_columns`): `participant_id`, `contact_id`, `contact_first_name`, `contact_last_name`, `event_id`, `event_title`, `event_type`, `session_time`, `occurrence_date`, `status`, `role`, `source`, `created_at`. Join: `Participant` → `Contact` (LEFT JOIN; empty strings on missing) → `Event` (INNER JOIN). Filter on `events.occurrence_date` or `events.start_at` for `date_from`/`date_to`.

**`_build_contact_query(filters: dict) -> Select`** — composes a SELECT on `contacts` applying the filter params from §4.4. Calls `_audience_filter(filters)` to join on `group_members` when `group_id` is set.

**`_build_participant_query(filters: dict) -> Select`** — composes the three-table join above with the filter params from §4.4.

#### `backend/app/services/queue_manager.py` (modify — add export job processor)

Add `_process_export_jobs(db: AsyncSession) -> None` alongside the existing `_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup` functions, and include it in the `run()` loop:

```python
async def _process_export_jobs(db: AsyncSession) -> None:
    """Drain pending export_jobs: pick oldest pending, generate file, mark ready."""
    from app.models import ExportJob, utc_now
    from app.services import export_service
    from datetime import timedelta
    import os
    from pathlib import Path

    # Pick the oldest pending job (SELECT FOR UPDATE SKIP LOCKED on Postgres).
    result = await db.execute(
        select(ExportJob)
        .where(ExportJob.status == "pending")
        .order_by(ExportJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if not job:
        await _purge_expired_export_jobs(db)
        return

    job.status = "running"
    await db.commit()

    try:
        storage_root = _get_storage_root()
        exports_dir = storage_root / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        ts = utc_now().strftime("%Y%m%d_%H%M%S")
        filename = f"{job.job_type}_{ts}_{job.id}.{job.fmt}"

        if job.job_type == "contacts":
            if job.fmt == "csv":
                tmp_path, row_count = await _collect_csv_to_file(
                    db, export_service.stream_contacts_csv, job.params,
                    exports_dir / filename
                )
            else:
                tmp_path, row_count = await export_service.build_contacts_xlsx(
                    db, job.params
                )
        else:  # participants
            if job.fmt == "csv":
                tmp_path, row_count = await _collect_csv_to_file(
                    db, export_service.stream_participants_csv, job.params,
                    exports_dir / filename
                )
            else:
                tmp_path, row_count = await export_service.build_participants_xlsx(
                    db, job.params
                )

        final_path = exports_dir / filename
        if tmp_path != str(final_path):
            os.replace(tmp_path, final_path)

        job.status = "ready"
        job.file_path = f"exports/{filename}"
        job.file_bytes = final_path.stat().st_size
        job.row_count = row_count
        job.expires_at = utc_now() + timedelta(hours=24)
        job.finished_at = utc_now()
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:500]
        job.finished_at = utc_now()

    await db.commit()
    await _record_job_run(db, "export", job.status, f"job_id={job.id}")
    await _purge_expired_export_jobs(db)
```

`_purge_expired_export_jobs(db)`: finds `status IN ('ready','failed')` rows past `expires_at`, unlinks the file if present, sets `status="expired"`, nulls `file_path`.

`_record_job_run(db, name, status, detail)`: guarded try/except for the S16-owned `job_runs` table.

`_collect_csv_to_file(db, generator_fn, params, dest_path) -> (str, int)`: iterates the async generator and writes chunks to `dest_path`; returns `(str(dest_path), row_count)`.

#### `backend/app/routers/bulk_participants.py` (new, or extend S04's router)

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_volunteer, get_current_user
from app.schemas import (
    BulkParticipantAddRequest, BulkParticipantStatusRequest,
    BulkParticipantRemoveRequest, BulkParticipantPreviewRequest,
    BulkParticipantResult, BulkParticipantPreview,
)
from app.services import bulk_service

router = APIRouter(prefix="/participants", tags=["participants"])


@router.post("/bulk-add", response_model=BulkParticipantResult)
async def bulk_add(
    req: BulkParticipantAddRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    return await bulk_service.bulk_add_participants(db, req, caller_id=user["id"])


@router.post("/bulk-status", response_model=BulkParticipantResult)
async def bulk_status(
    req: BulkParticipantStatusRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    return await bulk_service.bulk_update_status(db, req, caller_id=user["id"])


@router.post("/bulk-remove", response_model=BulkParticipantResult)
async def bulk_remove(
    req: BulkParticipantRemoveRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    if req.hard and user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hard delete requires admin role",
        )
    return await bulk_service.bulk_remove(db, req, caller_id=user["id"])


@router.post("/bulk-preview", response_model=BulkParticipantPreview)
async def bulk_preview(
    req: BulkParticipantPreviewRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    return await bulk_service.preview(db, req)
```

#### `backend/app/routers/export.py` (new)

```python
import os
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.dependencies import require_viewer, get_current_user
from app.models import ExportJob, utc_now
from app.schemas import ExportJobCreate, ExportJobResponse
from app.services import export_service

router = APIRouter(prefix="/export", tags=["export"])


def _storage_exports_dir() -> Path:
    from app.config import legacy_settings
    return Path(os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)) / "exports"


# ── Synchronous streaming exports ──────────────────────────────────────────

@router.get("/contacts.csv")
async def export_contacts_csv(
    contact_type: str | None = None,
    contact_subtype: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    group_id: int | None = None,
    columns: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    filters = dict(contact_type=contact_type, contact_subtype=contact_subtype,
                   q=q, include_deleted=include_deleted,
                   group_id=group_id, columns=columns)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        export_service.stream_contacts_csv(db, filters),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="contacts_{ts}.csv"'},
    )


@router.get("/contacts.xlsx")
async def export_contacts_xlsx(
    contact_type: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    group_id: int | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    filters = dict(contact_type=contact_type, q=q,
                   include_deleted=include_deleted, group_id=group_id)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    tmp_path, _ = await export_service.build_contacts_xlsx(db, filters)
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        filename=f"contacts_{ts}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=background_tasks,
    )


@router.get("/participants.csv")
async def export_participants_csv(
    event_id: int | None = None,
    status: str | None = None,
    source: str | None = None,
    event_type: str | None = None,
    session_time: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    group_id: int | None = None,
    columns: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    filters = dict(event_id=event_id, status=status, source=source,
                   event_type=event_type, session_time=session_time,
                   date_from=date_from, date_to=date_to,
                   group_id=group_id, columns=columns)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        export_service.stream_participants_csv(db, filters),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="participants_{ts}.csv"'},
    )


@router.get("/participants.xlsx")
async def export_participants_xlsx(
    event_id: int | None = None,
    status: str | None = None,
    event_type: str | None = None,
    session_time: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    filters = dict(event_id=event_id, status=status, event_type=event_type,
                   session_time=session_time, date_from=date_from, date_to=date_to)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    tmp_path, _ = await export_service.build_participants_xlsx(db, filters)
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        filename=f"participants_{ts}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        background=background_tasks,
    )


# ── Async export jobs ───────────────────────────────────────────────────────

@router.post("/jobs", response_model=ExportJobResponse, status_code=201)
async def create_export_job(
    req: ExportJobCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    job = ExportJob(
        job_type=req.job_type,
        fmt=req.fmt,
        params=req.params,
        status="pending",
        requested_by_id=user["id"],
        created_at=utc_now(),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _to_response(job)


@router.get("/jobs", response_model=list[ExportJobResponse])
async def list_export_jobs(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    limit = min(max(limit, 1), 100)
    q = select(ExportJob).order_by(ExportJob.created_at.desc()).offset(offset).limit(limit)
    if user["role"] != "admin":
        q = q.where(ExportJob.requested_by_id == user["id"])
    result = await db.execute(q)
    return [_to_response(j) for j in result.scalars().all()]


@router.get("/jobs/{job_id}", response_model=ExportJobResponse)
async def get_export_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    job = await db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export job not found")
    if user["role"] != "admin" and job.requested_by_id != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    return _to_response(job)


@router.get("/jobs/{job_id}/download")
async def download_export_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_viewer),
):
    job = await db.get(ExportJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export job not found")
    if user["role"] != "admin" and job.requested_by_id != user["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if job.status == "expired":
        raise HTTPException(status.HTTP_410_GONE, "Export has expired")
    if job.status != "ready" or not job.file_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not ready")

    # Path containment check (mirrors routers/storage.py pattern).
    from app.config import legacy_settings
    storage_root = Path(
        os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)
    ).resolve()
    requested_path = (storage_root / job.file_path).resolve()
    try:
        requested_path.relative_to(storage_root)
    except ValueError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Path traversal blocked")

    if not requested_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File missing on disk")

    media_type = (
        "text/csv"
        if job.fmt == "csv"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return FileResponse(
        str(requested_path),
        filename=f"{job.job_type}_{job_id}.{job.fmt}",
        media_type=media_type,
    )


def _to_response(job: ExportJob) -> ExportJobResponse:
    return ExportJobResponse(
        id=job.id,
        job_type=job.job_type,
        fmt=job.fmt,
        status=job.status,
        row_count=job.row_count,
        file_bytes=job.file_bytes,
        error=job.error,
        download_url=f"/export/jobs/{job.id}/download" if job.status == "ready" else None,
        expires_at=job.expires_at,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )
```

### 4.6 Legacy analytics.py cleanup

`backend/app/routers/analytics.py` lines 118–184 contain `export_attendance_csv` (analytics.py:118-154, buffers `.all()`, emits `push_status` column) and `export_logs_csv` (analytics.py:157-184, also buffers, emits `push_status`). Both are broken after S01 drops `push_status`.

In this sprint:
- **Delete** `export_attendance_csv` (lines 118–154) — superseded by `GET /export/participants.csv`.
- **Rewrite** `export_logs_csv` body: remove the `push_status` column from the SELECT (lines 163–165) and from the header row (line 173) and from the row writer (line 177). The bounded `.limit(5000)` and `io.StringIO` buffering are acceptable for the operational logs export (logs are bounded, not attendance-scale). Keep the endpoint at its existing path.
- Remove now-unused imports of `Attendance` and push-related symbols from `analytics.py:14`.

### 4.7 `main.py` registration

Add to `backend/app/main.py` in the auth-gated block:

```python
from app.routers import bulk_participants, export as export_router

app.include_router(bulk_participants.router, dependencies=[Depends(check_setup_complete)])
app.include_router(export_router.router, dependencies=[Depends(check_setup_complete)])
```

If S04 registered a `/participants` router already (i.e. the router file exists), add the four bulk handlers to that file instead of creating a new one. Do not register two routers with the same `prefix="/participants"`.

### 4.8 File-by-file change list (backend)

**Create:**
- `backend/app/routers/bulk_participants.py` — four bulk endpoints (or merge into S04's participants router if it exists).
- `backend/app/routers/export.py` — streaming sync exports + job CRUD + download.
- `backend/app/services/audience.py` — `resolve_audience`, `count_audience`, `_compile_criteria`.
- `backend/app/services/bulk_service.py` — `bulk_add_participants`, `bulk_update_status`, `bulk_remove`, `preview`, `_write_audit`.
- `backend/app/services/export_service.py` — `_contact_columns`, `_participant_columns`, `_render`, `_build_contact_query`, `_build_participant_query`, `_audience_filter`, `stream_contacts_csv`, `stream_participants_csv`, `build_contacts_xlsx`, `build_participants_xlsx`.
- `backend/tests/factories.py` — `make_contacts(db, n)`, `make_event(db, **kw)`, `make_participants(db, event, contacts, status)` (if not already created by S03/S04).
- `backend/tests/test_bulk_participants.py`
- `backend/tests/test_export_stream.py`
- `backend/tests/test_export_jobs.py`
- `backend/alembic/versions/<hash>_s05_export_jobs.py`

**Modify:**
- `backend/app/models.py` — add `ExportJob` class after `AdminSetting` (§3).
- `backend/app/schemas.py` — add `AudienceSelector`, `BulkParticipant*`, `ExportJobCreate`, `ExportJobResponse` (§4.3).
- `backend/app/dependencies.py` — add `require_viewer` shim (§4.2).
- `backend/app/main.py` — register `bulk_participants.router` and `export_router.router` (§4.7).
- `backend/app/services/queue_manager.py` — add `_process_export_jobs`, `_purge_expired_export_jobs`, `_collect_csv_to_file`, `_record_job_run`; include `_process_export_jobs` in the `run()` loop alongside the existing three jobs.
- `backend/app/routers/analytics.py` — delete `export_attendance_csv`; rewrite `export_logs_csv` to remove `push_status` (§4.6).
- `backend/requirements.txt` — add `XlsxWriter>=3.2.0` (pure-Python, no native deps; `constant_memory` mode verified at version 3.x).

**No new Alembic migration** other than the one in §3. Bulk writes use S04's `participants` table unchanged.

---

## 5. Frontend

### 5.1 Pages / routes / components

**New component `frontend/src/components/participants/BulkParticipantPanel.tsx`**

Mounts above the participant grid on `EventDetailPage.tsx` (S04). Responsibilities:

- **Audience picker** (radio group): "Everyone (all contacts)" → `mode="all"`, "Group…" → `mode="group"` + group select (query `['groups']`; disable with tooltip if endpoint 404s or S09 not shipped yet), "Saved search…" → `mode="saved_search"` (same guard), "Selected rows" → `mode="ids"` using the grid's current multi-select `selectedIds` set.
- **Operation tabs** (segmented control): **Add**, **Set status**, **Remove**.
  - Add: status dropdown (`attended`/`registered`/`no_show`/`cancelled`), optional role text input.
  - Set status: new status dropdown; optional "only if currently" narrowing dropdown.
  - Remove: soft (cancel) by default; "Hard delete (permanent)" admin-only checkbox rendered only when `useAuthStore(s => s.isAdmin)`.
- **"Preview"** button → `POST /participants/bulk-preview` → renders summary strip: `"Audience: 1,432 · Would add: 1,410 · Already present: 22"`.
- **"Apply"** button (disabled if `audience_size === 0` or preview has not been run yet):
  - Opens `ConfirmDialog` (from `frontend/src/components/ui/ConfirmDialog.tsx`) with message: `"Mark 1,432 contacts as 'attended' for 'Sunday Celebration — 8AM'?"` (uses preview counts + event title from parent page's query).
  - On confirm: calls the matching bulk endpoint → on success toast: `"Added 1,410 · 22 already present · 318 ms"` → `queryClient.invalidateQueries({ queryKey: ['participants', eventId] })`.
  - On error: `toast.error(err.response?.data?.detail ?? 'Bulk operation failed')`.
- Props: `eventId: number`, `eventTitle: string`, `selectedIds?: number[]`.

**New component `frontend/src/components/export/ExportMenu.tsx`**

Props: `jobType: "contacts" | "participants"`, `filters: Record<string, unknown>`.

Dropdown/popover menu with three items:
- "Download CSV" → sync blob download (see below).
- "Download XLSX" → sync blob download.
- "Run in background (large export)" → async job mode (see below).

**Sync download flow:**  
`api.get('/export/<jobType>.csv?<filters>', { responseType: 'blob' })` → call `downloadBlob(blob, filename)` → create `URL.createObjectURL(blob)` → click a hidden `<a download>` element → `URL.revokeObjectURL`. Show a spinner on the button while in-flight. Toast on error. Carry the parent list's currently active filters into the query string.

**Async job flow:**  
`POST /export/jobs` with `{ job_type, fmt, params: filters }` → store `jobId` in local state → start TanStack Query with `queryKey: ['export-job', jobId]` and `refetchInterval: 2000` (disabled once status is terminal) → render a status chip: "Export running…" / "Ready — downloading…" / "Export failed". When `status === 'ready'`, fetch `/export/jobs/<id>/download` as blob and trigger `downloadBlob`, then stop polling.

**New service `frontend/src/services/export.ts`:**

```typescript
import api from './api';
import type { ExportJobResponse } from '../types/bulk';

export async function downloadBlob(url: string, filename: string): Promise<void> {
  const res = await api.get(url, { responseType: 'blob' });
  const objectUrl = URL.createObjectURL(new Blob([res.data]));
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(objectUrl);
}

export async function enqueueExportJob(
  jobType: 'contacts' | 'participants',
  fmt: 'csv' | 'xlsx',
  params: Record<string, unknown>,
): Promise<{ id: number; status: string }> {
  const res = await api.post('/export/jobs', { job_type: jobType, fmt, params });
  return res.data;
}

export async function pollExportJob(id: number): Promise<ExportJobResponse> {
  const res = await api.get(`/export/jobs/${id}`);
  return res.data;
}
```

**New types `frontend/src/types/bulk.ts`:**

```typescript
export type AudienceMode = 'all' | 'group' | 'saved_search' | 'ids';

export interface AudienceSelector {
  mode: AudienceMode;
  group_id?: number;
  saved_search_id?: number;
  contact_ids?: number[];
  include_deleted?: boolean;
}

export interface BulkParticipantResult {
  event_id: number;
  requested: number;
  inserted: number;
  skipped: number;
  matched: number;
  updated: number;
  elapsed_ms: number;
}

export interface BulkParticipantPreview {
  event_id: number;
  audience_size: number;
  already_participants: number;
  would_insert: number;
  would_update: number;
  would_cancel: number;
}

export interface ExportJobResponse {
  id: number;
  job_type: string;
  fmt: string;
  status: 'pending' | 'running' | 'ready' | 'failed' | 'expired';
  row_count?: number;
  file_bytes?: number;
  error?: string;
  download_url?: string;
  expires_at?: string;
  created_at: string;
  finished_at?: string;
}
```

### 5.2 TanStack Query keys

- `['groups']`, `['saved-searches']` — audience options (read from S09 when available; graceful 404 handled by disabling the audience mode option).
- `['participants', eventId]` — invalidated on every successful bulk write.
- `['export-job', jobId]` — polled at 2 s `refetchInterval`; `enabled: !!jobId && jobStatus !== 'ready' && jobStatus !== 'failed'`.
- `['export-jobs']` — optional "my recent exports" list in the menu.

No new Zustand store. All bulk-panel transient state (audience selection, operation, preview result, in-flight flag) is local `useState`. Role checks read from existing `useAuthStore(s => s.role)` and `useAuthStore(s => s.isAdmin)`.

### 5.3 Role gating

| Feature | Minimum role | Gate |
|---|---|---|
| Export buttons (CSV/XLSX sync + async) | viewer+ (any authenticated) | `ProtectedRoute` |
| Bulk Add / Set status / soft Remove panel | volunteer+ | `role !== 'viewer'`; hide panel entirely for viewer |
| Hard-delete toggle | admin only | `useAuthStore(s => s.isAdmin)` |

S15 makes `viewer` a real user role. Until then `role !== 'viewer'` hides bulk controls from any user with role `viewer` (currently no such users), so all current users (admin, volunteer) see the panel.

### 5.4 UX states, design tokens, mobile-first

**Design tokens only** — no hardcoded hex. Use: `bg-card`, `text-foreground`, `bg-background`, `bg-primary`, `text-primary-foreground`, `border-border`, `text-muted-foreground` (for disabled state). Destructive buttons: `bg-destructive text-destructive-foreground`. Dark mode works automatically via token classes (`.dark` variants applied via the existing `main.tsx:9-14` dark-mode bootstrap).

**Loading / empty / error:**
- Use `LoadingState` / `EmptyState` / `ErrorState` from `frontend/src/components/ui/StateViews.tsx`.
- Bulk panel: audience resolves to 0 → inline note "0 contacts match — Apply disabled" (`text-muted-foreground`); preview pending → spinner on Preview button; write in-flight → spinner on Apply button (disabled).
- Export menu: download in-flight → spinner on button; async job running → chip with rotating spinner; failed → red text + retry link.
- Confirm dialog: always use `ConfirmDialog` from `frontend/src/components/ui/ConfirmDialog.tsx`. Never `window.confirm()`.

**Mobile-first (375px base):**
- Bulk panel: full-width stacked card. Audience options stack vertically. Operation tabs as a 3-segment button bar (`flex`). Preview summary wraps to two lines on narrow screens.
- Export menu: bottom-sheet-style dropdown on `< md` (768px); inline popover dropdown on `md:`.
- Counts in the toast summary (`"Added 1,410 · 22 already present"`) wrap gracefully on mobile.

### 5.5 File-by-file change list (frontend)

**Create:**
- `frontend/src/components/participants/BulkParticipantPanel.tsx`
- `frontend/src/components/export/ExportMenu.tsx`
- `frontend/src/services/export.ts`
- `frontend/src/types/bulk.ts`
- `frontend/src/__tests__/BulkParticipantPanel.test.tsx`
- `frontend/src/__tests__/ExportMenu.test.tsx`

**Modify:**
- `frontend/src/pages/ContactsPage.tsx` (S03) — add `<ExportMenu jobType="contacts" filters={currentFilters} />` in the page header row.
- `frontend/src/pages/EventDetailPage.tsx` (S04) — add `<BulkParticipantPanel eventId={eventId} eventTitle={event.title} selectedIds={gridSelectedIds} />` above the participant grid; pass the grid's `selectedIds` set.
- Any surviving participant/attendance list page post-S01/S04 — add `<ExportMenu jobType="participants" filters={currentFilters} />`. If `AttendancePage.tsx` still renders CiviCRM push/dead-letter UI after S01, that is an S01 cleanup item — S05 only mounts the export menu on the surviving list surface.

**No change needed:**
- `frontend/src/services/api.ts` — reused as-is; blob downloads pass `{ responseType: 'blob' }` per-call.
- `frontend/src/store/authStore.ts` — role accessed read-only; no changes.

---

## 6. Migration / data

No data migration in S05. The `contacts`, `events`, and `participants` data is migrated in S06. The only schema artifact is the empty `export_jobs` table from §3.

**Load-test fixture seeding:** to validate the < 1 s / 1,500-row and 33k-export targets, the test suite uses factory helpers in `backend/tests/factories.py`. Performance tests are marked `@pytest.mark.perf` (skipped in fast CI, run in nightly/full CI job):

```python
# backend/tests/factories.py (create if absent; S03/S04 may have started this file)
from app.models import Contact, Event, Participant, utc_now


async def make_contacts(db, n: int, prefix: str = "Test") -> list[Contact]:
    contacts = [
        Contact(
            first_name=f"{prefix}{i}", last_name="Bench",
            contact_type="Individual", is_deleted=False, created_at=utc_now(),
        )
        for i in range(n)
    ]
    db.add_all(contacts)
    await db.flush()
    return contacts


async def make_event(db, **kw) -> Event:
    e = Event(
        title=kw.get("title", "Test Event"),
        event_type=kw.get("event_type", "Sunday Celebration"),
        start_at=kw.get("start_at", utc_now()),
        is_active=True,
        created_at=utc_now(),
    )
    db.add(e)
    await db.flush()
    return e


async def make_participants(
    db, event: Event, contacts: list[Contact], status: str = "attended"
) -> list[Participant]:
    ps = [
        Participant(
            contact_id=c.id, event_id=event.id,
            status=status, source="manual", created_at=utc_now(),
        )
        for c in contacts
    ]
    db.add_all(ps)
    await db.flush()
    return ps
```

**SQLite vs Postgres perf note:** the hard < 1 s target is against Postgres with asyncpg. SQLite perf tests use generous bounds (< 10 s) since SQLite has no true streaming server-side cursor. The memory-bounded assertion is validated on Postgres in the load test by checking peak RSS before and after a 33k-row CSV download via `psutil.Process().memory_info().rss` snapshot. On SQLite, `yield_per=1000` with `partitions(1000)` still avoids ORM identity-map materialisation.

---

## 7. Acceptance criteria

1. `POST /participants/bulk-add` with `audience.mode="all"` against an event with no prior participants inserts exactly `COUNT(contacts WHERE NOT is_deleted)` rows, all with `source="bulk"`, `status` as requested, `registered_by_id == caller_id`; response: `inserted == requested`, `skipped == 0`.

2. Re-running the identical `bulk-add` inserts **0** rows (`inserted == 0`, `skipped == requested`, HTTP 200 with no error) — proving `ON CONFLICT (event_id, contact_id) DO NOTHING` idempotency.

3. `bulk-add` for a 1,500-contact audience on Postgres is executed as a **single** `INSERT ... SELECT ... ON CONFLICT` statement (verifiable via `sqlalchemy.event.listen(engine, "before_cursor_execute", ...)` query log; no Python per-row loop) and completes in **< 1 s** (load-test p95).

4. `POST /participants/bulk-status` with `new_status="no_show"` updates exactly the `(event_id, contact_id)` rows in the intersection of the event and the audience in **one** `UPDATE` statement; `matched == updated`; rows belonging to a different event or outside the audience are untouched.

5. `bulk-status` with `only_if_status="registered"` leaves rows with `status == "attended"` unchanged — only rows currently `"registered"` are updated.

6. `POST /participants/bulk-remove` with `hard=false` sets matched rows to `status="cancelled"` without deleting them; with `hard=true` physically removes matched rows from `participants`; a volunteer calling `hard=true` receives HTTP 403; an admin calling `hard=true` succeeds and rows are gone.

7. `POST /participants/bulk-preview` returns `audience_size`, `already_participants`, `would_insert`, `would_update`, `would_cancel` without performing any write — the total participant row count for the event is identical before and after the call.

8. `audience.mode="ids"` drops unknown contact ids silently; `requested` reflects only resolvable ids. `mode="group"` (static) resolves correctly via `group_members`. `mode="group"` (smart) and `mode="saved_search"` return HTTP 400 with a message referencing the S09 criteria compiler when that module is not importable.

9. `GET /export/contacts.csv` returns a valid RFC-4180 CSV with a UTF-8 BOM; headers include all 13 core columns, the 7 derived snapshot columns (values may be null/empty), and one column per active `custom_field_def`; multi-select values are joined with `"; "`; `contact_reference` values are rendered as display names (not raw ids); non-owner or anonymous caller → 401/403.

10. `GET /export/participants.csv` at **33,000 rows** completes without error; the server process peak RSS does not grow proportionally with row count (memory-bounded streaming verified by `db.stream` + `partitions` — not `.all()`); the response body line count equals 33,001 (header + 33,000 data rows).

11. `GET /export/contacts.xlsx` and `GET /export/participants.xlsx` return openable, valid XLSX files. The participants XLSX at 33k rows is generated with `xlsxwriter` `constant_memory=True`; the temp file is deleted after the response completes.

12. `POST /export/jobs` creates an `export_jobs` row with `status="pending"`. The worker `_process_export_jobs` transitions `pending → running → ready`, writes the file under `STORAGE_PATH/exports/`, sets `row_count`, `file_bytes`, `expires_at = created_at + 24h`. `GET /export/jobs/{id}` returns `download_url` exactly when and only when `status == "ready"`.

13. `GET /export/jobs/{id}/download`: non-owner non-admin → HTTP 403; expired job → HTTP 410; file missing on disk → HTTP 404; path traversal attempt (`file_path="../etc/passwd"`) → HTTP 403; valid owner download → file streamed with correct `Content-Disposition`. The worker purge sweep transitions `ready`/`failed` rows past `expires_at` to `expired` and removes the file from disk.

14. Auth gating: anonymous → 401 on all endpoints. `viewer` role → 403 on all bulk write endpoints; 200 on all export endpoints. `volunteer` → 200 on add/status/soft-remove; 403 on `hard=true`. `admin` → 200 everywhere.

15. A single summary `audit_log` row is written per successful bulk write operation (`action="participant.bulk_add"` / `"participant.bulk_status"` / `"participant.bulk_remove_hard"`) containing `event_id`, counts, and `audience_mode`. The write is guarded — if `audit_log` does not yet exist, the bulk op still succeeds.

16. `alembic upgrade head` → `alembic downgrade -1` is clean and idempotent on both SQLite and Postgres: `export_jobs` table and its two indexes are created then dropped cleanly with no error.

17. Frontend: the Bulk panel preview→confirm→apply flow on `EventDetailPage` invalidates `['participants', eventId]` and surfaces counts in a `sonner` success toast; export buttons on `ContactsPage` and the participant list page download CSV/XLSX via blob with the currently active filters applied; async-job mode polls at 2 s and auto-downloads on `ready` status; `npm run build`, `npm run lint`, and `npm run test:run` all pass green.

---

## 8. Test plan

### Backend (`backend/tests/`, pytest + pytest-asyncio; `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`)

**`test_bulk_participants.py`**

- `test_bulk_add_all_inserts_each_contact` — seed N contacts + 1 event; POST `mode="all"`; assert `inserted==N`, all rows have `source="bulk"`, `registered_by_id==caller_id` (AC1).
- `test_bulk_add_idempotent_on_conflict` — same call twice; second call `inserted==0`, `skipped==N`; total participant count still N (AC2).
- `test_bulk_add_single_statement` — use `sqlalchemy.event.listen(engine, "before_cursor_execute", ...)` to capture SQL; assert exactly one INSERT was issued and it contains `ON CONFLICT` (AC3 structural check).
- `test_bulk_add_excludes_deleted_contacts` — contact with `is_deleted=True` excluded when `include_deleted=False`; included when `True` (AC1 nuance).
- `test_bulk_add_rowcount_fallback` — monkeypatch `res.rowcount` to `-1`; assert the fallback path returns correct `inserted` (AC3 / §4.5 fallback path).
- `test_bulk_status_updates_audience_only` — seed mixed statuses across two events; POST `mode="all"`, `new_status="no_show"` for event A; assert event A rows changed; event B rows untouched (AC4).
- `test_bulk_status_only_if_status` — `only_if_status="registered"` leaves `attended` rows at `attended` (AC5).
- `test_bulk_remove_soft_sets_cancelled` — soft remove; rows become `cancelled`, row count unchanged (AC6).
- `test_bulk_remove_hard_requires_admin` — volunteer role → HTTP 403; admin role → rows deleted; confirm row count drops (AC6).
- `test_bulk_preview_no_writes` — participant count before == participant count after; returns correct `audience_size`, `already_participants`, `would_insert` (AC7).
- `test_audience_ids_drops_unknown` — `contact_ids` includes nonexistent ids; `requested` reflects only resolvable ids (AC8).
- `test_audience_static_group` — seed `groups` + `group_members`; verify audience resolves to group members only; contacts outside group not included.
- `test_audience_smart_group_without_s09_returns_400` — monkeypatch `app.services.criteria_compiler` to `ImportError`; `mode="group"` (smart group) → HTTP 400 with message referencing S09 (AC8).
- `test_bulk_add_event_not_found` — invalid `event_id` → HTTP 404.
- `test_bulk_writes_rbac` — anonymous → 401; volunteer → 200; hard-remove by volunteer → 403 (AC14).
- `test_bulk_add_writes_audit_row` — one `AuditLog` row written with `action="participant.bulk_add"`, correct counts; if `AuditLog` not yet migrated, test passes via guard (AC15).

**`test_export_stream.py`**

- `test_export_contacts_csv_headers_include_custom_fields` — seed one active `custom_field_def` (label "Birthday Year"); download `/export/contacts.csv`; assert "Birthday Year" in header row; assert value in data row rendered from `custom_data` (AC9).
- `test_export_contacts_csv_derived_snapshot_columns` — assert the 7 derived columns appear in headers (values may be None/empty for uncomputed contacts) (AC9).
- `test_export_contacts_csv_multivalue_join` — multiselect field with value `["A","B"]` renders as `"A; B"` in the CSV (AC9).
- `test_export_contacts_csv_contact_reference_renders_name` — contact_reference field pointing to contact id 42 renders as `"John Doe"` not `"42"` (AC9).
- `test_export_participants_csv_row_integrity` — small dataset; line count == row count + 1 (header); columns `event_title`, `session_time`, `contact_first_name` present.
- `test_export_uses_db_stream_not_all` — monkeypatch `AsyncSession.stream` to a spy; assert it is called (not `execute().all()`) for the export endpoints (AC10 mechanism).
- `test_export_csv_33k_rows` (`@pytest.mark.perf`) — seed 33,000 participant rows; consume `/export/participants.csv`; assert response is chunked (multiple yield calls); line count == 33,001 (AC10).
- `test_export_xlsx_constant_memory_contacts` — build contacts XLSX; assert temp file is a valid XLSX (`zipfile.is_zipfile`); assert `constant_memory=True` was passed to `xlsxwriter.Workbook` (mock) (AC11).
- `test_export_csv_filters_by_event_id` — seed two events' participants; `?event_id=X` returns only participants for X (AC9/10 filter correctness).
- `test_export_csv_filters_by_status` — `?status=attended` excludes `no_show` rows.
- `test_export_bom_first_chunk` — first yielded bytes chunk starts with `b"\xef\xbb\xbf"` (UTF-8 BOM).

**`test_export_jobs.py`**

- `test_create_export_job_enqueues_pending` — POST `/export/jobs` returns 201, `status="pending"`, row exists in DB (AC12).
- `test_worker_processes_csv_job` — seed a pending contacts CSV job; call `_process_export_jobs(db)` directly; assert `status="ready"`, `file_path` starts with `exports/`, `row_count >= 0`, `file_bytes > 0`, `expires_at` approximately now + 24h (AC12).
- `test_worker_processes_xlsx_job` — same for XLSX format (AC12).
- `test_worker_sets_failed_on_exception` — monkeypatch export service to raise; assert `status="failed"`, `error` truncated to ≤ 500 chars (AC12).
- `test_export_job_ownership` — non-owner non-admin GET `/{id}` → 403; owner → 200; admin → 200 (AC13).
- `test_export_job_download_ownership_enforced` — same ownership check for download endpoint (AC13).
- `test_export_job_download_traversal_blocked` — craft job row with `file_path="../etc/passwd"`; download → HTTP 403 (AC13).
- `test_export_job_expired_returns_410` — set `expires_at` to 1 hour ago, `status="ready"`; run `_purge_expired_export_jobs(db)`; assert `status="expired"`, `file_path=None`; GET download → HTTP 410 (AC13).
- `test_export_jobs_list_own_only` — non-admin user sees only their own jobs; admin sees all (AC14).

**`test_migration_s05.py`** (or append to existing alembic migration test):
- Run `alembic upgrade head` on test SQLite DB; assert `export_jobs` table exists with all expected columns.
- Run `alembic downgrade -1`; assert table gone. Both directions complete without error (AC16).

**`test_analytics_legacy.py`** (modify or create):
- Assert `GET /analytics/export/attendance` returns 404 or 405 (deleted in §4.6).
- Assert `GET /analytics/export/logs` returns 200 and the response body does not contain a `push_status` column header.

### Frontend (`frontend/src/**`, vitest + React Testing Library)

**`BulkParticipantPanel.test.tsx`**
- Renders three audience radio options; group/saved-search options are disabled with tooltip text when backend returns 404 for groups (AC8 guard visible to user).
- "Preview" button calls `POST /participants/bulk-preview` and renders returned counts in the summary strip.
- "Apply" button opens `ConfirmDialog` with a message including the audience size and event title; confirming POSTs the correct endpoint for the active operation tab; success toast includes counts; `queryClient.invalidateQueries` is called with `['participants', eventId]`.
- Hard-delete checkbox absent for `volunteer` role (`isAdmin=false`); present for admin; sends `hard: true` in the request body.
- Error path: API error → `toast.error(err.response.data.detail)`.

**`ExportMenu.test.tsx`**
- "Download CSV" calls `api.get('/export/contacts.csv?<filters>', { responseType: 'blob' })` and triggers `downloadBlob`.
- "Download XLSX" calls the xlsx endpoint with blob response type.
- Error on sync download → `toast.error(detail)`.
- "Run in background" → calls `POST /export/jobs`; shows chip "Export running…"; when polled job returns `status: "ready"`, fetches `/export/jobs/<id>/download` as blob and triggers download; polling stops (refetchInterval disabled).

**Gates:** `npm run test:run` passes green; `npm run build` exits 0; `npm run lint` exits 0.

---

## 9. Rollout / rollback / risks

### Rollout steps

1. **Ship Alembic migration `s05_export_jobs`** (additive empty table — zero downtime, no data change needed).
2. At worker startup create `STORAGE_PATH/exports/` if missing: `exports_dir.mkdir(parents=True, exist_ok=True)` (add to worker `_process_export_jobs` entry path and/or lifespan).
3. **Deploy backend** (new routers, services, worker job, `xlsxwriter` added to `requirements.txt`) and **frontend** (bulk panel, export menu) in the same release.
4. **Load-test gate (required before merge):** against a Postgres instance seeded with ≥ 1,500 contacts:
   - Run `POST /participants/bulk-add mode="all"` for a fresh event 5× and assert p95 < 1 s.
   - Run `GET /export/participants.csv` against ≥ 33k participant rows; measure peak RSS (must not grow proportionally — stay flat or grow < 20 MB regardless of row count).
   - Capture results in the PR description.
5. Verify `GET /analytics/export/attendance` is gone (404) and `GET /analytics/export/logs` no longer contains `push_status`.

### Rollback

- **Frontend only:** revert `BulkParticipantPanel` and `ExportMenu` — read-only UI change; no data at risk.
- **Backend:** revert new routers and services; `alembic downgrade -1` drops `export_jobs`. Any files in `STORAGE_PATH/exports/` are orphaned (purged manually or by the TTL sweep after re-upgrading). Bulk-written `participants` rows represent real attendance data and are **not** rolled back — they are intentional CRM records.

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| `rowcount` after `ON CONFLICT DO NOTHING` returns −1 on aiosqlite | §4.5 fallback count path; `test_bulk_add_rowcount_fallback` asserts the fallback returns the correct `inserted` count |
| SQLite has no true server-side cursor | `yield_per=1000` + `partitions(1000)` avoids ORM identity-map materialisation; 33k test passes on SQLite; hard memory claim validated on Postgres in the load test |
| Smart-group/saved-search audiences depend on S09 | Guarded with HTTP 400 + clear message; static-group/ids/all modes cover the daily pain ("mark everyone attended") without S09 |
| Bulk mistakes have large blast radius (accidentally marking 1,440 cancelled) | Mandatory preview step + explicit `ConfirmDialog` showing resolved count; summary `audit_log` row enables after-the-fact review; soft remove is the default (status → `cancelled`, recoverable); hard delete is admin-only |
| XLSX temp file fills disk before purge | `constant_memory=True` writer (no in-memory accumulation); temp file deleted in `BackgroundTask` after response; async job files subject to 24h TTL purge |
| `exports/` directory missing after fresh deploy | `mkdir(parents=True, exist_ok=True)` in worker startup and lifespan initialisation |
| `export_jobs.status="running"` stuck if worker crashes | Worker picks orphaned `running` rows older than a configurable threshold (e.g. 30 min) on next cycle; a single-instance deployment (Unraid) makes double-pickup very unlikely; can add a reset endpoint as needed |
| Custom-field column count very large | `columns` query param lets callers narrow the export; acceptable for this org (few custom groups expected) |
| asyncpg `rowcount` semantics change across versions | Fallback count path in `bulk_add_participants` is always available; the load-test PR asserts the asyncpg behaviour explicitly |

---

## 10. Open questions & pending owner artifacts

1. **S04 constraint name** — before implementing `ON CONFLICT`, confirm the exact constraint name on `participants(event_id, contact_id)` (expected `uq_participants_event_contact`). If S04 used a different name, update `index_elements` in `bulk_add_participants`.

2. **S04 participant router prefix** — if S04 registered a `/participants` router in `main.py`, S05's four bulk handlers must be added to that same router file (no duplicate prefix). Confirm the exact import path before wiring `main.py`.

3. **S09 availability ordering** — smart-group and saved-search audience modes require S09's criteria compiler. Recommendation: ship S05 with static-group/ids/all fully working (the primary daily pain), and enable smart audiences when S09 lands. The master doc should confirm whether S09 is sequenced before S05 or whether phased capability is acceptable.

4. **S01/S16 table guards** — `audit_log` (S01) and `job_runs` (S16) are referenced but may not yet exist when S05 first ships. Both writes are guarded with try/except. The master doc should confirm S01 creates `audit_log` before S05 (Phase A precedes Phase B — this is the expected ordering).

5. **Async export TTL** — default is 24 h. If the owner wants a longer TTL (e.g. 48 h for weekend exports), add `export_job_ttl_hours` to `admin_settings` in S16.

6. **Export column defaults** — current policy: soft-deleted contacts excluded by default (`include_deleted=False`); `custom_data` keys with no matching active `custom_field_def` are not exported (only active defs become columns). Confirm with owner before S05 implementation.

7. **Derived snapshot columns in export** — if S23 has not yet run, all derived snapshot columns (`tier`, `is_active`, etc.) will be NULL in every export. This is intentional and acceptable pre-S23; the column headers still appear. The owner should understand this ordering.

8. **asyncpg rowcount reliability** — asyncpg returns the count of actually inserted rows after `INSERT ... ON CONFLICT DO NOTHING`. Validate this in the load-test PR with an explicit `assert result.rowcount == expected_inserts` on a real Postgres instance. If behaviour differs between asyncpg versions, the §4.5 fallback count path is always available.

---

### Cross-sprint notes for the master doc

- **Shared model touchpoint:** `participants.source` gains the value `"bulk"`. The full canonical source list after S05 is: `face | manual | zoom | name_list | community_report | import | bulk`. S04 owns the column definition; the master doc should declare this final source list so no later sprint introduces a competing enum migration.
- **Shared service contract:** `services/audience.py` (`resolve_audience` / `count_audience`) is designed for reuse by S06 (ETL preview), S09 (group/search management), S10 (import wizard pre-import audience check), and S14 (reporting filter). S09 *owns* `services/criteria_compiler.py`; S05 imports it via a guarded try/except. The master doc should record S09 as the single home for criteria→SQL compilation.
- **Worker host shared infra:** `_process_export_jobs` is added to the `queue_manager.py` `run()` loop alongside `_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup`. S16's `job_runs` viewer should surface `job_name="export"` entries.
- **Dependency the depends-on line understates:** smart-group/saved-search audiences and their corresponding export filters require **S09**; the `viewer` role and `require_viewer` properly land in **S15** (S05 ships a forward-compatible shim that is exactly right after S15 and no-op before it).
- **Analytics.py cleanup is S05-owned:** the two broken legacy exports (`export_attendance_csv` referencing `push_status`, `export_logs_csv` emitting `push_status`) must be removed/rewritten in this sprint. They will fail at runtime after S01 drops `push_status`, so they cannot be deferred.
- **`xlsxwriter` dependency:** `XlsxWriter>=3.2.0` must be added to `backend/requirements.txt` in this sprint. It is pure-Python and has no native build step. `constant_memory=True` is available since xlsxwriter 0.x but is validated in tests for version ≥ 3.2.
