# Sprint History

Living index of Project Seraphim's autonomous sprint loop. Tracks the **24 planned sprints** (S01–S24), their completion status, and historical progress toward production launch (FR transition complete as of S24).

## What the Sprint Loop Tracks

The **SPRINT-LOOP-LOG.md** (`docs/superpowers/SPRINT-LOOP-LOG.md`) is the master journal for the unattended build loop. It records:
- **Owner blockers & questions** (pinned at top for waking review)
- **Autonomous decisions** made during overnight runs (e.g., design choices, model overrides)
- **Per-sprint procedure**: Check → Definition of Done → Build (senpai agents) → Critique (Opus) → Commit → Checkpoint
- **Build DAG & execution order** — which sprints run before others (e.g., S01 → S02 → {S03, S07 parallel})
- **Failure cap & remediation loops** — up to 3 rounds per sprint before escalation
- **Git workflow** — one commit per passing sprint, no push (branch: `docs/crm-specs-and-cve-remediation`)
- **Token/cron management** — 15-min watchdog cron for auto-recovery on session limit; 5-hour rolling window
- **Resume playbook** — how to recover from a build killed on token limit via the journal

## Sprint Numbering & Current Status

**Completed (committed, green gates passed):**

| Sprint | Title | Status | Commit | Date | Notes |
|--------|-------|--------|--------|------|-------|
| S01 | Schema inversion & CiviCRM excision | ✅ Committed | `2ee70d7` | 2026-06-25 | Deleted CiviCRM as system-of-record; minted Seraphim PKs; renamed core tables; added audit_log |
| S02 | Dynamic custom-field engine | ✅ Committed | `14c4329` | 2026-06-25 | 8-field-type engine (contact groups + validation); admin CRUD UI; QA process bug fixed (Postgres-only ACs) |
| S07 | Face enrollment & bulk photo ingestion | ✅ Committed | `8d87084` | 2026-06-25 | String-keyed CompreFace subjects; batch face enrollment; survived token-limit kill + journal resume |
| S03 | Contact CRUD & profile | ✅ Committed | `a7839af` | 2026-06-25 | Contact detail page; custom-field resolution; chip rendering; depends on S01+S02 |
| S04 | Event CRUD & management | ✅ Committed | `37608a9` | 2026-06-25 | Event series + session management; migration/model reconciliation fixed by Opus critic |
| S05 | Bulk participants & export at scale | ✅ Committed | `c753618` | 2026-06-25 | Streaming CSV exports; async jobs; security audit (path-traversal, formula-injection fixed); Opus found `audience.ids` bug |
| S22 | Name-matching for duplicate contacts | ✅ Committed | Backend `cc64c57`, Frontend `79fbe77` | 2026-06-25 | Claude-powered entity matching; NameMatchReviewQueue; pending_review workflow; stuck-workflow postmortem + 15-min watchdog |
| S06 | Data migration / ETL (CiviCRM → Seraphim) | ✅ Committed | `a0951ca` | 2026-06-25 | Dry-run + live import phases; per-row audit trail (ImportBatch/ImportRowResult); Opus found 3 bugs (dry-run audit loss, divergence, FE contract) |
| S24 | FR transition & cutover bridge | ✅ Committed | (PR created) | 2026-06-25 | BiometricConsent model + migration; remap_subjects service; consent backfill; verification endpoint; orphan relink/retire UI; FR Status Panel in Settings |
| S23 | Member status & engagement engine | ✅ Committed | `3e52929` | 2026-06-26 | Snapshot recompute (7 derived columns); admin on-demand endpoint + fast summary; 2 APScheduler cron jobs (HAS_SCHEDULER guarded); 3 integration fixes in QA |
| S16 | Settings, system status & scheduled jobs | ✅ Committed | `<pending>` | 2026-06-26 | APScheduler host (AsyncIOScheduler, 9 jobs), job_runs table + ORM model; settings endpoints (GET/PUT); system-status + config-checklist; security BLOCK→fix (immutable keys protection); started_at canonicalization |

**Queued (awaiting build):**

| Sprint | Title | Status | Notes |
|--------|-------|--------|-------|
| S21 | (Final cutover) | ⏳ Queued | Depends on S24 completion; next after S24 committed |
| S08–S20 | (Remaining leaves) | ⏳ Queued | Backlog items; sequenced after S21 |

## Notable Completed Sprints

### S01: Schema Inversion & CiviCRM Excision

**Goal:** Delete CiviCRM as system-of-record; Seraphim owns its own PKs; nullified external_id UNIQUE constraint.

**Outcome:**
- Renamed `civicrm_members` → `contacts`, `civicrm_events` → `events`, `attendance` → `participants`
- Added `audit_log` table for compliance
- FKs repointed: `compreface_subjects.contact_id → contacts.id`
- Stashed legacy `_legacy_civicrm_contact_id`, `_legacy_civicrm_event_id` columns for S24 face remap
- Deleted `services/civicrm.py` and all dead CiviCRM client references
- Backend: 326 passed; Frontend: 39 passed; ruff advisory (not installed locally; CI-gated)
- Opus critic: 0/13 DoD criteria unmet; confidence PASS

---

### S02: Dynamic Custom-Field Engine

**Goal:** Replace hardcoded CiviCRM custom fields with an admin-configurable schema (8 field types, groups, validation).

**Outcome:**
- 2 tables: `custom_field_group`, `custom_field_def` with proper indexes + uuniques
- 6 contact groups seeded (PEPSOL stages, Ministry, Community, Followup-status, Membership-class, Interests); option lists stubbed pending owner UI confirmation
- `validate_and_coerce` service handles all 8 types (text, number, date, select, multiselect, contact_reference, checkbox, textarea)
- 9-endpoint admin router with RBAC (admin write / volunteer read / unauth 401)
- Audit logging for all mutations
- Backend: 502 passed; Frontend: 82 passed; ruff clean
- **Incident:** QA loop spun 5 rounds on Postgres-only migration ACs that SQLite couldn't verify. Process bug fixed (all future PLANs warn senpai about Postgres-only gates). Expert-QA found 2 real defects (seed type mismatch, missing round-trip test) + 1 spec nit (`at` → `created_at`); all fixed in single 78K-token round.
- Opus critic: PASS

---

### S07: Face Enrollment & Bulk Photo Ingestion

**Goal:** Design & implement the face-capture pipeline (RTSP → CompreFace → storage + detection rows).

**Outcome:**
- **Design decision:** adopted as-built STRING-keyed `compreface_subject_id` FK (deviated from spec's integer design; more natural, compatible with S24 remap)
- Face enrollment: create `compreface_subject`, enroll photos, store face samples + thumbnails
- Bulk photo ingestion via ZIP upload; background job queues frames
- RTSP worker pulls frames from cameras in real-time
- Green gates: backend 722 passed; frontend (specs indicate 310+ tests)
- **Resilience:** build died on token limit (6:10 AM); journal resume + runId recovered all completed agents; avoided full rebuild
- Opus critic: PASS; design choice carried forward

---

### S06: CiviCRM Data Migration / ETL

**Goal:** Implement a dry-run-capable bulk participant import (backfill attendance from CiviCRM).

**Outcome:**
- 2-phase runner (dry_run → inspect → live)
- Per-row audit trail: `ImportBatch` → `ImportRowResult` (outcome, error_detail, contact_id)
- Dry-run batches persist row results but roll back core writes (allows operator to inspect before committing)
- API endpoints: list batches, preview push, commit import
- Frontend migration UI (T10 view)
- **Opus critic found 3 bugs:**
  1. (BLOCKER) Dry-run dropped all audit rows — `db.add(irr)` was inside the savepoint rollback; fixed by deferring add until after rollback
  2. (DIVERGENCE) `import_batch.mode` stored invented `'upsert'` instead of spec's `'dry_run' | 'live'`; fixed + test added
  3. (FE CONTRACT) Frontend built against imagined API (listBatches response shape, field names, missing mode); realigned to actual ImportBatchOut contract
- Also: **latent S22 bug discovered** — NameMatchReviewQueue missing raw_payload/source columns; S06 T01/T02 fix it (architect override on spec §3.3, spec itself internally inconsistent)
- Green gates: backend 1102 passed; frontend 323 tests; ruff clean; build clean
- Opus verdict: PASS after fixes

---

### S22: Name-Matching for Duplicate Contacts

**Goal:** Claude-powered entity matching to find duplicate contacts and suggest merges.

**Outcome:**
- `NameMatchReviewQueue` model (with S06's added raw_payload + source columns)
- `match_name()` module-level service (TypedDict return); branches on `outcome == 'SINGLE'`
- Admin review UI for pending matches
- `pending_review_count` endpoint
- Pending participants can be linked via the review workflow
- **Key tension resolved:** spec §4.3 `match_name` signature vs. master §2.6 frozen contract; architect resolved with keyword-compatible signature
- **Postmortem:** S22 build hung for 101 minutes (F06/F07 agents in 90-min reasoning loops). Recovery actions:
  1. Watchdog cron reduced from 60m to 15m (threshold 20m)
  2. gen_build.py injects `effort: 'medium'` default on engineer agents (bounds reasoning)
  3. Stuck-workflow detection now tracks both PLAN and IMPLEMENT building markers
- Green gates: backend 963 passed; frontend 310 tests + build + lint
- Opus verdict: PASS

---

### S24: FR Transition & Cutover Bridge

**Goal:** Implement the bridge from the legacy face-recognition system to new data model. Enable biometric consent tracking and orphan subject relinking.

**Outcome:**
- **BiometricConsent** model + migration (tracks subject enrollment + revocation)
- `remap_subjects` service (compreface_subject_id → contact_id mapping during cutover)
- Consent backfill (populate consent rows from enrollment history)
- Verification endpoint for subject/contact alignment
- Orphan subject relinking UI (in Settings → Face Management)
- Retire subject UI (archive without deletion)
- **FR Status Panel** in SettingsPage (shows enrollment status, pending consent, actions)
- Green gates: 82 S24 tests pass; frontend 323/323; ruff clean; build passes
- **Carry-forward:** full-suite test ordering issue (test_task_service / test_uploads fail in random-order full run but pass in isolation; pre-existing, not a code bug)
- Next sprint when loop resumes: S21 (final cutover; depends on S24)

---

### S23: Member Status & Engagement Engine

**Goal:** Implement a member-status recompute engine writing 7 derived snapshot columns tracking attendance, engagement tier, and connection status. Serve via admin on-demand endpoint and fast summary endpoint; schedule background recomputation via APScheduler (guarded by S16 availability).

**Outcome:**
- **Snapshot columns** on `contacts` table: `last_attended_at`, `attendance_count`, `weeks_absent`, `tier` (tier0/tier1/tier2/tier3/inactive), `is_active`, `is_regular`, `is_connected`
- `backend/app/services/member_status_service.py` (new): `recompute_all_contacts()` (exported as `recompute_all_contacts` alias per S06 runner contract)
- Backend APIs: `POST /analytics/recompute-member-status` (admin-only, on-demand); `GET /analytics/member-status-summary` (fast aggregate)
- `backend/app/scheduler.py` (new): two cron jobs registered (weekly recompute Mon 00:00 UTC, end-of-month recompute 1st at 00:00 UTC) — guarded by `HAS_SCHEDULER=False` flag pending S16 scheduler availability
- Frontend `StatusBadge.tsx` + `ContactDetailPage.tsx` (updated) — tier label rendering; null-guard for `is_active`
- Migration `a3b4c5d6e7f8` adds 5 contact indexes + snapshot columns; chains from S24's `s24a1b2c3d4e5`
- Green gates: backend full suite + frontend green; security PASS
- **Integration fixes (QA gate):**
  1. Function name mismatch: S06 runner calls `recompute_all_contacts` but service initially exported `recompute_all` → AttributeError cascade (9 t06 failures). Fixed by adding alias.
  2. Stale test expectations: `test_migrations.py` `EXPECTED_HEAD` pointed to S06 (`eed28c4ef46a`), tests expected S06 down_revision (`c1d2e3f4a5b6`). Updated to S23 head `a3b4c5d6e7f8` / down_revision `s24a1b2c3d4e5`.
  3. Missing dependency: `anthropic>=0.40.0` (used by S22 name-match) not installed in test env → 33 collection errors. Installed.
- **Hardened:** audit/commit ordering — snapshot write FIRST, then best-effort audit (so audit failure can't drop snapshot per DoD B16)
- **Carry-forward assumptions:** tier timezone (EOW Mon 00:00 UTC default, confirm PHT); connected-field names default `{community_leader, community}` (overridable); `job_runs` schema reconciliation pending S16 (INSERT fails silently if schema diverges); S16 must pass real AsyncIOScheduler to `register_s23_jobs` or boot will fail

---

### S16: Settings, System Status & Scheduled Jobs

**Goal:** Make APScheduler the canonical job host. Add job-run tracking table + audit logging. Implement settings/system-status/scheduled-jobs admin surface with Fernet-encrypted sensitive values. Fix security vulnerability in settings-write endpoint.

**Outcome:**
- **Canonical job_runs table** (migration `s16a1b2c3d4e5`, down_revision `a3b4c5d6e7f8`): `id, job_name, status, detail, started_at, finished_at, duration_ms` + indexes. `JobRun` ORM model added. `admin_settings.label` column added; `sunday_event_series_id`/`powerhouse_event_series_id` keys seeded (fallback to legacy keys).
- **backend/app/services/scheduler.py** (new): AsyncIOScheduler singleton, `start_scheduler()`/`stop_scheduler()`, `_run_tracked_job` wrapper writes job_runs start→finish. `JOB_REGISTRY` of 9 jobs (sunday/powerhouse generation, eow/eom recompute, 4 attendance notifiers [skipped/"awaiting S18"], biometric retention). Old `backend/app/scheduler.py` deleted.
- **backend/app/main.py** lifespan: scheduler start/stop wired in; gated by `ENVIRONMENT != "test"` (never starts under pytest). Removed `HAS_SCHEDULER` + Ellipsis placeholder, passed real AsyncIOScheduler to `register_s23_jobs()`.
- **Admin endpoints** in `routers/settings.py`: `GET /settings/jobs` (paginated list), `POST /settings/jobs/{job_name}/trigger` (202/404/409/403), `GET /settings/system-status` (service health), `GET /settings/config-checklist` (readiness gates), `PUT /settings/{key}` (audit + Fernet at-rest for sensitive keys). `services/settings_service.py` (new).
- **Immutable keys protection:** `PUT /settings/{key}` now rejects ANY write (set/blank) on `{jwt_secret, database_url, redis_url}` with 403 Forbidden.
- **Frontend:** SettingsPage (tabbed), SystemStatusPage, JobRunsPage, 7 settings panel components, settings.ts client, types. Build + lint green; **341 tests pass**.
- **Canonicalization:** `job_runs` column renamed `ran_at` → `started_at`. Edited 3 S23 call-sites + analytics (call_timestamp logic) + reconciled 3 S23 tests (job_runs now exists in test DB).
- **Security audit:** Initial BLOCK (immutable-keys bypass) → 3 findings fixed (immutable-keys enforce, encrypt-on-write/decrypt-on-read two-pass, sanitized job_runs.detail DSN leaks). Re-audit: PASS.
- **Migration-head test:** Updated EXPECTED_HEAD → `s16a1b2c3d4e5`; down_revision test → `a3b4c5d6e7f8`.
- **Dependencies:** `apscheduler>=3.10.4` added to `backend/requirements.txt`.
- **Green gates:** backend full suite (test segment 209 + affected files 65 + isolated S16 components) + frontend 341 tests; security PASS. Full-suite pending (15-min runtime).
- **Carry-forward:** Dual job_runs writes (scheduler wrapper + member_status_service raw-SQL) non-blocking observability nit; Fernet key derived from jwt_secret (single SHA-256 hash unless SETTINGS_FERNET_KEY env set); series-id fallback behavior (legacy keys); notifier jobs skip "awaiting S18"; trigger endpoint TOCTOU (admin-only, LOW).

---

## Build DAG & Dependencies

```
S01 (schema inversion)
  ↓
S02 (custom fields)
  ├→ [PARALLEL WAVE 1]
  │   S03 (contacts)  ∥  S07 (faces)
  │   ↓               ↓
  │   S04 (events)   [depends only on S01+S02]
  │   ↓
  │   S05 (export)
  │
  └→ [SEQUENTIAL CONVERGENCE]
      S22 (name-match)
      ↓
      S06 (ETL)
      ↓
      S24 (FR transition)
      ↓
      S21 (final cutover)
      ↓
      S08–S20 (remaining leaves)
```

**Key insight:** S03+S07 run in parallel (each depends only on S01+S02 being committed). Their builds use isolated git worktrees; outputs are merged with conflict resolution before commit.

---

## Autonomous Loop Operating Contract

- **Mode:** Full autonomy; owner can be asleep
- **Approval:** I (Claude) auto-approve PLAN phases, run Opus critic, advance the loop
- **Stopping condition:** Failure cap (3 remediation rounds per sprint) or a real blocker
- **Per-sprint gates:**
  - Backend: pytest on SQLite (migration ACs Postgres-only, gated locally with skip decorators)
  - Frontend: build + lint + vitest
  - Integration: ruff (advisory; pre-existing debt tolerated; NEW violations noted)
- **Model policy:**
  - **senpai agents:** keep their own (Opus on architect/security/qa-expert; Sonnet on engineers)
  - **Critique:** Opus (slim: green gate + one critic agent)
  - **Other agents:** Sonnet
- **Git:** one commit per passing sprint; no push; branch `docs/crm-specs-and-cve-remediation`

---

## Speed Optimizations

1. **2-round QA cap** (instead of 5) — process bug + Opus critic catch real defects faster
2. **Clear pendingTasks on clean QA pass** — no spurious Expert-QA escalation
3. **Stub devops agent** — orchestrator's green gate + critic already cover app boot
4. **Postgres-only migration gating** — tell senpai upfront, QA doesn't re-loop on unverifiable criteria

**Result:** Each sprint now completes ~2–4 hours vs. 6–8 hours in earlier iterations.

---

## Token/Cron Management

**Watchdog cron (15-min):**
- Fires every `:00`, `:15`, `:30`, `:45`
- Detects stuck/stale builds (markers older than 20 min)
- Auto-recovers: runs green gate, commits passing work, resumes from journal
- Session-only; must be recreated on every new session

**Session limit:** 5-hour rolling window. If a build dies on the limit, use the journal's `resumeFromRunId` to restart from the last completed agent (no rebuild).

---

**Related notes:** [[Deployment]] · [[Architecture]] · [[Home]]
