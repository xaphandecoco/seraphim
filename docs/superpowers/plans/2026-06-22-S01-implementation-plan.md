# S01 — Implementation Plan (TDD)

**Spec:** `docs/specs/S01-schema-inversion-and-civicrm-excision.md` (grilled + gap-closed 2026-06-22)
**Branch:** `feat/s01-schema-inversion` (off `main`)
**Nature:** Big-bang schema swap — removing `CiviCRMMember`/`CiviCRMEvent`/`Attendance` breaks all consumers at once. Order is **migration + models first**, then repoint consumers until the suite is green. Acceptance criteria (AC 1–29) in the spec are the test targets; each step below names the ACs it satisfies.

**Definition of done (final gate):** `alembic upgrade head` + `downgrade -1` clean on SQLite AND Postgres; `pytest tests/ -q` green; `ruff check app` clean; `npm run build` + `lint` + `test:run` green. (AC 1,2,26–29)

---

## Phase 0 — Baseline (before any change)
- **0.1** Confirm green starting point: `pytest tests/ -q` passes on current `main` (355 passed, 1 xfailed). Record the number.
- **0.2** `alembic heads` → confirm single head `f3a4b5c6d7e8`. (AC: migration chains cleanly.)

## Phase 1 — Models + migration (the foundation)
*This is the irreducible core; it lands as one cohesive change because models and migration are interdependent.*

- **1.1 (RED)** Write `tests/test_schema_inversion.py` first, asserting the target schema via reflection:
  - tables `contacts`, `events`, `participants`, `audit_log` exist; `civicrm_members`, `civicrm_events`, `attendance` do not. (AC 3,4)
  - `contacts.external_id` partial-unique index allows multiple NULLs. (AC 5)
  - `compreface_subjects.contact_id` → `contacts.id`; `detections.event_id` → `events.id`; `logs.event_id` → `events.id`; `logs.push_status` gone. (AC 6,7,8)
  - `_legacy_civicrm_contact_id` / `_legacy_civicrm_event_id` stash columns exist + populated from old FK values (CN-28).
  These fail now (red).
- **1.2 (GREEN)** Rewrite `models.py` per spec §4.2/§3: add `Contact`, `Event` (minimal core, per CN-16), `Participant`, `AuditLog`; repoint `ComprefaceSubject`/`Detection`/`Log` FKs; drop `Log.push_status`; remove `CiviCRMMember`/`CiviCRMEvent`/`Attendance`; add `text` import. **Do NOT** add `EventSeries` (S04 owns it).
- **1.3 (GREEN)** Write migration `g7h8i9j0k1l2_schema_inversion_civicrm_excision.py` per spec §3.4 (down_revision = real head from 0.2, per CN-26). Include the CN-28 stash columns + `UPDATE` backfills in step 5. `__table_args__` must mirror migration indexes/constraints (dual-dialect).
- **1.4 (GATE)** `alembic upgrade head` then `downgrade -1` then `upgrade head` — clean on **both** SQLite and Postgres; `test_schema_inversion.py` green; `tests/test_migrations.py::test_single_head` green. (AC 1,2)

> After Phase 1 the app import is broken (consumers still reference deleted models). Phases 2–7 repair them; expect red until Phase 8.

## Phase 2 — schemas.py
- **2.1 (GREEN)** Per spec §4.2 schemas: rename `AttendanceRecord`→`ParticipantRecord` (drop `push_status`, add `source`); rename `EventResponse` fields `event_id→id`, `start_date→start_at`, `end_date→end_at` (no alias — resolved OQ2); delete `AttendeeSummary`/`PushDiff`/`DeadLetter*`; strip `civicrm_*` from `SetupRequest`/`ServiceTest*`; drop `LogResponse.push_status`. Confirm `SetupRequest` does not set `extra='forbid'` (resolved OQ1).

## Phase 3 — kill the CiviCRM core
- **3.1 (GREEN)** Delete `services/civicrm.py` entirely. (AC 9)
- **3.2 (GREEN)** `queue_manager.py`: remove `CiviCRMClient` import, `CiviCRMEvent`/`CiviCRMMember` imports, `MAX_PUSH_ATTEMPTS`, `_process_civicrm_push`, and its call in `run()`. (AC 10)
- **3.3 (GREEN)** `queue_consumer.py`: replace the `get_civicrm_url()` log line. `config.py`: remove the three `get_civicrm_*` getters.

## Phase 4 — repoint services (face → Participant)
- **4.1 (RED)** Adjust/confirm tests: tier-100 detection writes a `Participant(source="face", status="attended")`; task resolution writes `Participant(source="face")` with the `if member_id` guard. (AC 21,22)
- **4.2 (GREEN)** `face_pipeline.py` + `task_service.py` per spec §4.2: `Participant` inserts, `source="face"`, drop `push_status`, repoint the dedup existing-check to `Participant`, import `Contact`.

## Phase 5 — repoint routers + delete endpoints
- **5.1 (RED)** Endpoint tests: `/members/sync`, `/events/sync`, `/attendance/push-preview`, `/attendance/push`, `/attendance/dead-letter` → 404/405; `GET /events` items have `id`+`start_at`; `set-active?event_id=99999` → 404 with no "CiviCRM"; `/setup/test-services` has no civicrm keys; `/setup` accepts no-civicrm body; `GET /attendance` items have `source` not `push_status`; analytics CSV has `source` not `push_status`. (AC 11–20,23)
- **5.2 (GREEN)** Repoint `members.py`, `events.py`, `attendance.py`, `tasks.py`, `analytics.py`, `setup.py` per spec §4.2 (delete push/sync/dead-letter fns; repoint to `Contact`/`Event`/`Participant`). `audit.py` = verify-only (grill: no change). Global `grep -r CiviCRMMember backend/` must return zero. (AC: risk mitigation)

## Phase 6 — frontend
- **6.1 (GREEN)** `types/index.ts`: `ChurchEvent` `event_id→id`, `start_date→start_at?`, `end_date→end_at?`. (grill gap)
- **6.2 (GREEN)** `EventsPage.tsx`: 6 field refs → new names.
- **6.3 (GREEN)** `AttendancePage.tsx` → stub per spec §5.1; `SetupPage.tsx` → remove CiviCRM card/state/payload/result rows. (AC 24,25)
- **6.4** `MemberSearchModal.tsx`: verify, no change.

## Phase 7 — tests
- **7.1 (GREEN)** Delete `test_civicrm.py`, `test_members_sync.py`, `test_setup_connectivity.py`.
- **7.2 (GREEN)** Rewrite `conftest.py` fixtures per spec §8.3 (`sample_event` minimal per CN-16, `sample_contact`+alias, `sample_participant`+alias, fix `sample_detection.event_id`, drop `dead_letter_attendance`).
- **7.3 (GREEN)** `test_attendance.py`→`test_participants.py` per §8.2; add `test_setup_no_civicrm.py` per §8.5; fix `test_tasks_full.py`/`test_task_service.py` imports + assertions per §8.4; `SetupPage.test.tsx` per §8.6.

## Phase 8 — full green gate
- **8.1** `pytest tests/ -q` — all surviving green (target ≥ baseline minus deleted tests). (AC 26)
- **8.2** `ruff check app` clean. (AC 29)
- **8.3** `npm run build` + `npm run lint` + `npm run test:run` green. (AC 27,28)
- **8.4** Both-dialect migration re-confirm (idempotent upgrade). (AC 1)
- **8.5** Manual smoke per spec §9 rollout step 5 (optional in CI).

---

## Senpai-team handoff notes
- Feed this plan + the S01 spec to the senpai pipeline. Its intake should NOT re-grill — the grill is done (this doc records the resolutions). Point its architect at the phase order above.
- **Cross-sprint invariants senpai's per-task agents won't see on their own:** (a) `event_series` is S04's, never create it here; (b) `Event` is minimal-core only (CN-16) — no `event_type`/`session_time`; (c) the CN-28 stash columns must be added + backfilled in the migration (S24 depends on them); (d) clean rename, no `event_id` alias.
- Security review focus: the dropped push pipeline removes an egress path (good); confirm no remaining code writes to CiviCRM. RBAC unchanged.
- Migrations are append-only — never edit `g7h8i9j0k1l2` after it's applied anywhere.
