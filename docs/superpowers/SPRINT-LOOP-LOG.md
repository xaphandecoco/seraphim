# Autonomous Sprint Loop — Night Log & Owner Handoff

> **Purpose:** Living log for the unattended CRM-build sprint loop. The owner (you) reads this on waking. Newest blockers/questions are pinned at the top. I update it after every sprint and whenever I make an autonomous judgement call you might want to revisit.

---

## ⚠️ BLOCKERS / QUESTIONS FOR OWNER (read me first)

_None yet. I will append here the moment I hit anything that genuinely needs your decision. If this section stays empty, the loop ran clean._

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
- **Gates deferred to CI:** Postgres `alembic upgrade head` (no local Docker) and `ruff` (not installed locally). Backend tests run on SQLite `create_all`; frontend runs build+lint+vitest.
- **Token discipline (5-hour window):** Opus/ultracode reserved for orchestration + hard critique synthesis only; my fan-out agents stay Sonnet; senpai keeps its own models. Main loop stays lean — dispatch workflows, read only compact return values, don't pull big files into orchestration context. Don't re-run senpai unnecessarily.
- **Compaction:** I can't self-invoke `/compact`. Instead every sprint boundary is made LOSSLESS — commit + this log fully updated + memory checkpoint — so auto-summarization (or a manual `/compact`) loses nothing. Each sprint ends with a `✅ S0X committed — safe to /compact` marker. Post-compact resume = read this log's progress table + `git log`.

---

## 📊 SPRINT PROGRESS

| Sprint | Title | Status | Commit | Notes |
|--------|-------|--------|--------|-------|
| S01 | Schema inversion & CiviCRM excision | ✅ committed | `2ee70d7` | critic PASS, 0/13 unmet; 3 non-blocking polish items carried forward |
| S02 | Dynamic custom-field engine | 🔄 in progress | — | next per DAG |
| S03 | Contact CRUD & profile | ⏳ queued | — | |
| S04 | Event CRUD & management | ⏳ queued | — | owns event_series + session_time |
| S05 | Bulk participants & export at scale | ⏳ queued | — | |
| S22 | (per master) | ⏳ queued | — | |
| S06 | Data migration / ETL | ⏳ queued | — | |
| S24 | FR transition & cutover bridge | ⏳ queued | — | depends S06/S07/S08 |
| S21 | (per master) | ⏳ queued | — | |
| … | remaining leaves | ⏳ queued | — | S07, S08, S09, etc. |

Legend: ✅ committed · 🔄 in progress · ⏳ queued · ⛔ blocked (see top)

---

## 📝 PER-SPRINT LOG (newest first)

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
