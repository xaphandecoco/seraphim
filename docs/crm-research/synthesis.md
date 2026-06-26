I have everything needed. The four reports are exhaustive. Producing the synthesis now.

---

# Project Seraphim — CiviCRM Replacement: Architecture Synthesis & Grilling Prep

> **⚠️ Superseded note (2026-06-22):** Notification target later changed to **Google Chat (single channel) + Gmail** (Mattermost dropped — `decisions.md` Round 8). "Mattermost" below reflects the church's current n8n setup, not the target. Authoritative: `docs/specs/` + `00-MASTER.md`.

Scale anchor: 1,440 contacts, 33,579 attendance rows, ~4 n8n workflows, 3 roles. This is a small-data, high-leverage migration. The dominant engineering theme is: **invert the source of truth, then deliberately under-build CiviCRM's generic machinery** (EAV, ACL DSL, report kernel, API kernel) in favor of plain Postgres columns + a few JSONB escape hatches + purpose-built endpoints.

---

## 1. ARCHITECTURE INVERSION

Today CiviCRM is the system of record and this app is a satellite that mirrors-in and pushes-back. After this work, **this app mints the IDs and owns the truth**; CiviCRM is deleted. Four concrete inversions:

**(a) ID ownership flips.** `contact_id` and `event_id` are currently CiviCRM's own PKs, copied in with no sequence (`members.py:129`, `events.py:93`) and wired into FKs across the whole schema (`ComprefaceSubject.contact_id`, `Detection.event_id`, `Attendance.contact_id/event_id`, `Log.event_id`). After inversion these become **app-minted identity/serial columns**. This is the deepest change — every dependent FK follows. The migration must preserve old CiviCRM IDs (as `external_id`) so the name-only attendance linkage can be reconstructed.

**(b) The entire attendance-push pipeline is deleted, not rewired.** Once attendance is authoritative the moment it's written, there is no external target. Delete: `push_status`/`push_attempts`/`last_push_error` columns, `_process_civicrm_push` (the worker state machine), `/attendance/push`, `/push-preview`, `/dead-letter*` endpoints, `CiviCRMClient.push_attendance`, and the dead `expired` enum value. Note `Log.push_status` and analytics CSV export columns reference it too — clean those. **Caveat:** the outbox/retry/dead-letter *pattern* this code embodies is exactly what the n8n-replacement layer needs — preserve the pattern as a generic `outbox`, just not the CiviCRM target.

**(c) The active-event model survives almost intact.** One global active event in `admin_settings[active_event_id]`, read by the RTSP worker to stamp detections, reloaded every 60s. Only change: `set_active_event` currently validates against `civicrm_events` (`events.py:49`) — repoint to the native `events` table. The face→attendance pipeline (`process_face_crop`) is CiviCRM-independent and reusable as-is.

**(d) CiviCRM code deleted wholesale.** `services/civicrm.py` (entire `CiviCRMClient`), the sync endpoints in `members.py`/`events.py`, the CiviCRM branch of `setup.py` connectivity test, three config keys (`civicrm_url/api_key/site_key`) + their `DynamicSettings` getters + schema DTOs, and all the CiviCRM-framed frontend (sync buttons, AttendancePage push/dead-letter, SetupPage CiviCRM step). **What must be NET-NEW:** native member/event CRUD, and critically an **enroll-member-face flow** — no code today ever *creates* a `ComprefaceSubject` row with a `contact_id`; those links are seeded externally. A native CRM cannot ship without owning that orchestration.

---

## 2. PROPOSED TARGET DATA MODEL

Opinionated default: **typed columns for the 10 known church custom fields** (best search/index/report performance), plus a JSONB escape hatch + a `custom_field_def` metadata table for future admin-defined fields. Do NOT replicate CiviCRM EAV.

| Table | Key columns | Notes |
|---|---|---|
| **contacts** | `id` (app-minted PK), `external_id` UNIQUE (old CiviCRM id), `contact_type` enum, `first/last/suffix`, `gender`, `birth_date`, `phone`, `email`, `street_address`, `barangay`, `pepsol`, `ministry`, `facebook_name`, `marital_status`, `community`, `community_add_date`, `followup_listing`, `invited_by_id`→contacts, `consolidated_by_id`→contacts, `community_leader_id`→contacts, `custom_data JSONB`, `is_deleted`, `created_at/updated_at` | Renamed from `CiviCRMMember`. The three "by" fields become **FK self-references** (unlocks reverse-lookup reports). Typed columns for the known 10. |
| **events** | `id` (app-minted), `external_id`, `title`, `event_type` (Service/Prayer/…), `start_at`, `end_at`, `location`, `max_participants`, `is_active` | Renamed from `CiviCRMEvent`. `set-active` repoints here. |
| **participants** (attendance) | `id`, `contact_id`→contacts, `event_id`→events, `status` enum (registered/attended/no_show/cancelled/pending), `role`, `register_date`, `registered_by_id`, `source` | **UNIQUE(event_id, contact_id)**; index each FK. The 33k-row table. Merges today's `Attendance` minus push columns. Decide: nullable `contact_id` for unidentified-but-confirmed faces? |
| **activities** | `id`, `activity_type`, `subject`, `details`, `activity_date`, `duration_min`, `status` enum, `priority`, `assignee_id`→contacts/users, `target_contact_id`→contacts, `case_id`→cases (nullable), `created_by_id`, `created_at` | Collapse the contact-role join into `assignee_id`+`target_contact_id` unless multi-target proves real. |
| **cases** | `id`, `case_type`, `status` enum, `client_contact_id`, `assigned_to_id`, `opened_at`, `closed_at` | Thin parent of child activities. Skip timelines/sequences v1 (optional JSON `case_type_timeline` later). |
| **custom_field_def** | `name`, `label`, `data_type`, `options JSONB`, `is_required`, `weight` | Metadata layer over `contacts.custom_data` JSONB. Only if admin-defined fields are actually wanted. |
| **profiles** | `name`, `entity`, `fields JSONB` (`[{field, label, required, order}]`), `settings JSONB` | Config-driven form renderer. Seed 2-3 presets (New Visitor, Community Member, Volunteer). |
| **groups** + **group_members** | groups: `id`, `name`, `type` (static/smart), `criteria JSONB`; members: `group_id`, `contact_id` | Smart group = saved filter resolved live (no cache at 1,440). |
| **saved_searches** | `id`, `name`, `owner_id`, `criteria JSONB` | Backs advanced search; promotable to smart group. |
| **automation_rules** | `id`, `trigger` enum, `conditions JSONB`, `actions JSONB`, `delay_minutes`, `is_active` | ECA dispatcher, not a DSL. |
| **outbox** | `id`, `event_type`, `payload JSONB`, `status` (pending/sent/failed/dead_letter), `attempts`, `last_error`, `run_at` | Generalized from today's push_status pattern. At-least-once delivery for Mattermost/Zoom/email. |
| **job_runs** | `job_name`, `started_at`, `finished_at`, `status`, `detail` | Cron health/log equivalent. |
| **users** | existing + `role` enum (admin/volunteer/viewer), optional `scope` | Extend the existing 2-role union to 3. |
| **audit_log** | `id`, `actor_id`, `action`, `entity`, `entity_id`, `before JSONB`, `after JSONB`, `at` | Net-new; critical for merge reversibility + RBAC accountability. |

Reusable as-is: face pipeline (`compreface.py`, `quality_gate.py`, `dedup.py`, `face_storage.py`), worker host + 3 of 4 jobs, RTSP capture, task lifecycle, auth/SSE/rate-limit/storage infra.

---

## 3. FEATURE-TO-IMPLEMENTATION MAP

| Owner need | Recommended approach | Effort | Key risks |
|---|---|---|---|
| Contact search (name/email) | Already exists: `GET /members?search` — rename + paginate (add offset). | **S** | None; current search is limit-capped, no offset. |
| **Advanced search (any field)** | Query-builder endpoint: structured filter JSON `[field,op,value]` → SQLAlchemy `where`, **column whitelist** to block injection. Save as `saved_searches`. | **L** | Injection if not whitelisted; UI complexity (no multi-criteria filter UI exists today). |
| Cases/activities assigned to someone | New `activities`+`cases` tables, assignee FK, list/detail/assign UI. Reuse user data for assignee picker. | **L** | Entirely net-new model + UI; scope creep into full CiviCase. |
| Create contact from template (profiles) | `profiles` table as JSON form schema; React renders form; backend validates. Seed presets. | **M** | Needs a shared `FormField` component set (absent today). |
| **CSV mass import** | Upload→column-map→dedupe-match→skip/update/fill; batched txn (chunks of 500); per-row result report; run in existing worker. | **L** | Encoding/messy data; dedupe-match correctness; XLSX support. |
| **Find & merge duplicates** | Config rules (`{field,length,weight}`+threshold); SQL blocking + Python scoring; side-by-side merge UI; transactional FK reassignment loser→survivor + soft-delete + audit row. | **XL** | Correctness of FK reassignment; irreversibility (mitigate w/ audit); name-collision false merges. |
| Create events (Services/Prayers) | Native `POST/PUT/DELETE /events` (CRUD). Strip sync/camera framing from EventsPage; keep list/card. | **S/M** | Low. |
| **BULK add participants, no downtime** | Single set-based `INSERT … ON CONFLICT (event_id,contact_id) DO NOTHING`; bulk `UPDATE` for status. 1,500 rows < 1s. | **M** | This is *the* headline fix; risk is doing it row-by-row (the CiviCRM mistake). Lock/timeout tuning on the 33k table. |
| Manage events | CRUD + participant grid (editable status table). | **M** | Bulk-status UX. |
| **Export contacts+attendance CSV/XLSX (>20k, 2yr)** | Server-side **streaming** response (chunked query, generator) for CSV; XLSX via streaming writer; never materialize all rows in memory. | **L** | Theirs crashes >20k — must stream, not buffer. XLSX memory blowup; long-running request → background + download link. |
| Reporting dashboard | Fixed purpose-built aggregate endpoints (attendance over time, by Barangay/Ministry/Community, new-this-month, invited-by rollups) → Recharts. Reuse DashboardPage shell. | **M** | Don't build a generic report engine. |
| Settings: system status, config checklist, custom fields, roles, CiviRules-equiv, cron | System-status page (reuse `/health/queue`); custom-field admin CRUD; 3-role admin (extend UserManagementPage); automation-rule admin; job-run viewer. | **L** | Several sub-features; custom-field admin is the spiky one. |
| Internal automations/API (replace 4 n8n flows) | `routers/public.py` (public newcomer form + HMAC webhooks), `services/mattermost.py`, `services/zoom.py` (S2S OAuth), ECA dispatcher + outbox + APScheduler. | **XL** | Needs owner's n8n JSON exports + Zoom/Mattermost creds; Zoom UUID/recurring-meeting gotchas; public endpoint abuse. |
| Permissions admin/volunteer/viewer | 3-role enum + FastAPI dependency per route; viewer=GET, volunteer=write contacts/activities, admin=all. | **M** | Extending isAdmin-only frontend gating to 3 roles touches authStore + guards. |
| Integrated cron | APScheduler in worker `lifespan`; advisory lock if multi-replica; `job_runs` log. | **M** | Single-vs-multi replica decision (in-process vs locked). |
| **Full data migration** | ETL: contacts first (mint IDs, keep `external_id`), then resolve attendance by **name** → contact, with a collision/unmatched review queue. | **XL** | Name-only linkage (no Contact ID in event export) → false matches; the single biggest data-correctness risk. |

---

## 4. THE HARD PROBLEMS (ranked)

1. **Name-only migration linkage (highest data-correctness risk).** The Event Report has NO Contact ID — it links to contacts only by name. 33,579 rows against 1,440 contacts means guaranteed name collisions, suffix mismatches ("Jr."), and unmatched rows. A wrong match silently corrupts someone's attendance history. *Mitigation:* deterministic match on normalized (first+last+suffix), then a **human review queue** for ambiguous/unmatched rows; never auto-assign on a fuzzy tie; preserve `external_id` for re-runs; make the migration idempotent and re-runnable.

2. **Dedupe/merge correctness + irreversibility.** Merge reassigns every FK (participants, activities, emails) loser→survivor in one transaction and soft-deletes the loser. A missed FK orphans data; a wrong merge is destructive. *Mitigation:* enumerate every FK referencing `contacts` before building merge; full `before/after` audit row for reversibility; batch-merge only conflict-free pairs; never merge across a name-collision without human confirm.

3. **Bulk participant insert/update without downtime.** The owner's #1 pain. Easy to get right (set-based `ON CONFLICT`), easy to get catastrophically wrong (ORM per-row flush = the CiviCRM 10-15min crash reproduced). *Mitigation:* `session.execute(insert(...), rows)` / `on_conflict_do_nothing`; statement timeout + lock awareness on the 33k table; load-test at 1,500+ before shipping.

4. **Export at scale (>20k rows, 2yr).** Theirs crashes because it buffers. *Mitigation:* stream via server-side cursor + generator response for CSV; streaming XLSX writer (or offload to background job + signed download link); cap/paginate XLSX if memory-bound; test at full 33k.

5. **Custom-field schema strategy (architectural fork).** Typed columns (fast, indexable, rigid) vs JSONB (flexible, slower to query/report) vs EAV (don't). *Recommendation:* typed columns for the known 10 + JSONB `custom_data` + `custom_field_def` metadata for future admin fields. Risk is choosing JSONB-only and then suffering on every Barangay/Ministry report and GIN-index maintenance.

6. **Safe rules engine / automation.** A misconfigured rule that emails/SMSs the whole database, or an infinite trigger loop (rule writes a field that re-fires the rule). *Mitigation:* declarative ECA (data, not DSL — no embedded expression language); whitelist trigger/action types; per-rule rate caps; delayed actions via outbox with idempotency keys; dry-run mode.

7. **The enrollment gap (face↔contact link).** No code creates `ComprefaceSubject.contact_id` today — it's externally seeded. A native CRM must own this, and getting it wrong breaks all future recognition. *Mitigation:* first-class "enroll member face" endpoint that creates the subject row + calls CompreFace `add_subject`/`add_example` atomically; backfill existing links during migration.

8. **Zoom morning-prayers integration correctness.** Recurring-meeting UUID resolution (bare ID returns only latest occurrence; UUIDs with `/` need double-encoding), email masking breaking contact matching, report API lag after meeting end. *Mitigation:* resolve specific-occurrence UUID; match email-first then fuzzy-name with an unmatched review list; poll on delay or use `meeting.ended` webhook.

---

## 5. OPEN DECISIONS TO GRILL THE OWNER ON

### Data model & custom fields
1. **Custom-field volatility:** Are the 10 fields (Barangay, PEPSOL, Ministry, etc.) stable, or will staff want to add/rename fields themselves? → **Typed columns** (stable, fast) vs **JSONB + admin custom-field UI** (flexible, slower). This decides a core schema fork.
2. **"Invited By / Consolidated By / Community Leader":** Are these always existing contacts (→ FK references, enabling "who did X invite" reports) or sometimes free text for non-contacts? Pick one; mixed is painful.
3. **PEPSOL / Followup Listing / Marital Status:** Are these fixed pick-lists (→ enum/lookup) or free text? Provide the allowed values now if pick-lists.
4. **Multi-value fields:** Can one contact have multiple ministries / communities / phones / emails, or strictly one each? (Decides child tables vs single columns.)
5. **Household/Organization contacts:** Do you track families or orgs, or are all 1,440 individuals? (Decides whether `contact_type` is real or vestigial.)

### Migration
6. **Name-collision policy:** When two contacts share a name and an attendance row can't be disambiguated, do we (a) drop to a review queue for manual assignment, (b) attach to the most-recently-created, or (c) skip? (Strongly recommend (a).)
7. **Unmatched attendance rows:** If an Event Report name matches NO contact, do we auto-create a stub contact, hold in a review queue, or discard? How many such rows can you tolerate?
8. **Cutover model:** Big-bang (export CiviCRM once, migrate, go live) or parallel-run (both live for a period)? Is there a freeze window where no new CiviCRM data is entered during migration?
9. **Historical fidelity:** Must we preserve CiviCRM Contact IDs (as `external_id`) for traceability, or is a clean re-key acceptable? Any external systems still referencing the old IDs?
10. **Attendance statuses:** Event Report shows "Attended" — are there other statuses in your real data (No-show, Cancelled, Registered) we must preserve, or is everything "Attended"?

### Events & face-recognition attendance
11. **One global active event vs many:** Today there's exactly one camera-active event. Do you ever run two services/cameras simultaneously needing different events? (Decides whether to keep the single-active model or go per-camera.)
12. **Unidentified-but-confirmed faces:** When a volunteer confirms attendance but the face isn't linked to a contact, is "anonymous attendance" allowed (nullable `contact_id`) or must every attendance row have a contact?
13. **Face enrollment ownership:** Who enrolls member faces and when (at contact creation, at first detection, bulk)? This flow is net-new and must be designed around your actual process.

### Cases / Activities
14. **"Cases like activities assigned to someone":** Is a case (a) a single assigned task, or (b) a container of multiple follow-up activities with a workflow? Give one concrete real example end-to-end (e.g. "new visitor follow-up").
15. **Assignment target:** Are activities assigned to **staff users** (admin/volunteer logins) or to **contacts** (e.g. a community leader who isn't a system user)? Or both?
16. **Auto-generated follow-ups:** Do you want "open case → auto-create these N follow-up activities on a schedule" (timeline), or is manual creation enough for v1?

### Search / Reporting / Dashboard
17. **Top 5 reports:** Name the exact reports you run today (or wish you could). This determines the purpose-built endpoints; we will NOT build a generic report engine.
18. **Smart groups:** Do you need saved searches that auto-update membership (smart groups), or are static lists + re-running a search enough?
19. **Export format priority:** CSV, XLSX, or both? Is a "your export is ready, click to download" background flow acceptable for big exports, or must it be instant?

### Automation / API (n8n replacement)
20. **n8n JSON exports:** Can you provide all four workflow JSON exports? (Single most valuable artifact — reveals exact field mappings, statuses, conditionals.) Without them we reverse-engineer.
21. **Newcomer form:** Where is it hosted, what fields does it collect, and may we add anti-spam (CAPTCHA/honeypot)? What contact sub-type/group does a newcomer get tagged with?
22. **Mattermost:** Webhook URL + target channel(s); per-attendee pings or a batched digest per event?
23. **Zoom prayers:** Can you provide S2S OAuth creds (account_id/client_id/client_secret w/ `report:read:admin`), the morning-prayers meeting ID, whether it recurs, and confirmation that attendee emails are NOT masked?
24. **Notification volume safety:** What's the max acceptable blast radius for an automation (e.g. should any rule ever be able to message all 1,440 contacts)? Sets the rate caps.

### Permissions / Ops / Scope
25. **Viewer role definition:** What exactly can a "viewer" see — everything read-only, or scoped (e.g. only their own ministry/community)? Per-record scoping is a meaningful cost.
26. **Deployment topology:** How many app replicas run in prod? (Decides in-process APScheduler vs. scheduler + DB advisory locks for cron.)
27. **Sequencing priority:** If you could only have THREE features live first, which three? (My straw-man bet: migration + contacts/search + bulk participants — but you decide.)

### Branding/scope
28. **Rebrand:** "LNC Attendance / Volunteer Portal" branding is everywhere — what's the new product name, and is a desktop-friendly layout (sidebar/master-detail) wanted, or stay mobile-first?

---

## 6. STRAW-MAN SPRINT SEQUENCE

*(First cut — refine after grilling. Roughly dependency-ordered; later sprints parallelizable.)*

1. **Schema Inversion & Rename** — App-mint `contacts.id`/`events.id` as identity columns, add `external_id`, rename `CiviCRMMember/Event`→`Member/Event`, repoint all FKs; new Alembic migration (no editing applied ones).
2. **CiviCRM Excision** — Delete `CiviCRMClient`, push pipeline, `push_status` columns, sync endpoints, config keys, CiviCRM frontend surfaces; rewrite affected tests.
3. **Native Contact & Event CRUD** — Create/update/delete/list for contacts and events; repoint `set-active`; the contact detail/edit screen + reusable `FormField`/`DataTable`/`Pagination` components.
4. **Data Migration ETL** — Idempotent contact import (preserve external_id) + name-resolved attendance import with a collision/unmatched **review queue**.
5. **Bulk Participants & Export-at-Scale** — Set-based `ON CONFLICT` bulk add/update; streaming CSV/XLSX export tested at 33k rows. (The owner's two biggest pains.)
6. **Advanced Search & Smart Groups** — Whitelisted query-builder endpoint + saved searches + static/smart groups + multi-criteria filter UI.
7. **CSV Import Wizard** — Upload→map→dedupe→skip/update/fill, batched, per-row report, mapping reuse.
8. **Find & Merge Duplicates** — Config rules + candidate-pair finder + side-by-side merge UI + transactional FK reassignment + audit/reversibility.
9. **Activities & Cases** — Tables + assignment + list/detail UI; optional case timelines.
10. **Profiles (Templated Contact Creation)** — JSON form-schema renderer + seed presets.
11. **Reporting Dashboard** — Purpose-built aggregate endpoints + Recharts tiles + per-report export.
12. **RBAC: 3 Roles** — admin/volunteer/viewer enum + per-route dependencies + frontend guard/store extension.
13. **Face Enrollment Flow** — First-class enroll-member-face endpoint creating `ComprefaceSubject(contact_id)` + CompreFace add_subject/add_example; backfill.
14. **Automation Core: Outbox + ECA + Scheduler** — Generalized outbox, declarative rules table, APScheduler cron, `job_runs` log, custom-field admin + system-status settings.
15. **n8n Replacement: Public Edge & Integrations** — `routers/public.py` (newcomer form + HMAC webhooks), `services/mattermost.py`, `services/zoom.py` (S2S OAuth), wire the four workflows through outbox + automations.
16. **Rebrand & Desktop Shell** — New product name/manifest; optional sidebar/master-detail layout for data-dense CRM.

---

Sharpest things to resolve before any code: **(1)** name-collision migration policy, **(2)** custom-field strategy (typed vs JSONB), **(3)** get the four n8n JSON exports, **(4)** replica count for cron, **(5)** the three-features-first priority. Everything else can be sequenced around those five answers.