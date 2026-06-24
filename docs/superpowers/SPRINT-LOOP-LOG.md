# Autonomous Sprint Loop — Night Log & Owner Handoff

> **Purpose:** Living log for the unattended CRM-build sprint loop. The owner (you) reads this on waking. Newest blockers/questions are pinned at the top. I update it after every sprint and whenever I make an autonomous judgement call you might want to revisit.

---

## ⚠️ BLOCKERS / QUESTIONS FOR OWNER (read me first)

**Non-blocking owner FYIs (loop continues with stated defaults — change anytime):**
- **[S02 Q2] Custom-field option lists are STUBBED.** PEPSOL stages, Ministry, Community, Followup-status, Membership-class select options are seeded as placeholders (help_text="Pending owner confirmation — update in admin UI"). The engine works; set real values via the admin UI. **Needed before the S06 data-migration dry-run**, not before S02.
- **[S02 Q3] "Community" defaulted to MULTI-select.** Confirm whether a contact can belong to multiple communities. If single, I'll flip the seed to single `select` (cheap). Defaulted to multi per spec.

---

## ⏰ AUTO-RESUME (token limit)

Token limit is a rolling **5-hour** window (first reset ~6:10 AM). **Recurring** in-session cron **`b52868be`** fires at **01:13, 06:13, 11:13, 16:13, 21:13** daily (~every 5h, max gap 5h) to re-poke this session and un-stick the loop if it stalled on the limit. Each poke re-reads this log + `git log` and is duplicate-safe (no-ops if work is already in flight; trusts git over the log; never restarts a committed sprint). Session-only but owner confirmed the app stays open, so this covers the rate-limited-but-open case. Auto-expires after 7 days (loop finishes well before). If you wake and the loop is mid-sprint with no recent progress, just say "continue".

---

## 🔧 OPERATING CONTRACT (locked 2026-06-25)

- **Mode:** Full autonomy. Owner is asleep. I approve senpai PLANs myself, act as sole critic, and advance the loop without waiting. I only stop and write here if I hit the failure cap or a real blocker.
- **Order:** Build DAG, not numeric — `S01 → S02 → S03 → S04 → S05 → S22 → S06 → S24 → S21`, then remaining leaves (S07, S08, S09, …). A sprint runs only after its upstream deps are committed.
- **Per-sprint procedure:**
  1. **Check** — read the sprint spec + Master CN rulings; confirm deps committed.
  2. **DoD + goal** — I write a concrete, testable Definition of Done (in this log).
  3. **Build** — `/senpai-team-v1` (Sonnet agents only) writes ALL code. I never code.
  4. **Critique** — independent verification: run the green gate (pytest / build / lint / test:run) + adversarial read of the diff vs. DoD. Verdict PASS/FAIL.
  5. **PASS** → commit (one commit/sprint, current branch, NO push/PR) → checkpoint here → next sprint.
     **FAIL** → I write a remediation spec and re-summon senpai for that sprint.
- **Failure cap:** ~3 remediation rounds per sprint, then I STOP and escalate here rather than loop forever.
- **Agents:** senpai team keeps its own models (key reviewer/architect roles on Opus 4.8). Agents I summon OUTSIDE senpai (my critique/verification) are pinned to Sonnet (`claude-sonnet-4-6`).
- **Git:** commit per passing sprint authorized by owner. No `push`, no PR. Branch: `docs/crm-specs-and-cve-remediation`.
- **Gates:** Backend tests on SQLite `create_all`; frontend build+lint+vitest. **ruff now installed locally (0.15.19)** but is **ADVISORY** — CI runs `ruff check app || true` (codebase has pre-existing ruff debt, ~29 errors, deliberately tolerated). Per-sprint I check ruff on touched files and note NEW violations, but lint is non-blocking. Only **Postgres `alembic upgrade head`** remains CI-only (no local Docker).
- **Model policy (CORRECTED 2026-06-25 ~06:00 per owner):**
  - **senpai team = AS-IS** — keep its own models, INCLUDING the Opus agents (architect/security/qa-expert + the Opus overrides) and the 5-round QA loop. Do NOT Sonnet-ize senpai.
  - **Critique = Opus** — but SLIM: I run the green gate myself, then ONE Opus critic agent checks the diff vs. DoD (not the old 15-agent/528K-token fan-out). High quality, ~1 Opus call/sprint.
  - **Any OTHER agent I summon outside senpai = Sonnet** (spec-reading, extraction helpers, merge-conflict resolver, etc.).
  - **Orchestrator (this session) = Opus**, used sparingly: delegate heavy reads to bash/python, hand plans to IMPLEMENT via generated inline-script (no 35KB shuttle), keep turns short.
- **SPEED — 2-sprint parallelism (owner request):** After S02, two independent chains run concurrently, each in its OWN git worktree, then merged:
  - **Chain-C (contacts/events):** S03 → S04 → S05
  - **Chain-F (faces):** S07 → S08   (S07 depends on S01+S02 only, not S03/S04)
  - Wave 1: **S03 ∥ S07**; Wave 2: **S04 ∥ S08**; then S05, then S22→S06→S24→S21 (these reconverge — built serially).
  - Each chain: senpai build in its worktree → Opus critique → green gate. Merge worktree→branch; the only conflicts are infra files (models.py/main.py/conftest.py/schemas.py) — resolved by a Sonnet merge agent, then re-gate. One commit per sprint.
- **Compaction:** I can't self-invoke `/compact`. Instead every sprint boundary is made LOSSLESS — commit + this log fully updated + memory checkpoint — so auto-summarization (or a manual `/compact`) loses nothing. Each sprint ends with a `✅ S0X committed — safe to /compact` marker. Post-compact resume = read this log's progress table + `git log`.

---

## 📊 SPRINT PROGRESS

| Sprint | Title | Status | Commit | Notes |
|--------|-------|--------|--------|-------|
| S01 | Schema inversion & CiviCRM excision | ✅ committed | `2ee70d7` | critic PASS, 0/13 unmet; 3 non-blocking polish items carried forward |
| S02 | Dynamic custom-field engine | ✅ committed | `14c4329` | Opus critic PASS; QA spun 5 rounds on Postgres-only ACs (process bug, fixed below); +incidental ruff cleanup |
| S03 | Contact CRUD & profile | 🔄 planning | — | **Chain-C**, parallel w/ S07 |
| S07 | Face enrollment & bulk photo ingestion | 🔄 planning | — | **Chain-F**, parallel w/ S03 (needs S01+S02 only) |
| S04 | Event CRUD & management | ⏳ queued | — | Chain-C; owns event_series + session_time |
| S05 | Bulk participants & export at scale | ⏳ queued | — | |
| S22 | (per master) | ⏳ queued | — | |
| S06 | Data migration / ETL | ⏳ queued | — | |
| S24 | FR transition & cutover bridge | ⏳ queued | — | depends S06/S07/S08 |
| S21 | (per master) | ⏳ queued | — | |
| … | remaining leaves | ⏳ queued | — | S07, S08, S09, etc. |

Legend: ✅ committed · 🔄 in progress · ⏳ queued · ⛔ blocked (see top)

---

## 📝 PER-SPRINT LOG (newest first)

### S03 ∥ S07 — parallel wave 1 (Chain-C contacts / Chain-F faces)
- Both depend only on S01+S02 (committed `14c4329`). **PLANs launched in parallel** (read-only, safe). Anti-spin seed applied (migrations Postgres-only / CI-gated).
- Build plan: run the two IMPLEMENTs in **isolated git worktrees**, then merge into the branch — resolving the few infra-file conflicts (`models.py`/`main.py`/`conftest.py`/`schemas.py`) and **linearizing alembic heads** (both may add a migration off `h1i2j3k4l5m6` → branched heads to rebase) with a Sonnet merge agent, then re-gate. One commit per sprint.
- Critique each with the Opus critic before its commit.

### S02 — Dynamic custom-field engine
- **Goal:** A dynamic custom-field engine (groups + 8 field types) so the church's contact attributes are admin-configurable, replacing CiviCRM custom fields. Single validation chokepoint for all `custom_data` writes; admin CRUD UI reused by S03+.
- **Definition of Done:**
  1. Two tables `custom_field_group`, `custom_field_def` (documented columns; uniques `uq_custom_field_group_entity_name`, `uq_custom_field_def_group_name`; weight indexes). Migration chains off `g7h8i9j0k1l2`; idempotent seed; asymmetric downgrade; does NOT add S04 columns to events.
  2. Seed: 6 contact groups (weights 10–60) with stub option lists (idempotent on re-run).
  3. `services/custom_fields.py`: `validate_and_coerce` for all 8 types per §4.3; unknown-key → 422; multiselect dedup; `contact_reference` stored int / list[int]; soft-deleted contact → 422; `count_contacts_with_field_data` works on SQLite **and** Postgres; `get_active_schema` is 2 queries (no N+1).
  4. 9-endpoint admin router `/custom-fields`: RBAC (admin write / volunteer read 200 / volunteer write 403 / unauth 401); audit_log rows; immutable-field guards (422/400); soft-delete default + `?hard=True` → 409 with `affected_contacts`.
  5. Audit `record()` helper uses `created_at` (NOT spec's `at`).
  6. Frontend: 8 data-type input components (Tailwind tokens, dark-mode, loading/error/empty); `contact_reference` picker returns number/number[]; `CustomFieldsPage` admin UI + routing + types/service.
  7. Tests: `test_custom_fields.py` + `test_custom_fields_validation.py` + `test_custom_fields_seed.py`; 5 new conftest fixtures (no dupes of `sample_contact`/auth fixtures); frontend component+page tests.
  8. **Green gate:** backend pytest green; frontend build+lint+test green. (Postgres alembic → CI; ruff advisory.)
- **Plan:** 7 feature tasks + 6 patch tasks (DB→service→router→tests; FE types→components→page→routing→tests). Architect caught 3 spec bugs (audit `at`→`created_at`; `sample_contact` already in conftest; seed must be dialect-aware). 8 open questions all resolved with assumptions; Q2/Q3 logged as owner FYIs above.
- **Status:** PLAN auto-approved (full autonomy). IMPLEMENT launched `w3wvei7he` via generated inline-plan script.
- **Result: PASS, committed `14c4329`.** Opus critic confirmed all 8 DoD items (file:line evidence), gates re-verified (backend 502 passed/8 skipped/1 xfailed, ruff clean; frontend build+lint+82 tests).
- **⚠️ COST INCIDENT + FIX (important):** senpai IMPLEMENT returned `blocked: QA after 6 rounds` and burned **2.7M tokens / 2.4 hrs**. Expert-QA root-caused it as a PROCESS bug, not a code bug: the QA loop kept re-running the SQLite suite to verify **Postgres-only migration ACs** (which SQLite structurally cannot test), so it never converged. Implementation was actually complete (502 passed). Expert-QA surfaced 2 real Postgres-only defects (seed inserted int for BOOLEAN; missing migration round-trip test) + 1 spec nit (`at` vs `created_at`) — all fixed by a single 78K-token senpai-db agent.
  - **LOOP FIX (applies S03+):** every PLAN seed now tells senpai that migrations are Postgres-only, the LOCAL gate is SQLite+ruff+frontend, migration ACs must be phrased as static-inspection + a Postgres-gated (skip-on-SQLite) round-trip test, and **QA must NOT re-loop on criteria unverifiable locally.** This is the single most important budget fix.
- **Carried-forward (non-blocking):** token-discipline leak — `CustomFieldsPage.tsx` Delete buttons + `OptionEditor.tsx` use `red-*` palette classes instead of `bg-destructive`/`text-destructive` (won't adapt in dark mode). Fold into a frontend cleanup pass.


### S01 — Schema inversion & CiviCRM excision
- **Goal:** Delete CiviCRM as system-of-record; Seraphim mints its own PKs with nullable UNIQUE `external_id`; rename tables `civicrm_members→contacts`, `civicrm_events→events`, `attendance→participants`; add `audit_log`; stash legacy FK ids for S24.
- **Definition of Done:**
  1. Tables `contacts`/`events`/`participants`/`audit_log` exist; `civicrm_members`/`civicrm_events`/`attendance` gone.
  2. `contacts.external_id` (and `events.external_id`) nullable + partial-unique (multiple NULLs allowed).
  3. FKs repointed: `compreface_subjects.contact_id→contacts.id`, `detections.event_id→events.id`, `logs.event_id→events.id`; `logs.push_status` dropped.
  4. CN-28 stash columns `_legacy_civicrm_contact_id`/`_legacy_civicrm_event_id` added + backfilled (so S24 can remap faces).
  5. `services/civicrm.py` deleted; zero live refs to `CiviCRMClient`/`CiviCRMMember`/`CiviCRMEvent`/`push_status`/dead-letter.
  6. Push/sync/dead-letter endpoints removed (404); `EventResponse` exposes `id`/`start_at`/`end_at` (no alias); participants carry `source`.
  7. Frontend `ChurchEvent` uses `id`/`start_at`/`end_at`; AttendancePage is the stub; SetupPage has no CiviCRM.
  8. Migration `g7h8i9j0k1l2` chains from `f3a4b5c6d7e8`; single head.
  9. **Green gate:** backend pytest green; frontend build+lint+test green. (Postgres `alembic upgrade head` + `ruff` deferred to CI.)
- **Prior-session state:** fully implemented on disk, green (backend 326 passed / 1 xfailed / 0 failed; frontend build+lint clean + 39 tests). Two test-isolation bugs already fixed (eager app import; dynamic_settings snapshot/restore). New migration `g7h8i9j0k1l2`.
- **Critique result:** **PASS** — adversarial critic (15 agents, one skeptic per criterion) found 0/13 criteria unmet; it re-ran both suites itself to confirm runtime (326 backend / 39 frontend / lint / build green). Two PARTIALs were not DoD misses (schema correct; ruff just not installed locally).
- **Committed:** `2ee70d7` — `feat(S01): schema inversion & CiviCRM excision`.
- **Carried-forward polish (non-blocking, NOT a sprint failure):**
  1. `backend/tests/test_schema_inversion.py` ~line 117 — add `"is_active"` to the `s04_columns` set so the test asserts `events.is_active` is absent (S04-owned per CN-16). → **fold into S04** (S04 introduces `is_active`, natural home for the assertion).
  2. **ruff not installed** in local Python 3.14 — AC-29 lint gate unverifiable locally (CI still runs it). Will attempt a local `pip install ruff` to make the gate real for S02+; if it fails, lint stays a CI-only gate. (Owner FYI.)
  3. `test_uploads.py` showed first-run-only flakiness (IntegrityError on a dirty `ci_test.db`), green on clean runs. Root cause of the 401 cascade (dynamic_settings) already fixed this session; residual is stale-DB-file hygiene, pre-existing. → watch; address if it recurs.

✅ **S01 committed — safe to /compact.**
