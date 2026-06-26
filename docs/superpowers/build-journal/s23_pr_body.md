# feat(S23): member status & engagement engine

## Summary

Automatic member engagement tracking via 7 derived snapshot columns written to the `contacts` table. Admins can trigger recomputation on-demand via a new endpoint, and background jobs will recompute engagement metrics weekly (Mon 00:00 UTC) and end-of-month (1st 00:00 UTC) once S16's scheduler is available. The scheduler integration is guarded by `HAS_SCHEDULER=False` to allow S23 to ship before S16 without runtime errors.

## What Changed

### Backend
- **`backend/app/services/member_status_service.py`** (new): Core recomputation logic. `recompute_all_contacts()` reads attendance history from participants + events, computes engagement tiers based on attendance frequency/recency, and writes 7 snapshot columns. Exported with alias for S06 data-migration runner contract compatibility.
- **`backend/app/scheduler.py`** (new): APScheduler job registration (weekly + EOM cron jobs). Jobs are registered but dormant behind `HAS_SCHEDULER=False` flag in `main.py`; activation waits for S16 to supply a real AsyncIOScheduler instance.
- **`backend/app/constants.py`** (new): Tier thresholds, default connected-field names, timezone config.
- **`backend/app/utils/db_helpers.py`** (new): Database utility helpers (batch attendance fetch, week calculations).
- **`backend/app/main.py`**: lifespan updated to call `register_s23_jobs(...)` with Ellipsis placeholder behind `HAS_SCHEDULER=False` guard.
- **`backend/app/models.py`**: Contact model adds 7 snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier` enum, `is_active`, `is_regular`, `is_connected`) + 5 indexes for query performance.
- **`backend/app/routers/analytics.py`** (updated): Added two endpoints:
  - `POST /analytics/recompute-member-status` — admin-only, triggers on-demand recomputation of all contacts
  - `GET /analytics/member-status-summary` — fast aggregate (counts by tier, active/inactive, engagement status)
- **Migration `a3b4c5d6e7f8_s23_member_status_snapshot_guard.py`** (new): Adds 7 snapshot columns + 5 indexes; chains from S24 migration `s24a1b2c3d4e5`.

### Frontend
- **`frontend/src/types/index.ts`** (updated): Added snapshot fields to `ContactDetail` type (tier, is_active, is_regular, is_connected, last_attended_at, attendance_count, weeks_absent).
- **`frontend/src/components/ui/StatusBadge.tsx`** (updated): Renders engagement tier with label mapping (tier0/tier1/tier2/tier3/inactive). Handles null `is_active` gracefully.
- **`frontend/src/pages/ContactDetailPage.tsx`** (updated): Displays member status snapshot via StatusBadge component; adds null-guard for `is_active` field.

### Tests
- **`backend/tests/test_member_status_service.py`** (new): Unit tests for recomputation logic, tier computation, snapshot correctness.
- **`backend/tests/test_analytics_member_status.py`** (new): HTTP integration tests for recompute and summary endpoints.
- **`backend/tests/test_db_helpers.py`** (new): Tests for database utility functions.
- **`frontend/src/components/ui/StatusBadge.test.tsx`** (new): Component rendering and tier label tests.

## Test Evidence

**Backend full-suite gates (~1177 tests, all passing):**
```
backend full suite: ~1177 passed, 0 failed
ruff check: clean (no new violations)
```

**Frontend gates:**
```
npm run build: success
npm run lint: success
npm run test:run: all tests passing
```

**Security:** PASS — no new vulnerabilities, auth checks in place for admin endpoint.

## Integration Fixes (QA Full-Suite Gate)

The full-suite test run uncovered three issues (all fixed before commit):

1. **Function export mismatch (9 t06 failures):** S06's migration runner (`backend/app/runner.py`) calls `member_status_service.recompute_all_contacts(db)`, but the service initially exported only `recompute_all()`. Added alias export to match runner's expected name.

2. **Test expectation staleness (test_migrations.py failures):** `EXPECTED_HEAD` and down_revision checks were stale from S06, still pointing at `eed28c4ef46a` / `c1d2e3f4a5b6`. Updated to S23 migration head `a3b4c5d6e7f8` and down_revision `s24a1b2c3d4e5`.

3. **Missing transitive dependency (33 collection errors):** `anthropic>=0.40.0` (required by S22's name-match service) was not in the local test env, causing pytest collection failures on name-match imports. Installed.

## Definition of Done Checklist

- [x] Contact model adds 7 snapshot columns (last_attended_at, attendance_count, weeks_absent, tier, is_active, is_regular, is_connected)
- [x] Migration adds columns + 5 indexes; chains from S24; alembic head = a3b4c5d6e7f8
- [x] member_status_service.recompute_all_contacts() exported with S06-compatible alias
- [x] POST /analytics/recompute-member-status (admin-only endpoint)
- [x] GET /analytics/member-status-summary (fast summary with pagination)
- [x] APScheduler jobs registered (weekly Mon 00:00 UTC, EOM 1st 00:00 UTC)
- [x] Jobs guarded by HAS_SCHEDULER=False; main.py calls register_s23_jobs(...) with placeholder
- [x] Frontend StatusBadge + ContactDetailPage integration
- [x] Backend full suite green (~1177 passed)
- [x] Frontend build + lint + tests green
- [x] Security audit PASS
- [x] All integration fixes applied (function alias, test expectations, dependency)
- [x] Hardened: snapshot write committed FIRST, then best-effort audit (DoD B16)

## Assumptions Documented (BLOCKERS.md)

- **Tier timezone:** EOW Mon 00:00 UTC default (confirm PHT); EOM cadence `0 0 1 * *`
- **Connected-field names:** default `{community_leader, community}`, overridable via `admin_settings.connected_field_names`
- **Weeks-absent formula:** calendar-day diff with start-of-day truncation
- **job_runs schema (S16):** raw-SQL INSERT uses `(job_name, status, detail, ran_at)`, fails silently if schema diverges
- **S16 hard blocker:** `main.py` lifespan passes `...` (Ellipsis) to `register_s23_jobs()` behind `HAS_SCHEDULER=False`. When S16 lands and enables the flag, MUST pass a real AsyncIOScheduler or boot will TypeError.

## Rollback & Rollforward

**Rollback:** Run `alembic downgrade s24a1b2c3d4e5` to remove snapshot columns and indexes. Service layer has no external dependencies; routers can be safely 404'd.

**Rollforward (after S16 ships):** Flip `HAS_SCHEDULER=True` in `.env` or `settings.py`. S16 must provide a real AsyncIOScheduler instance to `register_s23_jobs()`. No data migration needed (snapshot columns already present).

## Next Sprint

S16 (Job Runner & Scheduler Foundation) — unblocks S23's APScheduler integration once the `AsyncIOScheduler` is available.

---

Co-Authored-By: Claude (Haiku 4.5) <noreply@anthropic.com>
