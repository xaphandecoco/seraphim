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

---
### Format
```
- [SPRINT] **ASSUMPTION:** <what was unknown> → chose <default> because <reason>. Affects: <files/sprints>.
```
