# BLOCKERS & ASSUMPTIONS — senpai-v2 sprint loop

Deferred open questions. Agents never stall: on an open question they pick a documented
default, record it here as an `**ASSUMPTION:**`, and continue. The human answers these at
the end of a run. **Hard blockers (could build on bad data) are pinned at the top.**

> Note from v1 retro: S02's stubbed option-lists were *required before the S06 dry-run* —
> if a hard blocker like that is silently deferred, the loop will happily build on bad data.
> Pin anything load-bearing here and raise it loudly before the dependent sprint runs.

## 🔴 Hard blockers (resolve before the dependent sprint)
- **[S21 cutover] Dependencies NOT met — do not autonomously build the full cutover.** Spec assumes S01–S23 built; repo has only S01–S07, S22, S24. Missing & load-bearing: **S16** (`JobRun` model + `/job-runs` router — `/migration/verify` won't import without it), **S15** (viewer role), **S18** (Zoom/Google Chat), **S23** (nightly recompute). Also requires **live prod data, real CiviCRM exports, DNS/Cloudflare flip, operator action** — not autonomously verifiable.
  - **Owner decision pending:** (a) build S21 *artifacts-only* (settings-cleanup migration, defensive `/migration/verify`, `smoke_test_crm.py`, runbook rewrite, cutover docs) and mark live steps operator-executed [architect's recommendation]; OR (b) build S15/S16/S18/S23 first; OR (c) stay paused. **Currently: PAUSED at owner request (2026-06-26).**
  - **Sub-blockers when S21 does run:** re-pin migration `down_revision` to live `alembic heads` (not a stale rev); reconcile `Participant.source` literal — S06 writes `'migration'`, spec §6.4 verify expects `'import'` → align verify endpoint + verification.md to the as-built value or the count check silently reads zero.

## 🟡 Assumptions (documented defaults — confirm when convenient)

- [S23] **ASSUMPTION:** tier timezone default is EOW Mon 00:00 UTC (confirm PHT); EOM cadence is `0 0 1 * *`; connected-field names default `{community_leader, community}` (overridable via admin_settings `connected_field_names`); is_active formula is None when never-attended, False when inactive tier. Affects: `backend/app/services/member_status_service.py`, snapshot read/write logic.

- [S23] **ASSUMPTION:** `job_runs` table schema (S16) is unknown at implementation time. The raw-SQL INSERT in `member_status_service._recompute` uses columns `(job_name, status, detail, ran_at)`. If S16's actual schema differs (e.g., no `status` / `detail` columns), the INSERT will fail silently (try/except) and `job_run_id` returns None — no crash, no data loss. Affects: `backend/app/services/member_status_service.py`.

- [S23] **ASSUMPTION:** `admin_settings` row with `key="connected_field_names"` stores its value as a JSON list (e.g., `["community_leader", "community"]`) OR as a dict with a `"names"` key. Fallback is `DEFAULT_CONNECTED_FIELD_NAMES`. No such row in test DB; tests use the default. Affects: `backend/app/services/member_status_service.py`.

- [S23] **ASSUMPTION:** `weeks_absent` is computed as calendar-day difference (both `today_midnight` and `last_attended_at` are truncated to start-of-day before dividing by 7). This prevents rounding errors when events have non-midnight `start_at`. Affects: snapshot fields on Contact.

- [S23/S16] **ASSUMPTION:** `main.py` lifespan calls `register_s23_jobs(...)` with an Ellipsis placeholder behind `HAS_SCHEDULER=False`; when S16 lands and flips the flag it MUST pass a real AsyncIOScheduler or boot will TypeError. Affects: `backend/app/main.py`, `backend/app/scheduler.py`. **HARD note for S16 implementation.**

- [S16] **ASSUMPTION:** `GET /settings/system-status` response shape uses a flat `services: { postgres: boolean, redis: boolean, compreface: boolean }` object (per the locked API contract in the task), NOT the `ServiceStatusItem { ok, detail, latency_ms }` shape in the Pydantic schema (spec §4.2). The frontend `SystemStatus` type and `SystemStatusPanel` component are built against the flat boolean shape. If the backend emits the richer shape, update `SystemStatus.services` in `types/index.ts` and adjust `SystemStatusPanel`. Affects: `frontend/src/types/index.ts`, `frontend/src/components/settings/SystemStatusPanel.tsx`.

- [S16] **ASSUMPTION:** `ScheduledJobsPanel` shares the `['settings', 'system-status']` TanStack Query cache with `SystemStatusPage` to surface last-run status per job. If the backend's `/settings/system-status` jobs array becomes expensive to compute, the panel can be switched to use `/settings/jobs?page=1&page_size=9` instead without changing the public API contract. Affects: `frontend/src/components/settings/ScheduledJobsPanel.tsx`.

- [S16/Wave2] **ASSUMPTION:** `PUT /settings/{key}` returns 404 for unknown keys because requiring keys to pre-exist in the DB prevents phantom-key proliferation. The alternative (auto-creating any key) was not chosen. Callers must ensure the key row exists (e.g., seeded by bootstrap or prior setup) before calling PUT. If the product needs to CREATE new settings dynamically via this endpoint, flip to upsert logic. Affects: `backend/app/routers/settings.py`.

- [S16/Wave2] **ASSUMPTION:** `GET /settings/system-status` `overall` logic: `ok` = pg+redis both up; `degraded` = pg up but redis down (or compreface down with pg+redis ok); `down` = pg down. CompreFace down alone does NOT set overall to 'degraded' (matches spec wording "compreface down/unset, pg ok → degraded"). The settings_service currently maps only pg+redis to overall; compreface is surfaced in `services.compreface` but does not downgrade overall. Revisit if the frontend needs compreface-down to degrade overall. Affects: `backend/app/services/settings_service.py`.

- [S16/Wave2] **ASSUMPTION:** Notifier jobs (notifier_8am, notifier_10am, notifier_3pm, notifier_powerhouse) always record status='skipped' with detail='awaiting S18' until S18 is built. They ARE registered in JOB_REGISTRY and will appear in the trigger endpoint's 202 response. Cron triggers are registered and fire on schedule but immediately skips. Affects: `backend/app/services/scheduler.py`.

- [S16] **ASSUMPTION:** `SettingsPage` tabs use `useSearchParams` for URL-driven state (`?tab=system|tunables|jobs`). Default tab is `system`. The Jobs tab mounts `ScheduledJobsPanel` (TanStack Query) lazily — only when selected — so existing `SettingsPage.test.tsx` tests (which never switch tabs) do not need a `QueryClientProvider` wrapper. Affects: `frontend/src/pages/SettingsPage.tsx`, `frontend/src/pages/SettingsPage.test.tsx`.

- [S16] **ASSUMPTION:** Scheduled recompute jobs write TWO `job_runs` rows — one from the scheduler's `_run_tracked_job` wrapper, one from `member_status_service`'s internal raw-SQL insert. Non-blocking observability nit; `member-status-summary.last_recomputed_at` keys off the `member_status_recompute` row. Consider de-dup when convenient. Affects: `backend/app/services/scheduler.py`, `backend/app/services/member_status_service.py`.

- [S16] **ASSUMPTION:** Fernet key for sensitive settings (`jwt_secret`, `database_url`, `redis_url` at-rest encryption) derived from `jwt_secret` via single SHA-256 hash unless `SETTINGS_FERNET_KEY` env var is set. Rotating `jwt_secret` re-keys sensitive settings. (LOW, prod note.) Affects: `backend/app/services/settings_service.py`, `backend/app/config.py`.

- [S16] **ASSUMPTION:** Series-id keys `sunday_event_series_id`/`powerhouse_event_series_id` in `admin_settings` fall back to legacy `sunday_series_id`/`powerhouse_series_id` if the new keys are unset; both unset → generation jobs skip with "not configured" detail. Affects: `backend/app/services/scheduler.py` job registry.

- [S16] **ASSUMPTION:** Notifier jobs (notifier_8am, notifier_10am, notifier_3pm, notifier_powerhouse) are registered in `JOB_REGISTRY` and appear in the trigger endpoint's 202 response. Cron fires on schedule but immediately skips with status='skipped' detail='awaiting S18' until S18 is built. Affects: `backend/app/services/scheduler.py`.

- [S16] **ASSUMPTION:** `POST /settings/jobs/{job_name}/trigger` concurrency guard is TOCTOU (no atomic-check-and-lock). No rate-limit on trigger calls (admin-only, LOW carry-forward). Affects: `backend/app/routers/settings.py`.

---
### Format
```
- [SPRINT] **ASSUMPTION:** <what was unknown> → chose <default> because <reason>. Affects: <files/sprints>.
```
