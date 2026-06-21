# 00-MASTER — Project Seraphim CRM: Master Spec

**Status:** Authoritative. All 24 sprint specs (S01–S24) exist on disk and are reconciled against this document. The CN consistency rulings (§9) were pushed down into the individual sprint texts in the 2026-06-22 patch pass. Where an individual sprint spec disagrees with this file, **this file wins**. Open a follow-up to patch the sprint spec.

**Last reconciled:** 2026-06-22 — added S24 (FR Transition & Cutover Bridge) + CN-28; applied the CN patch pass to S01–S23; removed the duplicated draft from the S01 file. Prior pass 2026-06-21 against all S01–S23 spec files + `docs/crm-research/{framework,decisions,reports,n8n-analysis}.md`.

---

## Table of Contents

1. [Project overview & architecture inversion](#1-project-overview--architecture-inversion)
2. [Canonical target data model](#2-canonical-target-data-model)
3. [Phase + 23-sprint plan](#3-phase--23-sprint-plan)
4. [Build-order dependency graph](#4-build-order-dependency-graph)
5. [Global conventions](#5-global-conventions)
6. [Shared-ownership contracts](#6-shared-ownership-contracts)
7. [Pending owner artifacts](#7-pending-owner-artifacts)
8. [Glossary](#8-glossary)
9. [Consistency notes — rulings from the 2026-06-21 reconciliation pass](#9-consistency-notes--rulings-from-the-2026-06-21-reconciliation-pass)

---

## 1. Project overview & architecture inversion

**Goal (framework.md §1):** Replace CiviCRM. Make Seraphim the **system of record** — a *face-recognition-native CRM* for Light North Caloocan church. Migrate ~1,440 contacts + ~33.6k attendance rows. Stay on the current stack (FastAPI + Postgres + React + CompreFace + Unraid/Cloudflare).

**The inversion (what fundamentally changes):**

| Today | After (Seraphim) |
|---|---|
| CiviCRM is source of truth; Seraphim mirrors members/events IN, pushes attendance BACK | **Seraphim mints IDs and owns the truth; CiviCRM deleted** |
| PKs `contact_id`/`event_id` are CiviCRM's own ids | App-minted identity PKs + nullable UNIQUE `external_id` (old CiviCRM id, trace only) |
| `Attendance.push_status` retry/dead-letter worker pushes to CiviCRM | **Push pipeline deleted.** Its outbox/retry *pattern* is generalized into a reusable `outbox` (Google Chat / Zoom / Gmail) |
| Members/events seeded externally; nothing enrolls a face↔contact | First-class **face enrollment** owned by the CRM (the headline net-new gap) |
| Custom data lives in CiviCRM custom groups | **Native dynamic custom-field engine** (groups + types + multi-select + contact-reference) |
| Automation in n8n; notifications to Mattermost | **Internal visual rules engine + outbox + scheduled jobs + clean API**; notifications consolidate to **Google Chat (one channel) + Gmail** |

**Deleted wholesale (S01):** `services/civicrm.py` (`CiviCRMClient`), `_process_civicrm_push`, `push_status/push_attempts/last_push_error`, `/attendance/push`, `/push-preview`, `/dead-letter*`, `/members/sync`, `/events/sync`, CiviCRM keys in `config.py`/`setup.py`/`schemas.py`, and all CiviCRM frontend surfaces.

**Reused as-is:** CompreFace client, quality gate, dedup, face storage, RTSP capture, task/PIT lifecycle, the worker host (3 of 4 jobs), auth/SSE/rate-limit/storage infra, gamification (leaderboard/volunteer_stats).

**The seven hard problems (framework.md §3):** (1) bulk participants without downtime, (2) export at scale (>20k), (3) migration linkage by real Contact ID + Event ID, (4) face↔contact enrollment (net-new, #1 priority), (5) dedupe/merge correctness, (6) safe declarative rules engine, (7) biometric governance (consent + retention + RTBF).

---

## 2. Canonical target data model (authoritative)

This is the consolidated, deduplicated model. **Each table is owned by exactly one sprint** (the "owner" column); other sprints **consume** it and must not redefine it. JSON columns use `JSON().with_variant(JSONB, "postgresql")` (the `JSONB` alias at `models.py:22`). All timestamps are **naive UTC** (`utc_now()`).

### 2.1 Core entities

**`contacts`** — owner **S01** (incl. all core columns, `custom_data`, **and the derived snapshot columns** — ruling C2/C7).
- Identity: `id` (app-minted identity PK), `external_id` (Integer, nullable, **UNIQUE** — old CiviCRM Contact ID).
- Type: `contact_type` (`individual|household|organization`, default `individual`), `contact_subtype` (nullable; canonical subtype vocab below).
- Core: `first_name`, `last_name`, `nickname`, `suffix`, `gender`, `birth_date` (Date), `phone`, `email`, `street_address`.
- Dynamic: `custom_data` JSONB (`{}` default) — all S02 custom-field values keyed by field `name`.
- Soft-delete + audit: `is_deleted` (bool, default false), `created_at`, `updated_at`.
- **Derived snapshot columns (owner S01 schema, written only by S23 nightly job):** `last_attended_at` (DateTime), `attendance_count` (Int), `weeks_absent` (Int), `tier` (`tier0|tier1|tier2|tier3|inactive`), `is_active` (bool), `is_regular` (bool), `is_connected` (bool). **All nullable; never written by S03.**
- Indexes: `UNIQUE(external_id)`, `ix_contacts_last_first(last_name,first_name)`, `ix_contacts_is_deleted`, `ix_contacts_subtype (partial WHERE is_deleted=false)` (S14), `ix_contacts_created_at` (S14), `ix_contacts_custom_data_gin` (S14, Postgres-only GIN).

> **Canonical `contact_subtype` vocabulary** (ruling C9): `New Friend`, `Regular Attendee`, `Regular Member`, `Volunteer`, `Student`, `Parent`, `Staff`; org subtypes `Team`, `Sponsor`. This is admin-set. It is **distinct** from the derived `is_regular` flag (ruling C10).

**`events`** — owner **S01** (core) + **S04** (`recurring_series_id`, session columns).
- `id` (app-minted), `external_id` (nullable UNIQUE), `title`, `event_type` (canonical vocab below), `start_at`, `end_at` (nullable), `location`, `recurring_series_id` (FK→`event_series.id` ON DELETE SET NULL, **added in S04** — not S01), `is_active` (bool), `created_at`.
- **Session column (owner S04, required by S14/S23 reporting):** `session_time` `String(20)` nullable (`8AM|10AM|3PM`; NULL for non-Sunday). **(2026-06-22 ruling)** This is the SINGLE canonical column — there is no `session_type` (deprecated, CN-15) and no `service_time` (duplicate, removed). S14's reporting taxonomy derives buckets from `(event_type, session_time)`.
- Indexes: `ix_events_start_at`, `ix_events_event_type`, `ix_events_recurring_series_id`, `ix_events_type_start` (S14).

> **Canonical `event_type` vocabulary:** `Sunday Celebration`, `Prayer Meeting`, `Powerhouse`, `Community Meeting`, `Conference`, `Event` (6 values, per CN-05). Reporting session taxonomy (Morning Prayer / Powerhouse / Community / Sunday 8AM·10AM·3PM) is derived from `(event_type, session_time)` — Sunday buckets split on `session_time`, non-Sunday buckets key on `event_type` alone (2026-06-22 ruling; supersedes the old `session_type`/`service_time` framing).

**`event_series`** — owner **S04**. `id`, `title`, `event_type`, `cadence` JSONB (`{freq,interval,weekdays,monthday,start_time,duration_minutes}`), `default_location`, `is_active`, `created_at`. Spawns dated `events` occurrences; `monthday` capped at 28.

**`participants`** (attendance) — owner **S01**. `id`, `contact_id` (FK→contacts ON DELETE CASCADE), `event_id` (FK→events ON DELETE CASCADE), `status` (`attended|registered|no_show|cancelled`), `role`, `source` (`face|manual|import|bulk|zoom|community_report` — ruling C12), `detection_id` (FK→detections ON DELETE SET NULL, nullable), `registered_by_id` (FK→users ON DELETE SET NULL, nullable), `created_at`. **UNIQUE(event_id, contact_id)** = `uq_participants_event_contact`. Indexes: `ix_participants_event_id`, `ix_participants_contact_id`, `ix_participants_status_event` (S14).

### 2.2 Dynamic custom fields — owner **S02**

**`custom_field_group`** — `id`, `name` (snake_case), `label`, `entity` (`contact|event|activity` — ruling C8: **lowercase**), `weight`, `is_active`, `created_at`, `updated_at`. UNIQUE(entity,name).

**`custom_field_def`** — `id`, `group_id` (FK CASCADE), `name` (snake_case, **unique per entity** — storage is keyed by bare name), `label`, `data_type` (`text|textarea|select|multiselect|date|number|checkbox|contact_reference`), `options` JSONB, `is_required`, `is_multi`, `weight`, `is_active`, `help_text`, timestamps. UNIQUE(group_id,name).

> **`contact_reference` stored shape (ruling C13):** single = JSON **integer** (app-minted contact id); multi (`is_multi`) = JSON **list[int]**. S06 link-loader, S13 coercer, S09 compiler, and S14 leader-rollups all rely on this. Store as JSON **number**, not string.

### 2.3 Face recognition — owner **S07** (+ **S08** consent linkage)

**`compreface_subjects`** (existing; S01 repoints `contact_id`→`contacts.id`) — gains (S07): `enrollment_source`, `last_trained_at`, `is_orphan`, `purged_at`; gains (S08): `consent_id` (FK→biometric_consent ON DELETE SET NULL). Existing `enrollment_status` enum extended to `pending|active|purged`. **Co-owned table — ruling C5: S07 owns enrollment columns, S08 owns `consent_id`; one migration each, sequenced S07→S08.**

**`face_samples`** — owner **S07**. `id`, `contact_id` (FK CASCADE), `subject_id` (FK CASCADE), `image_path`, `thumb_path`, `source` (`manual|detection|bulk_ingest|backfill`), `quality`, `detection_id` (FK SET NULL), `compreface_image_id`, `added_by_id` (FK SET NULL), `created_at`.

**`photo_ingest_batches`** — owner **S07**. Bulk-upload bookkeeping (status/counters/report JSONB).

**`biometric_consent`** — owner **S08**. 1:1 with contacts (`contact_id` UNIQUE). `consent_given`, `consented_at`, `recorded_by_id`, `basis_note`, `retention_until`, `deletion_requested_at`, `deletion_requested_by_id`, `purged_at`, `purge_detail` JSONB, timestamps.

### 2.4 CRM power features

**`saved_searches`** — owner **S09**. `id`, `name`, `owner_id` (FK CASCADE), `entity` (default `contact`), `criteria` JSONB, timestamps.

**`groups`** — owner **S09**. `id`, `name`, `type` (`static|smart`), `entity`, `criteria` JSONB (smart only), `description`, `created_by_id`, timestamps. UNIQUE(name,entity).

**`group_members`** — owner **S09**. Composite PK `(group_id, contact_id)`, `added_by_id`, `added_at`. **`contact_id` is in the S11 FK-reassignment manifest (ruling C4).**

**`profiles`** — owner **S13**. `id`, `name`, `entity`, `slug` (UNIQUE), `fields` JSONB (form schema), `settings` JSONB, `is_public`, `is_active`, `owner_id`, timestamps. Drives templated create + the public newcomer form.

**`activities`** — owner **S12** (the assignable-task entity; **distinct from the face `tasks` table** — ruling C14). `id`, `activity_type`, `subject`, `details`, `activity_date`, `due_date`, `status` (`scheduled|in_progress|completed|cancelled`), `priority` (`low|normal|high|urgent`), `assignee_user_id` (FK SET NULL), `target_contact_id` (FK→contacts CASCADE, nullable), `created_by_id`, `completed_at`, `reminder_sent_at`, `created_at`, `updated_at`. **`target_contact_id` is in the S11 FK manifest.**

**`dedupe_rule_set`** — owner **S11**. `id`, `name` (UNIQUE), `description`, `rules` JSONB (`[{field,length,weight}]`), `threshold`, `is_default`, `is_active`, timestamps, `updated_by_id`. Seeded "Default".

### 2.5 Migration / import staging

**`import_batch`** — owner **S06** (S10 reuses + adds 2 columns). `id`, `source_filename`, `entity` (`contacts|events|participants|links`), `mode` (`dry_run|live` + S10's `wizard_preview|wizard_run`), `status`, `column_map` JSONB, `options` JSONB, count columns, `started_at`, `finished_at`, `created_by_id`, **`staging_file` + `expires_at` (added by S10)**.

**`import_row_result`** — owner **S06**. `id`, `batch_id` (FK CASCADE), `row_number`, `external_id`, `outcome` (`created|updated|skipped|error|review`), `entity_id`, `message`, `raw` JSONB, `created_at`.

**`migration_link_review`** — owner **S06**. People-link review queue: `id`, `batch_id` (FK CASCADE), `source_contact_external_id`, `target_contact_id` (FK SET NULL), `field_name`, `raw_name`, `candidates` JSONB, `reason` (`ambiguous|unmatched`), `status`, `resolved_by_id`, `resolved_at`, `created_at`. UNIQUE(batch_id, source_contact_external_id, field_name).

**`import_mapping_preset`** — owner **S10**. Saveable header→target mapping presets.

### 2.6 Name-matching, community reports, attendance intake — owner S22

**`name_alias`** — owner **S22**. The learned "common names" alias dictionary (replaces n8n's Google-Sheet list). `id`, `alias_text` (String(255), normalized lowercase, UNIQUE), `contact_id` (FK→contacts ON DELETE CASCADE, in S11 FK manifest), `source` (`learned|manual`), `created_by_id` (FK→users SET NULL), `created_at`. Index: `ix_name_alias_contact_id`.

**`name_match_review_queue`** — owner **S22**. Replaces "contact ID 1 if unsure." Columns: `id`, `source` (String(40): `community_report|zoom|name_list|migration|newcomer`), `raw_name`, `normalized_name`, `raw_payload` JSONB (includes `batch_id`, `field_name`, `source_contact_id` for S06 queries), `event_id` (FK→events SET NULL), `community_report_id` (FK→community_report SET NULL), `candidate_contact_ids` JSONB, `match_stage_reached` (`fuzzy|alias|claude|none`), `status` (`pending|matched|unmatched|skipped`), `resolved_contact_id` (FK→contacts SET NULL — in S11 FK manifest), `resolved_by_id` (FK→users SET NULL), `resolved_at`, `created_at`. Indexes: `ix_nmrq_status`, `ix_nmrq_event_id`, `ix_nmrq_community_report_id`, `ix_nmrq_resolved_contact_id`.

**`community_report`** — owner **S22**. Leader-submitted attendee-name-list reports (event + names + topics/prayer/remarks + photo paths). `id`, `event_id` (FK→events SET NULL), `report_date` (Date), `community_zone` (String(100), one of 32 labels), `attendees_raw` (Text), `topics_discussed`, `prayer_items`, `remarks`, `photo_paths` JSONB, `submitted_by_contact_id` (FK→contacts SET NULL — in S11 FK manifest), `event_leader_contact_id` (FK→contacts SET NULL — in S11 FK manifest), `status` (`pending|processing|processed|archived`), `processed_at`, `created_at`, `updated_at`.

**NameMatch public API (S22; locked signature — consumed by S06/S10/S13/S18):**
```python
async def match_name(raw_name: str, event_id: int | None = None, source: str = "manual") -> MatchResult
async def match_name_batch(names: list[str], event_id: int | None = None, source: str = "manual") -> list[MatchResult]
# MatchResult.outcome ∈ SINGLE | AMBIGUOUS | UNMATCHED
# MatchResult.contact_id set when SINGLE
```

Migration table creation order (CN-22): `name_alias` → `community_report` → `name_match_review_queue`.

### 2.7 Automation, ops, audit, users

**`audit_log`** — **owner S01** (ruling CN-03). `id`, `actor_id` (FK→users SET NULL; NULL for system/API-key actors), `action` (String(100), e.g. `contact.create`, `contact_merge`, `import.run`), `entity` (String(100)), `entity_id` (Integer), `before` JSONB, `after` JSONB, `created_at`. Indexes: `(entity, entity_id)`, `(created_at)`. Write helper: `app/services/audit.py::record(...)` created by **S02** (consumed everywhere; never forked).

**`outbox`** — **owner S17** (S12/S13 use capability-guard stubs until S17 lands). Full schema: `id`, `event_type` (String(100)), `payload` JSONB, `status` (`pending|processing|sent|failed|dead_letter`), `attempts` (int, default 0), `last_error` (Text), `run_at` (DateTime, default utc_now), `idempotency_key` (String(255), UNIQUE partial WHERE NOT NULL), `rule_id` (FK→automation_rules SET NULL), `created_at`. Indexes: `ix_outbox_status_run_at` on `(status, run_at)`, `ix_outbox_rule_id`.

**`automation_rules`** — owner **S17**. `id`, `name` (UNIQUE), `description`, `trigger` (String(100), whitelisted), `conditions` JSONB, `actions` JSONB, `delay_minutes` (int, default 0), `rate_cap_per_entity_minutes` (int, default 0), `max_entities_per_hour` (int, default 0), `is_active` (bool, default True), `dry_run` (bool, default False), `created_by_id` / `updated_by_id` (FK→users SET NULL), timestamps. Index: `ix_automation_rules_trigger_active` on `(trigger, is_active)`.

**`rule_executions`** — owner **S17**. `id`, `rule_id` (FK→automation_rules CASCADE), `entity_id`, `trigger_payload` JSONB, `status` (`fired|skipped_rate_cap|skipped_conditions|dry_run|circuit_broken`), `dry_run_log` JSONB, `actions_taken` JSONB, `outbox_ids` JSONB, `created_at`. Indexes: `(rule_id, entity_id, created_at)`, `(rule_id, created_at)`.

**`job_runs`** — owner **S16**. `id`, `job_name` (String(100)), `started_at`, `finished_at`, `status` (`running|completed|failed|skipped`), `duration_ms`, `detail` JSONB.

**`export_jobs`** — owner **S05**. `id`, `job_type` (`contacts|participants`), `fmt` (`csv|xlsx`), `params` JSONB, `status` (`pending|running|ready|failed|expired`), `requested_by_id` (FK→users SET NULL), `row_count`, `file_path`, `file_bytes`, `error`, `expires_at`, `created_at`.

**`api_keys`** — owner **S19**. `id`, `name`, `key_hash` (SHA-256 hex, UNIQUE), `prefix` (first 8 chars, display only), `scopes` JSONB, `owner_user_id` (FK→users CASCADE), `is_active`, `last_used_at`, `expires_at`, `revoked_at`, `created_at`.

**`webhook_secrets`** — owner **S19**. `id`, `source` (String(100), UNIQUE slug), `secret_hash`, `is_active`, `created_at`, `updated_at`.

**`users`** — existing; **S15** adds `CheckConstraint("role IN ('admin','volunteer','viewer')", name="ck_users_role")` via `batch_alter_table`.

---

## 3. Phase + 23-sprint plan

| ID | Title | Phase | Depends on | Effort | One-line goal |
|---|---|---|---|---|---|
| **S01** | Schema Inversion & CiviCRM Excision | A Foundation | — | L | App-mint ids + `external_id`; rename to Contact/Event/Participant; delete all CiviCRM coupling; create `audit_log`. |
| **S02** | Dynamic Custom-Field Engine | A Foundation | S01 | L | Runtime custom-field groups/defs (8 types incl. multiselect + contact_reference) + JSONB storage + `validate_and_coerce` + admin UI. |
| **S03** | Native Contact CRUD & Detail Profile | B Core | S01, S02 | L | First native contact write path: list/create/edit/soft-delete + detail page with slots; reusable `FormField`/`DataTable`/`Pagination`. |
| **S04** | Native Event CRUD, Sessions & Series | B Core | S01 (S02 soft) | L | Event CRUD + owns the `event_series` table in full + `session_time` (8AM/10AM/3PM) + participant grid. |
| **S05** | Bulk Participants & Export-at-Scale | B Core | S03, S04 | L | Set-based `ON CONFLICT` bulk add/status + streaming CSV/XLSX export + async `export_jobs`. |
| **S06** | Data Migration ETL | B Core | S02, S03, S04, S05, S22 | XL | CLI importer of CiviCRM XLSX re-exports (anchor on Contact ID + Event ID), people-link resolution via S22. |
| **S07** | Face Enrollment & Bulk Photo Ingestion | C Face-native (PRIORITY) | S01, S03, S04 | XL | First-class enroll endpoint + bulk photo ingest → detection→PIT→confirm→auto-enroll + profile face panel. |
| **S08** | Biometric Consent & Right-to-be-Forgotten | C Face-native | S01, S07, S03 | L | Per-contact consent + retention + RTBF purge (CompreFace + crops) + audit. |
| **S09** | Advanced Search, Saved Searches & Smart Groups | D Power | S02, S03 | L | Whitelisted criteria compiler → `saved_searches` + static/smart `groups`; the canonical contact-set resolver. |
| **S10** | CSV/XLSX Import Wizard | D Power | S02, S03, S04, S05, S06 | L | Browser 4-step import (upload→map→dedupe-preview→batched run) reusing S06 ETL core. |
| **S11** | Find & Merge Duplicates | D Power | S03 (+ all contact-FK owners) | L | Dedupe rules + candidate finder + side-by-side merge + transactional loser→survivor FK reassignment + audit. |
| **S12** | Activities (Assignable Tasks) | D Power | S03 | M | `activities` entity (typed, assignable, dated) + profile panel + "My Tasks" + due-reminder outbox producer. |
| **S13** | Profiles & Public Newcomer Form | D Power | S02, S03 | L | `profiles` form-schema engine + templated create + public anti-spam newcomer intake. |
| **S14** | Reporting & Analytics Dashboard | E Insight | S02 (soft), S03, S04, S05 (+ S23 for derived) | L | Purpose-built aggregate endpoints (trends/segment/funnel/leader-rollup) + Recharts tiles; full report suite per reports.md. |
| **S15** | RBAC: admin / volunteer / viewer | E Insight | S03 | M | Add `viewer` read-only role end-to-end; `require_viewer`/`require_role`; nav + route gating. |
| **S16** | Settings, System Status & Scheduled Jobs | E Insight | S01 | M | System-status page + config checklist + **APScheduler host + `job_runs`** (the cron brain replacing n8n scheduler). |
| **S17** | Automation Core: Outbox + Visual Rules Engine | F Automation | S16 | XL | Generalized `outbox` + declarative ECA `automation_rules` engine + admin UI; dry-run + rate caps. |
| **S18** | Integrations: Google Chat, Zoom & Gmail | F Automation | S16, S17, S22 | L | Google Chat (single channel) + Gmail notifiers; Zoom S2S OAuth attendance pull; wire the 4 n8n flows. |
| **S19** | Clean Public/Internal API | F Automation | S15, S17 | M | Documented REST API + API keys / HMAC webhooks (extends `routers/public.py` from S13). |
| **S20** | Rebrand & Desktop-Responsive Pass | G Polish | (most UI sprints) | M | Product name/manifest; responsive desktop for data-dense views (mobile-first preserved). |
| **S21** | Cutover & CiviCRM Decommission | G Polish | S06 (+ all) | M | Freeze CiviCRM, final migration run, parallel verification, go-live, decommission. |
| **S22** | Attendance Intake & AI Name-Matching | C Face-native (priority) | S03, S04 | XL | Name-list attendance intake + `community_report` entity + 4-stage matching service (alias→fuzzy→Claude→review queue). |
| **S23** | Member Status & Engagement Engine | C Face-native (priority) | S01, S03, S04, S05, S16 | M | Nightly APScheduler recompute of 7 derived snapshot columns (tier/active/regular/connected) + on-demand endpoint. |
| **S24** | FR Transition & Cutover Bridge | G Polish (cutover) | S06, S07, S08 | M | Remap existing face↔contact links via `external_id` (CompreFace instance persists); own the Attendance→Participant code cutover + tests; redefine leaderboard scoring; consent backfill (soft-gate); cutover verification gating S21. |

> **Effort key:** M ≈ 1 sprint, L ≈ 1–1.5, XL ≈ 2. All sprints are contract-first (schema → backend → frontend → tests) and "one-shot-able."

---

## 4. Build-order dependency graph

Text form (→ = "must land before"). Phases A→G are the primary axis. S22/S23 were added to the original 21-sprint plan and are now priority-scheduled in Phase C alongside S07/S08.

```
Phase A:  S01 ──► S02
                   │
Phase B:           ├──► S03 ──► S04 ──► S05
                   │      │       │       │
Phase C:           │      └───────┴───────┼──► S22 ──► S06 (S06 must follow S22)
                   │      └──────►S07 ──► S08          (S07 also needs S01)
                   │              │
                   │              └──► S23  (needs S04 + S05 + S16)
Phase D:   S02,S03 ───────────────────────────► S09
           S05,S06,S22 ────────────────────────► S10  (S22 for participant-name path)
           S03 (+all FK owners) ────────────────► S11
           S03 ────────────────────────────────► S12
           S02,S03,S22 ──────────────────────── ► S13 (S22 runtime dep for name matching)
Phase E:   S03,S04,S05,S22,S23 ────────────────► S14
           (any point) ─────────────────────────► S15
           S01,S04,S23 ─────────────────────────► S16 (S16 hosts S23 scheduler jobs)
Phase F:   S16 ──► S17 ──► S18  (S18 also needs S22)
           S15,S17 ──► S19
Phase G:   (S03,S04,S14) ──► S20
           S06,S07,S08 ──► S24 ──► S21   (FR transition runs after migration+enrollment, gates go-live)
           (all sprints) ──► S21
```

**Critical path for cutover:** `S01 → S02 → S03 → S04 → S05 → S22 → S06 → S24 → S21`.
**Face-native priority chain:** `S01 → S03/S04 → S07 → S08`.
**Shared-table creation order (binding):** `audit_log` created in **S01** (before S02/S03). `outbox` created in **S17** (S12/S13 use guard stubs). `job_runs` created in **S16** (S05/S08/S12/S23 use `has_table` guards). S22 tables land before S10's participant-name path and before S18.

---

## 5. Global conventions (binding)

**Backend**
- `async`/`await` for all I/O-bound code; type-hint every public function.
- No `/api` prefix in FastAPI routers (nginx strips it). Domain routers register with `dependencies=[Depends(check_setup_complete)]`; `setup`/`auth`/`public` register **before** that gate and outside auth.
- One Alembic migration per schema change; **never edit an applied migration**; `down_revision` = the real `alembic heads` at implementation time (do not invent ids). CI runs `alembic upgrade head` clean + **idempotent** on Postgres, and `create_all` on SQLite — so model `__table_args__` must mirror migration constraints/indexes.
- Dual-dialect: JSON columns use `JSON().with_variant(JSONB,"postgresql")`; partial indexes / CHECK / `ON CONFLICT` / GIN must branch on `db.bind.dialect.name`; SQLite is the test path, Postgres is prod.
- Timestamps are **naive UTC** (`utc_now()`). FK `ondelete` declared explicitly. List endpoints always paginate + clamp (`page_size ≤ 100`/`200`).
- Mock external services (CompreFace, SMTP, Zoom, Google Chat) in tests.

**Frontend**
- React 18 + TS + Vite + Tailwind + TanStack Query + Zustand + sonner.
- **Design tokens only** (`bg-card`, `text-foreground`, `bg-background`, `bg-primary`, `border-border`) — never hardcoded hex; must work under `.dark`. Chart fills come from `lib/chartColors.ts`.
- Access token **in-memory** (Zustand); refresh token via HttpOnly cookie.
- Toasts via `sonner`, always surfacing `err.response?.data?.detail`. Destructive actions use `ConfirmDialog`, never `confirm()`. Loading/empty/error use `StateViews`.
- **Shared primitives:** `FormField`/`DataTable`/`Pagination`/`StatusBadge` (owner **S03**); `CustomFieldRenderer`/`CustomFieldsSection` (owner **S02**); `ProfileFormRenderer` (owner **S13**). Later sprints **reuse, do not re-create** these.
- All API calls go through `services/api.ts` **except** the two public newcomer calls (`fetch('/api/public/...')`, no bearer/refresh interceptor).

**Roles** (S15): `admin` (configure everything), `volunteer` (edit contacts/events/activities), `viewer` (reports/dashboard only — **no individual contact records**). Pre-S15, specs ship a forward-compatible `require_viewer`/`require_reporting` shim. Assignees/actors are **system users only**, never contacts.

**Reuse contracts (single home for shared logic):**

| Symbol / module | Owner sprint | Consumers |
|---|---|---|
| `audit_log` table + `AuditLog` model | S01 | S02/S03/S05/S06/S08/S11/S12/S13/S17/S22/S23 |
| `app/services/audit.py::record(...)` | S02 | all above consumers |
| `CustomFieldRenderer`, `CustomFieldsSection` | S02 | S03, S06, S10, S13 |
| `validate_and_coerce(db, entity, raw)` | S02 | S03, S06, S10, S13 |
| `FormField`, `DataTable`, `Pagination`, `StatusBadge` | S03 | S04, S05, S09, S10, S11, S12 |
| `ContactPickerModal` (renamed from `MemberSearchModal`) | S03 | S04, S05, S09 |
| `services/event_generator.py` (`generate_sunday_events`, `generate_powerhouse_event`) | S04 | S16 |
| `bulk_upsert_participants(session, rows)` | S05 | S06 |
| `services/audience.py` (`resolve_audience`, `count_audience`) | S05 | S06, S09, S10, S14 |
| ETL core (`scripts/reader.py`, `normalize.py`, `mapper.py`, `loader.py`) | S06 | S10 |
| `EnrollmentService` incl. `reassign_subject_contact` | S07 | S11 |
| `_delete_image_files` / `remove_sample` | S07 | S08 |
| `services/search_service.py` (`resolve_group_contacts`, `build_contact_query`) | S09 | S12, S14, S17, S18 |
| `services/name_match.py` (`match_name`, `match_name_batch`, `teach_alias_from_resolution`) | S22 | S06, S10, S13, S18 |
| `services/member_status_service.py` (`recompute_all_contacts`, `recompute_contacts`) | S23 | S11 (post-merge), S06/S10 (post-import), S16 (scheduler) |
| `services/scheduler.py` (`start_scheduler`, `register_jobs`) | S16 | S17, S18, S23 |
| `automation_engine.emit(event_type, payload, db)` | S17 | S12, S13, S18 |
| `require_viewer`, `require_reporting` shims | S05/S12/S14 (shims) → S15 (canonical) | S14, S16, S19 |
| `require_role(*allowed)` factory | S15 | S16, S19 |

**Cron cadences (canonical — from n8n `fKkPUolayRyZrjao`; owner S16):**

| Job name | Cron (UTC) | Business logic owner |
|---|---|---|
| `job_sunday_generation` | `0 18 * * 5` | S04 event_generator |
| `job_powerhouse_generation` | `0 8 * * 3` | S04 event_generator |
| `job_eow_recompute` | `0 0 * * 1` | S23 member_status_service |
| `job_eom_recompute` | `0 0 1 * *` | S23 member_status_service |
| `job_attendance_notifier_8am` | `30 9 * * 0` | S18 notification_service |
| `job_attendance_notifier_10am` | `0 12 * * 0` | S18 notification_service |
| `job_attendance_notifier_3pm` | `0 17 * * 0` | S18 notification_service |
| `job_attendance_notifier_powerhouse` | `0 21 * * 3` | S18 notification_service |
| `job_biometric_retention` | `0 2 * * *` | S08 BiometricPurgeService |
| alias re-scan (REST call) | `0 0 * * 1` (same as EOW) | S22 `POST /name-match/review-queue/reprocess-aliases` |
| Zoom pull | `0 15 * * *` (≈23:00 PHT) | S18 ZoomClient |

**n8n workflow → sprint mapping:**

| n8n workflow | Replacement |
|---|---|
| `jdVHzcMWXdANwG8R` New Friend V2 | S13 (form) + S22 (matching) + S18 (notify) |
| `3yaS8JZfct8BVe8Z` Gforms AI V2 (community reports) | S22 (intake + matching) + S18 (notify) |
| `fKkPUolayRyZrjao` CiviCRM Scheduler/Updater | S04 (event gen) + S16 (scheduler) + S23 (recompute) + S18 (notifiers) + S22 (alias re-scan) |
| `sJ7EgCZkk9wKSj3D` Morning Prayer Zoom | S18 (Zoom pull) + S22 (matching) + S04 (prayer event) |
| `pz7sHlUbU6jV1Hqm` Create Schedule | S04 (event CRUD) + S19 (webhook) |

Finance branches of `3yaS8JZfct8BVe8Z` (Liquidation/BRF) are **out of scope** for v1.

---

## 6. Pending owner artifacts checklist

These do **not** block spec-writing but block specific implementations / the live cutover:

1. **Fresh CiviCRM XLSX re-exports** with real **Contact ID + Event ID** (+ email + all custom fields where available). — Blocks **S06 live run / S21**. (#1 hard dependency.)
2. **Authoritative custom-field list + option values** (Barangay, Ministry, Community, PEPSOL stages, Followup, Church Info, the 6 people-link fields). — Blocks **S02 seed completeness / S06 normalization maps**.
3. **n8n workflow JSON exports (×4 in-scope)** — already partially pulled (`docs/crm-research/n8n/`); confirm field mappings. — Refines **S13/S18/S22**.
4. **Reports screenshot** (current Google-Sheet dashboard). — Finalizes **S14** (the four families are confirmed; screenshot may add KPI number-tiles).
5. **Google Chat incoming webhook URL (single channel)** + **Zoom S2S OAuth creds** + **Gmail send creds**. — Blocks **S18**.
6. **Product name** (working name "Seraphim"). — Blocks **S20** rebrand.
7. **Policy confirmations:** biometric retention years (default 7), enroll-without-consent gate (default non-blocking), viewer CSV-export + report-PII policy, public-form launch-day + CAPTCHA, historical participant status default (`attended`).

---

## 7. Glossary

- **Contact** — a person or org in the CRM (`contacts`). App-minted `id`; `external_id` traces the old CiviCRM Contact ID. Replaces CiviCRM's contact + the old `CiviCRMMember`.
- **Event** — one distinct occurrence (`events`). App-minted `id`; one event = one occurrence even if titles repeat (migration anchors on real Event ID, never title).
- **Participant** — one attendance row (`participants`), `UNIQUE(event_id, contact_id)` = one attendance per person per event. 4+ sources: `face`, `manual`, `zoom`, `community_report` (plus `import`/`bulk` for loads).
- **Community Report** — a leader-submitted **attendee NAME LIST** (not photos/counts) with topics/prayer/remarks/docs photos (`community_report`, S22). Feeds attendance + the S14 "Reports Attendance" lines. Replaces n8n "Gforms AI V2".
- **name_alias** — the learned "common names" dictionary (S22) that recovers previously-unmatched names (n8n's AI-Agent1 alias list).
- **Review queue** — `name_match_review_queue` (S22, name-list matching) and `migration_link_review` (S06, people-link resolution). Ambiguous/unmatched items wait for a human (the n8n "ID 1 if unsure" behavior, done right).
- **Tier / Active** — derived from weeks since last attendance (S23): Tier0=0wk, Tier1=1–4wk, Tier2=5–8wk, Tier3=9–12wk, Inactive=13+wk (CN-08: 13+ not 12+); Active=Tier0–3 (attended within last 12 complete weeks).
- **Regular Attendee (derived `is_regular`)** — lifetime attendance count **> 9** (S23). **Distinct** from the admin-set `contact_subtype="Regular Attendee"`.
- **Connected (`is_connected`)** — has a Community/CG assignment (S23).
- **PEPSOL** — the discipleship pathway (Encounter Graduate, Prepare to Serve, SOL 1/2/3, Graduate), stored as a custom field; reported in S14.
- **Outbox** — the at-least-once delivery queue (`outbox`, S17), generalizing CiviCRM's deleted push retry pattern; drains to Google Chat / Zoom / Gmail.
- **PIT** — Pending-Identity-Task: the existing face-review queue where unknown/low-confidence detections wait for a volunteer to confirm identity → then auto-enroll (S07).
- **Task vs Activity** — **Task** = the existing face-detection-review entity (`tasks`/`Task`); **Activity** = the new CRM assignable follow-up entity (`activities`/`Activity`). Unrelated; never share code. UI labels Activities as "Tasks" but every symbol is `activity`.
- **Smart group** — a `groups` row with `type='smart'`: a stored criteria tree resolved **live**, never materialized. Static group = a frozen `group_members` snapshot.
- **Profile** — a `profiles` row = a JSON form schema for templated contact creation; the public-flagged one is the newcomer form.

---

## 9. Consistency notes — rulings from the 2026-06-21 reconciliation pass

All sprint specs are now on disk. The issues below were found by comparing every S01–S23 spec against each other and against the research docs. Each ruling supersedes the individual spec.

---

### CN-01 — `nickname` column missing from S01 contacts schema

**Conflict:** S01 §3.1 `contacts` table does NOT include `nickname`. S03 `ContactCore` searches across `first/last/nickname/email` and `ContactListItem` includes `nickname`. S03 creates a thin migration guard for snapshot columns — but not for `nickname`.

**Ruling CN-01:** Add `nickname String(255) nullable` to `contacts` in **S01**'s migration alongside the other core columns. S03 does not add a separate migration for this field. If S01 has already been applied, S03's guard migration adds it idempotently.

---

### CN-02 — Derived snapshot column ownership (CONFIRMED RESOLVED)

**Ruling CN-02 (reconfirmed):** The seven derived snapshot columns (`last_attended_at`, `attendance_count`, `weeks_absent`, `tier`, `is_active`, `is_regular`, `is_connected`) are **created by S01** (all nullable, no defaults, no backfill). S23 is their sole runtime writer. S03 only reads them (with a guard migration as safety net). S23's guard migration is a no-op if S01 included them. This is the binding single-writer contract.

---

### CN-03 — `audit_log` table + `audit.py` service: single ownership

**Conflict:** Prior 00-MASTER (pre-rerun) listed S01 as audit_log owner. S11's spec says "`audit_log` is created by S01." S02 creates `app/services/audit.py::record(...)`. S08 says "if S01 does not create it, S08 must." S12 has an idempotent `has_table('audit_log')` guard.

**Ruling CN-03:** `audit_log` table + `AuditLog` SQLAlchemy model = **S01** (in the `g7h8i9j0k1l2` migration, created before S02 needs it). `app/services/audit.py` with `record(...)` = **S02** (the table is pre-created; S02 adds the helper module). S12 MUST NOT create a separate `audit_service.py` or `AuditLog` model — it imports from `app.services.audit`. All `has_table` guards in S05/S12/S13 become no-ops after S01 lands; they are kept as defensive code only.

---

### CN-04 — S11 FK-manifest must cover all contact FKs including S22 tables

**Ruling CN-04:** S11's loser→survivor transactional reassignment must cover:
- `participants.contact_id`
- `compreface_subjects.contact_id`
- `face_samples.contact_id` (S07)
- `biometric_consent.contact_id` (S08)
- `group_members.contact_id` (S09; de-dup against composite PK)
- `activities.target_contact_id` (S12)
- `name_alias.contact_id` (S22)
- `name_match_review_queue.resolved_contact_id` (S22; nullable — only rewrite if set)
- `community_report.submitted_by_contact_id` (S22; nullable)
- `community_report.event_leader_contact_id` (S22; nullable)
- `contacts.custom_data` JSONB — scan for `contact_reference` values (integer or list[int]) and rewrite loser_id → survivor_id
- `biometric_consent` merge (S08 note): S11 must merge the loser's consent row into the survivor's — S08 §10 flags this as a required S11 patch.

S11 ships a `test_manifest_covers_all_contact_fks` reflection test that enumerates all FK columns via SQLAlchemy inspector and asserts coverage. Any sprint adding a new FK to `contacts` must update this manifest in the same PR.

---

### CN-05 — `event_type` vocabulary: "Powerhouse" missing from S01

**Conflict:** S01 §3.1 lists 5 `event_type` values: `Sunday Celebration | Prayer Meeting | Community Meeting | Conference | Event`. S04 §3.1 lists 6: adds "Powerhouse." The n8n `fKkPUolayRyZrjao` Powerhouse Generator confirms it as a distinct recurring session.

**Ruling CN-05:** Canonical `event_type` vocabulary = **6 values**: `Sunday Celebration | Prayer Meeting | Powerhouse | Community Meeting | Conference | Event`. S01 spec text must be updated to include "Powerhouse" in the `events.event_type` column comment. Since `event_type` is `String(100)` (not a DDL enum), no migration change is required — only the documentation.

---

### CN-06 — `start_at` vs `start_date` (RESOLVED — `start_at` is canonical)

S04 §3.2 confirms S01 uses `start_at`/`end_at`. S23's `MAX(events.start_at)` is correct. All specs already use `start_at`/`end_at`. No further action.

---

### CN-07 — `participants.source` full vocabulary: all 8 values must be documented in S01

**Conflict:** S01 lists 6 source values. S05 adds `bulk`. S06 adds `migration`. The canonical set is 8.

**Ruling CN-07:** S01's `participants` model comment must list all 8 canonical source values: `face | manual | zoom | name_list | community_report | import | bulk | migration`. Since `source` is `String(30)` (not a DB enum), no DDL change is needed. S01 implementer adds the comment; later sprints (S05, S06, S18, S22) add their respective values without a migration.

---

### CN-08 — Tier "inactive" boundary: 12+ weeks vs 13+ weeks

**Conflict:** `decisions.md` Round 7 says "Inactive = 12+ weeks." `reports.md §B` says Tier3 = 9–12 weeks. S23 §1 table says `inactive` = 13+.

**Ruling CN-08:** Adopt S23's algorithm as canonical. `weeks_absent` is an integer floor division. Tier3 = `weeks_absent` in {9,10,11,12}. Inactive = `weeks_absent >= 13`. Active = tier0–tier3 (attended in the last 12 complete weeks). The colloquial "12+ weeks" in decisions.md means "more than 12 complete weeks of absence" which = 13+ integer weeks. All sprint specs use the 13+ boundary.

---

### CN-09 — `outbox` schema missing `idempotency_key` and `rule_id` in prior 00-MASTER

The prior §2.7 `outbox` description lacked `idempotency_key` and `rule_id`. **These are now in the canonical model (§2.6 above).** S17 §3.1 is the authoritative schema definition and includes both columns.

---

### CN-10 — S12 outbox stub → S17 canonical outbox: ordering safe

S12 ships `has_table('outbox')` guards for its due-reminder outbox producer. S17 creates the canonical table. After S17 lands, S12's guard becomes a permanent no-op. S13 also ships a raw outbox INSERT stub — this must be patched post-S17 to call `automation_engine.emit()` (tracked as an S17-landing follow-up).

---

### CN-11 — S06 depends on S22 (ordering implication)

S06's `Depends on` includes S22 because `MatchService.resolve` is called during people-link resolution. S22 depends on S03/S04. The implementation order is: complete S22 before S06. The phase B label on S06 is aspirational grouping; the dependency graph (§4) is authoritative. S06 ships after S22 in practice.

---

### CN-12 — S16 job count typo: prose says "7 jobs", list has 9

S16 §2 intro says "7 jobs" but the numbered list has 9 entries. **9 is correct** (Sunday gen, Powerhouse gen, EOW recompute, EOM recompute, 4 notifiers, biometric retention). The "7" in the prose is a copy error. S16 implementer ignores the prose count and uses the numbered list.

---

### CN-13 — `contact_reference` stored shape: integer, not string

**Confirmed binding:** single `contact_reference` = JSON integer; multi = JSON list[int]. S09 GIN containment queries (`custom_data @> '{"field": <int>}'`), S14 leader-rollup fast-path, and S06 link-loader all depend on the numeric form. S02's `validate_and_coerce` must cast to `int` before storing.

---

### CN-14 — S13 newcomer-form seed field names must match S02 canonical names

S13's "New Friend" profile seed references `custom_field_def.name` values (`facebook_name`, `new_friend_add_date`, `invited_by`, `consolidated_by`, etc.). These must exactly match S02's seeded field names. **S02 seeds first; S13 consumes.** The owner's CiviCRM custom-field export (pending artifact §7 item 9) is the final arbiter. Until that export arrives, both sprints agree on the snake_case names listed in §2.2 of this document. S13 must NOT define its own field names independently.

---

### CN-15 — S14 `session_type` column does not exist; use `event_type` + `session_time`

**Conflict:** S14 references `session_type` as a reporting taxonomy key. No `session_type` column exists on `events`.

**Ruling CN-15:** S14 derives the reporting session bucket from `events.event_type` + `events.session_time` via a static Python mapping in `reporting.py`. No new column is needed. S14's `SESSION_CATEGORIES` constants map bucket names to `(event_type, session_time)` filter tuples. See also CN-16.

**Clarification (2026-06-22):** `session_time` (`String(20)`, values `8AM|10AM|3PM`, nullable, Sunday-only) is the SINGLE canonical column. There is no `service_time` column (it was a duplicate that briefly appeared in S14's draft and has been removed). Sunday buckets split on `session_time`; non-Sunday buckets (Morning Prayer, Powerhouse, Community, Events) key on `event_type` alone with `session_time = None` — Morning Prayer must NOT use an invented `session_time="morning"` token.

---

### CN-16 — S04 columns: S01 creates `events` without type/session cols; S04 adds them

**Ruling CN-16:** S01's `CREATE TABLE events` must NOT include `event_type`, `session_time`, `occurrence_date`, `recurring_series_id`, `is_active`, or `location`. S04's migration adds all six. S01 only creates the minimal core: `id`, `external_id`, `title`, `start_at`, `end_at`, `created_at`. S04 implementer: these additions are entirely S04's migration responsibility.

---

### CN-17 — `require_reporting` ownership: S14 creates stub; S15 absorbs it

S14 introduces `require_reporting` (admin+volunteer check). S15 ships `require_viewer` and `require_role` factory. **S15 must absorb and patch `require_reporting`** to allow viewer role using the new `require_role("admin", "volunteer", "viewer")` factory. S15 must NOT create a duplicate function. The shim S14 ships is forward-compatible: `require_reporting = require_volunteer` until S15 patches it.

---

### CN-18 — `analytics.py` legacy `push_status` and `CiviCRMEvent` imports: fixed in S01

S01's scope explicitly lists "Repoint `routers/analytics.py` from `CiviCRMEvent` to `Event`; remove `push_status` column from CSV exports." S05 and S14 may assume `analytics.py` is clean after S01. S01 implementer must not skip this file.

---

### CN-19 — S15 SSE handler: viewer must receive events but not face-recognition PII

S15 §10 flags the SSE handler in `main.py` lacks a viewer-aware filter. **Ruling CN-19:** Viewers receive SSE events of types `system.*` and `job.*` only. `detection.*` and `task.*` events (which may include contact/face data) are filtered out for viewer sessions. S15 implementer adds this filter in the SSE generator before yielding.

---

### CN-20 — EOM recompute cron: `0 0 1 * *` (NOT the disabled daily-noon node)

The n8n workflow has a disabled `Rescan EOM` node that would fire at `0 0 12 * *` daily but is gated disabled. The canonical EOM cadence is `0 0 1 * *` (midnight UTC on the 1st of each month). S16/S23 use `0 0 1 * *`.

---

### CN-21 — S23 NULL tier for never-attended contacts

When `last_attended_at IS NULL` (contact has never attended): `weeks_absent = NULL`, `tier = NULL`, `is_active = NULL`, `is_regular = False` (if `attendance_count = 0`). `is_connected` is computed independently. UI renders NULL tier as "Unrated." S09 filter `tier IS NULL` finds unrated contacts. S14 reports exclude NULL-tier from tier-count tiles but include in total-contact count.

---

### CN-22 — `community_report` FK ordering in S22 migration

S22's migration must create tables in this order: (1) `name_alias`, (2) `community_report`, (3) `name_match_review_queue` (which has FK→`community_report.id`). Reversing 2 and 3 causes a FK constraint error at migration time.

---

### CN-23 — S11 must call `recompute_contacts([survivor_id])` after merge

S23 §10 requires S11 to call `member_status_service.recompute_contacts(db, [survivor_id])` after completing the loser→survivor reassignment. S11's spec does not mention this. **Ruling CN-23:** S11 implementer MUST add this call at the end of the merge transaction (or immediately after commit, same request). Signature: `async def recompute_contacts(db: AsyncSession, contact_ids: list[int]) -> dict`.

---

### CN-24 — S06 and S10 must call `recompute_all_contacts` after successful live run

S23 §10 requires both. **Ruling CN-24:** After a successful live S06 migration run and after each S10 wizard import run, call `await member_status_service.recompute_all_contacts(db)`. At ~1,440 contacts this takes < 5s and can be synchronous. Return updated counts in the run response.

---

### CN-25 — `services/audit.py` must never import from routers (circular import prevention)

`app/services/audit.py` imports only from `models.py` and SQLAlchemy. It must not import from any router module. All routers import `audit.py`; `audit.py` never imports a router. S02 implementer enforces this direction.

---

### CN-26 — `down_revision` placeholders in specs are illustrative only

Several sprint specs contain illustrative revision IDs (`g4b5c6d7e8f9` appears in multiple specs; `f3a4b5c6d7e8` appears in S01). These are copy-paste artifacts. At implementation time, every migration resolves `down_revision` by running `alembic heads` against the current live migration chain. Never hardcode an id from another spec.

---

### CN-27 — `event_series` ownership — SUPERSEDED (2026-06-22): S04 owns it in full

**Original ruling (now superseded):** S01 creates a minimal `event_series` stub; S04 adds columns via `batch_alter_table`.

**Superseding ruling (2026-06-22):** **S04 owns `event_series` entirely** — a plain `op.create_table('event_series', ...)` with the full column set, then S04 adds the `events.recurring_series_id` FK. **S01 does NOT create the table or a stub.** Reason: CN-16 moved the `events.recurring_series_id` FK out of S01, so S01 has no consumer for the table, and a stub would collide with S04's column adds (duplicate-column migration error). S01's spec, model-change list, migration steps, and acceptance criteria were updated accordingly.

---

### CN-28 — FR transition is S24-owned; S01 must stash legacy ids (supersedes S01 §10 OQ3)

**Context:** S01 repoints `compreface_subjects.contact_id` → `contacts.id` and `detections.event_id` → `events.id`, then drops the CiviCRM tables. The already-running live face system depends on those links. The CompreFace instance persists across cutover, so the existing enrolled faces can be **remapped** (not orphaned). S01 §10 OQ3 previously proposed NULLing stale ids; **S24 supersedes that** with a remap.

**Ruling CN-28:**
- The **FR transition is owned by a dedicated sprint, S24** (remap, Attendance→Participant code cutover + tests, leaderboard scoring redefinition, consent backfill, cutover verification). It is NOT S01's or S07's responsibility beyond the obligations below.
- **S01 must add transitional stash columns** during its FK-repoint step, capturing the old CiviCRM ids before the repoint: `compreface_subjects._legacy_civicrm_contact_id Integer nullable` and `detections._legacy_civicrm_event_id Integer nullable`. Without this, S24's remap is impossible (the old ids are gone after `civicrm_members`/`civicrm_events` drop).
- **S24 consumes the stash columns** (remap via `contacts.external_id`/`events.external_id`) and **drops them** at the end of its migration.
- S24 runs at cutover after S06 (needs `external_id`) and S07 (needs `EnrollmentService`), and gates S21.
- Consent posture for pre-existing enrolled faces: backfill `consent_given=false, basis_note="pre-cutover-unknown"` and set `enroll_without_consent=true` (soft gate); staff collect consent over time.

---

### Patches required in sprint specs (implementation obligations)

| Sprint | Patch required |
|---|---|
| S01 | Add `nickname` to contacts (CN-01); add audit_log table creation (CN-03); list all 8 source values (CN-07); add "Powerhouse" to event_type comment (CN-05); do NOT include event_type/session_time/etc. in CREATE TABLE events (CN-16); add `_legacy_civicrm_contact_id`/`_legacy_civicrm_event_id` stash columns during FK repoint (CN-28) |
| S02 | `audit.py::record(...)` helper — single owner; enforce no circular import (CN-25) |
| S04 | Add `session_time String(20)` (single canonical col — no `session_type`/`service_time`, CN-15) and `occurrence_date Date` to events; confirm event_type NOT in S01 CREATE TABLE (CN-16); **own `event_series` in full via `create_table`** — S01 no longer makes a stub (CN-27 superseded) |
| S06 | Depends on S22 — sequence after S22 (CN-11); call `recompute_all_contacts` at end of live run (CN-24) |
| S10 | Call `recompute_all_contacts` at end of wizard import run (CN-24) |
| S11 | Add S22 FK tables to manifest (CN-04); add biometric_consent merge (CN-04); call `recompute_contacts([survivor_id])` post-merge (CN-23) |
| S12 | Do NOT create `audit_log` or `audit_service.py` — import from S02's `app.services.audit` (CN-03) |
| S13 | Post-S17: patch raw outbox INSERT to use `automation_engine.emit()` (CN-10) |
| S14 | Use `event_type + session_time` mapping instead of `session_type` column (CN-15); `require_reporting` is a shim patched by S15 (CN-17) |
| S15 | Absorb and patch `require_reporting` (CN-17); add SSE viewer filter (CN-19) |
| S16 | Job count is 9 (not 7 as stated in prose) (CN-12); EOM cron = `0 0 1 * *` (CN-20) |
| S22 | Migration table order: name_alias → community_report → name_match_review_queue (CN-22) |
| S23 | NULL handling for never-attended contacts (CN-21) |
| S24 | Consume + drop S01's legacy-id stash columns; own Attendance→Participant code cutover + tests; redefine leaderboard scoring; consent backfill soft-gate; verification gates S21 (CN-28) |
