# S24 — Facial-Recognition Transition & Cutover Bridge — Design

**Date:** 2026-06-22
**Status:** Approved (brainstorm complete; ready for writing-plans / formal spec)
**Sprint owner:** S24 (new)
**Depends on:** S06 (contact `external_id` map), S07 (`EnrollmentService`, `face_samples`)
**Gates:** S21 (cutover / go-live)

---

## 1. Problem

S01 excises CiviCRM and repoints/drops the tables the **already-built live face-recognition pipeline** depends on. S07 rebuilds *enrollment*, but nothing owns the **transition of the existing running system**. The completeness/FR audit (`2026-06-22-spec-completeness-and-fr-integration-findings.md`) found the FR-migration logic smeared across S01 (demolish) + S07 (rebuild), with the glue living only in 00-MASTER and several breaks having no migration path.

S24 is the single owner of that transition.

### Established facts (from brainstorm)
- **Attendance history is canonical in CiviCRM** and migrates via S06's XLSX import. S24 does **not** convert local attendance history.
- **Enrolled faces matter** and must be preserved (re-enrolling ~1,440 contacts is real effort).
- **CompreFace stays the same instance at cutover** — embeddings/subjects persist. The fix is a **DB relink**, not a re-upload. Recognition keeps working through cutover.
- Existing enrolled faces have **no recorded biometric consent** (CiviCRM never tracked it).

---

## 2. Decisions Made

| Decision | Choice | Rationale |
|---|---|---|
| Transition ownership | One dedicated spec (S24) | Single owner/reviewer; nothing smeared across S01/S06/S07/S08 |
| Face preservation | DB remap via `external_id` | CompreFace instance persists; no re-enrollment needed |
| Attendance history | Out of scope (S06 owns it) | CiviCRM is canonical source |
| Consent posture | Backfill `consent_given=false`, soft-gate | Pragmatic for an existing congregation; recognition keeps working while staff collect consent over time |
| Sequencing | After S06 + S07, gates S21 | Needs `external_id` map and enrollment service; must pass before go-live |

---

## 3. Scope

### 3.1 Face-link remap (core fix — replaces S01's orphan-and-NULL)

S01 currently repoints `ComprefaceSubject.contact_id` FK to `contacts.id` then drops `civicrm_members`, leaving all rows NULL. S24 replaces the "accept NULL" outcome with an **idempotent remap run after S06**:

```sql
-- After S06 populates contacts.external_id = old CiviCRM contact_id
UPDATE compreface_subjects cs
SET    contact_id = c.id
FROM   contacts c
WHERE  c.external_id = cs._legacy_civicrm_contact_id;   -- see note
```

Implementation notes:
- S01's FK repoint must run with `ON DELETE SET NULL` **but** S24 needs the *old* CiviCRM contact_id preserved long enough to remap. Two options for the plan to resolve: (a) S24 runs **before** `civicrm_members` is dropped and stashes the old id in a temp column `compreface_subjects._legacy_civicrm_contact_id`; or (b) S01 stashes it. **Recommendation:** S01 adds the stash column during its FK-repoint step; S24 consumes and drops it. The plan must lock this seam.
- Same remap for `detections.event_id` (via `events.external_id`) and `detections.matched_name` (the `"member:{old_id}"` form → new id).
- **Idempotent**: safe to re-run; only updates rows still pointing at a stale/NULL link.
- **Unmatched report**: any `ComprefaceSubject` whose `_legacy_civicrm_contact_id` has no matching `contacts.external_id` is written to a remap-review list (logged + surfaced in cutover UI). These are subjects whose contact didn't migrate — manual decision (re-link or retire).

### 3.2 Attendance→Participant cutover (owns the code rewrites S01 only sketches)

S01 §4.2 *instructs* rewriting `Attendance(...)` → `Participant(...)` in three sites but adds no tests. S24 owns the rewrite + coverage:

- `face_pipeline.py` tier-100 auto-log → `Participant(contact_id, event_id, detection_id, status="attended", source="face")`.
- `task_service.py::_log_attendance` and `confirm/edit/add` paths → same, `source="face"`.
- `pit.py` enroll path → same.
- **`source="face"` must be set in all three** (no schema default; missing it corrupts S22 reporting/audit).
- **Test coverage S01 omits:** tier-100 auto-log writes a `Participant` row; tier-<100 routes to Task; resolved task writes a `Participant`; `source` is always populated.

### 3.3 Leaderboard scoring redefinition

`VolunteerStat` scoring keys off `Attendance.status == "confirmed"`, which no longer exists (`participants.status` ∈ `attended|registered|no_show|cancelled`). S24 redefines scoring against **task resolution events** (confirm/edit/add actions) + `participants`, so points keep accruing post-cutover. Exact formula to be set in the plan; default proposal: award on task resolution regardless of participant status, preserving existing point weights per action.

### 3.4 Consent backfill (soft-gate)

One-time script (run at cutover, after remap):
- For every `ComprefaceSubject` with `enrollment_status="active"` and a non-null remapped `contact_id`, insert `biometric_consent(contact_id, consent_given=false, basis="pre-cutover-unknown", recorded_at=<cutover_ts>)` if no consent row exists.
- Set `admin_settings.enroll_without_consent = true` at cutover so recognition continues (soft gate).
- Surface a "consent needed" flag (count + per-contact) in the contact detail / admin dashboard so staff collect consent over time, then the owner can flip `enroll_without_consent → false` once coverage is sufficient.
- Idempotent: skips contacts that already have a consent row.

### 3.5 Cutover verification (feeds S21 checklist)

Read-only sanity queries:
- subjects remapped vs orphaned (target: orphans reviewed/zero);
- `participants` count sanity vs S06 import;
- recognition smoke test (one known face → tier-100 → `Participant(source="face")` written);
- consent backfill coverage count.

---

## 4. Out of Scope
- Attendance **history** conversion — S06 owns it (CiviCRM canonical).
- CompreFace **re-enrollment** — instance persists; not needed.
- New enrollment mechanics — S07 owns `EnrollmentService`, `face_samples`.
- The 10 spec-level CN follow-through items — tracked separately in the completeness findings (a "CN pre-flight patch pass"), not part of S24.

---

## 5. Seams the implementation plan must lock
1. **Who stashes the legacy CiviCRM contact id** before `civicrm_members` drops (S01 vs S24) — §3.1. Recommendation: S01 adds the stash column.
2. **Run order at cutover:** S06 import → S24 remap → S24 consent backfill → S24 verification → S21 go-live.
3. **Scoring formula** exact weights — §3.3.
4. **00-MASTER update:** register S24, add a CN ruling that the FR transition is S24-owned (not S01/S07), and note the `_legacy_civicrm_contact_id` stash seam.

---

## 6. Appendix
- Findings: `2026-06-22-spec-completeness-and-fr-integration-findings.md`
- Settings engine: `2026-06-22-configurable-settings-design.md` (owns `enroll_without_consent` key)
