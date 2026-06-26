# S24 — Facial-Recognition Transition & Cutover Bridge

**Phase:** G Polish (cutover) · **Depends on:** S06, S07 (and S08 for the consent table) · **Gates:** S21 go-live · **Effort:** M

---

## 1. Goal & rationale

S01 demolishes the CiviCRM-coupled face plumbing (drops `attendance`, repoints `compreface_subjects.contact_id`/`detections.event_id` to the new `contacts`/`events`, deletes the push pipeline). S07 rebuilds *enrollment* mechanics. **Neither owns the transition of the already-running live face-recognition system.** That glue currently lives only as scattered notes (S01 §10 OQ3, 00-MASTER) with several breaks having no migration path.

S24 is the single owner of that transition. It runs at cutover, after the data migration (S06) and the enrollment rebuild (S07) have landed, and must pass before S21 go-live.

**Key facts that shape this sprint (locked at brainstorm 2026-06-22):**
- **Attendance history is canonical in CiviCRM** and migrates via S06's XLSX import. S24 does **not** convert local attendance history.
- **Enrolled faces matter** (~1,440 contacts) and must be preserved.
- **CompreFace stays the same instance at cutover** — embeddings/subjects persist. The fix is a **DB relink**, not a re-upload; recognition keeps working through cutover.
- Existing enrolled faces have **no recorded biometric consent** (CiviCRM never tracked it).

**This sprint supersedes S01 §10 OQ3** (which proposed NULLing stale `compreface_subjects.contact_id`). Instead of NULLing, S24 preserves the legacy CiviCRM id and remaps it to the new app-minted contact via `external_id`.

---

## 2. Scope

### In scope
- **Face-link remap**: relink `compreface_subjects.contact_id`, `detections.event_id`, and `detections.matched_name` from old CiviCRM ids to new app-minted ids via `contacts.external_id` / `events.external_id`. Idempotent. Reports unmatched subjects.
- **Attendance→Participant code cutover ownership**: own (and add test coverage for) the `face_pipeline.py` / `task_service.py` / `pit.py` rewrites that S01 only sketches — write `Participant(source="face", status="attended")` and ensure `source` is set in every site.
- **Leaderboard scoring redefinition**: redefine `VolunteerStat` scoring against task-resolution events + `participants` (the old formula keyed off the deleted `Attendance.status="confirmed"`).
- **Consent backfill (soft-gate)**: one-time script creating `biometric_consent(consent_given=false, basis_note="pre-cutover-unknown")` rows for remapped active subjects; set `enroll_without_consent=true`; surface a "consent needed" flag.
- **Cutover verification**: read-only sanity queries feeding the S21 checklist.

### Out of scope
- Attendance **history** conversion — owned by S06 (CiviCRM canonical).
- CompreFace **re-enrollment** — the instance persists; no re-upload.
- New enrollment mechanics — owned by S07 (`EnrollmentService`, `face_samples`).
- The CN spec-level patch pass — already applied to S01–S23.

---

## 3. Data model

No new tables. S24 adds **one transitional column** and writes existing tables.

### 3.1 Legacy-id stash column (transitional)

To remap after `civicrm_members` is dropped, the old CiviCRM contact id must survive S01's FK repoint. **Ownership ruling (CN-28, see 00-MASTER):** **S01** adds `compreface_subjects._legacy_civicrm_contact_id Integer nullable` during its FK-repoint step (capturing the old `contact_id` value before the repoint), and likewise `detections._legacy_civicrm_event_id Integer nullable`. **S24** consumes these and **drops them** at the end of its migration.

> If S01 has already shipped without the stash columns, S24's migration adds them, but they will be empty (the old ids are gone) — so the stash MUST be done by S01. This is the single most important seam in this sprint.

### 3.2 Tables written (no schema change)
- `compreface_subjects.contact_id` — remapped.
- `detections.event_id`, `detections.matched_name` — remapped.
- `participants` — written by the live pipeline post-cutover (via the code in §4).
- `biometric_consent` — backfilled rows (table owned by S08).
- `volunteer_stats` — recompute under the new formula.

---

## 4. Backend

### 4.1 Migration `s24_fr_transition_bridge`

Runs **after** S06's live import has populated `contacts.external_id` / `events.external_id`. Steps (idempotent; safe to re-run):

1. **Remap subjects:**
   ```sql
   UPDATE compreface_subjects cs
   SET    contact_id = c.id
   FROM   contacts c
   WHERE  c.external_id = cs._legacy_civicrm_contact_id
     AND  cs._legacy_civicrm_contact_id IS NOT NULL;
   ```
2. **Remap detections.event_id** via `events.external_id` (same shape).
3. **Remap detections.matched_name**: rows of the form `"member:{old_id}"` → `"member:{new_id}"` using the same `external_id` map; rows already in the new form or free-text names are skipped.
4. **Unmatched report:** select `compreface_subjects` where `_legacy_civicrm_contact_id` is set but no matching `contacts.external_id` exists → write to `job_runs` detail (job_name `s24_remap`) and log; these are subjects whose contact didn't migrate (manual review — relink or retire).
5. **Drop** `compreface_subjects._legacy_civicrm_contact_id` and `detections._legacy_civicrm_event_id`.

Dual-dialect: plain UPDATE…FROM on Postgres; correlated-subquery form on SQLite (branch on `db.bind.dialect.name`).

### 4.2 Code cutover (`services/face_pipeline.py`, `services/task_service.py`, `routers/pit.py`)

S24 owns these rewrites (S01 lists them but ships no tests):
- Tier-100 auto-log and all task-resolution paths write `Participant(contact_id, event_id, detection_id, status="attended", source="face")`.
- The dedup-check that referenced `Attendance.contact_id`/`Attendance.event_id` (face_pipeline.py ~132–138 per S01 risk table) repoints to `Participant`.
- `source="face"` is set in **every** insertion site (no schema default; missing it corrupts S22 reporting/audit).

### 4.3 Leaderboard scoring (`services/task_service.py` / wherever `VolunteerStat` is updated)

Redefine point accrual to fire on **task resolution** (confirm/edit/add actions), independent of `participants.status`. Preserve the existing per-action weights (confirm/edit/add) from the pre-cutover formula; only the trigger key changes (was `Attendance.status=="confirmed"`, now task resolution + a written `Participant`). Document the exact weights in the implementation.

### 4.4 Consent backfill (`scripts/backfill_consent.py`, one-time)

For every `compreface_subjects` row with `enrollment_status="active"` and a non-null remapped `contact_id` that has no `biometric_consent` row:
```python
biometric_consent(contact_id=…, consent_given=False,
                  basis_note="pre-cutover-unknown", recorded_at=utc_now())
```
Then set `admin_settings.enroll_without_consent = true` (soft gate — recognition continues). Idempotent (skips contacts that already have a consent row).

### 4.5 Verification endpoint / script (feeds S21)

Read-only `GET /admin/fr-transition/status` (admin-only) returning:
- subjects remapped vs orphaned (target: orphans reviewed),
- `participants` count sanity vs S06 import totals,
- recognition smoke result (one known face → tier-100 → `Participant(source="face")`),
- consent backfill coverage (rows created / contacts still unflagged).

---

## 5. Frontend

Minimal — a cutover panel (admin-only), surfaced under Settings/System Status (S16):
- **FR transition status card**: remapped/orphaned counts, consent coverage, smoke-test result, with a "re-run verification" button hitting §4.5.
- **Orphaned-subjects review list**: subjects whose contact didn't migrate → relink (contact picker) or retire.
- **"Consent needed" indicator** on contact detail (S03 slot) for contacts whose only consent row is `pre-cutover-unknown` / `consent_given=false`, so staff collect consent over time and the owner can later flip `enroll_without_consent → false`.

Reuse `DataTable`/`StatusBadge`/`ContactPickerModal` (S03), `StateViews`, design tokens.

---

## 6. Acceptance criteria

1. After S06 live import + S24 migration, every pre-existing active `ComprefaceSubject` with a migrated contact has `contact_id` pointing to the correct `contacts.id` (verified via `external_id`).
2. Subjects whose contact didn't migrate are reported (job_runs + UI list), not silently NULLed.
3. `detections.event_id` and `"member:{id}"` `matched_name` values are remapped; the stash columns are dropped at migration end.
4. A live tier-100 detection post-cutover writes a `Participant(source="face", status="attended")` — covered by a test.
5. A resolved low-confidence task writes a `Participant(source="face")` — covered by a test.
6. `source` is non-null on every pipeline-written participant.
7. Volunteer leaderboard accrues points on task resolution under the new formula (test asserts points change on confirm/edit/add).
8. Consent backfill creates exactly one `pre-cutover-unknown` consent row per active remapped subject lacking one; re-running creates none.
9. `enroll_without_consent` is `true` after backfill; recognition still produces participants.
10. `GET /admin/fr-transition/status` returns the four verification sections; the smoke test passes on a seeded known face.
11. Migration is idempotent (second `upgrade head` is a no-op) and runs clean on both Postgres and SQLite.

## 7. Test plan
- `test_s24_remap.py`: seed `compreface_subjects` with `_legacy_civicrm_contact_id` + matching/non-matching `contacts.external_id`; assert remap, unmatched report, stash-column drop.
- `test_s24_pipeline_participant.py`: tier-100 and task-resolution paths write `Participant(source="face")`; dedup-check uses `Participant`.
- `test_s24_scoring.py`: confirm/edit/add update `VolunteerStat` under the new trigger.
- `test_s24_consent_backfill.py`: backfill idempotency + `enroll_without_consent` flip.
- `test_s24_verification.py`: status endpoint shape + smoke test (CompreFace mocked).

## 8. Rollout / rollback / risks

**Cutover run order (binding):** S06 live import → **S24 migration (remap)** → **S24 consent backfill** → **S24 verification (green)** → S21 go-live.

**Rollback:** S24's migration downgrade re-adds the stash columns but cannot reconstruct dropped values — so downgrade is only safe before go-live. Document as irreversible post-go-live (consistent with S01/S21).

| Risk | Likelihood | Mitigation |
|---|---|---|
| S01 ships without the stash columns → remap impossible | Medium | CN-28 makes the stash an S01 obligation; S24 migration asserts the columns exist and are populated, fails loudly otherwise |
| `external_id` not populated (S06 not run / dry-run only) | Medium | S24 migration guards: abort with clear message if `contacts.external_id` is all-NULL |
| Orphaned subjects silently lost | Low | Explicit unmatched report + UI review list (AC #2) |
| Scoring weights drift from old behavior | Low | Port exact per-action weights; test asserts deltas |
| Consent posture wrong for jurisdiction | Low | Soft-gate default is owner-confirmed; flag-driven, flippable |

## 9. Open questions
1. Exact leaderboard point weights — confirm the pre-cutover values to port (§4.3).
2. Retire vs relink default for orphaned subjects in the UI (§5).
3. Whether the "consent needed" flag should block any *new* enrollment for that contact until cleared, or only warn (ties to `enroll_without_consent`).

---

*Design source: `docs/superpowers/specs/2026-06-22-s24-fr-transition-design.md`. Audit source: `docs/superpowers/specs/2026-06-22-spec-completeness-and-fr-integration-findings.md`.*
