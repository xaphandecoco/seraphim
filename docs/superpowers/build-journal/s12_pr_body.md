feat(S12): activities (assignable tasks)

## Summary

Complete activity/task engine for contact-tied workflows. Staff assign, track, and complete activities; role-aware mutations with status-transition matrix. Due-reminder producer stub (awaiting S16/S17 expansion). Full React UI: "My Tasks" / "All Tasks" page, ActivitiesPanel on contact profile, ActivityFormModal, ActivityCard. Security: admin-only reassignment (HIGH vulnerability fixed pre-commit); enum-validated status/priority transitions.

- **Activities table & ORM models:** full CRUD with role-aware mutations; Outbox model for S16/S17 producers
- **ActivityService:** status-transition matrix, due-reminder scanner stub, all operations audit-logged via S02 `record(...)`
- **8 API endpoints:** list/create/detail/update/status-transition/reassign/due-reminders/outbox; require_viewer (safe) for assignees list
- **Frontend UI:** "My Tasks" / "All Tasks" tabs, ActivitiesPanel on contacts, create/edit modal, activity cards, TanStack Query integration
- **Security:** PASS (HIGH reassign vulnerability + MEDIUM enum validation fixed pre-commit); role-aware authz + audit logging
- **Dedupe reconciliation:** FK manifest introspection test NOW PASSES (S11 carry-forward resolved by adding `('activities', 'target_contact_id')`)

## Definition of Done

- [x] Migration `s12a1b2c3d4e5` (down_revision `s11a1b2c3d4e5`): activities table + outbox table
- [x] Activity + Outbox SQLAlchemy ORM models with proper indexes + constraints
- [x] ActivityService (CRUD, role-aware status matrix, due-reminder producer stub `scan_due_reminders()` + module-level `run_due_reminder_job()`)
- [x] 8 API endpoints: list (paginated) / create / detail / update / status-transition / reassign (admin-only) / due-reminders / outbox
- [x] require_viewer reused (already existed as S15 shim; /activities/assignees uses it per AC10 + test plan)
- [x] Full React UI: "My Tasks" / "All Tasks" page, ActivitiesPanel on contact profile, ActivityFormModal (create/edit), ActivityCard, hooks/service/types
- [x] Audit ALL mutations via S02 `record(...)`
- [x] Security audit PASS (HIGH: volunteer reassign PATCH removed from update schema, now admin-only POST /activities/{id}/reassign; MEDIUM: status/priority enum-validated, completed_at stamped on 'completed' creation)
- [x] Dedupe FK manifest now includes `('activities', 'target_contact_id')` — introspection test passes (S11 carry-forward resolved)
- [x] Green gates: backend 38 S12 tests + 42 migrations+manifest+dedupe + 42 regression passed; app imports clean. Frontend: build + lint clean, 483 tests; security PASS
- [x] test_migrations.py EXPECTED_HEAD updated to `s12a1b2c3d4e5`

## Test Plan

- [x] Backend 38 isolated S12 tests: **passed/0 failed** (incl. 3 security regression tests: reassign authz, enum validation, completed_at stamping)
- [x] Migrations + manifest + dedupe guard: **42 passed/0 failed** (introspection test now passes with activities FK added)
- [x] Regression slice (contacts/audit/participants/tasks): **42 passed/0 failed**
- [x] App imports clean (no circular deps, all new modules properly exported)
- [x] Frontend build + lint: **clean**
- [x] Frontend `npm run test:run`: **483 tests passed** (35 files, incl. ActivitiesPanel, ActivityFormModal, ActivityCard, hooks)
- [x] Security audit: **PASS** (HIGH reassign vulnerability fixed pre-commit; MEDIUM enum validation + audit logging in place)

## Notable Implementation Details

1. **Security fix (HIGH → PASS):** Volunteer could reassign activities via PATCH (`assignee_user_id` in `ActivityUpdate`). Fixed: removed from update schema; reassignment now admin-only via `POST /activities/{id}/reassign` (separate endpoint with require_admin).

2. **Security hardening (MEDIUM → PASS):** `ActivityCreate` and `ActivityUpdate` now validate status/priority against Literal/Enum (not free-form strings). `completed_at` is automatically stamped when creating an activity with `status='completed'`.

3. **Dedupe reconciliation:** S11's introspection test (`test_manifest_covers_all_contact_fks`) **now passes**. S12 added `('activities', 'target_contact_id')` to the `_REASSIGNMENT_TARGETS` manifest. This resolves the S11 carry-forward: the test is a self-maintaining guard that breaks when a new contacts.id FK is added; the break is now repaired.

4. **require_viewer reuse:** `/activities/assignees` (user list for assignment dropdowns) uses `require_viewer` (spec §4.1 said `require_volunteer`, but AC§10 + test plan §8 require viewer 200). Endpoint is read-only, projection safe (names/emails/roles only, no secrets).

5. **Outbox producer stub:** `Outbox` table created; `run_due_reminder_job()` stub scans due activities. Full S16/S17 expansion (NotificationService, push messages, email) deferred. Today: producer runs but produces no messages (graceful stub).

6. **Audit compliance:** Every activity write (create/update/status-change/reassign) is recorded via S02's `record(...)` helper, capturing operation, delta, actor, timestamp.

## Carry-Forwards

**🟡 Documented in docs/BLOCKERS.md — Owner decisions pending:**

- **[S12] BottomNav 6-slot UX (spec §10 Q6):** "Ranking" moved to admin "More" sheet; new "Tasks" tab (`/activities`) added to main bar. Non-admin users no longer see Ranking in main bar. **Owner UX confirmation requested.**

- **[S12] Dual "Tasks" labels:** Both detection-review tab (`/`, label "Tasks") and activities tab (`/activities`, label "Tasks") show same label in BottomNav. BottomNav test uses regex to handle matches. **Owner may want to rename detection tab** (e.g., "Review") for clarity.

- **[S12] /activities/assignees endpoint authz:** Uses `require_viewer` (spec §4.1 table said `require_volunteer`, but AC§10 + §8 test plan require viewer 200 for read endpoints). Accessor projection is safe (no secrets). **Confirm if acceptable; else tighten to require_volunteer + update AC10.**

- **[S12] ActivityFormModal target-contact field:** Plain numeric ID input when not pre-filled from a contact profile (no S03 contact-picker component exported). When modal launched from `/contacts/{id}`, field is read-only/locked. Full contact-search picker is a **post-S12 enhancement**.

## Rollback Notes

Rollback is safe (Alembic downgrade):
```bash
cd backend && alembic downgrade s11a1b2c3d4e5
```

Migration is pure-additive (new `activities` + `outbox` tables); downgrade drops them. No data loss in contacts or other core tables.

## Migration Head

**New Alembic head:** `s12a1b2c3d4e5`  
**Down revision:** `s11a1b2c3d4e5`

Applied via:
```bash
cd backend && alembic upgrade head
```

---

Generated by senpai-v2-docs  
Green gate: backend ✅ + frontend ✅ + security ✅  
Date: 2026-06-26
