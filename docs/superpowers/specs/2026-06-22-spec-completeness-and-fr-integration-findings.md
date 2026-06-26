# Spec Completeness & Facial-Recognition Integration — Findings

**Date:** 2026-06-22
**Author:** Claude (codebase + spec audit, 3 parallel research passes)
**Scope:** (a) Are the 23 sprint specs complete? (b) Does the already-built facial-recognition system integrate cleanly with the new CRM specs?
**Status:** Findings only — input to next brainstorm. No code or spec changes made.

---

## TL;DR

1. **Specs as a paper package: complete & internally consistent.** All 23 sprints (S01–S23) + 00-MASTER.md are present, each with scope, data model, endpoints, frontend, tests, and dependencies. 00-MASTER's 29 consistency rulings (CN-01…CN-27) resolve nearly all cross-sprint conflicts. No data domain a CiviCRM replacement needs is missing. **Completeness ~92/100, consistency ~85/100.**

2. **The real risk is NOT the specs — it's the built system.** The existing facial-recognition pipeline (live since the CiviCRM era) does **not survive the CRM transition in place.** S01 excises CiviCRM and, as a side effect, orphans the existing face↔contact links, drops the `attendance` table the live pipeline writes to, and breaks the tier-100 auto-log path. The specs treat this as an intentional demolition-and-rebuild (S01 demolishes, S07 rebuilds) — but **several breaks have no automatic migration path and are documented only inside 00-MASTER, not in the sprint a junior implementer would be reading.**

3. **Top thing to decide in the next brainstorm:** how the live camera → detection → participant bridge is bootstrapped at cutover, and whether the existing volunteer-queue / dual-approval / leaderboard machinery is preserved with intent or left to silently rot.

---

## Part A — Spec Completeness (PASS, with 10 follow-through items)

### What's solid
- Every sprint has clear In/Out scope, precise column/type/FK definitions, Pydantic-shaped endpoints, named frontend components, and pytest/vitest test plans.
- Dependency DAG is coherent. Critical path S01→S02→S03→S04→S05→S22→S06→S21 verified sound.
- Each core table is owned by exactly one sprint. No dangling FK targets.
- Coverage vs. "what a CiviCRM replacement needs": contacts, households (partial — no relationship fields, deliberately out of scope), events, attendance, custom fields, search/groups, reporting, import/migration, dedupe/merge, activities, biometric consent/RTBF, automation, RBAC, audit, API/webhooks — **all covered.**

### The 10 follow-through items (CN rulings not yet echoed into sprint text)
These are real but low-risk *if* implementers read 00-MASTER. The danger is that they live **only** in the master doc:

| # | Item | Where it bites |
|---|------|----------------|
| 1 | **CN-16**: S01 must create only minimal `events` columns; S04 adds the other 6. S01 spec text currently lists all columns. | Migration conflict S01↔S04 |
| 2 | **CN-02**: Snapshot-column ownership (S01 vs S03 vs S23) still "recommended", not locked. | Migration race |
| 3 | **CN-19**: S15 must filter `detection.*`/`task.*` SSE events from viewers — not in S15 text. | Viewer sees biometric PII |
| 4 | **CN-23/24**: S06/S10/S11 must call `recompute_contacts(...)` after runs — not in those sprints' text. | Stale member-status |
| 5 | **CN-14**: S13 newcomer-form field names must match S02 seeds exactly — no reconciliation in S13. | Form/field mismatch |
| 6 | **CN-10**: S12/S13 ship raw outbox-insert stubs to be patched after S17 — patch obligation only in master. | Automation bypass |
| 7 | **CN-15**: S14 still says `session_type` in places; canonical is `event_type + service_time`. | Wrong schema coded |
| 8 | **CN-04**: S11 merge must include S22 tables + `biometric_consent` in FK manifest. | Orphaned rows on merge |
| 9 | **S10 under-specified**: 375 lines for an L-effort 4-step import wizard; dedupe-on-import algorithm absent. | Implementation ambiguity |
| 10 | **Artifact blocker**: fresh CiviCRM XLSX exports (real Contact/Event IDs) gate S06 + S21. | Cutover cannot run |

**Recommended fix:** a single "pre-flight patch pass" that pushes each CN ruling down into the owning sprint's text, so no implementer has to cross-reference the master. Cheap, removes the whole class of "I didn't read CN-19" bugs.

---

## Part B — Facial-Recognition Integration (the real problem)

### Continuity verdict: **NO — does not survive in place; controlled demolition + rebuild.**

The built pipeline today:
```
RTSP/upload → quality_gate → compreface.recognize → tier (100/91-99/below90/unknown)
  → dedup → face_storage → Detection row
    → tier 100: Attendance(status=confirmed, push_status=pending) → background push to CiviCRM
    → tier <100: Task (dual-approval) → resolve → Attendance → push
```
Everything hangs off CiviCRM PKs: `ComprefaceSubject.contact_id → civicrm_members.contact_id`, `Detection.event_id → civicrm_events.event_id`, and the `Attendance` table.

S01 deletes `civicrm_members`, `civicrm_events`, and `attendance`; repoints FKs to new `contacts`/`events`; replaces `Attendance` with `participants` (different schema). Result: the live face pipeline **breaks at runtime** and existing enrolled faces **orphan**.

### The four CRITICAL breaks (block go-live)

1. **`Attendance` table dropped → `face_pipeline.py` crashes.**
   `face_pipeline.py:143–146` constructs `Attendance(..., push_status="pending")`. Post-S01 that class is gone → `AttributeError` on the first tier-100 detection. S01 §4.2 *instructs* the manual rewrite to `Participant(..., source="face", status="attended")` but it's a hand patch, not a migration, and **adds no test coverage.**

2. **`ComprefaceSubject.contact_id` orphaned.** S01 repoints the FK from `civicrm_members.contact_id` to `contacts.id`, then drops `civicrm_members`. Every existing enrolled subject ends with `contact_id = NULL`. **Live recognition cannot resolve anyone until faces are re-enrolled via S07.** S01 §6 accepts this as data-lossy by design.

3. **`source` field silently unset.** S22 needs `participants.source` to distinguish face/manual/zoom/name-list. The S01 patch must explicitly set `source="face"` in three places (`face_pipeline.py`, `task_service.py`, `pit.py`). No schema default — easy to miss → corrupt reporting/audit.

4. **Tier-100 auto-log disabled until re-enrollment.** Even with the code patch, `_find_member_id()` returns `None` for all faces until S07 re-enrolls them, so **every** detection (including former tier-100) routes to the volunteer queue until a human confirms it once. This is "by design" (incremental learning, no auto-clustering) but is a real interim UX regression at cutover that no spec calls out as a risk.

### HIGH-severity gaps

5. **Volunteer leaderboard scoring breaks.** `VolunteerStat` survives structurally, but the points formula keys off `Attendance.status="confirmed"`, which no longer exists (`participants.status` ∈ attended/registered/no_show/cancelled). **No spec redefines the scoring logic** → leaderboard silently stops awarding points.

6. **No consent retrofit for existing enrollments.** S08 adds `biometric_consent` but explicitly does **no** backfill. Pre-existing enrolled subjects keep `consent_id = NULL`; the `enroll_without_consent` policy default is deferred to the owner. **Compliance gap** (RA 10173 / GDPR lawful-basis unknown for already-enrolled faces) with no migration step.

7. **CiviCRM push pipeline removed entirely** (by design — S17 outbox/S18 Google Chat replace it), but this means no attendance leaves Seraphim after cutover. Fine if intended; must be stated in cutover runbook.

### Orphaned-but-unmentioned built features
Survive structurally but no spec governs their new semantics: **dual-approval vote aggregation**, **PIT queue** (S07 redefines enroll semantics but not the rest), **leaderboard/gamification**, **dedup + quality gate** (reused, OK), **RTSP capture** (untouched, OK), **SSE** (S15 adds viewer filter only). The risk is these drift out of sync because no sprint "owns" them post-transition.

---

## Recommended decisions for the next brainstorm

1. **Bridge bootstrap at cutover.** Decide and *specify* how live detections become participants on day one: bulk re-enrollment job from the migrated `face_samples`? Accept the "everyone goes through the queue once" interim? This is currently unwritten.
2. **A dedicated "FR transition" sprint or section.** Right now the FR migration is smeared across S01 (demolish) + S07 (rebuild) with the glue only in 00-MASTER. Consider a single spec that owns: Attendance→Participant data conversion (or explicit "no history" decision), re-enrollment job, scoring-formula update, consent backfill, and tier-100 path test coverage.
3. **Consent backfill policy** — set `enroll_without_consent` default and write the one-time backfill script into S08.
4. **Leaderboard scoring** — redefine points against the new `participants` model in S01 or S07.
5. **CN pre-flight patch pass** — push all 10 master-only rulings into their owning sprint texts.

---

## Appendix — Sources
- Built-code map: routers/services/models/workers + frontend (Explore pass 1).
- Spec completeness matrix S01–S23 + CN compliance table (Explore pass 2).
- FR integration gap analysis with 22 ranked risks (Explore pass 3).
- Existing-settings design already captured in `2026-06-22-configurable-settings-design.md`.
