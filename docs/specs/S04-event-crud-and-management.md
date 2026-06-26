# S04 — Native Event CRUD, Sessions & Series
**Phase:** B — Core CRM · **Depends on:** S01 (Schema Inversion & CiviCRM Excision) · **Effort:** L · **Status:** Not started

> **S01 dependency note.** S01 renames `CiviCRMEvent` → `Event` (table `events`), app-mints `events.id` as a serial PK, adds `events.external_id` UNIQUE nullable, repoints `Detection.event_id` / `participants.event_id` / `Log.event_id` FKs, renames `attendance` → `participants` with status enum (`attended | registered | no_show | cancelled`), drops `push_status / push_attempts / last_push_error` from participants, deletes `POST /events/sync`, and removes `CiviCRMClient` imports in `events.py`. **This spec assumes those S01 changes have landed.** Where S04 touches a symbol that S01 also touches it is called out explicitly.

> **n8n note.** The charter cites workflow `pz7sHlUbU6jV1Hqm` ("Create Schedule"). Reading the actual JSON (`docs/crm-research/n8n/pz7sHlUbU6jV1Hqm.json`), this is a **Telnyx-triggered external client discovery-call scheduler** (Google Calendar + Gmail for john.atienza@lightnc.org) — unrelated to church service events. The authoritative source for cadences is `fKkPUolayRyZrjao` ("CiviCRM Scheduler/Updater", 92 nodes), documented verbatim in `docs/crm-research/n8n-analysis.md §3`. S04 defines the cadence contracts; **S16** executes them via APScheduler.

---

## 1. Goal & rationale

Today `events` is a read-only mirror of CiviCRM. The `CiviCRMEvent` model (`backend/app/models.py:77`) has only `event_id` (PK, CiviCRM's own integer), `title`, `start_date`, `end_date`, `last_synced_at`. There is **no event type, no session time, no recurrence, and no way to create, edit, or delete events inside Seraphim**. The `EventsPage` (`frontend/src/pages/EventsPage.tsx`) is entirely framed around "Sync from CiviCRM". The set-active-event endpoint (`events.py:35`) even errors with "Sync events from CiviCRM first."

S04 replaces that with a full native event surface:

1. **Full native event CRUD** on the (post-S01) `events` table with the locked vocabulary: `event_type` (Sunday Celebration / Prayer Meeting / Powerhouse / Community Meeting / Conference / Event) and `session_time` (8AM / 10AM / 3PM / null). These map 1:1 to the session taxonomy in `docs/crm-research/reports.md §A` that S14 depends on.
2. **Recurring series** (`event_series`) — a cadence template that spawns dated `events` occurrences idempotently, eliminating manual creation of 52+ rows/year for Sunday services alone.
3. **Scheduled auto-generation cadence contracts** — the exact cron expressions and business rules (from n8n workflow `fKkPUolayRyZrjao`) defined here as constants consumed by S16. S04 does not run the scheduler; S16 does.
4. **Unique-vs-Total counting model** — `participants.UNIQUE(event_id, contact_id)` (from S01) already enforces one row per person per event; this sprint formalises the counting definitions and adds `participant_counts` aggregate to every event response for S14 to consume.
5. **Repoint the active-event mechanism** off the CiviCRM error message onto native events (`dynamic_settings.get_active_event_id()` at `config.py:157-164` already works; only the error text and table reference in `events.py:49` need updating).
6. **Participant grid** — a paginated read-and-edit view of who attended an event (`GET /events/{id}/participants`, `PATCH /events/{id}/participants/{pid}/status`).
7. **Rework `EventsPage`** — strip the CiviCRM sync button, add event type/session badges, Create-Event drawer, series management, and the participant grid accessible from each event card.

---

## 2. Scope

### In scope
- **`event_series` table** (new) and **`events` column additions** (`event_type`, `session_time`, `occurrence_date`, `recurring_series_id`, `is_active`). One Alembic migration.
- **Event CRUD endpoints**: `GET /events` (paginated, filterable), `POST /events`, `GET /events/{id}`, `PATCH /events/{id}`, `DELETE /events/{id}` (soft-delete via `is_active=false`).
- **Event Series CRUD endpoints**: `POST /event-series`, `GET /event-series`, `GET /event-series/{id}`, `PATCH /event-series/{id}`, `DELETE /event-series/{id}`, `POST /event-series/{id}/generate` (idempotent occurrence spawner).
- **Active-event repoint**: fix `set_active_event` error message; validate against `events` (not `civicrm_events`).
- **Participant grid endpoints**: `GET /events/{id}/participants` (paginated), `PATCH /events/{id}/participants/{pid}/status` (admin/volunteer status override), `POST /events/{id}/participants` (manual add single participant).
- **Counting model** — `participant_counts` object (unique_count, total_count) on `EventDetailResponse`; service function `get_event_participant_counts(event_id)`.
- **Cadence contract constants** in `backend/app/services/event_generator.py`: `SUNDAY_CRON`, `POWERHOUSE_CRON`, `EOW_RECOMPUTE_CRON`, `EOM_RECOMPUTE_CRON`, `NOTIFIER_CRONS`, and the business logic functions `generate_sunday_events(series_id, db)` and `generate_powerhouse_event(series_id, db)` that S16 calls.
- **Frontend rework** of `EventsPage.tsx` → full CRUD page with: filter bar (type/date), event cards with type/session/count badges, Create Event drawer (admin), series management panel (admin), participant grid sheet (per-event, all roles).
- **Schemas**: `EventCreate`, `EventUpdate`, `EventResponse`, `EventDetailResponse`, `EventSeriesCreate`, `EventSeriesUpdate`, `EventSeriesResponse`, `ParticipantStatusUpdate`, `ParticipantManualAdd`, `ParticipantListResponse`, `EventParticipantItem`.
- **Audit logging** of event create/update/delete and manual participant add/status change.
- Frontend types: `Event`, `EventSeries`, `EventParticipant` added to `frontend/src/types/index.ts`.

### Out of scope
- APScheduler setup, job_runs table, actual cron scheduling — **S16** consumes the constants and function signatures defined here.
- Bulk participant add/update, CSV/XLSX import of participants, streaming export — **S05**.
- All four attendance sources (face-rec, manual bulk, Zoom, name-list) beyond manual single-add — **S22** (name-list), **S18** (Zoom).
- Notification cadences (posting attendance summaries to Google Chat) — **S18**.
- Community Reports entity (`community_report` table and submission form) — **S22**.
- Derived-attribute recompute jobs triggered by event generation — **S23**.
- RBAC viewer-role gating of event endpoints — **S15** adds `require_viewer_plus` dep; S04 uses `require_volunteer` throughout; viewer may read events (read-only) once S15 adds the dep — noted in §10.
- Full reporting dashboard with SMA averages, zone rollups — **S14** consumes the counting model defined here.
- Desktop sidebar shell / responsive-layout pass — **S20**.

---

## 3. Data model changes

### 3.1 Table: `event_series` — created in full by S04 (supersedes CN-27)

**Ownership (2026-06-22 ruling, supersedes CN-27):** **S04 owns `event_series` entirely** and creates it with a plain `op.create_table('event_series', ...)`. S01 does NOT create a stub — CN-16 moved the `events.recurring_series_id` FK to S04, so S01 has no consumer for the table and creating a stub would collide with S04's columns. S04 creates `event_series` first, then adds the `events.recurring_series_id` FK pointing at it.

```sql
CREATE TABLE event_series (
    id          SERIAL PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    event_type  VARCHAR(50)  NOT NULL,          -- Sunday Celebration | Prayer Meeting | Powerhouse | Community Meeting | Conference | Event
    session_time VARCHAR(20) NULL,              -- 8AM | 10AM | 3PM | NULL (String(20), matches events.session_time)
    cadence     JSONB        NOT NULL,          -- {"freq":"weekly","day_of_week":6,"hour":8,"minute":0}
    default_location VARCHAR(255) NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP    NOT NULL DEFAULT now()
);
```

`cadence` JSONB shape (all values optional per field):
```json
{
  "freq": "weekly",        // weekly | monthly | custom
  "day_of_week": 0,        // 0=Mon … 6=Sun (ISO weekday)
  "hour": 8,
  "minute": 0,
  "generate_days_ahead": 7 // how many days in advance to spawn the next occurrence
}
```

SQLAlchemy: `cadence: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict)`.

Index: none required (small table, admin CRUD only).

### 3.2 Column additions to existing `events` table (post-S01)

S01 already renamed the table and migrated the PK. **S01 creates only the minimal core `events` columns** (`id`, `external_id`, `title`, `start_at`, `end_at`, `created_at`). The six columns below — `event_type`, `session_time`, `occurrence_date`, `recurring_series_id`, `is_active`, `location` — are **owned by S04 and added entirely by S04's migration** (per CN-16). S01 must NOT include any of these six in its `CREATE TABLE events`.

S04 adds (`session_time` is `String(20)`, `occurrence_date` is `Date`, per CN-16):

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `event_type` | VARCHAR(50) | NOT NULL | `'Event'` | Vocabulary: Sunday Celebration / Prayer Meeting / Powerhouse / Community Meeting / Conference / Event |
| `session_time` | VARCHAR(20) | NULL | NULL | 8AM / 10AM / 3PM; NULL for non-Sunday or unspecified. Width is `String(20)` per CN-16 |
| `occurrence_date` | DATE | NULL | NULL | Calendar date of the event (separate from `start_at`); enables date-based filtering independent of time. Stored as `Date` per CN-16 |
| `recurring_series_id` | INTEGER | NULL | NULL | FK → `event_series(id)` ON DELETE SET NULL; nullable for one-off events |
| `is_active` | BOOLEAN | NOT NULL | TRUE | Soft-delete flag (FALSE = archived) |
| `location` | VARCHAR(255) | NULL | NULL | Physical location string |

S01 already added `external_id`, `start_at`, `end_at`, `created_at`. Confirm S01 spec cols before migrating; if S01 uses different column names for start/end call them out in §10 Q1.

The existing `CiviCRMEvent` columns `start_date` / `end_date` become `start_at` / `end_at` in S01 (datetime). `occurrence_date` is a redundant DATE for calendar-day queries; it is set to `DATE(start_at)` at write time.

Index: `CREATE INDEX ix_events_event_type ON events(event_type)` and `CREATE INDEX ix_events_occurrence_date ON events(occurrence_date DESC)`.
Unique: none beyond `external_id UNIQUE` (from S01).

### 3.3 Alembic plan

One new revision chained after S01's final revision.

**Upgrade:**
1. **`event_series`**: `op.create_table('event_series', ...)` with the full column set in §3.1 (S04 owns the table; supersedes CN-27 — S01 does not create a stub). Create this BEFORE step 5's `recurring_series_id` FK.
2. `ALTER TABLE events ADD COLUMN event_type VARCHAR(50) NOT NULL DEFAULT 'Event'`.
3. `ALTER TABLE events ADD COLUMN session_time VARCHAR(20)` (`String(20)` per CN-16).
4. `ALTER TABLE events ADD COLUMN occurrence_date DATE`.
5. `ALTER TABLE events ADD COLUMN recurring_series_id INTEGER REFERENCES event_series(id) ON DELETE SET NULL`.
6. `ALTER TABLE events ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE`.
7. `ALTER TABLE events ADD COLUMN location VARCHAR(255)`.
8. `CREATE INDEX ix_events_event_type ON events(event_type)`.
9. `CREATE INDEX ix_events_occurrence_date ON events(occurrence_date DESC)`.
10. **Backfill** existing rows: `UPDATE events SET occurrence_date = DATE(start_at)` (Postgres) / `DATE(start_at)` (SQLite); set `event_type = 'Event'` (already the default so no explicit update needed).

**Downgrade:**
1. Drop indexes.
2. `ALTER TABLE events DROP COLUMN location, is_active, recurring_series_id, occurrence_date, session_time, event_type`.
3. `op.drop_table('event_series')` — S04 owns the table (supersedes CN-27), so its downgrade drops it (after dropping the `events.recurring_series_id` FK in step 2).

SQLite note: SQLite does not support `ALTER TABLE … DROP COLUMN` before 3.35.0; the test suite uses aiosqlite — downgrade is untested on SQLite and that is acceptable (prod is Postgres).

### 3.4 ORM models to add/modify

**`backend/app/models.py`** — add after existing models:

```python
class EventSeries(Base):
    __tablename__ = "event_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    session_time: Mapped[Optional[str]] = mapped_column(String(20))  # String(20), matches events.session_time
    cadence: Mapped[dict] = mapped_column(JSON().with_variant(_PG_JSONB, "postgresql"), nullable=False, default=dict)
    default_location: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
```

**`backend/app/models.py`** — modify the post-S01 `Event` model (currently named `CiviCRMEvent`, renamed by S01):

```python
class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True)  # from S01
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, default="Event")
    session_time: Mapped[Optional[str]] = mapped_column(String(20))  # String(20) per CN-16
    occurrence_date: Mapped[Optional[date]] = mapped_column(Date)  # Date per CN-16
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)          # from S01
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)            # from S01
    location: Mapped[Optional[str]] = mapped_column(String(255))
    recurring_series_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("event_series.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
```

---

## 4. Backend

### 4.1 Endpoints table

| METHOD | Path | Role | Request body / params | Response | Notes |
|---|---|---|---|---|---|
| GET | `/events` | volunteer | `?type=&date_from=&date_to=&series_id=&is_active=true&page=1&page_size=20` | `PaginatedEventResponse` | Excludes `is_active=false` by default; admin can pass `is_active=` any value |
| POST | `/events` | admin | `EventCreate` | `EventDetailResponse` | Creates one-off or series occurrence |
| GET | `/events/{id}` | volunteer | — | `EventDetailResponse` (incl. `participant_counts`) | 404 if not found |
| PATCH | `/events/{id}` | admin | `EventUpdate` | `EventDetailResponse` | Partial update; audit-logged |
| DELETE | `/events/{id}` | admin | — | `{"message": "..."}` | Soft-delete: sets `is_active=false`; 404 if not found |
| GET | `/events/active-event-id` | volunteer | — | `{"active_event_id": int \| null}` | Existing endpoint; no change |
| POST | `/events/set-active` | admin | `?event_id=int\|null` | `{"active_event_id":..., "event_title":..., "message":...}` | Fix error message; validate against `events` table (not `civicrm_events`) |
| GET | `/event-series` | volunteer | `?is_active=true` | `list[EventSeriesResponse]` | All series |
| POST | `/event-series` | admin | `EventSeriesCreate` | `EventSeriesResponse` | Create template |
| GET | `/event-series/{id}` | volunteer | — | `EventSeriesResponse` | 404 if not found |
| PATCH | `/event-series/{id}` | admin | `EventSeriesUpdate` | `EventSeriesResponse` | Partial update |
| DELETE | `/event-series/{id}` | admin | — | `{"message": "..."}` | Sets `is_active=false` on series; does NOT cascade to events |
| POST | `/event-series/{id}/generate` | admin | `{"target_date": "YYYY-MM-DD"}` | `{"created": int, "skipped": int, "events": list[EventDetailResponse]}` | Idempotent spawn |
| GET | `/events/{id}/participants` | volunteer | `?page=1&page_size=50&source=&status=` | `ParticipantListResponse` | Paginated participant list with contact display name |
| POST | `/events/{id}/participants` | volunteer | `ParticipantManualAdd` | `EventParticipantItem` | Manually register a single contact; 409 on duplicate |
| PATCH | `/events/{id}/participants/{pid}/status` | volunteer | `ParticipantStatusUpdate` | `EventParticipantItem` | Edit attendance status; audit-logged |

All paths registered with `prefix="/events"` or `prefix="/event-series"` in their respective routers; `main.py` includes them under `Depends(check_setup_complete)` (no `/api` prefix per CLAUDE.md convention).

### 4.2 Cadence contract constants (consumed by S16)

File: **`backend/app/services/event_generator.py`** (new file).

These are the exact cron expressions extracted from n8n workflow `fKkPUolayRyZrjao` as documented in `docs/crm-research/n8n-analysis.md §3`:

```python
# Cron expressions — defined here, consumed by S16 APScheduler
SUNDAY_CRON       = {"day_of_week": "fri", "hour": 18, "minute": 0}   # Fri 18:00 → generates Sun events
POWERHOUSE_CRON   = {"day_of_week": "wed", "hour": 8,  "minute": 0}   # Wed 08:00 → generates Powerhouse
EOW_RECOMPUTE_CRON = {"day_of_week": "mon", "hour": 0,  "minute": 0}  # Mon 00:00 → end-of-week recompute (S23)
EOM_RECOMPUTE_CRON = {"day": 1, "hour": 0, "minute": 0}               # 1st of month 00:00 → EOM recompute (S23)

# Attendance notifier cadences (S18 posts to Google Chat)
NOTIFIER_CRONS = {
    "sunday_8am":   {"day_of_week": "sun", "hour": 9,  "minute": 30}, # 09:30 Sun
    "sunday_10am":  {"day_of_week": "sun", "hour": 12, "minute": 0},  # 12:00 Sun
    "sunday_3pm":   {"day_of_week": "sun", "hour": 17, "minute": 0},  # 17:00 Sun
    "powerhouse":   {"day_of_week": "wed", "hour": 21, "minute": 0},  # 21:00 Wed
}
```

### 4.3 Event generation business logic

**`generate_sunday_events(series_id: int, db: AsyncSession, target_date: date | None = None) -> list[Event]`**

Logic:
1. Load `EventSeries` by `series_id`; 404 if not found or `is_active=False`.
2. If `target_date` is None, compute the **next Sunday** from `datetime.now(timezone.utc).date()`.
3. For each `session_time` in `["8AM", "10AM", "3PM"]`, attempt to create one `Event`:
   - `title = f"Sunday Celebration – {target_date.strftime('%B %d, %Y')}"` (or override from series).
   - `event_type = "Sunday Celebration"`, `session_time = session_time`.
   - `occurrence_date = target_date`.
   - Compute `start_at`: combine `target_date` with the session hour (8AM → 08:00 PHT → stored as UTC; PHT = UTC+8, so 8AM PHT = 00:00 UTC; 10AM PHT = 02:00 UTC; 3PM PHT = 07:00 UTC).
   - Insert with `INSERT ... ON CONFLICT DO NOTHING` using a unique constraint on `(occurrence_date, event_type, session_time, recurring_series_id)` — see §3 note; alternatively check for existing row before insert.
4. Return list of created events (skip already-existing ones, report `skipped` count).

**`generate_powerhouse_event(series_id: int, db: AsyncSession, target_date: date | None = None) -> Event | None`**

Logic:
1. If `target_date` is None, compute the **next Wednesday** from today.
2. Check for existing `(occurrence_date=target_date, event_type='Powerhouse', session_time=None, recurring_series_id=series_id)` — if exists, return None (skipped).
3. Create `Event(title="Powerhouse – {date}", event_type="Powerhouse", session_time=None, occurrence_date=target_date, start_at=target_date + timedelta(hours=13))` (Powerhouse is typically 9PM PHT = 13:00 UTC; adjust per series.cadence if set).
4. Return the new event.

**`generate_series_occurrence(series_id: int, db: AsyncSession, target_date: date) -> Event | None`**

Generic version: reads `series.event_type` and delegates to the appropriate typed generator or a generic one. Used by `POST /event-series/{id}/generate`.

**Idempotency guard**: a natural uniqueness check on `(occurrence_date, event_type, session_time, recurring_series_id)` — query before insert rather than a DB constraint (the combination is only soft-unique; a constraint would prevent intentional double-events of the same type on the same day). If row exists, skip and count as `skipped`.

### 4.4 Services / files — create or modify

**Create `backend/app/routers/event_series.py`** (new):
- `router = APIRouter(prefix="/event-series", tags=["event-series"])`
- Imports: `Event`, `EventSeries` models; all event-series schemas; `event_generator` service.
- All CRUD endpoints from §4.1.

**Modify `backend/app/routers/events.py`**:
- Remove `CiviCRMClient` import (done by S01; confirm and skip if already done).
- Remove `sync_events` endpoint (done by S01).
- Fix `set_active_event`: replace `await db.get(CiviCRMEvent, event_id)` (events.py:49) → `await db.get(Event, event_id)`; reword error message from "Sync events from CiviCRM first." → "Event not found.".
- Add all new event CRUD endpoints (`POST /events`, `GET /events`, `GET /events/{id}`, `PATCH /events/{id}`, `DELETE /events/{id}`).
- Add participant sub-resource endpoints (`GET /events/{id}/participants`, `POST /events/{id}/participants`, `PATCH /events/{id}/participants/{pid}/status`).

**Create `backend/app/services/event_generator.py`** (new):
- Cadence constants (§4.2).
- `generate_sunday_events(...)`, `generate_powerhouse_event(...)`, `generate_series_occurrence(...)`.
- `async def get_event_participant_counts(event_id: int, db: AsyncSession) -> dict` — returns `{"unique_count": int, "total_count": int}`. The `participants` table has `UNIQUE(event_id, contact_id)` from S01, so unique_count = total row count for that event; "total" in the reports sense means the same thing for face-rec/manual attendance (one row per confirmed person). For S14 reporting, "Total" vs "Unique" refers to whether the same person is counted once or across all services — the participant table's constraint already guarantees one row per (event, contact). S14 will need to aggregate across multiple events of the same type on the same date. This is out of scope for S04 beyond defining the concepts; `get_event_participant_counts` returns `unique_count` (distinct contacts) and `total_count` (all participant rows regardless of status).

**Modify `backend/app/models.py`**: add `EventSeries` model; add columns to `Event` (§3.4).

**Modify `backend/app/schemas.py`**: add all schemas listed in §2.

**Modify `backend/app/main.py`**: add `event_series` router import and `app.include_router(event_series_router.router, dependencies=[Depends(check_setup_complete)])`.

**Modify `backend/app/config.py`**: no change needed (active-event mechanism is already CRM-agnostic in `config.py:157-164`).

### 4.5 Schema definitions (Pydantic)

```python
# Event types and session times — use Literal for validation
EVENT_TYPE_VALUES = Literal[
    "Sunday Celebration", "Prayer Meeting", "Powerhouse",
    "Community Meeting", "Conference", "Event"
]
SESSION_TIME_VALUES = Literal["8AM", "10AM", "3PM"]

class EventCreate(BaseModel):
    title: str
    event_type: EVENT_TYPE_VALUES
    session_time: Optional[SESSION_TIME_VALUES] = None
    occurrence_date: Optional[date] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    recurring_series_id: Optional[int] = None

class EventUpdate(BaseModel):
    title: Optional[str] = None
    event_type: Optional[EVENT_TYPE_VALUES] = None
    session_time: Optional[SESSION_TIME_VALUES] = None
    occurrence_date: Optional[date] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    is_active: Optional[bool] = None

class ParticipantCounts(BaseModel):
    unique_count: int
    total_count: int

class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    external_id: Optional[str] = None
    title: str
    event_type: str
    session_time: Optional[str] = None
    occurrence_date: Optional[date] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    location: Optional[str] = None
    recurring_series_id: Optional[int] = None
    is_active: bool
    created_at: datetime

class EventDetailResponse(EventResponse):
    participant_counts: ParticipantCounts

class PaginatedEventResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EventResponse]

class EventSeriesCreate(BaseModel):
    title: str
    event_type: EVENT_TYPE_VALUES
    session_time: Optional[SESSION_TIME_VALUES] = None
    cadence: dict  # validated at service layer
    default_location: Optional[str] = None

class EventSeriesUpdate(BaseModel):
    title: Optional[str] = None
    event_type: Optional[EVENT_TYPE_VALUES] = None
    session_time: Optional[SESSION_TIME_VALUES] = None
    cadence: Optional[dict] = None
    default_location: Optional[str] = None
    is_active: Optional[bool] = None

class EventSeriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    event_type: str
    session_time: Optional[str] = None
    cadence: dict
    default_location: Optional[str] = None
    is_active: bool
    created_at: datetime

class EventParticipantItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contact_id: int
    contact_display_name: str   # computed: "first_name last_name"
    event_id: int
    status: str
    role: Optional[str] = None
    source: str
    detection_id: Optional[int] = None
    registered_by_id: Optional[int] = None
    created_at: datetime

class ParticipantListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EventParticipantItem]

class ParticipantManualAdd(BaseModel):
    contact_id: int
    status: Literal["attended", "registered"] = "attended"
    role: Optional[str] = None

class ParticipantStatusUpdate(BaseModel):
    status: Literal["attended", "registered", "no_show", "cancelled"]
```

### 4.6 Business rules & edge cases

- **`event_type` vocabulary is closed**: validated via `Literal` in Pydantic; any value outside the six is rejected with 422.
- **`session_time` only valid for Sunday Celebration**: enforced at the service layer (not Pydantic-level) — if `event_type != "Sunday Celebration"` and `session_time is not None`, return 422 with detail "session_time is only valid for Sunday Celebration events."
- **Soft-delete semantics**: `DELETE /events/{id}` sets `is_active=False`; the event is excluded from list queries by default (`?is_active=true` default). The active-event setting is **not** auto-cleared on soft-delete — the detection pipeline would notice the next recognition cycle when it cannot find the event. Add a warning in the delete response if the event being deleted is the current active event.
- **`set_active_event` — repoint to native table**: `await db.get(Event, event_id)` instead of `await db.get(CiviCRMEvent, event_id)`. Error message: `"Event {event_id} not found."` (drop the CiviCRM hint).
- **Participant duplicate on manual add**: the `UNIQUE(event_id, contact_id)` constraint (from S01) returns `IntegrityError` on duplicate. Catch and convert to `409 Conflict` with `detail = "Contact is already registered for this event."`.
- **Idempotent generation**: `POST /event-series/{id}/generate` is safe to call multiple times for the same `target_date`. The service layer checks before insert and returns `{"created": 0, "skipped": 3}` if all occurrences already exist.
- **Participant `source`**: when a participant is added via `POST /events/{id}/participants`, set `source="manual"`. Do not accept `source` as an input from the API (it is inferred from the code path).
- **`occurrence_date` backfill**: on event create, if `occurrence_date` is None and `start_at` is provided, automatically set `occurrence_date = start_at.date()`.
- **Pagination**: default `page_size=20` for events; `page_size=50` for participants. Cap at 200 and 500 respectively to prevent runaway queries.
- **Performance**: the `events` list endpoint with pagination and date filters on `occurrence_date` will be fast given the `ix_events_occurrence_date` index. Participant counts use a `COUNT(*) WHERE event_id = :id` query — cache not needed for <5000 participants per event.

### 4.7 Audit logging

Use the `app/services/audit.py` `record(actor_id, action, entity, entity_id, before, after)` helper (from S01/S02 convention).

Actions:
- `event.create`, `event.update`, `event.delete` (soft).
- `event_series.create`, `event_series.update`, `event_series.delete`.
- `participant.add` (manual add), `participant.status_change`.

---

## 5. Frontend

### 5.1 Pages and routes

| Route | Component | Guard | Description |
|---|---|---|---|
| `/events` | `EventsPage` (reworked) | ProtectedRoute | Event list with filter bar, type/session badges, Create drawer |
| `/events/:id` | `EventDetailPage` (new) | ProtectedRoute | Event detail: info + participant grid |
| `/settings/event-series` | `EventSeriesPage` (new) | AdminRoute | Series management (create/edit/delete/generate) |

Add `/settings/event-series` to the admin "More" sheet in `BottomNav.tsx`.

### 5.2 Component inventory

**`frontend/src/pages/EventsPage.tsx`** — rewrite (strip CiviCRM, add CRUD):

- **Header**: title "Events" + `[+ New Event]` button (admin only, opens `EventFormDrawer`).
- **Filter bar**: `<EventTypeFilter>` (pill buttons: All / Sunday Celebration / Prayer Meeting / Powerhouse / …); date-range picker (optional); search by title.
- **Event cards**: each card shows `event_type` badge (color-coded), `session_time` badge (if set), `occurrence_date` formatted, `location`, `participant_counts.unique_count` "attendees" chip. Still shows **LIVE** badge for the active event.
- **Active-event banner**: retained for admin; text updated to "Active event for camera detections: …".
- **"Set Active" button**: retained for admin per-card.
- Remove: "Sync from CiviCRM" button entirely.

**`frontend/src/pages/EventDetailPage.tsx`** (new):

- **Header**: back button, event title, type/session badges, edit/delete actions (admin).
- **Info card**: all scalar fields.
- **Participant grid**: `<ParticipantGrid event_id={id} />` — paginated table (mobile: card list; desktop: table via `DataTable` from S03). Columns: contact name, status badge, source badge, registered_by, created_at. Admin/volunteer can click status cell to change it via `ParticipantStatusUpdate`.
- **Add Participant** button (admin/volunteer): opens `<ContactPickerModal>` (reuse or extend `MemberSearchModal` from `frontend/src/components/tasks/MemberSearchModal.tsx`; after S03 this becomes `ContactPickerModal`).

**`frontend/src/pages/EventSeriesPage.tsx`** (new, AdminRoute):

- List of series (cards: title, event_type, cadence description, is_active toggle).
- Create / edit inline (drawer or inline form).
- **Generate** button per series: calls `POST /event-series/{id}/generate` with a date picker. Shows `created / skipped` result toast.

**`frontend/src/components/events/EventFormDrawer.tsx`** (new):

- Controlled form with: title (text), event_type (select — all 6 values), session_time (select — 8AM/10AM/3PM/none, enabled only when event_type = Sunday Celebration), occurrence_date (date input), start_at / end_at (datetime-local), location (text), recurring_series_id (select from loaded series or none).
- Submit: `POST /events` (create) or `PATCH /events/{id}` (edit).
- Uses `FormField` component from S03.
- Validation: 422 from server surfaced via `toast.error(err.response?.data?.detail || "Save failed")`.

**`frontend/src/components/events/ParticipantGrid.tsx`** (new):

- TanStack Query key: `['event-participants', eventId, page, pageSize, source, status]`.
- Mobile: stacked cards with contact name, source chip, status dropdown.
- Desktop: table rows (reuse `DataTable` from S03).
- Status change: inline `<select>` or click-to-cycle; on change calls `PATCH /events/{id}/participants/{pid}/status`; invalidates query.

**`frontend/src/components/events/EventTypeBadge.tsx`** (new):

- Maps `event_type` → Tailwind token color classes (no hardcoded hex per AGENTS.md):
  - Sunday Celebration → `bg-primary/20 text-primary`
  - Prayer Meeting → `bg-blue-500/20 text-blue-700 dark:text-blue-300`
  - Powerhouse → `bg-purple-500/20 text-purple-700 dark:text-purple-300`
  - Community Meeting → `bg-green-500/20 text-green-700 dark:text-green-300`
  - Conference / Event → `bg-muted text-muted-foreground`

**`frontend/src/components/events/SessionTimeBadge.tsx`** (new):

- Maps `session_time` → small pill: `bg-accent text-accent-foreground` with the time string.

### 5.3 TanStack Query keys

```typescript
['events', { type, dateFrom, dateTo, seriesId, isActive, page, pageSize }]
['event', id]
['event-participants', eventId, { page, pageSize, source, status }]
['event-series']
['event-series', id]
['active-event-id']   // retained from current EventsPage
```

Mutations invalidate the relevant list key and the detail key (`['event', id]`).

### 5.4 Zustand

No new store slices required. Auth store's `isAdmin` / `isVolunteer` used for role gating (same pattern as `EventsPage.tsx:20`). S15 will add `isViewer`; S04 gates on `isAdmin` for write ops and shows read-only view to all authenticated users.

### 5.5 Role gating (frontend)

- Create/edit/delete event: show only to `isAdmin`.
- Set-active button: `isAdmin`.
- Add participant / change status: show to `isAdmin || isVolunteer`.
- View events and participants: all authenticated (`ProtectedRoute`).
- Event series management page: `AdminRoute`.

### 5.6 UX loading / empty / error

- Events list loading: `<LoadingState />` from `components/ui/StateViews.tsx`.
- Events list empty: `<EmptyState icon={CalendarDays} title="No events yet" action={isAdmin && <button onClick={openCreateDrawer}>Create first event</button>} />`.
- Event detail 404: `<ErrorState message="Event not found" />` with back button.
- Participant list loading/empty: inline skeleton rows or `<EmptyState title="No participants yet" />`.
- Error from PATCH participant: `toast.error(err.response?.data?.detail || "Failed to update status")`.
- Duplicate participant (409): `toast.error("Contact is already registered for this event.")`.

### 5.7 Mobile-first / desktop

- EventsPage: single-column card list on mobile; 2-column grid on `sm:` breakpoint.
- EventDetailPage: stacked sections on mobile; side-by-side info + participant grid on `lg:`.
- EventFormDrawer: full-height bottom sheet on mobile; right-side drawer on `md:` (same pattern as S03's ContactFormPage drawer).
- ParticipantGrid: card stack on mobile; table via `DataTable` (from S03) on `md:`.
- All spacing uses Tailwind spacing scale; no hardcoded px values beyond what tokens define.

### 5.8 File-by-file

**Modify:**
- `frontend/src/pages/EventsPage.tsx` — full rewrite (strip CiviCRM sync, add Create + filter + CRUD wiring).
- `frontend/src/App.tsx` — add routes `/events/:id`, `/settings/event-series`.
- `frontend/src/types/index.ts` — add `Event`, `EventSeries`, `EventParticipant`, `ParticipantCounts` interfaces.
- `frontend/src/components/layout/BottomNav.tsx` — add "Event Series" link in admin "More" sheet.

**Create:**
- `frontend/src/pages/EventDetailPage.tsx`
- `frontend/src/pages/EventSeriesPage.tsx`
- `frontend/src/components/events/EventFormDrawer.tsx`
- `frontend/src/components/events/ParticipantGrid.tsx`
- `frontend/src/components/events/EventTypeBadge.tsx`
- `frontend/src/components/events/SessionTimeBadge.tsx`
- `frontend/src/services/events.ts` — typed API functions (`listEvents`, `createEvent`, `updateEvent`, `deleteEvent`, `getEvent`, `listParticipants`, `addParticipant`, `updateParticipantStatus`, `listEventSeries`, `createEventSeries`, `updateEventSeries`, `generateSeriesOccurrences`).

### 5.9 TypeScript types

```typescript
// frontend/src/types/index.ts additions

export interface Event {
  id: number;
  external_id: string | null;
  title: string;
  event_type: EventType;
  session_time: SessionTime | null;
  occurrence_date: string | null;  // ISO date "YYYY-MM-DD"
  start_at: string | null;          // ISO datetime
  end_at: string | null;
  location: string | null;
  recurring_series_id: number | null;
  is_active: boolean;
  created_at: string;
}

export interface EventDetail extends Event {
  participant_counts: { unique_count: number; total_count: number };
}

export type EventType =
  | "Sunday Celebration"
  | "Prayer Meeting"
  | "Powerhouse"
  | "Community Meeting"
  | "Conference"
  | "Event";

export type SessionTime = "8AM" | "10AM" | "3PM";

export interface EventSeries {
  id: number;
  title: string;
  event_type: EventType;
  session_time: SessionTime | null;
  cadence: Record<string, unknown>;
  default_location: string | null;
  is_active: boolean;
  created_at: string;
}

export interface EventParticipant {
  id: number;
  contact_id: number;
  contact_display_name: string;
  event_id: number;
  status: "attended" | "registered" | "no_show" | "cancelled";
  role: string | null;
  source: "face" | "manual" | "zoom" | "name_list" | "community_report";
  detection_id: number | null;
  registered_by_id: number | null;
  created_at: string;
}
```

---

## 6. Migration / data

S04's migration (§3.3) adds columns to `events` and creates `event_series`. All existing event rows get `event_type = 'Event'` (default), `session_time = NULL`, `is_active = TRUE`. The backfill of `occurrence_date` from `start_at` is handled in the `upgrade()` function.

No seed data is created. Admins will:
1. Navigate to Settings → Event Series after deploy.
2. Create a Sunday Celebration series with cadence `{"freq":"weekly","day_of_week":6,"hour":8,"minute":0}`.
3. Create a Powerhouse series with cadence `{"freq":"weekly","day_of_week":2,"hour":21,"minute":0}`.
4. Create a Prayer Meeting series.
5. Click Generate on each series to spawn past/upcoming occurrences.

The CiviCRM XLSX migration (S06) will use `POST /events` to import historical events with `external_id` set from CiviCRM event IDs.

---

## 7. Acceptance criteria

1. `POST /events` with `event_type="Sunday Celebration"` and `session_time="10AM"` creates an event; `GET /events/{id}` returns all fields including `participant_counts`.
2. `POST /events` with `event_type="Prayer Meeting"` and `session_time="8AM"` returns 422 with detail containing "session_time is only valid for Sunday Celebration events."
3. `POST /events` with an invalid `event_type="Picnic"` returns 422.
4. `DELETE /events/{id}` sets `is_active=false`; the event no longer appears in `GET /events` (default `is_active=true` filter); it does appear in `GET /events?is_active=false` (admin).
5. `DELETE /events/{id}` when the event is the current active event returns 200 and includes a warning in the response body.
6. `POST /event-series/{id}/generate` with `target_date` for a Sunday spawns 3 events (8AM, 10AM, 3PM). A second call with the same `target_date` returns `{"created": 0, "skipped": 3}`.
7. `POST /events/{id}/participants` with a valid `contact_id` creates a participant with `source="manual"`. A second call with the same `contact_id` returns 409 with "Contact is already registered for this event."
8. `PATCH /events/{id}/participants/{pid}/status` with `{"status":"no_show"}` updates the status and the change is reflected in `GET /events/{id}/participants`.
9. `POST /events/set-active?event_id=999` where 999 does not exist returns 404 with "Event not found." (no CiviCRM mention).
10. `GET /events` paginates correctly: with 25 events and `page_size=20`, first page returns 20 items and `total=25`; second page returns 5 items.
11. Frontend: EventsPage renders without the "Sync from CiviCRM" button; event cards show `EventTypeBadge` and `SessionTimeBadge`.
12. Frontend: Create Event drawer opens (admin), fills required fields, submits, and the new event appears in the list without a page reload.
13. Frontend: EventDetailPage participant grid renders, and an admin can change a participant's status inline.
14. Frontend: `npm run build` and `npm run lint` pass with no errors.
15. Backend: `ruff check app` passes with no errors.
16. Backend: all new tests pass under `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q`.

---

## 8. Test plan

### 8.1 Backend pytest

File: **`backend/tests/test_events.py`** (new; or extend existing if present after S01).

```python
# test_create_event_with_session_time
# POST /events as admin with event_type="Sunday Celebration", session_time="10AM"
# Assert 201, response.event_type == "Sunday Celebration", response.session_time == "10AM"
# Assert response.participant_counts == {"unique_count": 0, "total_count": 0}

# test_create_event_invalid_session_time
# POST /events with event_type="Prayer Meeting", session_time="8AM"
# Assert 422

# test_create_event_invalid_type
# POST /events with event_type="Birthday"
# Assert 422

# test_soft_delete_event
# POST /events → DELETE /events/{id} → GET /events (default)
# Assert event not in list; GET /events?is_active=false → assert event in list

# test_event_series_generate_idempotent
# POST /event-series (Sunday Celebration series, weekly cadence)
# POST /event-series/{id}/generate with target_date=next Sunday
# Assert response.created == 3, response.skipped == 0
# POST /event-series/{id}/generate same target_date again
# Assert response.created == 0, response.skipped == 3

# test_event_series_generate_powerhouse
# POST /event-series (Powerhouse series)
# POST /event-series/{id}/generate with target_date=next Wednesday
# Assert response.created == 1

# test_add_participant_manual
# POST /events, POST /events/{id}/participants with contact_id
# Assert 201, participant.source == "manual", participant.status == "attended"

# test_add_participant_duplicate_409
# POST /events/{id}/participants twice with same contact_id
# Assert second call returns 409

# test_update_participant_status
# Setup: participant with status="attended"
# PATCH /events/{id}/participants/{pid}/status {"status": "no_show"}
# Assert 200, response.status == "no_show"

# test_set_active_event_native
# POST /events (create event), POST /events/set-active?event_id={id}
# Assert 200, response.active_event_id == id
# POST /events/set-active?event_id=999999
# Assert 404 (no "CiviCRM" in detail)

# test_events_pagination
# Create 25 events, GET /events?page=1&page_size=20
# Assert len(items) == 20, total == 25
# GET /events?page=2&page_size=20
# Assert len(items) == 5

# test_events_filter_by_type
# Create 2 "Sunday Celebration" + 1 "Powerhouse"
# GET /events?type=Powerhouse
# Assert len(items) == 1

# test_volunteer_cannot_create_event
# POST /events as volunteer (not admin)
# Assert 403

# test_viewer_can_read_events (Note: viewer role added in S15; mark this test with @pytest.mark.skip until S15)
```

File: **`backend/tests/test_event_series.py`** (new):

```python
# test_create_series, test_get_series, test_update_series, test_delete_series (soft)
# test_generate_sunday_events_pht_utc — verify start_at UTC offsets are correct for PHT
```

### 8.2 Frontend vitest

File: **`frontend/src/components/events/EventTypeBadge.test.tsx`** (new):
- Renders the correct label and Tailwind class for each event type.
- Snapshot test.

File: **`frontend/src/pages/EventsPage.test.tsx`** (new or extend):
- Mocks `/events` API; asserts no "Sync" button rendered.
- Asserts EventTypeBadge rendered for each mocked event.
- Asserts "New Event" button visible to admin, hidden to volunteer.

---

## 9. Rollout / rollback / risks

### Rollout
1. Apply Alembic migration (upgrade) on Postgres — additive only, safe to run with app running.
2. Deploy new backend image; confirm `GET /events` returns events with new fields (defaulted).
3. Deploy new frontend image; confirm EventsPage renders without "Sync" button.
4. Admin: create event series and run first generate.
5. Admin: verify new events appear with correct type/session/date.
6. Admin: verify set-active works on a native event.

### Rollback
1. Revert frontend deploy (no data impact).
2. Revert backend deploy (no data impact from router changes).
3. If schema rollback needed: `alembic downgrade -1` drops the `event_series` table and the 6 new columns from `events`. **Caution:** any events created with non-null `event_type` / `session_time` / `occurrence_date` will lose that data. Soft-deleted events will reappear as they were. This is an acceptable risk pre-cutover; post-cutover, do not downgrade.

### Risks

| Risk | Mitigation |
|---|---|
| S01 not yet landed when S04 is implemented | S04 **must not merge** before S01. Master doc sequences them. |
| `events.start_at` column name differs from S01 spec | Confirmed in §10 Q1 — reconcile before migration. |
| PHT/UTC conversion errors in event generation (wrong UTC hour) | Unit test `test_generate_sunday_events_pht_utc` asserts exact UTC timestamps. |
| Active-event pointing at a soft-deleted event | Warn in DELETE response; detection pipeline continues gracefully (event lookup returns None, no attendance logged — existing behavior). |
| Large number of events from migration (33k+ attendance rows, but events < ~500) | Pagination + index mitigates; no risk at this scale. |
| `session_time` validation bypass via direct DB insert | Only API and scheduled generator write events; validation is at the service layer for generated events and Pydantic for API events. |

---

## 10. Open questions & pending owner artifacts

**Q1 (BLOCKING — master must reconcile before coding):** S01 spec renames `CiviCRMEvent` and adds new columns. Confirm the **exact column names** S01 uses for start/end datetime: is it `start_at` / `end_at` (this spec assumes these) or `start_date` / `end_date` (current model has `start_date`/`end_date`)? S04's migration must reference the correct names. If S01 keeps `start_date`/`end_date`, update §3.2 and all references in schemas accordingly.

**Q2:** Confirm whether the `participants` table (renamed from `attendance` in S01) includes a `source` column (`face | manual | zoom | name_list | community_report`) from S01 or if S04 must add it. The canonical data model lists `source` on `participants`; if S01 adds it, S04 just uses it. If not, S04 should add it as a nullable VARCHAR(30) in the same migration.

**Q3 (owner):** The Morning Prayer series (`sJ7EgCZkk9wKSj3D`) runs on a schedule "≈23:00" nightly to generate the prayer event and pull Zoom attendance. Should the Prayer Meeting event generation be daily (every day) or only on specific days? Cadence for the `event_series` record depends on the answer. Recommend: daily, so Zoom attendance pull in S18 always has an event to attach to.

**Q4 (owner):** What is the canonical Powerhouse time in PHT? The n8n notifier cron fires at Wed 21:00 (PHT) but the generation cron fires at Wed 08:00 (PHT). The Powerhouse event itself presumably starts at some evening time — confirm (e.g., 7PM or 8PM PHT) so `start_at` is set correctly in `generate_powerhouse_event`.

**Q5 (S16 contract):** S16 must call `generate_sunday_events(series_id, db)` and `generate_powerhouse_event(series_id, db)` where `series_id` comes from a dynamic setting (`admin_settings` key `sunday_series_id` / `powerhouse_series_id`). S04 should add these two admin-setting keys with `NULL` defaults so S16 can read them. Added to `backend/app/config.py` `DynamicSettings` as `get_sunday_series_id() -> int | None` and `get_powerhouse_series_id() -> int | None`.

**Q6 (S14 counting model):** "Total" vs "Unique" in reports.md §A means: Unique = count of distinct contacts (which the `UNIQUE(event_id,contact_id)` constraint already enforces per event); Total = total across all services of the same type on the same occurrence_date (e.g., 8AM + 10AM + 3PM Sunday combined). S14 will aggregate across events by `(occurrence_date, event_type)`. Confirm this interpretation with the master doc owner so S14 is not surprised.

---

### Cross-sprint dependencies for master reconciliation

- **S01**: rename `CiviCRMEvent` → `Event`, app-mint PKs, add `external_id`, rename `attendance` → `participants`, repoint FKs. S04 cannot start until S01 is merged. Also confirm S01 adds `participants.source` (so S04 does not need a separate migration for it).
- **S03**: `FormField`, `DataTable`, `Pagination`, `DataTable` components — S04 reuses them. If S03 has not shipped yet, S04 must either implement its own or wait. Recommend S03 ships first.
- **S03**: `ContactPickerModal` (extended from `MemberSearchModal`) — used by `POST /events/{id}/participants` flow in `EventDetailPage`. If S03 renames it, update the import.
- **S14**: consumes `participant_counts` and the `(occurrence_date, event_type, session_time)` grouping defined in S04. S14 must not re-define these; they are locked here.
- **S15**: adds `require_viewer` dependency for read-only access; S04 read endpoints currently use `require_volunteer`. After S15, switch `GET /events`, `GET /events/{id}`, `GET /events/{id}/participants` to `require_viewer_plus` (viewer + volunteer + admin) to allow dashboard-only users to see events.
- **S16**: APScheduler job file imports `SUNDAY_CRON`, `POWERHOUSE_CRON`, `generate_sunday_events`, `generate_powerhouse_event` from `backend/app/services/event_generator.py`. S16 also reads `dynamic_settings.get_sunday_series_id()` / `get_powerhouse_series_id()`.
- **S18**: notifier crons from `NOTIFIER_CRONS` dict are consumed by S18 to fire Google Chat attendance summaries after each service. S18 will query `participants` grouped by event for the summaries.
- **S22**: name-list attendance submission will call `POST /events/{id}/participants` (single-add) or the bulk endpoint (S05) with `source="name_list"`. S04's participant endpoint must accept source override only from internal service calls, not from public API input.
- **S23**: nightly derived-attribute recompute (`EOW_RECOMPUTE_CRON`) is defined in `event_generator.py` as a constant; S23 implements the actual recompute job that reads it from S16.
