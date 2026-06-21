# Project Seraphim — Face-Recognition-Native CRM: Framework

**Goal:** Replace CiviCRM. Make Seraphim the system of record — a **face-recognition-native CRM** for Light North Caloocan church. Migrate 1,440 contacts + ~33.6k attendance rows. Stay on the current stack (FastAPI + Postgres + React + CompreFace + Unraid/Cloudflare).

Decisions that shaped this: see [decisions.md](decisions.md). Research: [synthesis.md](synthesis.md), [backend.md](backend.md), [frontend.md](frontend.md), [civicrm.md](civicrm.md), [integ.md](integ.md).

> **⚠️ Superseded note (2026-06-22):** Where this doc says outbox delivers to "Mattermost/Zoom/email", the target is now **Google Chat (single channel) + Gmail** (Mattermost dropped — see `decisions.md` Round 8). The authoritative sprint plan is `docs/specs/` (24 sprints) + `00-MASTER.md`; this framework predates S22/S23/S24 and the CN rulings.

---

## 1. Architecture inversion (what fundamentally changes)

| Today | After |
|---|---|
| CiviCRM is source of truth; Seraphim mirrors members/events IN and pushes attendance BACK | **Seraphim mints IDs and owns the truth; CiviCRM deleted** |
| PKs `contact_id`/`event_id` are CiviCRM's own IDs | App-minted identity columns + `external_id` (old CiviCRM id, kept for migration/trace) |
| `Attendance.push_status` retry/dead-letter worker pushes to CiviCRM | **Push pipeline deleted.** Its outbox/retry *pattern* is generalized into a reusable `outbox` for Mattermost/Zoom/email |
| Members/events seeded externally; **nothing enrolls a face↔contact** | First-class **face enrollment** owned by the CRM (the headline gap to close) |
| Custom data lives in CiviCRM custom groups | **Native dynamic custom-field engine** (groups + types + multi-select + contact-reference) |
| Automation in n8n | **Internal visual rules engine + outbox + scheduled jobs + clean API** |

**Deleted wholesale:** `services/civicrm.py` (entire `CiviCRMClient`), `_process_civicrm_push`, `push_status/push_attempts/last_push_error`, `/attendance/push`, `/push-preview`, `/dead-letter*`, sync endpoints in `members.py`/`events.py`, CiviCRM keys in `config.py`/`setup.py`/`schemas.py`, and CiviCRM frontend surfaces.
**Reused as-is:** CompreFace client, quality gate, dedup, face storage, RTSP capture, task/PIT lifecycle, the worker host (3 of 4 jobs), auth/SSE/rate-limit/storage infra, gamification (leaderboard/volunteer_stats).

---

## 2. Target data model (high level)

**Core**
- `contacts` — app-minted `id`, `external_id` (old CiviCRM id, UNIQUE), `contact_type` + subtype (New Friend/Regular Attendee/Regular Member/Volunteer/Student/Parent/Staff; Org subtypes Team/Sponsor), core fields (first/last/suffix, gender, birth_date, phone, email, street_address), `custom_data` JSONB, `is_deleted`, timestamps.
- `events` — app-minted `id`, `external_id`, `title`, `event_type` (Sunday Celebration/Prayer Meeting/Community Meeting/Conference/Event), `start_at`, `end_at`, `location`, `recurring_series_id` (nullable), `is_active`.
- `event_series` — recurring template that spawns dated `events` occurrences.
- `participants` (attendance) — `id`, `contact_id`→contacts, `event_id`→events, `status` (attended/registered/no_show/cancelled), `role`, `source` (face/manual/import), `detection_id` (nullable), `registered_by_id`. **UNIQUE(event_id, contact_id)**.

**Dynamic custom fields**
- `custom_field_group` — name, label, entity (Contact/Event/Activity), weight.
- `custom_field_def` — group_id, name, label, `data_type` (text/textarea/select/multiselect/date/number/checkbox/**contact_reference**), `options` JSONB, is_required, is_multi, weight. Storage = `contacts.custom_data` JSONB keyed by field name; contact_reference stores target contact id(s).

**Face recognition** (extend existing)
- `compreface_subjects` — gains a real **enrollment flow** (creates row + CompreFace add_subject/add_example). Add `consent_*` fields.
- `face_samples` — per enrolled image (path, source, quality, added_by). Supports re-enroll + RTBF purge.
- `biometric_consent` — contact_id, consented_at, recorded_by, retention_until, deletion_requested_at.

**CRM power**
- `activities` — type, subject, details, due/activity date, status, priority, `assignee_user_id`→users, `target_contact_id`→contacts, created_by. (Cases deferred.)
- `saved_searches` — name, owner, `criteria` JSONB. `groups` + `group_members` (static + smart/criteria-backed).
- `profiles` — JSON form schema for templated contact creation + public newcomer form.

**Automation & ops**
- `automation_rules` — trigger, `conditions` JSONB, `actions` JSONB, delay, is_active, dry_run, rate caps.
- `outbox` — event_type, payload JSONB, status (pending/sent/failed/dead_letter), attempts, run_at. At-least-once delivery (Mattermost/Zoom/email).
- `job_runs` — scheduled-job health log (APScheduler in-process, single instance).
- `audit_log` — actor, action, entity, before/after JSONB, at. (Critical for merge reversibility + RBAC.)
- `users` — extend role enum to **admin / volunteer / viewer**.

---

## 3. The hard problems (and the plan for each)
1. **Bulk participants w/o downtime (top pain)** — set-based `INSERT ... ON CONFLICT (event_id,contact_id) DO NOTHING`; 1,500 rows <1s. Load-test at 1,500+.
2. **Export at scale (>20k)** — streaming response (server-side cursor + generator) for CSV; streaming XLSX writer or background job + download link. Test at 33k.
3. **Migration linkage** — solved by re-export with real Contact ID + Event ID. Column-mapping importer; people-link fields resolved by name → review queue; idempotent/re-runnable; option-value normalization (DEPARO/Deparo); split multi-value.
4. **Face↔contact enrollment (net-new, #1 priority)** — first-class enroll endpoint; bulk photo upload → existing detection→PIT→confirm→auto-enroll; backfill.
5. **Dedupe/merge correctness** — enumerate all FKs to contacts; transactional reassignment loser→survivor; before/after audit; never auto-merge across name collisions.
6. **Safe rules engine** — declarative ECA (data, not a DSL); whitelist triggers/actions; per-rule rate caps; delayed actions via outbox w/ idempotency; dry-run mode.
7. **Biometric governance** — consent + retention + right-to-be-forgotten (purge CompreFace subject + stored crops).

---

## 4. Proposed sprint plan (complete-before-cutover; ~21 sprints in 7 phases)

Each sprint is scoped to be independently spec-able and "one-shot"-able (contract-first: schema → backend → frontend → tests).

**Phase A — Foundation & decoupling**
- **S1. Schema Inversion & CiviCRM Excision** — app-mint ids + `external_id`, rename `CiviCRMMember/Event`→`Contact/Event`, repoint all FKs (new Alembic migration); delete `CiviCRMClient` + push pipeline + sync endpoints + config/setup/schema keys + CiviCRM frontend; rewrite affected tests.
- **S2. Dynamic Custom-Field Engine** — `custom_field_group/def` + JSONB storage + all types incl. multi-select & contact-reference + admin CRUD UI.

**Phase B — Core CRM**
- **S3. Native Contact CRUD + Detail Profile** — create/edit/list with core + custom fields; contact detail page (with face-panel slot); reusable `FormField`/`DataTable`/`Pagination`.
- **S4. Native Event CRUD + Management** — event types, recurring series, active-event repoint, participant grid (editable statuses).
- **S5. Bulk Participants + Export-at-Scale** — set-based bulk add/update; streaming CSV/XLSX export. (The two daily pains.)
- **S6. Data Migration ETL** — column-mapping importer; contacts (preserve `external_id`) → attendance by Contact ID+Event ID; people-link by name → review queue; multi-value split; normalization; idempotent.

**Phase C — Face-recognition-native (the #1 priority)**
- **S7. Face Enrollment + Bulk Photo Ingestion** — first-class enroll endpoint (`ComprefaceSubject(contact_id)` + add_subject/add_example); bulk photo upload → detection → PIT → confirm → auto-enroll; backfill; profile face panel (enrolled photos + re-enroll + recognition attendance history).
- **S8. Biometric Consent & Right-to-be-Forgotten** — per-contact consent (date/who), retention policy, deletion-request flow (purge faces from CompreFace + storage), surfaced on profile.

**Phase D — CRM power features**
- **S9. Advanced Search + Saved Searches + Smart Groups** — whitelisted query-builder over core+custom fields; saved searches; static/smart groups; multi-criteria UI.
- **S10. CSV/XLSX Import Wizard** — upload→map→dedupe-match→skip/update/fill, batched, per-row report (general-purpose).
- **S11. Find & Merge Duplicates** — config rules + candidate finder + side-by-side merge + transactional FK reassignment + audit/reversibility.
- **S12. Activities (assignable tasks)** — types, assignee (system users), due date, status, notes; profile + "my tasks" view. (Cases later.)
- **S13. Profiles + Public Newcomer Form** — JSON form-schema renderer + seed presets; public newcomer intake endpoint (replaces n8n newcomer flow front end).

**Phase E — Insight & access**
- **S14. Reporting Dashboard** — purpose-built aggregates (attendance trends, by segment, assimilation funnel, leader rollups) + Recharts; finalized from owner's reports screenshot.
- **S15. RBAC: 3 Roles** — admin/volunteer/**viewer (reports/dashboard only)**; per-route deps + frontend guards/store.
- **S16. Settings: System Status + Config Checklist + Cron** — system-status page, config checklist, APScheduler + `job_runs` viewer.

**Phase F — Automation & integration (replace n8n)**
- **S17. Automation Core: Outbox + Visual Rules Engine** — generalized outbox; ECA rules engine + admin UI (triggers/conditions/actions/delays); dry-run + rate caps.
- **S18. n8n Replacement: Integrations** — Mattermost service; Zoom S2S OAuth attendance; event-reports + zoom-prayers flows wired through rules+outbox. (Needs n8n JSON + creds.)
- **S19. Clean Public/Internal API** — documented REST API + API keys/HMAC webhooks for external automation.

**Phase G — Polish & cutover**
- **S20. Rebrand + Desktop-Responsive Pass** — product name/manifest; responsive desktop for data-dense views (mobile-first preserved).
- **S21. Cutover** — freeze CiviCRM, final migration run, parallel verification, go-live, decommission.

---

## 5. Pending owner artifacts (do not block specs; needed to implement)
1. Fresh CiviCRM exports with real **Contact ID + Event ID** (+ email + all custom fields if possible).
2. **n8n workflow JSON** exports (×4) → exact field mappings for S18.
3. **Reports screenshot** → finalizes S14.
4. Mattermost webhook URL + Zoom S2S OAuth creds → S18.
5. **Product name** (working name: "Seraphim").
