# S16 — Settings, System Status & Scheduled Jobs
**Phase:** F — Access & ops · **Depends on:** S01 (contacts/events/participants schema; audit_log; roles), S04 (event_series; cadence constants in event_generator.py; generate_sunday_events/generate_powerhouse_event), S23 (recompute_member_status service; job_runs writer) · **Effort:** L · **Status:** Not started

> **Reality check (real code, today):** The existing `backend/app/routers/settings.py` handles `GET/PUT /settings` and `POST /settings/safe-mode` (all `require_admin`). `backend/app/routers/health.py` exposes `GET /health` and `GET /health/queue`. There is **no APScheduler, no `job_runs` table, no scheduler process, no scheduled jobs, and no system-status or config-checklist page** beyond the existing `/health` endpoint. The worker process (`backend/app/workers/queue_consumer.py`) runs a `QueueManager` loop with four jobs — `_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup` (real), `_process_civicrm_push` (to be deleted by S01). APScheduler cron lives **inside the main FastAPI app lifespan** (single-instance; no distributed lock needed). The `job_runs` table does not exist yet; S16 creates it. All cron cadences are taken verbatim from `docs/crm-research/n8n/fKkPUolayRyZrjao.json` and canonicalised in `docs/crm-research/n8n-analysis.md §3`.

> **S04 dependency:** `backend/app/services/event_generator.py` (created by S04) must already define `SUNDAY_CRON`, `POWERHOUSE_CRON`, `generate_sunday_events(series_id, db)`, and `generate_powerhouse_event(series_id, db)`. S16 imports those symbols and registers them with APScheduler. If S16 merges before S04, gate the Sunday/Powerhouse jobs behind a `try/import` guard and document the gap.

> **S23 dependency:** `backend/app/services/member_status_service.py` (created by S23) must expose `async def recompute_all_contacts(db) -> dict`. S16 provides the APScheduler host; S23 registers its jobs via the `register_jobs(scheduler)` hook defined here. If S16 merges before S23, the EOW/EOM slots are registered as no-op stubs that write a `job_runs` row with `status="skipped"`.

> **S02 dependency (Custom Field Admin):** S16 surfaces S02's custom-field admin UI (`CustomFieldGroupList`, `CustomFieldDefList`) under the Settings page (new tab "Custom Fields"). S16 does not alter S02's backend; it only adds a frontend navigation entry.

---

## 1. Goal & rationale

The church's current cron brain is the n8n workflow `fKkPUolayRyZrjao` ("CiviCRM Scheduler/Updater", 92 nodes). It owns:

- **Sunday service event generation** — every Friday 18:00 Philippine Time (UTC `0 18 * * 5`), creates three Sunday events (8AM / 10AM / 3PM) for the coming Sunday, posts to Google Chat (via S18).
- **Powerhouse event generation** — every Wednesday 08:00 PHT (UTC `0 8 * * 3`), creates the mid-week event, posts to Google Chat (via S18).
- **EOW derived-attribute recompute** — every Monday 00:00 UTC (`0 0 * * 1`), rescans attendance, updates tier/active/regular/connected snapshots on contacts.
- **EOM recompute** — monthly midnight (first of month) (`0 0 1 * *`) — same full recompute; labeled "Rescan EOM" in n8n; the disabled node `Rescan EOM` fires `0 0 12 * *` (daily noon) gated by a disabled If node checking `$now.plus({days:1}).day === 2` — this is effectively a monthly-ish gate; **the canonical schedule is midnight first of month** per `n8n-analysis.md §3`.
- **Attendance notifiers** — four crons posting attendance counts to the notification channel: 8AM service (Sun 09:30), 10AM service (Sun 12:00), 3PM service (Sun 17:00), Powerhouse (Wed 21:00). Notification logic is delegated to S18; S16 registers the job slots and calls the S18 handoff function.
- **Biometric retention purge** — daily purge of expired / RTBF-requested biometric records (owned by S08's `_process_biometric_retention`; S16 adds an APScheduler trigger at 02:00 UTC daily to call it on schedule rather than inside the queue-consumer loop).

S16 also delivers:

- **System-status page** — live reachability of Postgres, Redis, CompreFace; queue depth; safe-mode; active event; job health tile (last run per job).
- **Config checklist** — a structured list of required and optional settings with completion state, surfaced prominently so admins know what still needs to be configured before go-live.
- **`job_runs` log viewer** — paginated table of scheduled job history with status/duration/detail; admin-only.
- **Custom Field Admin surface** — navigation entry into S02's custom-field group + field management UI under Settings (no backend change).
- **Settings page expansion** — replace the current single-file `SettingsPage.tsx` with a tabbed structure: System, Tunables, Users (→ navigate), Custom Fields (→ navigate), Jobs, Integrations (placeholder for S17/S18).

Single-instance Unraid deployment means **no distributed locking is required**; APScheduler `AsyncIOScheduler` runs in-process within `main.py`'s lifespan context.

---

## 2. Scope

### In scope

- **`job_runs` table** — new; created by Alembic migration in this sprint.
- **`backend/app/services/scheduler.py`** — new file; owns the `AsyncIOScheduler` singleton, `start_scheduler(app)`, `stop_scheduler()`, `register_jobs(scheduler, db_session_factory)`, and job functions.
- **`main.py` lifespan** — modified to start/stop the scheduler via `scheduler.py`.
- **Job registrations** (9 jobs — the numbered list below is authoritative; an earlier "7 jobs" count was a copy error, corrected per CN-12):
  1. `job_sunday_generation` — `0 18 * * 5` UTC (Fri 18:00 PHT) → `generate_sunday_events`
  2. `job_powerhouse_generation` — `0 8 * * 3` UTC (Wed 08:00 PHT) → `generate_powerhouse_event`
  3. `job_eow_recompute` — `0 0 * * 1` UTC → `recompute_member_status_eow`
  4. `job_eom_recompute` — `0 0 1 * *` UTC (midnight on the 1st of each month; NOT the disabled daily-noon `0 0 12 * *` n8n node, per CN-20) → `recompute_member_status_eom`
  5. `job_attendance_notifier_8am` — `30 9 * * 0` UTC (Sun 09:30) → handoff to S18
  6. `job_attendance_notifier_10am` — `0 12 * * 0` UTC (Sun 12:00) → handoff to S18
  7. `job_attendance_notifier_3pm` — `0 17 * * 0` UTC (Sun 17:00) → handoff to S18
  8. `job_attendance_notifier_powerhouse` — `0 21 * * 3` UTC (Wed 21:00) → handoff to S18
  9. `job_biometric_retention` — `0 2 * * *` UTC (daily 02:00) → `_process_biometric_retention`
- **Backend endpoints** added to `backend/app/routers/settings.py`:
  - `GET /settings/system-status` (admin)
  - `GET /settings/config-checklist` (admin)
  - `GET /settings/jobs` (admin) — paginated job_runs log
  - `POST /settings/jobs/{job_id}/trigger` (admin) — manual on-demand trigger
- **`backend/app/schemas.py`** — new Pydantic schemas: `SystemStatusResponse`, `ConfigChecklistResponse`, `ConfigChecklistItem`, `JobRunResponse`, `JobRunListResponse`.
- **`backend/app/services/settings_service.py`** (NEW — owned by S16): central read/write wrapper for `admin_settings`. Handles Fernet encryption/decryption for `sensitive=True` rows using `SETTINGS_ENCRYPTION_KEY` env var. Writes `audit_log` on every change (secrets redacted to `"[redacted]"`). Provides `test_google_chat()`, `test_zoom()`, `test_gmail()` async methods. All other services (S18, S08, S22) call `settings_service.get(db, key)` — never access `admin_settings` directly.
- **`DynamicSettings`** in `config.py`: existing typed getters remain for infrastructure settings (Redis, CompreFace, JWT). New integration/policy settings are read exclusively through `settings_service` at call time (not cached at startup), so changes take effect immediately.
- **`backend/app/routers/settings.py`** — add `PUT /settings/{key}` (replaces bulk `PUT /settings`), `GET /settings` (grouped by category, sensitive masked), `POST /settings/test/google-chat`, `POST /settings/test/zoom`, `POST /settings/test/gmail`.
- **Frontend — Settings page refactor** (`frontend/src/pages/SettingsPage.tsx`): four tabs — **Integrations** (Google Chat, Zoom, Gmail, Anthropic — each with masked secret fields + "Test connection" button), **Policies** (biometric retention, consent gate, viewer PII export, newcomer form toggles, attendance default dropdown), **Branding** (product name with live preview), **Scheduled Jobs** (existing APScheduler viewer + timezone + event series IDs). Tab state in URL (`/settings?tab=integrations`).
- **`frontend/src/pages/SystemStatusPage.tsx`** — new standalone status page at `/settings/status` (also accessible without a tab for direct linking); reuses `SystemStatusCard`.
- **`frontend/src/components/settings/SystemStatusCard.tsx`** — live-refreshing status card (poll `/settings/system-status` every 30 s); shows Postgres / Redis / CompreFace / Queue / Active-Event / Safe-Mode / job health.
- **`frontend/src/components/settings/ConfigChecklist.tsx`** — renders checklist items from `/settings/config-checklist`; green check / red X / amber warning; each item has a "Fix" button that deep-links to the relevant settings field.
- **`frontend/src/components/settings/JobRunsTable.tsx`** — paginated table (job_name, started_at, finished_at, status, duration_ms, detail); manual trigger button per registered job.
- **`frontend/src/components/settings/IntegrationsPanel.tsx`** — Google Chat / Zoom / Gmail / Anthropic sections. Sensitive fields show `●●●●●●` if set; "Change" button reveals empty input (submitting empty = no-op, keeps existing value). Each integration has a "Test connection" button calling `POST /settings/test/{integration}` → inline pass/fail badge with error detail.
- **`frontend/src/components/settings/PoliciesPanel.tsx`** — toggles and inputs for all `category="policies"` and `category="biometric"` keys: biometric retention years (number 1–99), enroll-without-consent toggle, viewer-can-export-PII toggle, newcomer-form-enabled toggle, newcomer-form-captcha toggle (disabled if form off), historical-attendance-default dropdown.
- **`frontend/src/components/settings/BrandingPanel.tsx`** — product name text input with live `<title>` and header preview.
- Role gating: all new backend endpoints are `require_admin`. All new frontend settings tabs are behind the existing `AdminRoute`. The `/health` and `/health/queue` endpoints remain public (no role guard).
- Backend pytest: `job_runs` CRUD, job trigger endpoint, system-status probe logic, config-checklist completeness logic, APScheduler registration (mock scheduler), cron expression correctness.
- Frontend vitest: `SystemStatusCard` loading/error/ok states, `ConfigChecklist` rendering, `JobRunsTable` pagination.

### Out of scope (explicit)

- **Notification delivery** (Google Chat / Gmail messages) — S18. S16 only registers the notifier job slots; the job function calls `await send_attendance_notification(service_time, db)` which is a stub returning `{"status": "pending_s18"}` until S18 lands.
- **Full automation / rules engine** — S17.
- **Zoom / S2S OAuth pull** — S18.
- **Custom field group/def CRUD backend** — S02. S16 only adds a navigation link in the settings UI.
- **Advanced search, saved searches, smart groups** — S09.
- **Audit log viewer** — may be included in a future sprint; `audit_log` writes from scheduler events are in scope.
- **Distributed lock / multi-replica APScheduler** — single Unraid instance; not needed.
- **APScheduler persistence** (writing jobs to DB across restarts) — in-memory schedule; cron expressions are code-level constants. Job misfire handling: `misfire_grace_time=600` (10 min) so a brief restart does not skip a job that fired within 10 minutes of startup.
- **`event_series.auto_generate` flag** or per-series cron customisation — S04 decides whether a series auto-generates; S16 calls the generator for the two known series; full series-driven scheduling is deferred.
- **RTSP camera health display** — the existing `GET /cameras` response (with `status` field) already serves this; the SystemStatusCard links to the cameras section, not duplicates it.
- **Desktop-responsive settings layout pass** — S20.

---

## 3. Data model changes

### 3.1 New table: `job_runs`

```sql
CREATE TABLE job_runs (
    id          SERIAL          PRIMARY KEY,
    job_name    VARCHAR(100)    NOT NULL,
    started_at  TIMESTAMP       NOT NULL,
    finished_at TIMESTAMP,
    status      VARCHAR(20)     NOT NULL DEFAULT 'running',
                                -- running | success | failed | skipped
    detail      TEXT,           -- human-readable summary or error message
    duration_ms INTEGER         GENERATED ALWAYS AS (
                    EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000
                ) STORED        -- Postgres 12+ generated column; SQLite: NULL (computed in Python)
);

CREATE INDEX ix_job_runs_job_name      ON job_runs (job_name);
CREATE INDEX ix_job_runs_started_at    ON job_runs (started_at DESC);
```

**Notes:**
- `duration_ms` is a Postgres generated column. For SQLite (tests), store `NULL` and compute `duration_ms` in Python as `int((finished_at - started_at).total_seconds() * 1000)` when serializing the response.
- `status` values: `running` (job started, not finished), `success`, `failed` (exception raised), `skipped` (guard condition not met — e.g. S23 not landed).
- `detail` stores: on success — a brief summary ("Recomputed 1440 contacts in 4.2s"), on failure — exception type + first line of traceback.
- No FK to `users` — system actor; no FK to jobs list (job names are code constants).
- Retention: no auto-purge in v1; admin sees last N runs per job via the viewer endpoint.

### 3.2 SQLAlchemy model

**File:** `backend/app/models.py` (append after `AdminSetting`)

```python
class JobRun(Base):
    __tablename__ = "job_runs"

    id:          Mapped[int]            = mapped_column(Integer, primary_key=True)
    job_name:    Mapped[str]            = mapped_column(String(100), nullable=False)
    started_at:  Mapped[datetime]       = mapped_column(DateTime, nullable=False, default=utc_now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status:      Mapped[str]            = mapped_column(String(20), default="running")
    detail:      Mapped[Optional[str]]  = mapped_column(Text)
```

Index declarations in `__table_args__`:
```python
    __table_args__ = (
        Index("ix_job_runs_job_name",   "job_name"),
        Index("ix_job_runs_started_at", "started_at"),
    )
```

`duration_ms` is **not** a model column (dialect-specific generated column). The service layer computes it.

### 3.3 `admin_settings` schema update + new keys

**S16 migration adds a `label` column** to the existing `admin_settings` table (short UI label for form fields, separate from the existing `description` help text):

```sql
ALTER TABLE admin_settings ADD COLUMN label VARCHAR(100);
```

**All new keys seeded by S16 migration** (`INSERT ... ON CONFLICT (key) DO NOTHING`). S18 and S08 no longer seed their own keys — S16 is the single seeder:

| key | category | sensitive | requires_restart | label | default |
|-----|----------|-----------|-----------------|-------|---------|
| `google_chat_webhook_url` | integrations | True | False | Google Chat Webhook URL | `""` |
| `zoom_account_id` | integrations | False | False | Zoom Account ID | `""` |
| `zoom_client_id` | integrations | False | False | Zoom Client ID | `""` |
| `zoom_client_secret` | integrations | True | False | Zoom Client Secret | `""` |
| `zoom_host_email` | integrations | False | False | Zoom Host Email | `"lightnorthcaloocan@gmail.com"` |
| `gmail_smtp_host` | integrations | False | False | Gmail SMTP Host | `"smtp.gmail.com"` |
| `gmail_smtp_port` | integrations | False | False | Gmail SMTP Port | `587` |
| `gmail_smtp_user` | integrations | True | False | Gmail SMTP User | `""` |
| `gmail_smtp_password` | integrations | True | False | Gmail App Password | `""` |
| `gmail_from_address` | integrations | False | False | Gmail From Address | `""` |
| `gmail_notification_recipients` | integrations | False | False | Notification Recipients | `""` |
| `anthropic_api_key` | integrations | True | False | Anthropic API Key | `""` |
| `biometric_retention_years` | biometric | False | False | Biometric Retention (years) | `7` |
| `enroll_without_consent` | policies | False | False | Allow Enroll Without Consent | `false` |
| `viewer_can_export_pii` | policies | False | False | Viewers Can Export PII | `false` |
| `newcomer_form_enabled` | policies | False | False | Newcomer Form Enabled | `false` |
| `newcomer_form_captcha` | policies | False | False | Require CAPTCHA on Newcomer Form | `false` |
| `historical_attendance_default` | policies | False | False | Historical Attendance Status | `"attended"` |
| `product_name` | branding | False | False | Product Name | `"Seraphim"` |
| `scheduler_timezone` | general | False | True | Scheduler Timezone | `"Asia/Manila"` |
| `sunday_event_series_id` | general | False | False | Sunday Event Series ID | `null` |
| `powerhouse_event_series_id` | general | False | False | Powerhouse Event Series ID | `null` |

**Encryption:** `settings_service.py` Fernet-encrypts `value["value"]` before writing to JSONB for any row with `sensitive=True`. `SETTINGS_ENCRYPTION_KEY` (32-byte URL-safe base64) must be set in the environment. Generate with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3.4 Alembic migration plan

**Migration ID:** `s16_job_runs_and_settings_engine`

```
backend/alembic/versions/s16_job_runs_and_settings_engine.py
```

`upgrade()`:
1. `op.create_table("job_runs", ...)` — all columns above.
2. `op.create_index("ix_job_runs_job_name", "job_runs", ["job_name"])`.
3. `op.create_index("ix_job_runs_started_at", "job_runs", ["started_at"])`.
4. `op.add_column("admin_settings", sa.Column("label", sa.String(100)))`.
5. Seed all 22 new `admin_settings` rows (`INSERT ... ON CONFLICT (key) DO NOTHING`).

`downgrade()`:
1. Drop both indexes.
2. `op.drop_table("job_runs")`.
3. `op.drop_column("admin_settings", "label")`.
4. Delete the 22 seeded `admin_settings` keys via `op.execute`.

**No backfill required.** `job_runs` starts empty; history accumulates from first scheduler run.

---

## 4. Backend

### 4.1 Endpoints

| Method | Path | Role | Request | Response | Notes |
|--------|------|------|---------|----------|-------|
| `GET` | `/settings/system-status` | admin | — | `SystemStatusResponse` | Probes DB/Redis/CompreFace live; reads `dynamic_settings` for queue/safe-mode/active-event; reads last `job_runs` row per job name. Max 3s total (CompreFace probe timeout=2s). |
| `GET` | `/settings/config-checklist` | admin | — | `ConfigChecklistResponse` | Evaluates completeness of each required/optional setting; no external probes (reads `admin_settings` + `event_series` count). |
| `GET` | `/settings/jobs` | admin | `?page=1&page_size=50&job_name=` | `JobRunListResponse` | Paginated, ordered by `started_at DESC`. Filter by `job_name` optional. |
| `POST` | `/settings/jobs/{job_name}/trigger` | admin | — | `{"job_name": str, "job_run_id": int, "status": "started"}` | Dispatches the named job immediately via `asyncio.create_task`. Returns 202 immediately. 404 if `job_name` not in the registered job registry. 409 if a `running` row for this job_name exists (in-flight guard). |

**New settings endpoints (S16 owns all):**

| Method | Path | Role | Notes |
|--------|------|------|-------|
| `GET` | `/settings` | admin | All rows grouped by category; sensitive non-empty values masked to `"***"` |
| `PUT` | `/settings/{key}` | admin | Validate key exists; encrypt if sensitive; write `audit_log`; 404 if unknown key |
| `POST` | `/settings/test/google-chat` | admin | POST webhook with test payload → `{ok, error?, latency_ms?}` |
| `POST` | `/settings/test/zoom` | admin | Exchange S2S creds for token → GET `/v2/users/me` → `{ok, error?}` |
| `POST` | `/settings/test/gmail` | admin | SMTP EHLO + STARTTLS + AUTH → `{ok, error?}` |

**Existing endpoints updated:**
- `GET /settings` — now returns rows grouped by `category` with `label` field; sensitive masked.
- `PUT /settings` (old bulk upsert) — **deprecated in favour of `PUT /settings/{key}`**; keep for backward compat with existing setup wizard but do not use in new UI.
- `POST /settings/safe-mode` — unchanged.
- `GET /health` — unchanged (no auth; called by load balancers / Docker healthcheck).
- `GET /health/queue` — unchanged (no auth; called by volunteer frontend).

### 4.2 Pydantic schemas (add to `backend/app/schemas.py`)

```python
class ServiceStatusItem(BaseModel):
    name: str
    ok: bool
    detail: Optional[str] = None
    latency_ms: Optional[int] = None

class JobStatusItem(BaseModel):
    job_name: str
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None   # success | failed | running | skipped | None
    last_detail: Optional[str] = None
    last_duration_ms: Optional[int] = None

class SystemStatusResponse(BaseModel):
    overall: str                        # ok | degraded | down
    postgres: ServiceStatusItem
    redis: ServiceStatusItem
    compreface: ServiceStatusItem
    queue_depth: int
    queue_saturated: bool
    safe_mode: bool
    active_event_id: Optional[int]
    jobs: list[JobStatusItem]
    checked_at: datetime

class ConfigChecklistItem(BaseModel):
    key: str
    label: str
    category: str                       # required | recommended | optional
    is_set: bool
    hint: Optional[str] = None          # what to do if not set
    settings_key: Optional[str] = None  # which admin_settings key to edit (for "Fix" deep-link)

class ConfigChecklistResponse(BaseModel):
    items: list[ConfigChecklistItem]
    required_complete: bool
    recommended_complete: bool
    all_complete: bool

class JobRunResponse(BaseModel):
    id: int
    job_name: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    detail: Optional[str]
    duration_ms: Optional[int]

class JobRunListResponse(BaseModel):
    items: list[JobRunResponse]
    total: int
    page: int
    page_size: int
```

### 4.3 New file: `backend/app/services/scheduler.py`

This is the heart of S16. Structure:

```python
"""
backend/app/services/scheduler.py
APScheduler AsyncIOScheduler host for Seraphim.
Single-instance (Unraid); no distributed lock.
All times are UTC. Cron expressions from n8n fKkPUolayRyZrjao.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import async_session
from app.models import JobRun

logger = logging.getLogger(__name__)

# ── Cron expressions (UTC) — sourced from n8n fKkPUolayRyZrjao ──────────────
CRON_SUNDAY_SVC          = "0 18 * * 5"    # Fri 18:00 UTC = Sat 02:00 PHT
                                            # n8n node "Sunday SVC": 0 18 * * 5
CRON_POWERHOUSE          = "0 8 * * 3"     # Wed 08:00 UTC = Wed 16:00 PHT
                                            # n8n node "Powerhouse Generator": 0 8 * * 3
CRON_EOW_RECOMPUTE       = "0 0 * * 1"     # Mon 00:00 UTC
                                            # n8n node "Rescan EOW": 0 0 * * 1
CRON_EOM_RECOMPUTE       = "0 0 1 * *"     # 1st of month 00:00 UTC
                                            # n8n node "Rescan EOM" (effective cadence)
CRON_NOTIFIER_8AM        = "30 9 * * 0"    # Sun 09:30 UTC
                                            # n8n node "8AM Attendance Notifier": 30 9 * * 0
CRON_NOTIFIER_10AM       = "0 12 * * 0"    # Sun 12:00 UTC
                                            # n8n node "10AM Attendance Notifier": 0 12 * * 0
CRON_NOTIFIER_3PM        = "0 17 * * 0"    # Sun 17:00 UTC
                                            # n8n node "3PM Attendance Notifier": 0 17 * * 0
CRON_NOTIFIER_POWERHOUSE = "0 21 * * 3"    # Wed 21:00 UTC
                                            # n8n node "Powerhouse Notifier": 0 21 * * 3
CRON_BIOMETRIC_RETENTION = "0 2 * * *"     # Daily 02:00 UTC

# ── Registered job names ─────────────────────────────────────────────────────
REGISTERED_JOBS: dict[str, str] = {
    "sunday_generation":              CRON_SUNDAY_SVC,
    "powerhouse_generation":          CRON_POWERHOUSE,
    "eow_recompute":                  CRON_EOW_RECOMPUTE,
    "eom_recompute":                  CRON_EOM_RECOMPUTE,
    "attendance_notifier_8am":        CRON_NOTIFIER_8AM,
    "attendance_notifier_10am":       CRON_NOTIFIER_10AM,
    "attendance_notifier_3pm":        CRON_NOTIFIER_3PM,
    "attendance_notifier_powerhouse": CRON_NOTIFIER_POWERHOUSE,
    "biometric_retention":            CRON_BIOMETRIC_RETENTION,
}

_scheduler: Optional[AsyncIOScheduler] = None


async def _run_tracked_job(job_name: str, fn: Callable) -> int:
    """
    Wrapper that writes a job_runs row before + after calling fn(db).
    Returns the job_run.id.
    Raises nothing — exceptions are caught and stored in detail.
    """
    async with async_session() as db:
        run = JobRun(job_name=job_name, started_at=_utc_now(), status="running")
        db.add(run)
        await db.commit()
        await db.refresh(run)
        run_id = run.id

    try:
        async with async_session() as db:
            detail = await fn(db)
        status = "success"
        detail_str = str(detail) if detail else "ok"
    except Exception as exc:
        status = "failed"
        detail_str = f"{type(exc).__name__}: {exc}"
        logger.exception("Scheduled job %s failed", job_name)

    async with async_session() as db:
        run = await db.get(JobRun, run_id)
        if run:
            run.finished_at = _utc_now()
            run.status = status
            run.detail = detail_str
            await db.commit()

    return run_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Job function implementations ─────────────────────────────────────────────

async def _job_sunday_generation(db) -> str:
    """Generate Sunday service events (8AM/10AM/3PM) for the coming Sunday."""
    from app.config import dynamic_settings
    from app.services.event_generator import generate_sunday_events
    series_id_str = dynamic_settings.get_str("sunday_event_series_id", "")
    if not series_id_str:
        return "skipped: sunday_event_series_id not configured"
    series_id = int(series_id_str)
    result = await generate_sunday_events(series_id, db)
    return f"generated {result['count']} events for {result['occurrence_date']}"


async def _job_powerhouse_generation(db) -> str:
    """Generate Powerhouse (mid-week) event."""
    from app.config import dynamic_settings
    from app.services.event_generator import generate_powerhouse_event
    series_id_str = dynamic_settings.get_str("powerhouse_event_series_id", "")
    if not series_id_str:
        return "skipped: powerhouse_event_series_id not configured"
    series_id = int(series_id_str)
    result = await generate_powerhouse_event(series_id, db)
    return f"generated event id={result['event_id']} for {result['occurrence_date']}"


async def _job_eow_recompute(db) -> str:
    """End-of-week derived-attribute recompute (Mon 00:00 UTC)."""
    try:
        from app.services.member_status_service import recompute_all_contacts
        result = await recompute_all_contacts(db)
        return f"recomputed {result['contact_count']} contacts in {result['duration_ms']}ms"
    except ImportError:
        return "skipped: member_status_service not yet available (awaiting S23)"


async def _job_eom_recompute(db) -> str:
    """End-of-month derived-attribute recompute (1st of month 00:00 UTC)."""
    try:
        from app.services.member_status_service import recompute_all_contacts
        result = await recompute_all_contacts(db)
        return f"eom: recomputed {result['contact_count']} contacts in {result['duration_ms']}ms"
    except ImportError:
        return "skipped: member_status_service not yet available (awaiting S23)"


async def _job_attendance_notifier(session_label: str, db) -> str:
    """
    Handoff stub for attendance notification jobs.
    S18 replaces this with a real Google Chat / Gmail send.
    """
    try:
        from app.services.notification_service import send_attendance_notification
        result = await send_attendance_notification(session_label, db)
        return f"notified for {session_label}: {result}"
    except ImportError:
        return f"skipped: notification_service not yet available (awaiting S18) for {session_label}"


async def _job_biometric_retention(db) -> str:
    """Daily biometric retention purge (02:00 UTC)."""
    try:
        from app.services.queue_manager import QueueManager
        qm = QueueManager(db_session_factory=None)
        result = await qm._process_biometric_retention(db)
        return f"purged {result} records"
    except Exception as exc:
        return f"failed: {exc}"


# ── Scheduler lifecycle ───────────────────────────────────────────────────────

def get_scheduler() -> Optional[AsyncIOScheduler]:
    return _scheduler


def start_scheduler() -> AsyncIOScheduler:
    """
    Create, configure, and start the AsyncIOScheduler.
    Called from main.py lifespan. Must run inside a running asyncio loop.
    misfire_grace_time=600: if the app restarts within 10 min of a cron fire, run it anyway.
    """
    global _scheduler
    _scheduler = AsyncIOScheduler(job_defaults={"misfire_grace_time": 600})

    def _add(job_name: str, cron: str, fn: Callable):
        _scheduler.add_job(
            _run_tracked_job,
            CronTrigger.from_crontab(cron),
            id=job_name,
            args=[job_name, fn],
            replace_existing=True,
            max_instances=1,        # single-instance guard
        )

    _add("sunday_generation",              CRON_SUNDAY_SVC,          _job_sunday_generation)
    _add("powerhouse_generation",          CRON_POWERHOUSE,           _job_powerhouse_generation)
    _add("eow_recompute",                  CRON_EOW_RECOMPUTE,        _job_eow_recompute)
    _add("eom_recompute",                  CRON_EOM_RECOMPUTE,        _job_eom_recompute)
    _add("attendance_notifier_8am",        CRON_NOTIFIER_8AM,
         lambda db: _job_attendance_notifier("8AM", db))
    _add("attendance_notifier_10am",       CRON_NOTIFIER_10AM,
         lambda db: _job_attendance_notifier("10AM", db))
    _add("attendance_notifier_3pm",        CRON_NOTIFIER_3PM,
         lambda db: _job_attendance_notifier("3PM", db))
    _add("attendance_notifier_powerhouse", CRON_NOTIFIER_POWERHOUSE,
         lambda db: _job_attendance_notifier("Powerhouse", db))
    _add("biometric_retention",            CRON_BIOMETRIC_RETENTION,  _job_biometric_retention)

    _scheduler.start()
    logger.info("APScheduler started with %d jobs", len(REGISTERED_JOBS))
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
    _scheduler = None
```

### 4.4 Modifications to `backend/app/main.py`

Modify the existing `lifespan` context manager (currently at `main.py:36-67`). Add scheduler start/stop:

```python
# In lifespan, after dynamic_settings.initialize(db):
from app.services.scheduler import start_scheduler, stop_scheduler
start_scheduler()

# In the yield-cleanup block (currently: aclose_redis, engine.dispose):
stop_scheduler()
```

**`requirements.txt` addition:** `apscheduler>=3.10.4`

### 4.5 Modifications to `backend/app/routers/settings.py`

Add the four new endpoints to the existing `router` (all `require_admin`):

**`GET /settings/system-status`** — service function:
1. Probe Postgres: `await db.execute(select(1))` with 2s timeout → `latency_ms`.
2. Probe Redis: `await r.ping()` with 2s timeout.
3. Probe CompreFace: `GET {compreface_url}/api/v1/health` with 2s timeout (reuse existing logic from `health.py:38-45`).
4. Read queue depth: `COUNT(Task.id WHERE status='pending')` (reuse `health.py:60-75` logic).
5. Read `dynamic_settings.is_safe_mode()`, `get_active_event_id()`.
6. Load last `job_runs` row per `job_name` via `SELECT DISTINCT ON (job_name) * FROM job_runs ORDER BY job_name, started_at DESC` (Postgres) / `GROUP BY job_name` + subquery (SQLite).
7. Compute `overall`: "ok" if postgres+redis ok; "degraded" if redis down or compreface down but postgres ok; "down" if postgres down.

**`GET /settings/config-checklist`** — evaluates these items (examples; full list in implementation):

| key | label | category | how to detect `is_set` |
|-----|-------|----------|------------------------|
| `database_url` | Database connection | required | `dynamic_settings.get_str("database_url")` nonempty |
| `redis_url` | Redis cache | required | nonempty |
| `jwt_secret` | JWT secret (≥32 chars) | required | `len(get_jwt_secret()) >= 32` |
| `compreface_url` | CompreFace URL | required | nonempty |
| `compreface_detect_api_key` | CompreFace detect key | required | nonempty |
| `compreface_recognize_api_key` | CompreFace recognize key | required | nonempty |
| `sunday_event_series_id` | Sunday service series linked | required | nonempty + `event_series` row exists |
| `powerhouse_event_series_id` | Powerhouse series linked | required | nonempty + row exists |
| `google_chat_webhook_url` | Google Chat webhook | recommended | nonempty |
| `anthropic_api_key` | Anthropic API key (name matching) | recommended | nonempty |
| `zoom_account_id` | Zoom S2S account ID | optional | nonempty |
| `google_client_id` | Google OAuth client ID | optional | nonempty |
| `allowed_domain` | Google OAuth domain restriction | optional | nonempty |

Returns `required_complete = all(item.is_set for item in items if item.category == "required")`.

**`GET /settings/jobs`** — paginated query against `job_runs` ordered `started_at DESC`. Support `job_name` filter. Compute `duration_ms` in Python for SQLite compatibility: `int((finished_at - started_at).total_seconds() * 1000)` when `finished_at` is not None.

**`POST /settings/jobs/{job_name}/trigger`**:
1. Validate `job_name` in `REGISTERED_JOBS`.
2. Check for in-flight run: `SELECT id FROM job_runs WHERE job_name=? AND status='running'` → 409 if found.
3. Dispatch: `asyncio.create_task(_run_tracked_job(job_name, JOB_FUNCTIONS[job_name]))`.
4. Return 202 `{"job_name": job_name, "status": "started"}`.

Add `JOB_FUNCTIONS` dict in `scheduler.py` mapping job names to async callables (imported by the router).

### 4.6 Additions to `backend/app/config.py`

Add these getter methods to `DynamicSettings` (after `get_active_event_id`, `config.py:157`):

```python
def get_google_chat_webhook_url(self) -> str:
    return self.get_str("google_chat_webhook_url", "")

def get_anthropic_api_key(self) -> str:
    return self.get_str("anthropic_api_key", "")

def get_zoom_account_id(self) -> str:
    return self.get_str("zoom_account_id", "")

def get_zoom_client_id(self) -> str:
    return self.get_str("zoom_client_id", "")

def get_zoom_client_secret(self) -> str:
    return self.get_str("zoom_client_secret", "")

def get_scheduler_timezone(self) -> str:
    return self.get_str("scheduler_timezone", "Asia/Manila")

def get_sunday_series_id(self) -> Optional[int]:
    val = self.get_str("sunday_event_series_id", "")
    try:
        return int(val) if val else None
    except ValueError:
        return None

def get_powerhouse_series_id(self) -> Optional[int]:
    val = self.get_str("powerhouse_event_series_id", "")
    try:
        return int(val) if val else None
    except ValueError:
        return None
```

### 4.7 Business rules & edge cases

**APScheduler timezone:** All cron expressions are UTC. The `scheduler_timezone` admin setting is for **display only** (e.g., showing "Fri 18:00 UTC = Sat 02:00 PHT" in the UI). APScheduler receives `CronTrigger.from_crontab(cron, timezone="UTC")`.

**Misfire handling:** `misfire_grace_time=600` seconds. If Seraphim restarts during a 10-minute window after a scheduled fire, APScheduler fires the job immediately on startup. If the restart window exceeds 600s, the job is skipped (recorded as `status="skipped"` by APScheduler's coalesce; S16 does not record a `job_runs` row for skipped fires — only jobs that actually execute get a row).

**Concurrent execution guard:** `max_instances=1` on every job prevents double-firing if a job runs longer than its cron interval.

**In-flight guard for manual trigger:** Return 409 if `job_runs WHERE job_name=? AND status='running'` exists. This prevents duplicate manual triggers.

**Sunday event generation (Fri 18:00 UTC):** Calls `generate_sunday_events(series_id, db)` from S04's `event_generator.py`. That function is idempotent (uses `INSERT ... ON CONFLICT DO NOTHING` keyed on `(series_id, occurrence_date)`). If `sunday_event_series_id` is not configured, logs a warning and writes `status="skipped"`.

**Powerhouse generation (Wed 08:00 UTC):** Same pattern with `generate_powerhouse_event`.

**Notification handoff jobs (S18 stub):** The four notifier jobs call `send_attendance_notification(session_label, db)` from `app.services.notification_service`. Until S18 lands, the `try/import` guard writes `status="skipped"` to `job_runs`.

**Biometric retention (02:00 UTC):** Calls `QueueManager._process_biometric_retention`. The QueueManager already has this method (S08). S16 adds the APScheduler trigger; S08 owns the implementation.

**`_run_tracked_job` exception safety:** The wrapper catches **all** exceptions from the job function and stores the traceback first line in `detail`. It never raises. This ensures the APScheduler event loop is not disrupted by a failed job.

**DB session lifecycle:** Each `_run_tracked_job` call opens and closes its own `async_session` context. The `JobRun` write for `started_at` and the update for `finished_at` are in separate sessions to ensure the start is persisted even if the job function raises immediately.

---

## 5. Frontend

### 5.1 Route additions (`frontend/src/App.tsx`)

Add to the `<Routes>` block (admin-gated):

```tsx
<Route path="/settings/status"       element={<AdminRoute><SystemStatusPage /></AdminRoute>} />
<Route path="/settings/jobs"         element={<AdminRoute><JobRunsPage /></AdminRoute>} />
```

The main `/settings` route is modified in place (tabbed layout).

### 5.2 Modified: `frontend/src/pages/SettingsPage.tsx`

**Refactor** from a single monolithic page to a **tabbed container**. Tabs:

1. **System** — existing content (Profile, Appearance, Safe Mode, Cameras, Admin shortcuts) + new `ConfigChecklist` card + navigation link to `/settings/status`.
2. **Tunables** — extract existing `TunablesEditor` (already in the file) to its own tab.
3. **Integrations** — new `IntegrationsPanel` component.
4. **Jobs** — navigation link to `/settings/jobs` (full page) + inline `JobStatusSummary` (last-run per job; 5-item list).
5. **Custom Fields** — navigation button to `/settings/custom-fields` (S02 page).
6. **Users** — navigation button to `/settings/users` (existing page).

Tab state: URL query param `?tab=system|tunables|integrations|jobs|custom-fields|users`. Default tab = `system`.

**Remove** the existing "Attendance & CiviCRM Push" navigation item (`line 615` in the current `SettingsPage.tsx`); replace with "Attendance" linking to a future S18 page.

**Mobile-first:** tabs rendered as a horizontally-scrollable pill row at top. On desktop (md+): vertical sidebar tabs.

### 5.3 New: `frontend/src/pages/SystemStatusPage.tsx`

Route: `/settings/status`

```tsx
export function SystemStatusPage() {
  // useQuery(['system-status'], () => api.get('/settings/system-status'), { refetchInterval: 30_000 })
  // Shows: overall badge, 3 service rows (postgres/redis/compreface), queue depth bar,
  //        safe-mode toggle (inline), active event chip, jobs health list.
}
```

TanStack Query key: `['settings', 'system-status']`.
Poll interval: 30 seconds (not SSE; GET endpoint is idempotent and lightweight).
Loading state: skeleton cards (3 service rows + jobs grid).
Error state: `ErrorState` component from `frontend/src/components/ui/ErrorState.tsx`.
Overall badge: green "All systems operational" / amber "Degraded" / red "Down".

### 5.4 New: `frontend/src/pages/JobRunsPage.tsx`

Route: `/settings/jobs`

```tsx
export function JobRunsPage() {
  // Paginated table of job_runs.
  // Filter by job_name dropdown.
  // "Trigger" button per registered job (POST /settings/jobs/{job_name}/trigger).
  // Shows: job_name, started_at, duration_ms, status badge, detail (truncated to 80 chars, expand on click).
}
```

TanStack Query key: `['settings', 'jobs', page, jobNameFilter]`.
Manual trigger: `useMutation`, toast on 202/409, refetch after 2s.
Status badges: `success`→green, `failed`→red, `running`→amber (animated spinner), `skipped`→muted.

### 5.5 New: `frontend/src/components/settings/SystemStatusCard.tsx`

Embeds in the System tab of `SettingsPage` as a compact card (not the full page). Shows 3 service dots + queue + safe-mode + active event. Links to `/settings/status` for full view.

```tsx
// Props: none (fetches own data)
// Query: ['settings', 'system-status'] — shared with SystemStatusPage via React Query cache
// Poll interval: 30s
```

### 5.6 New: `frontend/src/components/settings/ConfigChecklist.tsx`

```tsx
// Props: none (fetches /settings/config-checklist)
// Query key: ['settings', 'config-checklist']
// Renders: 3 groups (Required / Recommended / Optional)
// Each item: green check / red X / amber warning icon + label + (if !is_set) hint text + "Fix" button
// "Fix" button: navigates to /settings?tab=system#key or /settings?tab=integrations#key
// Shows overall completion bar ("8/8 required complete")
```

### 5.7 New: `frontend/src/components/settings/IntegrationsPanel.tsx`

Shows 4 integration cards:
- **Google Chat** — secret field for `google_chat_webhook_url` + connectivity badge (check if nonempty).
- **Zoom** — fields for `zoom_account_id`, `zoom_client_id`, `zoom_client_secret`.
- **Anthropic (Claude)** — field for `anthropic_api_key`.
- **Google OAuth** — existing `google_client_id`/`google_client_secret` (moved here from System tab).

Each card:
- Input fields (password type for secrets; empty = keep existing, per existing `settings.py` masking convention).
- "Save" button → `PUT /settings` with only the changed fields.
- Connectivity indicator: green dot if key is set (masked presence check from `/settings` response); no live connectivity test in v1 (deferred to S17/S18).

### 5.8 New: `frontend/src/components/settings/JobStatusSummary.tsx`

Compact version for the Jobs tab in SettingsPage: list of 9 registered jobs with last-run status and timestamp. "View all" link to `/settings/jobs`. "Trigger" dropdown for manual runs.

### 5.9 TanStack Query keys

| Key | Used by |
|-----|---------|
| `['settings', 'system-status']` | `SystemStatusPage`, `SystemStatusCard` (shared cache) |
| `['settings', 'config-checklist']` | `ConfigChecklist` |
| `['settings', 'jobs', page, filter]` | `JobRunsPage` |
| `['settings', 'jobs', 'summary']` | `JobStatusSummary` |
| `['settings']` (existing) | `SettingsPage` (admin settings list) |

### 5.10 Role gating

All new pages (`SystemStatusPage`, `JobRunsPage`) are wrapped in `AdminRoute` (defined in `frontend/src/components/layout/AdminRoute.tsx`). After S15 lands and `viewer` role is introduced, the `SystemStatusPage` should be widened to `RoleRoute(['admin', 'volunteer', 'viewer'])` — noted as a follow-up for S15.

### 5.11 UX loading / empty / error states

- **Loading:** skeleton bars in place of metric values; three pulsing dots for service status.
- **Error:** `ErrorState` component with retry button.
- **Empty (no job runs yet):** "No scheduled jobs have run yet. Trigger one manually to test." with a list of registered jobs.
- **Toast on trigger:** "Job started" (202) / "Job already running" (409) / surface `err.response?.data?.detail`.
- **Mobile-first:** System status card is a single-column stack on mobile; 2-column grid on md+. Job runs table is a vertically-stacked card list on mobile; table on md+.

### 5.12 File-by-file summary

| Action | File |
|--------|------|
| MODIFY | `frontend/src/App.tsx` — add 2 routes |
| MODIFY | `frontend/src/pages/SettingsPage.tsx` — tabbed refactor; remove CiviCRM link |
| CREATE | `frontend/src/pages/SystemStatusPage.tsx` |
| CREATE | `frontend/src/pages/JobRunsPage.tsx` |
| CREATE | `frontend/src/components/settings/SystemStatusCard.tsx` |
| CREATE | `frontend/src/components/settings/ConfigChecklist.tsx` |
| CREATE | `frontend/src/components/settings/IntegrationsPanel.tsx` |
| CREATE | `frontend/src/components/settings/JobStatusSummary.tsx` |
| CREATE | `frontend/src/components/settings/JobRunsTable.tsx` |
| MODIFY | `frontend/src/types/index.ts` — add `JobRun`, `SystemStatus`, `ConfigChecklistItem` types |

---

## 6. Migration / data

**No data migration required.** `job_runs` is a new table that starts empty. The eight new `admin_settings` rows are seeded by the Alembic migration via `INSERT ... ON CONFLICT DO NOTHING` — existing deployments get the keys with empty-string values; the config checklist immediately shows them as "not set." Admins configure the values through the Integrations panel on first visit.

**`requirements.txt`:** add `apscheduler>=3.10.4`. No other new Python dependencies.

---

## 7. Acceptance criteria

1. **Scheduler starts on app boot.** `GET /health` returns 200 and APScheduler logs show 9 registered jobs at startup.
2. **`job_runs` table exists and is writable.** After manually triggering any job via `POST /settings/jobs/{job_name}/trigger`, a `job_runs` row appears with `status IN ('running','success','failed','skipped')` within 30 seconds.
3. **Sunday generation job runs correctly when series is configured.** Set `sunday_event_series_id` to a valid `event_series.id`. Trigger `sunday_generation` manually. Verify 3 `events` rows are created (8AM/10AM/3PM) for the next Sunday's date. Re-trigger: no duplicate rows (idempotent).
4. **Powerhouse generation job runs correctly.** Same pattern with `powerhouse_event_series_id`; 1 event created.
5. **EOW and EOM recompute jobs delegate to S23.** If S23 is landed: trigger `eow_recompute`; verify `contacts.tier` is populated for all contacts. If S23 not landed: job writes `status="skipped"` with detail `"skipped: member_status_service not yet available"`.
6. **Notifier stubs write `skipped` until S18.** Triggering any `attendance_notifier_*` job writes `status="skipped"` with detail containing "awaiting S18".
7. **Biometric retention job runs.** Triggering `biometric_retention` creates a `job_runs` row; status is either `success` or `failed` (never hanging `running`).
8. **System status endpoint returns correct state.** `GET /settings/system-status` returns `overall="ok"` when all three services reachable, with non-null `latency_ms` for postgres. Returns `overall="degraded"` when CompreFace URL is not configured or unreachable.
9. **Config checklist identifies unset required items.** Immediately after a fresh setup (no integration keys set), `GET /settings/config-checklist` returns `required_complete=false` for any missing required key. After setting all required keys, returns `required_complete=true`.
10. **Job runs page paginates correctly.** After running 60+ jobs, `GET /settings/jobs?page=2&page_size=20` returns 20 items with the correct `total` count.
11. **Manual trigger rejects duplicate.** Trigger the same long-running job twice in rapid succession; second call returns 409 with a meaningful `detail`.
12. **Settings page tabs work correctly.** Navigating to `/settings?tab=jobs` renders the Jobs tab. Navigating to `/settings?tab=tunables` shows the tunables editor. On mobile (375px viewport), tabs scroll horizontally without overflow.
13. **Viewer role (post-S15) can see system status.** After S15: a viewer-role user can access `/settings/status` page. The job trigger button is hidden or disabled for viewers (admin-only action).
14. **All tests pass.** `pytest tests/ -q` (DATABASE_URL=sqlite+aiosqlite, REDIS_URL=memory://, ENVIRONMENT=test) and `npm run build && npm run lint && npm run test:run` all green.

---

## 8. Test plan

### 8.1 Backend pytest (`backend/tests/`)

**New test file: `tests/test_scheduler.py`**

```python
# test_scheduler_cron_expressions
# - Assert all 9 CRON_* constants parse without error via CronTrigger.from_crontab
# - Verify CRON_SUNDAY_SVC == "0 18 * * 5" (regression: n8n node name "Sunday SVC")
# - Verify CRON_EOW_RECOMPUTE == "0 0 * * 1" (Monday)
# - Verify CRON_NOTIFIER_8AM == "30 9 * * 0" (Sunday 09:30 UTC)

# test_job_runs_model_create_read
# - Create a JobRun row, commit, read back, assert job_name / status / started_at
# - Assert duration_ms computed correctly in Python (100ms delta → 100)

# test_trigger_endpoint_starts_job (async; AsyncClient)
# - POST /settings/jobs/eow_recompute/trigger as admin → 202
# - Wait (asyncio.sleep) → GET /settings/jobs?job_name=eow_recompute → status in (success, skipped)

# test_trigger_endpoint_unknown_job
# - POST /settings/jobs/nonexistent/trigger → 404

# test_trigger_endpoint_in_flight_guard
# - Insert a JobRun with status="running" for "eow_recompute"
# - POST /settings/jobs/eow_recompute/trigger → 409

# test_trigger_endpoint_requires_admin
# - POST /settings/jobs/eow_recompute/trigger as volunteer → 403

# test_system_status_endpoint (mock external probes)
# - Patch httpx, redis; GET /settings/system-status as admin → 200 with overall=ok
# - Remove CompreFace URL → overall=degraded (postgres+redis ok)
# - Patch db.execute to raise → overall=down

# test_config_checklist_incomplete
# - Ensure no "sunday_event_series_id" in admin_settings
# - GET /settings/config-checklist → required_complete=false, item for sunday_event_series_id has is_set=false

# test_config_checklist_complete
# - Insert all required keys with non-empty values
# - GET /settings/config-checklist → required_complete=true

# test_job_runs_list_pagination
# - Insert 25 JobRun rows
# - GET /settings/jobs?page=1&page_size=10 → 10 items, total=25
# - GET /settings/jobs?page=3&page_size=10 → 5 items

# test_sunday_generation_idempotent (mock event_generator)
# - Patch generate_sunday_events to return {"count": 3, "occurrence_date": "2026-06-28"}
# - Trigger job twice; assert event_generator called twice but DB has no duplicates
#   (idempotency enforced by event_generator itself per S04 spec)

# test_biometric_retention_job_writes_runs_row
# - Patch QueueManager._process_biometric_retention to return 5
# - Trigger "biometric_retention"; assert job_runs row with status="success", detail contains "5"

# test_notifier_stub_skips_without_s18
# - Ensure notification_service is NOT importable (mock ImportError)
# - Trigger "attendance_notifier_8am" → job_runs row status="success", detail contains "skipped"
```

**New test file: `tests/test_settings_extended.py`**

```python
# test_get_settings_system_status_unauthenticated → 401
# test_get_settings_system_status_volunteer → 403
# test_get_config_checklist_volunteer → 403
```

### 8.2 Frontend vitest (`frontend/src/`)

**New test file: `frontend/src/components/settings/SystemStatusCard.test.tsx`**

```ts
// test: renders "loading" state (skeleton) on mount
// test: renders "All systems operational" badge when all services ok=true
// test: renders "Degraded" badge when compreface.ok=false
// test: renders "Down" badge when postgres.ok=false
// test: shows job health list with success/failed/skipped badges
// test: refetches every 30s (vi.useFakeTimers + vi.advanceTimersByTime)
```

**New test file: `frontend/src/components/settings/ConfigChecklist.test.tsx`**

```ts
// test: renders required items with red X when is_set=false
// test: renders required items with green check when is_set=true
// test: "Fix" button triggers navigation to correct settings URL
// test: completion bar renders "8/8 required complete" correctly
```

**New test file: `frontend/src/pages/JobRunsPage.test.tsx`**

```ts
// test: renders loading skeleton
// test: renders empty state when items=[]
// test: renders paginated rows
// test: "Trigger" button calls POST /settings/jobs/{name}/trigger and shows toast
// test: 409 response shows "already running" toast
```

---

## 9. Rollout / rollback / risks

### Rollout order

1. **Run Alembic migration** (`alembic upgrade head`): creates `job_runs` table + seeds 8 new `admin_settings` rows. Non-destructive; app continues serving during migration.
2. **Deploy new backend** with `apscheduler` in `requirements.txt` and `scheduler.py` wired into lifespan. First boot: 9 jobs registered, no jobs fire until their cron time.
3. **Configure integration settings** via the new Integrations tab: set `sunday_event_series_id`, `powerhouse_event_series_id`, `google_chat_webhook_url`, `anthropic_api_key`.
4. **Test manual triggers** via the Jobs page: trigger `eow_recompute` manually, verify `job_runs` row, verify `contacts.tier` populated (if S23 landed).
5. **Deploy new frontend**: tabbed settings, status page, jobs page.

### Rollback

- Frontend: revert to previous `SettingsPage.tsx`; remove new routes from `App.tsx`. Zero data impact.
- Backend: stop scheduler via `stop_scheduler()` (no jobs fire). Revert `main.py` lifespan change. `job_runs` table and its rows remain (harmless). New `admin_settings` keys remain with empty values (harmless).
- Database: `alembic downgrade -1` drops `job_runs` and removes the 8 seeded `admin_settings` rows.

### Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| APScheduler job fires during DB migration (race) | Low | `misfire_grace_time=600`; migration takes <5s; all jobs are idempotent |
| Uncaught exception in job function crashes APScheduler | Low | `_run_tracked_job` wraps all exceptions; never re-raises; scheduler event loop unaffected |
| `job_runs` table grows unbounded | Medium | Table is small (9 jobs × 365 days × ~2–3 runs = <10k rows/year); no auto-purge in v1; add retention policy in S20/ops |
| `generate_sunday_events` not yet merged (S04 not landed) | Medium | `try/import` guard writes `status="skipped"`; no crash |
| `member_status_service` not yet merged (S23 not landed) | Medium | Same guard; EOW/EOM jobs write `skipped` |
| `notification_service` not yet merged (S18 not landed) | High (S18 is later) | Same guard; all 4 notifier jobs write `skipped` until S18 |
| Sunday generation fires at UTC 18:00 Fri = Sat 02:00 PHT | Verified correct | The n8n "Sunday SVC" node explicitly runs `0 18 * * 5`; generates events for `now + 2 days` = Sunday. Philippine Standard Time is UTC+8, so `0 18 UTC Friday = 02:00 PHT Saturday`, giving a ~22-hour lead time before the 8AM Sunday service. Correct and intentional. |

---

## 10. Open questions & pending owner artifacts

1. **`sunday_event_series_id` / `powerhouse_event_series_id` configuration.** These must be set by the admin after S04 creates the event series. The config checklist will flag them as unset. The Jobs page should include a "Setup guide" tooltip explaining where to get these IDs (from the Event Series list in S04's UI). **Owner action:** after S04 is deployed and series are created, copy the IDs into admin settings.

2. **Cron timezone confirmation.** The n8n cron expressions are recorded in UTC (`0 18 * * 5` for Sunday SVC), which equals PHT+8h = Sat 02:00 PHT. The church is in Caloocan, Metro Manila (UTC+8, no DST). APScheduler will run UTC. The `scheduler_timezone` display setting is for human reference only. **Confirm:** does the team expect the jobs to run at PHT times, or are the UTC expressions in n8n intentional? If PHT-native crons are wanted, shift all expressions by 8h.

3. **Biometric retention daily window.** S08 specifies `_process_biometric_retention` is called from the `QueueManager.run()` loop (which runs continuously). S16 adds an APScheduler trigger at 02:00 UTC. If both S08's continuous call and the APScheduler trigger are active simultaneously, the method runs twice per day. S08's method should be idempotent (it only purges records where `retention_until < NOW` or `deletion_requested_at IS NOT NULL AND purged_at IS NULL`). **Confirm with S08 implementer:** move the S08 worker-loop call to S16-only, or keep both (both-safe if idempotent).

4. **EOM Recompute exact cadence.** The n8n `Rescan EOM` node has cron `0 0 12 * * *` (daily noon) but is gated by a **disabled** `If` node checking `$now.plus({days:1}).day === 2`. This is a confusing partial implementation. The `n8n-analysis.md` canonical interpretation is "monthly" at midnight first of month (`0 0 1 * *`). **Confirm this is the intended cadence.** If daily is wanted, change `CRON_EOM_RECOMPUTE` to `"0 0 * * *"`.

5. **`send_attendance_notification` interface for S18.** S16 stubs `from app.services.notification_service import send_attendance_notification`. S18 must implement `async def send_attendance_notification(session_label: str, db: AsyncSession) -> dict`. S18 spec should reference this interface and confirm the signature before merging.

6. **Job history retention policy.** The `job_runs` table has no auto-purge. At 9 jobs × average 3 fires/day = ~27 rows/day = ~10k rows/year. Recommend a nightly purge of rows older than 90 days. Add as a 10th job in S20 or as an ops runbook note.

---

### Cross-sprint dependencies & touchpoints for the master to reconcile

- **S04** must deliver `backend/app/services/event_generator.py` with `generate_sunday_events(series_id, db) -> dict` and `generate_powerhouse_event(series_id, db) -> dict` before S16's Sunday/Powerhouse job functions are non-stub.
- **S23** must deliver `backend/app/services/member_status_service.py` with `async def recompute_all_contacts(db) -> dict` (returns at minimum `{contact_count: int, duration_ms: int}`). S16's `_run_tracked_job` reads `result['contact_count']` and `result['duration_ms']`.
- **S08** owns `QueueManager._process_biometric_retention`; S16 calls it from APScheduler. Confirm S08 does not remove this method from `QueueManager` as part of refactoring (see open question 3 above).
- **S18** must implement `backend/app/services/notification_service.py` with `send_attendance_notification(session_label, db)`. S16 establishes the import path and stub contract.
- **S15** widens `SystemStatusPage` from `AdminRoute` to `RoleRoute(['admin', 'volunteer', 'viewer'])`. S16 notes this but does not implement it (S15 owns RBAC routes). The manual-trigger button must remain admin-only regardless.
- **S02** — the "Custom Fields" tab in `SettingsPage` navigates to `/settings/custom-fields` (S02's admin page). S16 adds only the navigation link; S02 owns the page.
- **Shared model touchpoint:** `JobRun` is a new model in `models.py` created by S16. No other sprint depends on it in v1 except S23 (which reads `job_runs` to return `job_run_id` from on-demand recompute endpoint). Ensure S23 imports `JobRun` from `app.models` once S16 is merged.
- **`main.py` lifespan conflict risk:** S16 modifies `main.py:36-67` (the `lifespan` context manager). Any concurrent sprint modifying lifespan (e.g. S17 if it adds an outbox worker) must merge after S16 or coordinate the edit to avoid conflicts.
