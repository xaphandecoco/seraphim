# S01 — Schema Inversion & CiviCRM Excision

**Phase:** A — Foundation · **Depends on:** none · **Effort:** L · **Status:** Not started

---

## 1. Goal & rationale

Seraphim currently treats CiviCRM as source of truth: local tables `civicrm_members` / `civicrm_events` mirror CiviCRM IDs as their own PKs (no autoincrement), and a push pipeline writes confirmed attendance back to CiviCRM. This sprint inverts that relationship permanently: the app mints its own surrogate keys, retains old CiviCRM IDs as nullable `external_id` for migration traceability, renames the three core tables to domain names (`contacts`, `events`, `participants`), repoints every FK that depended on the old PKs, and deletes all CiviCRM coupling (client, push worker, sync endpoints, config keys, frontend surfaces). It also adds the canonical S23 derived-snapshot columns and the `source` enum on participants now, so those tables never need re-migrating by later sprints.

After S01, the app has no runtime dependency on CiviCRM. The database is the authoritative record; subsequent sprints add native CRUD, migration ETL, and analytics.

---

## 2. Scope

### In
- New Alembic migration: create `contacts`, `events`, `participants` tables with app-minted PKs + `external_id`.
- Drop `civicrm_members` and `civicrm_events` tables (data not preserved — migration ETL is S06; dev env is pre-data).
- Repoint all FKs: `compreface_subjects.contact_id` → `contacts.id`; `detections.event_id` → `events.id`; `logs.event_id` → `events.id`; participants FKs to new tables.
- Drop `attendance` table; rename concept to `participants` with expanded schema (status enum, source enum, role, registered_by_id).
- (per CN-03) Create the `audit_log` table + `AuditLog` SQLAlchemy model (in the `g7h8i9j0k1l2` migration, before S02 needs it). The `app/services/audit.py::record()` write helper is created by S02, NOT S01.
- Delete `push_status`, `push_attempts`, `last_push_error` columns (they were on `attendance`, which is dropped).
- Delete `logs.push_status` column.
- Delete `backend/app/services/civicrm.py` (entire `CiviCRMClient`).
- Delete `_process_civicrm_push` from `queue_manager.py`, remove `MAX_PUSH_ATTEMPTS`, remove import of `CiviCRMClient`, `CiviCRMEvent`, `CiviCRMMember` from that file.
- Remove `POST /members/sync` endpoint from `routers/members.py`; keep `GET /members` and `GET /members/attendees` but repoint to `Contact` model.
- Remove `POST /events/sync` endpoint from `routers/events.py`; repoint `list_events`, `get_active_event_id`, `set_active_event` to native `Event` model; fix error message at events.py:53.
- Remove `POST /attendance/push-preview`, `POST /attendance/push`, `GET /attendance/dead-letter`, `POST /attendance/dead-letter/{id}/retry` from `routers/attendance.py`; keep `GET /attendance` repointed to `Participant`.
- Remove CiviCRM branch from `routers/setup.py` (`test_services` and `create_setup`).
- Remove `get_civicrm_url`, `get_civicrm_api_key`, `get_civicrm_site_key` from `config.py` (`DynamicSettings`).
- Remove `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` from `schemas.py` (`SetupRequest`, `ServiceTestRequest`, `ServiceTestResponse`).
- Remove `DeadLetterRecord`, `DeadLetterListResponse`, `DeadLetterRetryResponse`, `PushDiff`, `AttendeeSummary` from `schemas.py`. Remove `push_status` field from `AttendanceRecord` (renamed to `ParticipantRecord`). Remove `push_status` field from `LogResponse`.
- Repoint `routers/analytics.py` from `CiviCRMEvent` to `Event`; remove `push_status` column from CSV exports.
- Repoint `services/face_pipeline.py`: remove `push_status="pending"` from `Attendance` insert; replace with `Participant` insert with `source="face"`, `status="attended"`.
- Repoint `services/task_service.py`: import `Contact` instead of `CiviCRMMember`; remove `push_status="pending"` from `_log_attendance`; replace with `Participant` insert with `source="face"`.
- Repoint `routers/tasks.py`: import `Contact` instead of `CiviCRMMember`.
- Repoint `workers/queue_consumer.py`: remove `civicrm_url` log line.
- Delete test files: `tests/test_civicrm.py`, `tests/test_members_sync.py`, `tests/test_setup_connectivity.py`.
- Rewrite `tests/test_attendance.py` for the new Participant schema (no push endpoints, no push columns).
- Rewrite `tests/conftest.py` fixtures: `sample_event` imports `Event`, `sample_member` imports `Contact`, `sample_attendance` becomes `sample_participant`, drop `dead_letter_attendance`. Update `sample_detection` to use `event_id` FK to new `events` table.
- Rewrite affected assertions in `tests/test_tasks_full.py`, `tests/test_task_service.py` (import `Contact` instead of `CiviCRMMember`).
- Frontend: remove CiviCRM step from `SetupPage.tsx` (step 2 CiviCRM block); rewrite `AttendancePage.tsx` to a stub (no push tab, no dead-letter tab); clean up associated TypeScript types and imports.
- `MemberSearchModal.tsx`: verify contact_id field compatibility (expected: no change needed).

### Out
- Native Contact CRUD endpoints (S03).
- Native Event CRUD endpoints (S04).
- Community-report or name-list participant sources (S22).
- Migration ETL from CiviCRM XLSX (S06).
- Zoom source (S18).
- Face enrollment first-class flow (S07).
- Biometric consent table (S08).
- Derived-attribute nightly recompute job (S23) — columns are added here but the job is S23.
- The `app/services/audit.py::record()` write helper (S02) — (per CN-03) S01 creates the `audit_log` table + `AuditLog` model only; the helper module is S02's.
- Any new frontend pages for contacts or events (S03, S04).

---

## 3. Data model changes

### 3.1 New tables

#### `contacts`
```
id            Integer    PK, autoincrement (app-minted)
external_id   Integer    UNIQUE nullable (old CiviCRM contact_id for migration traceability)
contact_type  String(50) NOT NULL default='Individual'   # Individual | Organization
contact_subtype String(100) nullable
first_name    String(255) NOT NULL
last_name     String(255) NOT NULL
nickname      String(255) nullable   # (per CN-01) core column created here; S03 does NOT re-add it
suffix        String(50)  nullable
gender        String(20)  nullable
birth_date    Date        nullable
phone         String(50)  nullable
email         String(255) nullable
street_address Text       nullable
custom_data   JSON().with_variant(JSONB,'postgresql')  NOT NULL default={}
is_deleted    Boolean     NOT NULL default=False
created_at    DateTime    NOT NULL default=utc_now
updated_at    DateTime    NOT NULL default=utc_now onupdate=utc_now

-- Derived snapshot fields (nullable; populated by S23 nightly job):
last_attended_at  DateTime  nullable
attendance_count  Integer   nullable
weeks_absent      Integer   nullable
tier              String(20) nullable   # Tier0|Tier1|Tier2|Tier3|Inactive
is_active         Boolean   nullable
is_regular        Boolean   nullable
is_connected      Boolean   nullable
```
Indexes: `ix_contacts_external_id` UNIQUE partial on `external_id WHERE external_id IS NOT NULL`; `ix_contacts_email` on `email`; `ix_contacts_last_name` on `last_name`; `ix_contacts_is_deleted` on `is_deleted`.

> **`event_series` is NOT created here.** Per the 2026-06-22 ruling (supersedes CN-27), **S04 owns `event_series` entirely** (plain `CREATE TABLE`). S01 does not create a stub — CN-16 moved the `events.recurring_series_id` FK to S04, so S01 has no consumer for the table. S04 adds both the table and the `events.recurring_series_id` FK in its own migration.

#### `events`
```
id               Integer    PK, autoincrement
external_id      Integer    UNIQUE nullable
title            String(255) NOT NULL
start_at         DateTime    nullable
end_at           DateTime    nullable
created_at       DateTime    NOT NULL default=utc_now
```
(per CN-16) `event_type`, `session_time`, `occurrence_date`, `recurring_series_id`, `is_active`, and `location` are added by S04's migration, NOT here. S01 creates only the minimal core columns above. (The canonical 6-value `event_type` vocabulary — `Sunday Celebration | Prayer Meeting | Powerhouse | Community Meeting | Conference | Event`, per CN-05 — is documented on the S04 column.)

Indexes: `ix_events_external_id` UNIQUE partial on `external_id WHERE external_id IS NOT NULL`; `ix_events_start_at` on `start_at`.

#### `participants`
```
id                 Integer    PK, autoincrement
contact_id         Integer    FK->contacts.id ON DELETE CASCADE NOT NULL
event_id           Integer    FK->events.id   ON DELETE CASCADE NOT NULL
status             String(20) NOT NULL default='attended'
                              # attended|registered|no_show|cancelled
role               String(50) nullable
source             String(30) NOT NULL default='manual'
                              # (per CN-07) canonical 8 values: face|manual|zoom|name_list|community_report|import|bulk|migration
detection_id       Integer    FK->detections.id ON DELETE SET NULL nullable
registered_by_id   Integer    FK->users.id ON DELETE SET NULL nullable
created_at         DateTime   NOT NULL default=utc_now
UNIQUE(event_id, contact_id)  -- constraint name: uq_participant_event_contact
```
Indexes: `ix_participants_contact_id`; `ix_participants_event_id`; `ix_participants_source`.

#### `audit_log` (per CN-03 — created by S01, before S02 needs it)
S01 creates the `audit_log` table AND the `AuditLog` SQLAlchemy model. The
`app/services/audit.py::record()` write helper is created by **S02**, not S01.
```
id          Integer    PK, autoincrement
actor_id    Integer    FK->users.id ON DELETE SET NULL nullable   # NULL for system/API-key actors
action      String(100) NOT NULL    # e.g. contact.create, contact_merge, import.run
entity      String(100) NOT NULL
entity_id   Integer    nullable
before      JSON().with_variant(JSONB,'postgresql')  nullable
after       JSON().with_variant(JSONB,'postgresql')  nullable
created_at  DateTime   NOT NULL default=utc_now
```
Indexes: `ix_audit_log_entity_entity_id` on `(entity, entity_id)`; `ix_audit_log_created_at` on `(created_at)`.

### 3.2 Modified tables

#### `compreface_subjects`
- Column `contact_id`: FK changes from `ForeignKey("civicrm_members.contact_id")` (models.py:94) to `ForeignKey("contacts.id", ondelete="SET NULL")`.
- Column type and nullability unchanged (Integer, nullable=True).

#### `detections`
- Column `event_id`: FK changes from `ForeignKey("civicrm_events.event_id", ondelete="SET NULL")` (models.py:124) to `ForeignKey("events.id", ondelete="SET NULL")`.

#### `logs`
- Column `event_id`: FK changes from `ForeignKey("civicrm_events.event_id", ondelete="SET NULL")` (models.py:242) to `ForeignKey("events.id", ondelete="SET NULL")`.
- **Drop column** `push_status` (String(20), nullable) (models.py:244).

### 3.3 Dropped tables
- `civicrm_members` (rows discarded; dev environment is pre-data).
- `civicrm_events` (rows discarded).
- `attendance` (replaced by `participants`; rows discarded).

### 3.4 Alembic migration

**File**: `backend/alembic/versions/g7h8i9j0k1l2_schema_inversion_civicrm_excision.py`
**Revision**: `g7h8i9j0k1l2`
**Down revision**: `f3a4b5c6d7e8` (current HEAD — task-action approval unique index)

**`upgrade()` execution order** (FK DAG order; SQLite requires `batch_alter_table` for FK changes):

```python
# Step 1: (event_series is created by S04, not here — supersedes CN-27)

# Step 2: Create contacts (no deps)
op.create_table('contacts', ...)
# Partial unique index (both dialects):
op.create_index('ix_contacts_external_id', 'contacts', ['external_id'], unique=True,
    postgresql_where=text('external_id IS NOT NULL'),
    sqlite_where=text('external_id IS NOT NULL'))
op.create_index('ix_contacts_email', 'contacts', ['email'])
op.create_index('ix_contacts_last_name', 'contacts', ['last_name'])
op.create_index('ix_contacts_is_deleted', 'contacts', ['is_deleted'])

# Step 3: Create events (minimal core only, per CN-16; no event_series FK here — S04 adds it)
op.create_table('events', ...)
op.create_index('ix_events_external_id', 'events', ['external_id'], unique=True,
    postgresql_where=text('external_id IS NOT NULL'),
    sqlite_where=text('external_id IS NOT NULL'))
op.create_index('ix_events_start_at', 'events', ['start_at'])

# Step 4: Drop attendance table (had FKs to both old tables)
op.drop_table('attendance')

# Step 5: Repoint FKs — use batch_alter_table (safe on both dialects)
# (per CN-28) Before repointing, STASH the old CiviCRM ids into transitional
# columns so S24 can remap face/detection links via contacts.external_id /
# events.external_id after the legacy tables are dropped. S24 consumes and drops
# these columns at the end of its migration.
# 5a. compreface_subjects.contact_id
with op.batch_alter_table('compreface_subjects', recreate='always') as batch:
    batch.add_column(sa.Column('_legacy_civicrm_contact_id', sa.Integer(), nullable=True))
    batch.drop_constraint('compreface_subjects_contact_id_fkey', type_='foreignkey')
    batch.create_foreign_key(
        'fk_compreface_subjects_contact_id', 'contacts', ['contact_id'], ['id'],
        ondelete='SET NULL')
op.execute('UPDATE compreface_subjects SET _legacy_civicrm_contact_id = contact_id')
# 5b. detections.event_id
with op.batch_alter_table('detections', recreate='always') as batch:
    batch.add_column(sa.Column('_legacy_civicrm_event_id', sa.Integer(), nullable=True))
    batch.drop_constraint('detections_event_id_fkey', type_='foreignkey')
    batch.create_foreign_key(
        'fk_detections_event_id', 'events', ['event_id'], ['id'], ondelete='SET NULL')
op.execute('UPDATE detections SET _legacy_civicrm_event_id = event_id')
# 5c. logs.event_id + drop push_status
with op.batch_alter_table('logs', recreate='always') as batch:
    batch.drop_constraint('logs_event_id_fkey', type_='foreignkey')
    batch.create_foreign_key(
        'fk_logs_event_id', 'events', ['event_id'], ['id'], ondelete='SET NULL')
    batch.drop_column('push_status')

# Step 6: Drop legacy tables (no remaining FKs pointing to them)
op.drop_table('civicrm_events')
op.drop_table('civicrm_members')

# Step 7: Create participants
op.create_table('participants', ...)
op.create_index('ix_participants_contact_id', 'participants', ['contact_id'])
op.create_index('ix_participants_event_id', 'participants', ['event_id'])
op.create_index('ix_participants_source', 'participants', ['source'])

# Step 8: Create audit_log (per CN-03 — S01 owns the table; S02 adds the
#         record() helper). Depends on users (actor_id FK).
op.create_table('audit_log', ...)  # columns per section 3.1 audit_log
op.create_index('ix_audit_log_entity_entity_id', 'audit_log', ['entity', 'entity_id'])
op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'])
```

**`downgrade()` order** (reverse): drop `audit_log` (per CN-03); drop `participants`; recreate `civicrm_members`, `civicrm_events`, `attendance` (empty schema); restore old FKs on `compreface_subjects`, `detections`, `logs`; drop the `_legacy_civicrm_contact_id`/`_legacy_civicrm_event_id` stash columns; restore `push_status` column on `logs`; drop `contacts`, `events`. (Does NOT drop `event_series` — S01 never creates it; S04 owns it.)

### 3.5 No data backfill
Development environment; no production data. Migration ETL from CiviCRM is S06. On upgrade, existing `compreface_subjects.contact_id` / `detections.event_id` values point at soon-to-be-dropped CiviCRM ids; the old ids are preserved in the `_legacy_civicrm_*` stash columns (per CN-28) so **S24** can remap them to the new app-minted ids via `external_id` after S06 runs. See section 10 and S24.

---

## 4. Backend

### 4.1 Endpoints table

| METHOD | Path | Role | Request | Response | Notes |
|--------|------|------|---------|----------|-------|
| GET | /members | volunteer | `?search=&limit=` | `list[ContactResponse]` | Repointed to Contact; search on first_name, last_name, email |
| GET | /members/attendees | volunteer | `?search=&limit=` | `list[AttendeeResponse]` | Repointed to Contact+ComprefaceSubject join |
| GET | /events | volunteer | — | `list[EventResponse]` | Repointed to Event; ordered by start_at desc |
| GET | /events/active-event-id | volunteer | — | `{active_event_id: int\|null}` | Unchanged |
| POST | /events/set-active | admin | `?event_id=int\|null` | `{active_event_id, event_title, message}` | db.get(Event, event_id); error message no longer mentions CiviCRM |
| GET | /attendance | volunteer | `?event_id=&date=&status=&limit=&offset=` | `list[ParticipantRecord]` | Was Attendance; now Participant; no push_status in response |
| POST | /setup/test-services | (pre-setup) | `ServiceTestRequest` (compreface fields only) | `ServiceTestResponse` (compreface fields only) | CiviCRM branch removed; returns 410 post-setup |
| POST | /setup | (pre-setup) | `SetupRequest` (no civicrm fields) | `{message, admin_email, setup_locked}` | civicrm_* not written to admin_settings |
| GET | /analytics/attendance-by-event | admin | — | `list[{id, title, start_at, count}]` | Repointed to Event; response key is `id` not `event_id` |
| GET | /analytics/export/attendance | admin | `?event_id=` | CSV | Column: `source` replaces `push_status` |
| GET | /analytics/export/logs | admin | — | CSV | `push_status` column removed |

**Deleted endpoints (respond 404 after this sprint):**
- `POST /members/sync`
- `POST /events/sync`
- `POST /attendance/push-preview`
- `POST /attendance/push`
- `GET /attendance/dead-letter`
- `POST /attendance/dead-letter/{id}/retry`

### 4.2 Services / workers / business rules

#### `backend/app/services/civicrm.py` — **DELETE ENTIRE FILE**
Contains only `CiviCRMClient` and `_is_transient_error`. No file may import from it after this sprint.

#### `backend/app/services/queue_manager.py` — MODIFY
- Remove line 14: `from app.services.civicrm import CiviCRMClient`.
- Remove from models import line 12: `CiviCRMEvent, CiviCRMMember`.
- Remove class attribute `MAX_PUSH_ATTEMPTS: int = 3` (line 51).
- Remove entire method `_process_civicrm_push` (lines 53–161).
- Remove call `worked |= await self._process_civicrm_push()` from `run` (line 38).
- Remaining jobs `_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup` are unchanged.

#### `backend/app/workers/queue_consumer.py` — MODIFY
- Remove line 24: `logger.info("Queue consumer: settings initialized (civicrm_url=%r)", dynamic_settings.get_civicrm_url())`.
- Replace with: `logger.info("Queue consumer: settings initialized")`.

#### `backend/app/services/face_pipeline.py` — MODIFY
- Line 12: change `from app.models import Attendance, ...` to import `Participant` instead of `Attendance`.
- Tier-100 auto-log block (lines 127–146): replace `Attendance(...)` with:
  ```python
  participant = Participant(
      contact_id=member_id,
      event_id=event_id,
      detection_id=detection.id,
      status="attended",
      source="face",
  )
  session.add(participant)
  ```
- The existing-check query (lines 132–138): change model references from `Attendance` to `Participant`.
- Remove `push_status="pending"` — field does not exist on `Participant`.
- Docstring update: remove "CiviCRM member" reference.

#### `backend/app/services/task_service.py` — MODIFY
- Line 12: change import `CiviCRMMember` → `Contact`.
- `_require_member` (line 121): `self.session.get(CiviCRMMember, member_id)` → `self.session.get(Contact, member_id)`. Docstring line 120: remove "CiviCRM".
- `_log_attendance` (lines 364–408): replace `Attendance(...)` insert with `Participant(contact_id=member_id, event_id=detection.event_id, detection_id=task.detection_id, status="attended", source="face")`. Remove `push_status="pending"`. The existing `if member_id` guard at task_service.py:382–386 must be maintained — never insert a Participant with `contact_id=None`.

#### `backend/app/routers/members.py` — MODIFY
- Remove line 13: `from app.services.civicrm import CiviCRMClient`.
- Line 11: change `CiviCRMMember` → `Contact`.
- `search_members`: `select(CiviCRMMember)` → `select(Contact)`; filter on `Contact.first_name`, `Contact.last_name`, `Contact.email`.
- `list_attendees`: join `Contact` to `ComprefaceSubject` on `Contact.id == ComprefaceSubject.contact_id`; `AttendeeResponse(contact_id=member.id, ...)`.
- **Delete** `sync_members` function and its router decorator (lines 118–162).
- `MemberResponse.contact_id` mapped from `Contact.id` (compatibility alias preserved for S03).

#### `backend/app/routers/events.py` — MODIFY
- Remove line 12: `from app.services.civicrm import CiviCRMClient`.
- Line 10: change `CiviCRMEvent` → `Event`.
- `list_events`: `select(Event).order_by(Event.start_at.desc())`.
- `set_active_event`: `db.get(Event, event_id)` (lines 49, 71); error message at line 53 → `"Event {event_id} not found."`.
- **Delete** `sync_events` function (lines 80–141).

#### `backend/app/routers/attendance.py` — MODIFY
- Line 11: remove `CiviCRMEvent, CiviCRMMember`; import `Participant` instead of `Attendance`.
- Line 12: remove schema imports `AttendeeSummary, DeadLetterListResponse, DeadLetterRecord, DeadLetterRetryResponse, PushDiff`; import `ParticipantRecord`.
- Keep `list_attendance` but repoint: `select(Participant)`, filter `Participant.event_id`, `Participant.status`, `Participant.created_at`.
- **Delete** functions: `push_preview` (lines 57–117), `push_attendance` (lines 120–154), `list_dead_letter` (lines 162–192), `retry_dead_letter` (lines 195–243).

#### `backend/app/routers/tasks.py` — MODIFY
- Line 11: `CiviCRMMember` → `Contact`.
- Name-resolution helper (lines 24–28): `db.get(CiviCRMMember, contact_id)` → `db.get(Contact, contact_id)`.

#### `backend/app/routers/analytics.py` — MODIFY
- Line 14: `CiviCRMEvent` → `Event`; `Attendance` → `Participant`.
- `attendance_by_event`: repoint join `Participant.event_id == Event.id`; use `Event.start_at`; response key `event_id` → `id`.
- `export_attendance_csv` (line 119): select from `Participant`; CSV headers: `["id","contact_id","event_id","status","source","created_at"]`; rows include `r.source` not `r.push_status`.
- `export_logs_csv` (line 157): remove `Log.push_status` from select and CSV header/rows.

#### `backend/app/routers/setup.py` — MODIFY
- `test_services` (lines 115–134): remove `if req.civicrm_url:` block; remove `civicrm_ok`, `civicrm_msg` variables; return `ServiceTestResponse(compreface_ok=..., compreface_message=...)` only.
- `create_setup` (lines 192–194): remove `"civicrm_url"`, `"civicrm_api_key"`, `"civicrm_site_key"` from `settings_data`. Lines 216–225: remove `"civicrm_api_key"` and `"civicrm_site_key"` from the `sensitive` tuple.

#### `backend/app/config.py` — MODIFY
- Remove methods from `DynamicSettings`: `get_civicrm_url` (lines 100–101), `get_civicrm_api_key` (lines 103–104), `get_civicrm_site_key` (lines 106–107).

#### `backend/app/schemas.py` — MODIFY
- `ServiceTestRequest` (line 98): remove `civicrm_url: Optional[str] = None`.
- `ServiceTestResponse` (line 106): remove `civicrm_ok: Optional[bool] = None` and `civicrm_message: str = ""`.
- `SetupRequest` (lines 214–216): remove `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` fields.
- **Delete** classes: `AttendeeSummary`, `PushDiff`, `DeadLetterRecord`, `DeadLetterListResponse`, `DeadLetterRetryResponse` (lines 334–346, 389–419).
- **Rename** `AttendanceRecord` → `ParticipantRecord`: remove `push_status: str` field; add `source: str` field.
- `LogResponse` (line 262): remove `push_status: Optional[str] = None`.
- `EventResponse` (line 354): `event_id: int` → `id: int`; `start_date: datetime` → `start_at: Optional[datetime] = None`; `end_date` → `end_at: Optional[datetime] = None`. (per CN-16) do NOT add `event_type` here — the `events.event_type` column is created by S04, so S04 adds the `event_type` field to `EventResponse` then.
- `MemberResponse` (line 366): keep `contact_id: int` (mapped from `Contact.id`; compatibility alias for S03).
- `AttendeeResponse` (line 374): keep `contact_id: int` (mapped from `Contact.id`).

#### `backend/app/models.py` — MODIFY (all changes listed in section 3)

**Replace** `CiviCRMMember` class (lines 66–74) with `Contact` class as specified in section 3.1.
**Do NOT add** an `EventSeries` model here — S04 owns the `event_series` table and model (supersedes CN-27).
**Replace** `CiviCRMEvent` class (lines 77–84) with `Event` class (minimal core columns only, per CN-16) as specified in section 3.1.
**Replace** `Attendance` class (lines 187–214) with `Participant` class as specified in section 3.1.
**Update** `ComprefaceSubject.contact_id` FK (line 94) as specified in section 3.2.
**Update** `Detection.event_id` FK (line 124) as specified in section 3.2.
**Update** `Log.event_id` FK (line 242) and **drop** `Log.push_status` column (line 244) as specified in section 3.2.
**Add** `AuditLog` class as specified in section 3.1 (per CN-03 — table + model owned by S01; the `app/services/audit.py::record()` helper is S02's).

Also add `text` to imports if not already present (needed for partial index `sqlite_where`/`postgresql_where`): `from sqlalchemy import ..., text`.

#### `backend/app/routers/audit.py` — VERIFY ONLY (grill 2026-06-22: no change needed)
Confirmed: `audit.py` does NOT import `CiviCRMMember`. `_extract_contact_id()` (line 27) parses the `member:{id}` string and returns a bare int; the `matched_name = f"member:{id}"` format (line 183) is database-agnostic and still valid (the id is now `contacts.id`). No code change required — just confirm during the global `CiviCRMMember` grep that no hits remain here.

### 4.3 File-by-file table

| Action | File |
|--------|------|
| DELETE | `backend/app/services/civicrm.py` |
| MODIFY | `backend/app/models.py` |
| MODIFY | `backend/app/schemas.py` |
| MODIFY | `backend/app/config.py` |
| MODIFY | `backend/app/routers/members.py` |
| MODIFY | `backend/app/routers/events.py` |
| MODIFY | `backend/app/routers/attendance.py` |
| MODIFY | `backend/app/routers/setup.py` |
| MODIFY | `backend/app/routers/tasks.py` |
| MODIFY | `backend/app/routers/analytics.py` |
| VERIFY/MODIFY | `backend/app/routers/audit.py` |
| MODIFY | `backend/app/services/queue_manager.py` |
| MODIFY | `backend/app/services/face_pipeline.py` |
| MODIFY | `backend/app/services/task_service.py` |
| MODIFY | `backend/app/workers/queue_consumer.py` |
| CREATE | `backend/alembic/versions/g7h8i9j0k1l2_schema_inversion_civicrm_excision.py` |

---

## 5. Frontend

### 5.1 Pages / routes / components

#### `frontend/src/pages/SetupPage.tsx` — MODIFY
- **Remove** the "CiviCRM Integration" card block (lines 539–575): the card with `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` inputs.
- Remove from `form` state (line 38–46): `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` fields.
- Remove from `runServiceTest` payload (lines 126–127): `civicrm_url` key.
- Remove `serviceTestResult.civicrm_ok` / `civicrm_message` display block from service test result panel (lines 599–612).
- Remove `civicrm_ok` / `civicrm_message` from the local `serviceTestResult` state type annotation.
- Remove `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` from the `handleSubmit` POST body (lines 174–175).
- Step 2 label stays "Services"; its content is now CompreFace only.
- Confirm review step (step 5): remove any CiviCRM summary rows.

#### `frontend/src/pages/AttendancePage.tsx` — REWRITE TO STUB
The entire file is CiviCRM push functionality. Replace with a minimal placeholder that the `/attendance` route can serve until S04/S05 implement the native participant grid:

```tsx
import { ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { EmptyState } from '@/components/ui/StateViews';

export function AttendancePage() {
  const navigate = useNavigate();
  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/settings')}
            aria-label="Back"
            className="text-foreground/50 hover:text-foreground"
          >
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h1 className="text-lg font-bold text-foreground">Attendance</h1>
        </div>
      </header>
      <main className="flex flex-1 items-center justify-center px-4">
        <EmptyState
          title="Participant management coming soon"
          description="Use the Event detail page to view and manage attendance records."
        />
      </main>
    </div>
  );
}
```

The route `/attendance` remains registered in `App.tsx`. No query keys, no API calls.

#### `frontend/src/types/index.ts` — MODIFY (grill 2026-06-22: confirmed gap)
The shared `ChurchEvent` interface (lines 41–45) is the canonical event type consumed by `EventsPage.tsx`. Update it to match the renamed `EventResponse`:
- `event_id: number` → `id: number`
- `start_date: string` → `start_at: string` (keep nullable semantics — make it `start_at?: string` to match the now-nullable column)
- `end_date?: string` → `end_at?: string`
Do NOT add `event_type` to `ChurchEvent` here — that field arrives with S04 (per CN-16). `MemberSearchModal`'s `Member` type is unaffected (it uses `contact_id`, kept as a compatibility alias).

#### `frontend/src/pages/EventsPage.tsx` — MODIFY (grill 2026-06-22: confirmed 6 refs)
Update all references to the renamed fields:
- `event.event_id` → `event.id` (lines 88, 116, 119, 151)
- `event.start_date` → `event.start_at` (line 139)
- `event.end_date` → `event.end_at` (line 142)
No local interface in this file — it relies on `ChurchEvent` from `@/types` (updated above). `AttendancePage.tsx`'s former use of these fields disappears when it becomes a stub.

#### `frontend/src/components/tasks/MemberSearchModal.tsx` — VERIFY
The modal reads `contact_id` from `GET /members/attendees` response. Since `AttendeeResponse.contact_id` is kept as a compatibility alias mapping `Contact.id`, no TypeScript change should be needed. Verify; document if no change is made.

### 5.2 TanStack Query keys affected
- `['dead-letter']` — removed from `AttendancePage` (stub has no queries).
- `['events-list']` — removed from `AttendancePage`; will be reintroduced as `['events']` in S04.

### 5.3 Role gating
No changes to role gating. Removed endpoints were admin-only; the stub `AttendancePage` requires volunteer auth (via `ProtectedRoute` in `App.tsx`) but shows no sensitive data.

### 5.4 UX states
- `AttendancePage` stub: only `EmptyState` (from `StateViews.tsx`; component already exists).
- `SetupPage`: step 2 is simpler; no new loading/error states needed.

### 5.5 File-by-file table

| Action | File |
|--------|------|
| MODIFY | `frontend/src/pages/SetupPage.tsx` |
| REWRITE (stub) | `frontend/src/pages/AttendancePage.tsx` |
| MODIFY | `frontend/src/types/index.ts` (ChurchEvent: event_id→id, start_date→start_at, end_date→end_at) |
| MODIFY | `frontend/src/pages/EventsPage.tsx` (6 field refs) |
| VERIFY (no change) | `frontend/src/components/tasks/MemberSearchModal.tsx` |

---

## 6. Migration / data

Not applicable. Development environment; no production data in `civicrm_members`, `civicrm_events`, or `attendance`. All tables are dropped and recreated empty. Production data migration from CiviCRM XLSX exports is Sprint S06.

**Known post-upgrade state**: `compreface_subjects` rows (if any exist from prior dev seeding) will have `contact_id = NULL` after the FK retarget because the referenced CiviCRM IDs no longer exist in any table. Sprint S07 re-establishes face↔contact links via the enrollment flow.

---

## 7. Acceptance criteria

1. `alembic upgrade head` completes without error on PostgreSQL (Alembic test target) and SQLite (CI test suite).
2. `alembic downgrade -1` restores the prior schema without error.
3. Tables `civicrm_members`, `civicrm_events`, `attendance` do not exist after upgrade.
4. Tables `contacts`, `events`, `participants` exist with all specified columns. (`event_series` is created by S04, not S01.)
5. `contacts.external_id` has a partial UNIQUE index allowing multiple NULL values.
6. `compreface_subjects.contact_id` FK references `contacts.id` (verified via `PRAGMA foreign_key_list` / `information_schema`).
7. `detections.event_id` FK references `events.id`.
8. `logs.event_id` FK references `events.id`; column `logs.push_status` does not exist.
9. File `backend/app/services/civicrm.py` does not exist.
10. `queue_manager.py` does not import `CiviCRMClient`, does not define `_process_civicrm_push` or `MAX_PUSH_ATTEMPTS`.
11. `POST /members/sync` → 404.
12. `POST /events/sync` → 404.
13. `POST /attendance/push-preview` → 404.
14. `POST /attendance/push` → 404.
15. `GET /attendance/dead-letter` → 404.
16. `GET /events` → 200; items have `id` and `start_at` fields (not `event_id` / `start_date`).
17. `POST /events/set-active?event_id=99999` → 404 with detail `"Event 99999 not found."` (no "CiviCRM" in message).
18. `POST /setup/test-services {"compreface_url":"http://x"}` → 200; response body has no `civicrm_ok` or `civicrm_message` keys.
19. `POST /setup` without `civicrm_url`/`civicrm_api_key`/`civicrm_site_key` → 201 with no validation error.
20. `GET /attendance` → 200; items have `source` field; items do not have `push_status` field.
21. Face pipeline tier-100 detection creates a `Participant` row with `source="face"`, `status="attended"` in a test with an active event.
22. Task service resolving a task creates a `Participant` row with `source="face"` — no FK error.
23. `GET /analytics/export/attendance` CSV headers include `source`; do not include `push_status`.
24. `SetupPage` step 2 contains no element with text "CiviCRM" (verified via test or manual check).
25. `AttendancePage` renders the "Participant management coming soon" empty state; no push button, no dead-letter tab.
26. `pytest tests/ -q` passes (all surviving tests green).
27. `npm run test:run` passes.
28. `npm run build` and `npm run lint` pass.
29. `ruff check app` passes.

---

## 8. Test plan

### 8.1 Delete these test files
- `backend/tests/test_civicrm.py` — tests `CiviCRMClient._call`, `sync_members`, `sync_events`, `push_attendance`, retry logic.
- `backend/tests/test_members_sync.py` — tests `POST /members/sync` (auth matrix + business cases).
- `backend/tests/test_setup_connectivity.py` — tests CiviCRM probe in `/setup/test-services`.

### 8.2 Rewrite `backend/tests/test_attendance.py` → rename to `backend/tests/test_participants.py`

Remove all push/dead-letter test cases. Rewrite to cover `GET /attendance` repointed to Participant:

```python
# test_participants.py

async def test_list_participants_returns_records(
    client, sample_participant, volunteer_auth_headers):
    resp = await client.get("/attendance", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1

async def test_list_participants_filter_by_event_id(
    client, sample_participant, volunteer_auth_headers):
    resp = await client.get(
        f"/attendance?event_id={sample_participant.event_id}",
        headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert all(r["event_id"] == sample_participant.event_id for r in resp.json())

async def test_list_participants_filter_by_status(
    client, sample_participant, volunteer_auth_headers):
    resp = await client.get("/attendance?status=attended", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert all(r["status"] == "attended" for r in resp.json())

async def test_list_participants_invalid_date_returns_400(client, volunteer_auth_headers):
    resp = await client.get("/attendance?date=not-a-date", headers=volunteer_auth_headers)
    assert resp.status_code == 400
    assert "Invalid date format" in resp.json()["detail"]

async def test_list_participants_requires_auth(client):
    resp = await client.get("/attendance")
    assert resp.status_code == 401

async def test_participant_record_has_source_not_push_status(
    client, sample_participant, volunteer_auth_headers):
    resp = await client.get("/attendance", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    item = resp.json()[0]
    assert "source" in item
    assert "push_status" not in item

async def test_push_preview_endpoint_removed(client, admin_auth_headers, sample_event):
    resp = await client.post(
        f"/attendance/push-preview?event_id={sample_event.id}",
        headers=admin_auth_headers)
    assert resp.status_code in (404, 405)

async def test_push_endpoint_removed(client, admin_auth_headers, sample_event):
    resp = await client.post(
        f"/attendance/push?event_id={sample_event.id}", headers=admin_auth_headers)
    assert resp.status_code in (404, 405)

async def test_dead_letter_endpoint_removed(client, admin_auth_headers):
    resp = await client.get("/attendance/dead-letter", headers=admin_auth_headers)
    assert resp.status_code in (404, 405)
```

### 8.3 Rewrite affected `conftest.py` fixtures

```python
# Replace sample_event
# (per CN-16) the S01 Event model has only id/external_id/title/start_at/end_at/
# created_at — do NOT pass event_type, session_time, or is_active here (those
# columns/fields land in S04). Fixtures/tests below that pass them must drop them.
@pytest_asyncio.fixture
async def sample_event(db_session):
    from app.models import Event
    event = Event(
        title="Sunday Service",
        start_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)
    return event

# Replace sample_member
@pytest_asyncio.fixture
async def sample_contact(db_session):
    from app.models import Contact
    contact = Contact(
        first_name="Juan",
        last_name="dela Cruz",
        email="juan@lightnc.org",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact

# Backwards-compat alias
sample_member = sample_contact

# Update sample_detection: use sample_event.id (not sample_event.event_id)
@pytest_asyncio.fixture
async def sample_detection(db_session, sample_camera, sample_event):
    from app.models import Detection
    detection = Detection(
        camera_id=sample_camera.id,
        image_path="/data/faces/test.jpg",
        confidence=0.95,
        tier="91-99",
        status="tasked",
        matched_name="Juan dela Cruz",
        event_id=sample_event.id,   # was sample_event.event_id
    )
    db_session.add(detection)
    await db_session.commit()
    await db_session.refresh(detection)
    return detection

# Replace sample_attendance with sample_participant
@pytest_asyncio.fixture
async def sample_participant(db_session, sample_contact, sample_event, sample_detection):
    from app.models import Participant
    record = Participant(
        contact_id=sample_contact.id,
        event_id=sample_event.id,
        detection_id=sample_detection.id,
        status="attended",
        source="face",
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record

# Backwards-compat alias (for tests that still reference sample_attendance)
sample_attendance = sample_participant

# DROP dead_letter_attendance fixture (table/concept no longer exists)
```

### 8.4 Update `tests/test_tasks_full.py` and `tests/test_task_service.py`

- Line 97 (`test_tasks_full.py`), line 97 (`test_task_service.py`): `from app.models import CiviCRMMember` → `from app.models import Contact`.
- `CiviCRMMember(contact_id=contact_id, ...)` → `Contact(first_name="Test", last_name="User", contact_type="Individual")`. Do not pass `id` explicitly (autoincrement). Retrieve the assigned id via `db_session.refresh(contact); contact.id`.
- Any assertion on `push_status` of a resolved attendance row → assert on `Participant.source == "face"` instead.

### 8.5 Add `tests/test_setup_no_civicrm.py`

```python
async def test_setup_test_services_response_has_no_civicrm_keys(
    client, bootstrap_absent):
    resp = await client.post(
        "/setup/test-services", json={"compreface_url": "http://x"})
    assert resp.status_code == 200
    body = resp.json()
    assert "civicrm_ok" not in body
    assert "civicrm_message" not in body
    assert "compreface_ok" in body

async def test_setup_create_without_civicrm_fields_succeeds(
    client, bootstrap_absent):
    payload = {
        "database_url": "sqlite+aiosqlite:///./ci_test.db",
        "redis_url": "redis://localhost/0",
        "compreface_url": "http://compreface:8080",
        "admin_email": "admin@lightnc.org",
        "admin_password": "TestAdmin1!secure",
        "admin_name": "Admin",
    }
    resp = await client.post("/setup", json=payload)
    assert resp.status_code in (200, 201)
```

### 8.6 Frontend — update `SetupPage.test.tsx`
Assert no element with text "CiviCRM" renders on step 2. Assert service test result panel does not render a "CiviCRM" row.

---

## 9. Rollout / rollback / risks

### Rollout sequence (dev environment only)
1. Code changes and migration file committed.
2. `alembic upgrade head` on fresh Postgres dev DB.
3. `pytest tests/ -q` — all green.
4. `npm run build && npm run lint && npm run test:run`.
5. Manual smoke: setup wizard (CompreFace only on step 2); create a test Event; set active; trigger a face detection; confirm `Participant` row with `source="face"`.

### Rollback
- `alembic downgrade -1` restores previous schema (dev environment; no production data at risk).
- Git revert of all code changes; `npm run build`.

### Risks

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| SQLite FK constraint names differ from Postgres; `batch_alter_table` constraint drop fails | Medium | Use `recreate='always'` in batch_alter; test migration under both dialects in CI |
| `test_tasks_full.py` / `test_task_service.py` hardcode numeric `contact_id` that becomes autoincrement | Medium | Replace with auto-id Contact; use `db_session.refresh` to obtain real id |
| `audit.py` imports `CiviCRMMember` (not fully read during spec draft) | Low-Medium | Implementer must grep all `*.py` for `CiviCRMMember` after the model rename; fix all hits |
| `analytics.py` uses `CiviCRMEvent.start_date`; field renamed to `Event.start_at` | Medium | Covered in section 4.2; verify via test |
| `EventsPage.tsx` or other frontend pages reference `event.event_id` / `event.start_date` | Low-Medium | Read file before editing; fix all occurrences |
| `face_pipeline.py` dedup-check references old `Attendance` model | Low | Grepped; the dedup check uses `Attendance.contact_id`/`Attendance.event_id` (lines 132–138) — must be repointed to `Participant` |
| `SchemaCache` or reflection in Alembic env might cache old schema metadata | Low | Run `alembic upgrade head` on a clean database in CI |

---

## 10. Open questions & pending owner artifacts

1. **`SetupRequest` extra fields policy** — RESOLVED (grill 2026-06-22): use Pydantic v2 default `extra='ignore'`. A client POSTing a legacy `civicrm_url` is silently ignored (not 422), for graceful forward-compat with any existing bootstrap scripts. No explicit `model_config` change needed unless the model currently sets `extra='forbid'` — implementer confirms and leaves default.

2. **`EventResponse.id` vs `event_id` field naming** — RESOLVED (grill 2026-06-22): **clean rename to `id` / `start_at` / `end_at`; NO compatibility alias.** Blast radius verified small — only `ChurchEvent` in `frontend/src/types/index.ts` and `EventsPage.tsx` consume these fields, and `AttendancePage.tsx` (the other consumer) becomes a stub this sprint. Both are updated in §5. Carrying an `event_id` alias would just create debt for S04 to unwind, so do the clean rename now.

3. **`compreface_subjects.contact_id` orphan handling** — RESOLVED (per CN-28). Instead of NULLing stale ids, S01 stashes them in `_legacy_civicrm_contact_id` / `_legacy_civicrm_event_id` during the FK repoint (Step 5). **S24** remaps them to the new app-minted ids via `external_id` after S06's live import, then drops the stash columns. The CompreFace instance persists across cutover, so existing enrolled faces keep working. See S24.

4. **`audit.py` CiviCRM references**: Not read in full during spec drafting. The backend.md reconnaissance (section E) notes that `audit.py:27` resolves `member:{id}` strings and likely references `CiviCRMMember`. The implementer must run `grep -r CiviCRMMember backend/` after all other changes and fix any remaining hits.

### Cross-sprint dependencies / shared-model touchpoints

- **S03** Contact CRUD: builds on `Contact` model added here. Will rename `MemberResponse.contact_id` to `id` and add full contact schemas. Do not change the `Contact` model after S01 without a new Alembic migration.
- **S04** Event CRUD: builds on the `Event` model (S01 creates only its minimal core columns, per CN-16). S04 adds the remaining `events` columns AND creates the `event_series` table + model in full (supersedes CN-27) — S01 does not touch `event_series`.
- **S05** Bulk Participants: the `Participant` table and `UNIQUE(event_id, contact_id)` constraint are the foundation for S05's set-based bulk-insert.
- **S06** Migration ETL: uses `Contact.external_id` and `Event.external_id` to link imported rows to native IDs.
- **S07** Face Enrollment: relies on `ComprefaceSubject.contact_id → contacts.id` FK introduced here.
- **S22** Name-List Attendance: will insert `Participant` rows with `source="name_list"` or `source="community_report"` — both values are already in the `source` enum column default definition here.
- **S23** Derived Attributes: the snapshot columns on `Contact` (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) are added here as nullable; the nightly recompute job is S23's responsibility.
- **S15** RBAC: extends `users.role` enum. `User` model not changed in S01. No conflict.
- **S17** Outbox / Rules Engine: the outbox/retry pattern is NOT reintroduced in S01. S17 creates `outbox` from scratch. The excised push pipeline code is not reused.
- **00-MASTER**: update all references from `Attendance` → `Participant`, `CiviCRMMember` → `Contact`, `CiviCRMEvent` → `Event`, `push_status` → (removed), `sync` endpoints → (removed).

