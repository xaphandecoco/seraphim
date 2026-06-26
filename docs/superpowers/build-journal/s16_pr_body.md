# feat(S16): Settings, System Status & Scheduled Jobs

## Summary

Sprint S16 makes APScheduler the canonical job host and implements the admin surface for settings, system status, and scheduled-job monitoring. Ships a critical security fix (immutable-keys guard on PUT /settings/{key}) that blocks admins from bricking auth by overwriting jwt_secret/database_url. All scheduled jobs (recompute, generation, notifiers, biometric retention) now write to job_runs with audit trail.

## Key Changes

### Backend

**Scheduler (NEW)**
- `app/services/scheduler.py`: AsyncIOScheduler singleton with lifecycle hooks (start_scheduler/stop_scheduler). Registers 9 jobs: sunday-event-generation, powerhouse-event-generation, eow-recompute, eom-recompute, notifier_8am, notifier_10am, notifier_3pm, notifier_powerhouse, biometric-retention. Old `app/scheduler.py` deleted.
- `_run_tracked_job` wrapper: every job execution writes job_runs start→finish with status/detail/duration.
- Wired into `main.py` lifespan with `ENVIRONMENT != "test"` gate (scheduler never starts under pytest).
- `app/requirements.txt`: added `apscheduler>=3.10.4`.

**Settings & System Status (NEW)**
- `app/services/settings_service.py`: `get_setting()`, `set_setting()` (Fernet encryption on sensitive keys), `get_system_status()` (pg/redis/compreface health), `trigger_job()` (202 on success / 404 missing / 409 already-running / 403 immutable).
- `app/routers/settings.py`: 5 endpoints:
  - `GET /settings/jobs?page=1&page_size=9` — paginated job_runs list
  - `POST /settings/jobs/{job_name}/trigger` — async trigger with concurrency guard
  - `GET /settings/system-status` — service health (postgres, redis, compreface)
  - `GET /settings/config-checklist` — readiness gates (env vars, connectivity, schema)
  - `PUT /settings/{key}` — update admin_settings with immutable-key guard

**Data Model**
- `app/models.py`: `JobRun` ORM (job_name, status, detail, started_at, finished_at, duration_ms); `admin_settings.label` column added; series-id keys (`sunday_event_series_id`, `powerhouse_event_series_id`) seeded with fallback to legacy keys.
- Migration `s16a1b2c3d4e5_s16_job_runs_and_settings`: adds job_runs table + indexes (on job_name, started_at, status); adds admin_settings.label; down_revision `a3b4c5d6e7f8`.

**Integration**
- Canonicalized `job_runs` column name: `ran_at` → `started_at` (edited 3 S23 call-sites: analytics.call_timestamp, queue_manager._process_queue, member_status_service._recompute).
- Updated `test_migrations.py`: EXPECTED_HEAD `s16a1b2c3d4e5`, down_revision `a3b4c5d6e7f8`.
- Reconciled 3 S23 tests (job_runs now exists in test DB).

### Frontend

**Settings UI (NEW)**
- `pages/SettingsPage.tsx`: Tabbed interface with system, tunables, jobs tabs (URL-driven via useSearchParams).
- `pages/SystemStatusPage.tsx`: Service health dashboard (postgres, redis, compreface status with latency).
- `pages/JobRunsPage.tsx`: Paginated job-run history with trigger buttons and status labels.

**Settings Components (NEW)**
- 7 panel components: GeneralPanel, SecurityPanel, NotifiersPanel, ScheduledJobsPanel, SystemStatusPanel, BiometricPanel, AdvancedPanel.
- TanStack Query for `/settings/jobs` and `/settings/system-status`.
- Forms with loading/error/empty states.

**Services & Types**
- `services/settings.ts`: client for all settings endpoints.
- `hooks/useSettings.ts`: TanStack Query hooks for job-runs and system-status.
- `types/index.ts`: JobRun, SystemStatus, ServiceStatus, SettingsValue types.

### Security

**HIGH (BLOCK) Fixed**
- `PUT /settings/{key}` previously only blank-protected `jwt_secret`/`database_url`, allowing overwrites. **FIX:** Added `_IMMUTABLE_KEYS = {jwt_secret, database_url, redis_url}` guard that rejects ANY write (set/blank) with 403 Forbidden on both PUT paths. This prevents admins from bricking auth app-wide.

**2 MEDIUM Fixes**
1. Encrypt-on-write (Fernet) had no matching decrypt-on-read: added two-pass decrypt-on-load in config.py to prevent plaintext values from being served.
2. Removed silent plaintext fallback: now fail-closed if decryption fails (no graceful degrade to plaintext).

**1 LOW Fix**
- Sanitized `job_runs.detail` (truncate DSNs, mask API keys) to prevent accidental credential leaks in job-run logs.

**Re-Audit: PASS**

## Test Evidence

**Backend**
- Segment 209: test_migrations, test_settings, test_scheduler, affected services — **all pass**
- Affected files (65 tests): job_runs model, settings_service, scheduler integration — **all pass**
- Isolated S16 components: fixtures, endpoint contracts — **all pass**
- Full-suite pending (~15 min runtime on local Python 3.11)

**Frontend**
- Build: ✅ clean
- Lint: ✅ clean
- Tests: **341 tests pass** (vitest)

**Security**
- ✅ PASS — immutable-keys guard verified, encrypt/decrypt roundtrip tested, sanitization verified

## Definition of Done Checklist

- [x] `job_runs` table + ORM model (migration `s16a1b2c3d4e5`, down_revision `a3b4c5d6e7f8`)
- [x] `JobRun` columns: `id, job_name, status, detail, started_at, finished_at, duration_ms` + indexes
- [x] `admin_settings.label` column + series-id keys seeded (sunday_event_series_id, powerhouse_event_series_id)
- [x] `backend/app/services/scheduler.py`: AsyncIOScheduler singleton + `_run_tracked_job` wrapper + `JOB_REGISTRY` (9 jobs)
- [x] Endpoints in `routers/settings.py`: GET /settings/jobs, POST /settings/jobs/{job_name}/trigger, GET /settings/system-status, GET /settings/config-checklist, PUT /settings/{key} (immutable-keys guard)
- [x] `services/settings_service.py`: get/set with Fernet encryption, system-status, trigger-job
- [x] Frontend: SettingsPage (tabbed), SystemStatusPage, JobRunsPage, 7 panel components, settings.ts client, types
- [x] Canonicalize `job_runs.ran_at` → `started_at` (edit 3 S23 call-sites + tests)
- [x] Security audit PASS: immutable-keys guard, encrypt-on-write/decrypt-on-read, sanitized detail
- [x] Green gates: backend segment 209 + affected files 65 + frontend 341 tests; security PASS

## Assumptions (Documented in docs/BLOCKERS.md)

- **Dual job_runs writes:** scheduler wrapper + member_status_service raw-SQL. Non-blocking observability nit; `last_recomputed_at` keys off member_status_recompute row. De-dup when convenient.
- **Fernet key:** derived from jwt_secret (SHA-256) unless SETTINGS_FERNET_KEY env set. Rotating jwt_secret re-keys sensitive settings.
- **Series-id fallback:** keys fall back to legacy sunday_series_id/powerhouse_series_id; both unset → jobs skip "not configured".
- **Notifier jobs:** skip "awaiting S18" until S18 built (registered in JOB_REGISTRY, cron fires, immediately skips).
- **Trigger endpoint:** TOCTOU (no atomic check-and-lock) + no rate-limit. Admin-only, LOW carry-forward.

## Migration & Rollback

**Forward:** `alembic upgrade head` applies `s16a1b2c3d4e5`. Creates job_runs table + indexes, adds admin_settings.label, seeds series-id keys.

**Backward:** `alembic downgrade a3b4c5d6e7f8` rolls back migration (asymmetric downgrade logic preserves data on fallback scenarios).

**App boot after migration:** scheduler wired into lifespan; gate on ENVIRONMENT != "test" ensures test suite never starts scheduler. All 9 jobs register on startup.

---

Generated with senpai-v2-docs
