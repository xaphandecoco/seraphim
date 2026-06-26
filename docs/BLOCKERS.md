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

- [S09] **LOW:** Search/groups endpoints (`POST /search`, `POST /search/validate`, `POST /groups/{id}/populate`) not individually rate-limited. No `@limiter.limit` decorators; codebase-wide gap (same class as S16/S08 trigger endpoint, S16 settings endpoints). **FOLLOW-UP:** add limits to search endpoints or install SlowAPIMiddleware globally.

- [S09] **LOW:** Groups are org-wide visible to all volunteers; any volunteer can rename/delete any group. Only smart-criteria EDIT is admin-gated (populate_static_group). **DESIGN:** by DoD; confirm org-wide visibility is acceptable to owner.

- [S09] **OPTIMIZATION:** `multiselect` field `contains_any` uses LIKE-containment on the JSON text (LIKE `%\"value\"%`). Postgres-native `@>` JSONB containment would be faster at scale (GIN-indexable). **FOLLOW-UP:** migrate to JSONB operator when scale demands; low priority.

- [S09] **DB RESERVED WORD:** `groups` is a SQL reserved word; SQLAlchemy auto-quotes it. Raw SQL must quote `"groups"` or queries fail. No action needed (ORM handles it); note for team.

- [S09] **MERGE DEPENDENCY (S11):** When S11 (merge contacts) runs, it must add `group_members.contact_id` to its loser→survivor FK manifest (Contact Namespace item CN-04). Without it, merging a contact in a group leaves orphaned group_members rows pointing to a purged loser contact.

- [S08] **MEDIUM (non-blocking, security-PASS):** detection-crop FILE can orphan on a rare unlink OSError → `det.image_path` is cleared regardless of unlink success, so a file that failed to delete has no DB retry handle and a later clean retry stamps purged_at while the orphan remains. Bounded: requires a real FS error; orphan has NO queryable DB linkage (path/subject/matched_name cleared → no re-identification); time-based FaceCleanupService is a backstop. **FOLLOW-UP PATCH:** only clear image_path after successful unlink (keep row as retry handle) + keep purged_at NULL until detection-crop files confirmed gone.

- [S08] **LOW:** purge / immediate deletion-request return HTTP 200 even on result.status=='partial_failure'; immediate-purge path doesn't set deletion_requested_at so a not-yet-due partial failure isn't auto-retried unless admin re-invokes. **FOLLOW-UP:** set deletion_requested_at on the immediate path and/or surface a distinct partial_failure signal.

- [S08] Detection.image_path is NOT NULL → purge uses image_path="" sentinel (file IS unlinked); a future migration could make it nullable.

- [S08] CompreFace 404-as-success uses ComprefaceClient private attrs; refactor to a delete_subject_tolerant() method later.

- [S08] biometric_retention endpoints lack @limiter.limit (admin-only, carry-forward; same class as S16 trigger).

- [S08/backend] **ASSUMPTION:** `Detection.image_path` is `NOT NULL` in the ORM model (`models.py`) and the SQLite test schema. The S08 RTBF spec says to set `image_path=None` when anonymizing detection crops during a purge. The Wave 1 migration (`s08a1b2c3d4e5`) did not make this column nullable. The `BiometricPurgeService` uses `""` (empty string) as the cleared-path sentinel to satisfy the SQLite constraint; the actual file is unlinked from disk in either case. **Action needed (DB engineer):** Add an Alembic migration (S09 or as a patch) to `ALTER TABLE detections ALTER COLUMN image_path DROP NOT NULL` and update `Detection.image_path` to `Mapped[Optional[str]]` in `models.py`. Once done, change `biometric_purge.py` line `det.image_path = ""` to `det.image_path = None` and update the test assertion.

- [S08/backend] **ASSUMPTION:** `BiometricPurgeService._delete_cf_subject` accesses `client.base_url` and `client.recognize_api_key` attributes directly (and `client._client` for the raw httpx call) to intercept the HTTP 404 response before `delete_subject()` would return False. This works with the current `ComprefaceClient` internals. If `ComprefaceClient` is refactored, update `_delete_cf_subject` to match. Alternative: add a `delete_subject_with_404_ok()` method to `ComprefaceClient` (compreface.py — DB/ML engineer's file) and call it from the purge service.

- [S08/frontend] **ASSUMPTION:** `User.role` in `frontend/src/types/index.ts` is typed `'volunteer' | 'admin'` only — no `'viewer'` type exists yet (S01 spec mentions viewer role but S15 is pending). For mutation-control gating, "volunteer+" is treated as any non-null `user` object (`!!user`). When S15 lands and adds `viewer` to the role union, `ConsentPanel` needs a second check `user.role !== 'viewer'` to gate the button strip. Affects: `frontend/src/components/contacts/ConsentPanel.tsx`.

- [S08/frontend] **ASSUMPTION:** FacePanel uses query key `['contacts', contactId, 'faces']` (confirmed by reading `FacePanel.tsx:57`). The spec says to invalidate `['contact-faces', contactId]` after a purge — these do not match. The `ConsentPanel` purge handler invalidates the actual FacePanel key `['contacts', contactId, 'faces']` so thumbnails clear correctly. Affects: `frontend/src/components/contacts/ConsentPanel.tsx`.

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

- [S10/frontend] **ASSUMPTION:** `User.role` in `types/index.ts` is `'volunteer' | 'admin'` only (no `'viewer'` — S15 pending). `isVolunteer` is derived as `role === 'admin' || role === 'volunteer'`. When S15 adds `'viewer'` to the type union, authStore already handles it correctly (any role not in `{admin, volunteer}` returns `isVolunteer: false`). No code change needed in authStore on S15 landing. Affects: `frontend/src/store/authStore.ts`.

- [S10/backend Wave 2] **ASSUMPTION:** `normalize.name_key(first, last)` takes TWO arguments (confirmed by reading mapper.py). The suggest.py normalization helper uses a custom `_nk(s)` function (`re.sub(r"[^a-z0-9]", "", s.lower())`) instead of the two-arg `name_key` since we are normalizing individual header/field names (not full person names). This matches the spec intent and passes all tests. Affects: `backend/app/services/imports/suggest.py`.

- [S10/backend Wave 2] **ASSUMPTION:** `map_participant_row` (S06 mapper) requires `event_ref` in the mapped row data, even when the wizard provides `target_event_id` at the API level. When `target_event_id` is present and no column is mapped to `event_ref`, `run_import` injects a synthetic `_wizard_event_ref_` column into both `s06_map` and each raw row so the S06 mapper sees the required field. This avoids modifying the S06 mapper and preserves its contract. Affects: `backend/app/services/imports/runner.py`.

- [S10/backend Wave 2] **ASSUMPTION:** `_purge_expired_imports` transitions staged batches to `status='expired'` and sets `staging_file=None` after deleting the file. It does NOT call `batch.delete()` or remove the ImportBatch row — the row is kept for audit/history. Affects: `backend/app/services/queue_manager.py`.

- [S10/backend Wave 2] **ASSUMPTION:** Header BOM stripping in `staging.py` uses `.strip('﻿')` (defensive) because real-world Windows-exported CSV files sometimes have two BOM sequences (one from the content string, one from the utf-8-sig codec). The staging code strips any leading/trailing BOM characters from header names. Affects: `backend/app/services/imports/staging.py`.

- [S10/frontend] **ASSUMPTION:** The wizard VolunteerRoute uses a NEW `VolunteerRoute.tsx` (does not modify `ProtectedRoute.tsx` per ownership constraints). Admin users get the Import nav entry inside the existing admin "More" sheet; non-admin volunteers get a direct Import tab in the bottom nav. Affects: `frontend/src/components/layout/BottomNav.tsx`, `frontend/src/components/layout/VolunteerRoute.tsx`.

- [S10/frontend] **ASSUMPTION:** `target_event_id` for participants mode uses a simple numeric input (not the full S04 event picker component) since S04's event search/select component interface is not documented in the sprint task. When S04's event picker component is available, swap the `<input type="number">` in `MapStep.tsx` for the S04 component. Affects: `frontend/src/components/imports/MapStep.tsx`.

- [S10/frontend] **ASSUMPTION:** S10 pages are lazy-loaded in `App.tsx` (matching the S16 pattern for TanStack-Query-heavy pages) rather than eagerly imported. This keeps the initial bundle lean and prevents a test-suite timeout in the AC10 dynamic-import check in `BulkPhotoUploadPage.test.tsx`. Affects: `frontend/src/App.tsx`.

- [S10/backend] **ASSUMPTION:** Staged-file TTL is 24h (config constant `IMPORT_STAGING_TTL_HOURS = 24`). Shorten for PII privacy without migration by editing the constant. Affects: `backend/app/config.py`.

- [S10/backend] **ASSUMPTION:** Preset uniqueness is GLOBAL per entity: `uq_import_mapping_preset_entity_name` (entity, name) unique constraint. A preset name colliding with ANY owner's preset for that entity → 409 Conflict. Affects: `backend/app/models.py`, `routers/imports.py` preset endpoints.

- [S10/backend] **ASSUMPTION:** Participant import uses `ON CONFLICT(event_id, contact_id) DO NOTHING` (idempotent re-run; source='import'). Duplicate participant rows in the same import batch are silently deduplicated. Affects: `backend/app/services/imports/runner.py`.

- [S10/backend] **ASSUMPTION:** SlowAPIMiddleware still not registered globally; S10 endpoints use explicit `@limiter.limit` decorators (same codebase-wide gap noted in S09/S16). `POST /imports/upload`, `POST /imports/preview`, `POST /imports/run` all have decorators. **FOLLOW-UP:** install SlowAPIMiddleware globally or add limits to remaining search endpoints. Affects: `backend/app/routers/imports.py`, `main.py`.

- [S10/backend] **ASSUMPTION:** `map_participant_row` (S06 mapper) requires `event_ref` in the mapped row data, even when the wizard provides `target_event_id` at the API level. When `target_event_id` is present and no column is mapped to `event_ref`, `run_import` injects a synthetic `_wizard_event_ref_` column into both `s06_map` and each raw row so the S06 mapper sees the required field. This avoids modifying the S06 mapper and preserves its contract. Affects: `backend/app/services/imports/runner.py`.

- [S10/backend] **ASSUMPTION:** `_purge_expired_imports` transitions staged batches to `status='expired'` and sets `staging_file=None` after deleting the file. It does NOT call `batch.delete()` or remove the ImportBatch row — the row is kept for audit/history. Affects: `backend/app/services/queue_manager.py`.

- [S10/backend] **ASSUMPTION:** Header BOM stripping in `staging.py` uses `.strip('﻿')` (defensive) because real-world Windows-exported CSV files sometimes have two BOM sequences (one from the content string, one from the utf-8-sig codec). The staging code strips any leading/trailing BOM characters from header names. Affects: `backend/app/services/imports/staging.py`.

- [S10/backend] **ASSUMPTION:** import_batch.mode widened to VARCHAR(20) on Postgres (wizard_preview=14 chars); downgrade does NOT narrow it (truncation risk). Affects: `backend/alembic/versions/s10a1b2c3d4e5_s10_csv_xlsx_import_wizard.py`, `backend/app/models.py`.

---
### Format
```
- [SPRINT] **ASSUMPTION:** <what was unknown> → chose <default> because <reason>. Affects: <files/sprints>.
```
