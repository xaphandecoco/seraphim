I now have exhaustive data-model detail across all 12 features. Here are the structured findings.

---

# CiviCRM Feature Research — Minimal Replication Guide

Scope note: target is ~1,440 contacts + ~33k attendance rows. At that scale you do NOT need CiviCRM's full generality (EAV custom-value tables, option_group/option_value indirection, ACL DSL). Where CiviCRM uses heavy indirection for multi-tenant flexibility, the minimal replica should use plain Postgres columns + JSONB + a few lookup tables. Recommendations below are written for FastAPI + SQLAlchemy 2.0 async + Postgres.

---

## 1. Contacts (types, core fields, custom fields)

**(1) What CiviCRM does.** Every person/group/company is a row in `civicrm_contact` with a `contact_type` of Individual, Household, or Organization (plus optional `contact_sub_type` like "Student"/"Volunteer"). One table holds all three types; type-specific fields (first/last name vs household_name vs organization_name) coexist as nullable columns. Emails/phones/addresses live in separate child tables (one contact → many).

**(2) Essential entities/fields.**
- `civicrm_contact` actual columns (from schema): `id`, `contact_type VARCHAR(64)`, `contact_sub_type VARCHAR(255)` (multi-valued, stored ctrl-A delimited), `display_name`, `sort_name`, `first_name`, `middle_name`, `last_name`, `nick_name`, `organization_name`, `household_name`, `job_title`, `gender_id`, `birth_date DATE`, `is_deceased`, `prefix_id`, `suffix_id`, `external_identifier VARCHAR(64)` (UNIQUE — the import/merge anchor), `source`, `do_not_email`, `do_not_phone`, `do_not_mail`, `do_not_sms`, `is_opt_out`, `preferred_communication_method`, `preferred_language`, `employer_id` (FK to an Organization contact), `is_deleted` (soft delete), `created_date`, `modified_date`.
- `civicrm_email` (contact_id, email, location_type_id, is_primary), `civicrm_phone`, `civicrm_address` — all 1-contact-to-many.
- `civicrm_contact_type` table defines the type/sub-type hierarchy (parent_id links sub-type to base type).

**Custom fields** (this is the church's core need — Barangay, PEPSOL, Ministry, etc.):
- `civicrm_custom_group`: a *set* of fields attached to an entity. Key columns: `name`, `title`, `extends` (which entity — "Individual", "Contact", "Activity"…), `extends_entity_column_value` (restrict to a sub-type), `is_multiple TINYINT` (single-record 1:1 vs multi-record 1:n), `style` ("Tab"/"Tab with table"/"Inline"), `table_name`.
- `civicrm_custom_field`: `custom_group_id`, `label`, `name`, `data_type` (String, Int, Float, Money, Date, Boolean, StateProvince, Country, File, ContactReference), `html_type` (Text, Select, Radio, CheckBox, Multi-Select, Autocomplete-Select, TextArea, Date, Link), `is_required`, `is_searchable`, `option_group_id` (for select/radio/checkbox options), `default_value`, `weight`.
- **Storage (EAV-ish):** each custom group gets its own physical table `civicrm_value_<groupname>` with `id`, `entity_id` (FK to contact), and one column per field. Single-record group = 1 row per contact. Multi-record group (`is_multiple=1`) = many rows per contact, and the API treats it as its own entity (`Custom_<group>`). Multi-select/checkbox values are serialized in one column (ctrl-A delimited), which is why CiviCRM needs `CONTAINS` not `IN`.

**(3) Minimal replication.**
- One `contacts` table, `contact_type` enum (`individual`/`household`/`organization`), nullable name columns, `external_id` UNIQUE, `is_deleted` boolean, `created_at`/`updated_at`. Child tables `emails`, `phones`, `addresses` (or, at 1,440 contacts, collapse to primary `email`/`phone` columns + an optional JSONB for extras).
- **Custom fields — do NOT replicate the EAV machinery.** The church's 10 fields are known and stable. Two clean options:
  1. **Plain typed columns** on `contacts` (or a 1:1 `contact_profile` table): `barangay`, `pepsol`, `ministry`, `facebook_name`, `invited_by_id` (FK contacts), `consolidated_by_id` (FK contacts), `community`, `community_leader_id` (FK contacts), `community_add_date DATE`, `followup_listing`. Best for search/index/report performance. Recommended.
  2. If you want admin-definable fields later, a small metadata-driven layer: `custom_field_def` (name, label, data_type, options JSONB) + a `JSONB custom_data` column on contacts. Validate types in the service layer. Index hot keys with a Postgres expression/GIN index.
- **"Invited By / Consolidated By / Community Leader" should be FK contact references** (CiviCRM's ContactReference type), not free text — this enables "who did X invite" reverse lookups, which is likely what the church wants. "Multi-record custom group" maps to a child table (e.g. `contact_followup` rows) if any of these fields are genuinely many-per-contact.

---

## 2. Profiles (form templates)

**(1) What CiviCRM does.** A Profile is a reusable, named collection of fields (core + custom + Groups/Tags) that can be rendered as a form for: creating/editing a contact, event registration, public sign-up, search-result columns, or a member directory. It is a *form/view definition*, not data — the same profile drives both data entry and listing. Rules: a profile can't mix two contact types, and (besides contact fields) can include fields from at most one other entity.

**(2) Essential entities/fields.**
- `civicrm_uf_group`: the profile (title, group_type = which entity it's for, settings like post-submit action, dedupe-on-submit behavior, `add_to_group_id`).
- `civicrm_uf_field`: each field in the profile — `uf_group_id`, `field_name` (the core/custom field it maps to), `label`, `is_required`, `is_view`, `is_searchable`, `in_selector` (show as result column), `visibility`, `weight`, `help_pre`/`help_post`.

**(3) Minimal replication.** Model a profile as a JSON form schema: a `profiles` table (`name`, `entity`, `settings JSONB`) + `profile_fields` rows (or just a `fields JSONB` array) — each entry `{field_name, label, required, readonly, order}`. The frontend (React) renders the form from this schema; the backend validates submissions against it. For the church, the highest-value use is "quick add contact from a template" (owner's phrasing): ship 2–3 preset profiles (New Visitor, New Community Member, Volunteer) as seed JSON. No separate engine needed — it's a config-driven form renderer over the contacts model.

---

## 3. Activities & CiviCase

**(1) What CiviCRM does.** An **Activity** is a single timestamped interaction (call, meeting, email, follow-up) with a type, subject, status, date/time, duration, priority, and contact roles. A **Case** (CiviCase) is a *container that groups many activities* under a workflow — it has a case type, a status (Ongoing/Resolved/Urgent), client(s), and assigned staff (case roles), and can auto-spawn a **timeline** (activities at day-offsets) or **sequence** (next activity created when previous completes). The owner's ask ("Cases like activities assigned to someone") is essentially: an activity that owns a small set of follow-up activities and is assigned to a worker.

**(2) Essential entities/fields.**
- `civicrm_activity` actual columns: `id`, `activity_type_id`, `subject VARCHAR(255)`, `activity_date_time DATETIME`, `duration INT`, `location`, `details LONGTEXT`, `status_id`, `priority_id`, `parent_id` (sub-activities), `source_record_id`, `campaign_id`, `is_deleted`, `created_date`, `modified_date`.
- `civicrm_activity_contact`: the many-to-many glue. `activity_id`, `contact_id`, `record_type_id` (1 = Assignee, 2 = Source/added-by, 3 = Target/with-contact). This is how one activity links to multiple contacts in different roles.
- Activity statuses (default): Scheduled, Completed, Cancelled, Left Message, Unreachable, Not Required.
- `civicrm_case` (case_type_id, status_id, start_date, end_date, subject), `civicrm_case_contact` (client links), `civicrm_case_activity` (joins activities into the case), and case roles via relationships (manager/coordinator).

**(3) Minimal replication.**
- `activities` table: `id`, `activity_type` (enum or small lookup), `subject`, `details`, `activity_date TIMESTAMP`, `duration_min`, `status` (enum: scheduled/completed/cancelled/no_action), `priority`, `created_by_id`, `created_at`.
- `activity_contacts` join table with a `role` column (`assignee`/`source`/`target`). Or, since the church likely needs only one assignee + one subject contact, collapse to `assignee_id` + `target_contact_id` columns on the activity itself (much simpler; add the join table only if multi-target becomes real).
- **For "cases as assigned activities":** add nullable `case_id` self-reference or a thin `cases` table (`id`, `case_type`, `status`, `client_contact_id`, `assigned_to_id`, `opened_at`, `closed_at`) and give activities a `case_id` FK. A "case" is then just: a parent record + its child activities + an assignee. Skip timelines/sequences initially; if wanted, a `case_type_timeline` JSON (`[{activity_type, day_offset}]`) lets you auto-create follow-up activities on case open with a date computed from `opened_at`.

---

## 4. Events + Participants (with the bulk-add problem)

**(1) What CiviCRM does.** An **Event** (type, dates, location, max participants, fee) has many **Participants**; a participant record is the contact↔event link carrying a status and role. Participant statuses: Registered, Attended, No-show, Cancelled, Pending (plus pay-later/approval variants). Staff can bulk/mass-register by selecting many contacts from a search and applying one event+status to all. **The owner's pain — 10–15 min and crashes adding 1,500 participants — is a known CiviCRM performance problem:** mass registration runs each participant through the full form/payment/hook stack with per-row PHP round-trips, no bulk insert.

**(2) Essential entities/fields.**
- `civicrm_participant` actual columns: `id`, `contact_id`, `event_id`, `status_id` (FK `civicrm_participant_status_type`), `role_id VARCHAR(128)` (FK option_value `participant_role`), `register_date DATETIME`, `source`, `fee_level`, `fee_amount DECIMAL(20,2)`, `registered_by_id` (who bulk-registered them), `transferred_to_contact_id`, `is_test`.
- `civicrm_event`: title, event_type_id, start_date, end_date, max_participants, is_active, fee info, location.
- `civicrm_participant_status_type`: status lookup with a `class` grouping (Positive/Pending/Negative) controlling whether the status counts toward the total.

**(3) Minimal replication — and fix the bulk problem.**
- `events` (`id`, `title`, `event_type`, `start_at`, `end_at`, `location`, `max_participants`, `is_active`).
- `participants` (`id`, `contact_id`, `event_id`, `status` enum: registered/attended/no_show/cancelled/pending, `role`, `register_date`, `registered_by_id`). UNIQUE (`event_id`, `contact_id`) to prevent dup registration. This is the ~33k-row attendance table — index `(event_id)` and `(contact_id)`.
- **Bulk add must be a single set-based DB operation, not row-by-row.** Accept a list of `contact_id`s + one event + one status and do one `INSERT ... ON CONFLICT (event_id, contact_id) DO NOTHING` (Postgres) or SQLAlchemy `insert().on_conflict_do_nothing()`. 1,500 rows insert in well under a second. For attendance marking, a bulk `UPDATE ... WHERE event_id = ? AND contact_id = ANY(:ids)`. Avoid ORM per-object flush in a loop; use `session.execute(insert(...), rows)` or `bulk_insert_mappings`. This single design choice eliminates the 10–15 min/crash issue entirely.
- Provide "register from search results" + a "batch update grid" (editable table to flip statuses) on the frontend, both backed by the bulk endpoints.

---

## 5. Import (CSV with mapping + dedupe)

**(1) What CiviCRM does.** Upload CSV → map each column to a CiviCRM field (or "do not import") → choose how to match existing contacts using a **dedupe rule** → choose on-duplicate action: **Skip**, **Update** (overwrite from CSV), or **Fill** (only populate blank fields). Mapping templates can be saved. Matching can use `external_identifier`, contact id, or a dedupe rule (e.g. first+last+email).

**(2) Essential entities/fields.** No special schema — it reuses the contact dedupe rules (see #6) and writes to `civicrm_contact` + child tables. Saved mappings in `civicrm_mapping`/`civicrm_mapping_field`.

**(3) Minimal replication.** An import endpoint that: (a) parses CSV (Python `csv`/`pandas`), (b) takes a column→field mapping dict from the UI, (c) for each row computes a match key (prefer `external_id`, else normalized email, else first+last+email), (d) applies skip/update/fill. Run as a batched transaction (chunks of ~500) and return a per-row result report (created/updated/skipped/errored). For 1,440 contacts this is trivial; a background task (you already have Redis/workers) keeps the request fast. Store recent mappings as JSON for reuse.

---

## 6. Dedupe rules + Find & Merge

**(1) What CiviCRM does.** A dedupe rule lists up to ~5 fields, each with a **length** (compare first N chars) and a **weight**; if matched-field weights sum ≥ a **threshold**, the pair is flagged. **Unsupervised** rules (narrow, auto-applied on import/online forms) vs **Supervised** rules (broader, warn-on-edit). "Find Matching Contacts" lists candidate pairs; the **Merge screen** shows the two records side-by-side, color-coded: green = identical, red = conflict (pick per field), yellow = additive data (tags/groups/activities migrate). One is "duplicate" (deleted), one "original" (kept); merge is irreversible and moves all related records to the original. **Batch merge** auto-merges only conflict-free pairs.

**(2) Essential entities/fields.** `civicrm_dedupe_rule_group` (name, contact_type, used = Unsupervised/Supervised/General, threshold) + `civicrm_dedupe_rule` (rule_field, rule_length, rule_weight). Merge has no persistent schema — it's an operation.

**(3) Minimal replication.** Represent rules as config (a list of `{field, length, weight}` + threshold) rather than a UI engine — the church needs maybe two: a strict one (exact email OR external_id) for import, and a fuzzy one (first 3 of first_name + last_name + phone) for "find duplicates." Compute candidate pairs with a SQL query (block on a cheap key like last_name or email domain, then score in Python). Merge endpoint: take `survivor_id` + `loser_id` + a per-field resolution map; in one transaction reassign FKs (participants, activities, emails…) from loser→survivor, apply field choices, soft-delete the loser. Provide a side-by-side React merge UI mirroring the green/red/yellow model. Keep an audit row so merges aren't truly irreversible.

---

## 7. Advanced Search + Saved Searches / Smart Groups

**(1) What CiviCRM does.** Advanced Search queries across core + custom fields + component data (events, activities, address) with grouped criteria. Save the criteria as a **Saved Search**; promote it to a **Smart Group** — a group whose membership is *defined by the query* and re-evaluated (and cached) each time it's opened, vs a **static group** with explicit member rows. A contact can be in static and smart groups simultaneously.

**(2) Essential entities/fields.** `civicrm_group` (`name`, `is_active`, `saved_search_id` — non-null ⇒ smart group), `civicrm_saved_search` (`form_values` = serialized search criteria), `civicrm_group_contact` (static membership: group_id, contact_id, status), `civicrm_group_contact_cache` (materialized smart-group membership, refreshed by cron).

**(3) Minimal replication.**
- `groups` (`id`, `name`, `type` enum static/smart, `criteria JSONB` for smart groups).
- `group_members` (`group_id`, `contact_id`) for static groups.
- Advanced search = a query builder endpoint that accepts a structured filter JSON over allowed fields and translates to SQLAlchemy `where` clauses (whitelist columns to avoid injection). A **smart group** is just that saved filter; resolve membership live at query time (1,440 contacts = no caching needed). Materialize to `group_members` only if you later want fast "is contact X in group Y" or notifications.

---

## 8. Reports & Dashboards

**(1) What CiviCRM does.** CiviReport runs parameterized queries from **report templates** (grouped by component: contact, contribution, event…). Each template configured + saved = a **report instance** with its own filters/columns. Output: on-screen, scheduled email (CSV/PDF), or a **dashlet** (a report tile) on the home **dashboard**, if "Available for Dashboard?" is checked.

**(2) Essential entities/fields.** `civicrm_report_instance` (report_id/template, `form_values` = filters+columns, output settings, is_dashlet) + `civicrm_dashboard` / `civicrm_dashboard_contact` (which dashlets each user has + layout).

**(3) Minimal replication.** Don't build a generic report engine. Ship a fixed set of purpose-built endpoints the church actually needs: attendance over time, contacts by Barangay/Ministry/Community, new contacts this month, follow-up listing, "invited by X" rollups. Each = a tuned SQL/SQLAlchemy aggregate returning JSON; the React frontend renders tables/charts. A "dashboard" is a page of these tiles; per-user layout (which tiles, order) can be a small `dashboard_prefs JSONB` per user. Add CSV export per report. This is far simpler and faster than CiviReport's generic template machinery.

---

## 9. CiviRules (automation engine)

**(1) What CiviCRM does.** A rule = exactly one **Trigger** + zero-or-more **Conditions** (AND/OR) + one-or-more **Actions**, with an optional **Delay**. Triggers are either entity-based (contact created/updated, contribution added, activity deleted) — fired immediately via hooks — or **scheduled** (evaluated when the CiviRules cron job runs). Conditions are field comparisons / group membership / "first-time donor" etc. Actions: send email/SMS/PDF, add/remove from group, add a tag, set a field, create an activity. Delay (minutes/days/weeks) defers the action; a checkbox controls whether conditions are re-checked after the delay (so a later state change can cancel the action).

**(2) Essential entities/fields.** `civicrm_rule` (trigger_id, is_active), `civicrm_rule_condition` (condition class + params + AND/OR), `civicrm_rule_action` (action class + params + delay + ignore_condition_with_delay), `civicrm_rule_log`. Triggers/conditions/actions are pluggable classes registered in `civicrm_*_trigger/condition/action` catalogs.

**(3) Minimal replication.** Model rules as data, execute in code:
- `automation_rules` (`id`, `trigger` enum [contact_created, contact_updated, participant_status_changed, scheduled], `conditions JSONB`, `actions JSONB`, `delay_minutes`, `is_active`).
- **Immediate path:** in service-layer write hooks (or SQLAlchemy events / domain events), after a relevant mutation, evaluate matching rules' conditions and enqueue actions to your existing Redis/worker queue.
- **Delayed/scheduled path:** enqueue with a run-at timestamp (or a periodic worker that scans). Implement a handful of concrete actions as Python functions (send_email via your mailer, add_to_group, set_field, create_activity/follow-up). For the church, the killer use cases are "new visitor → create follow-up activity assigned to their inviter" and "no-show → schedule follow-up call" — both are 1 trigger + 1 condition + 1 action. Start with those two hardcoded, generalize to the JSON model only if needed.

---

## 10. Scheduled Jobs / Cron

**(1) What CiviCRM does.** Admin-defined jobs (frequency: hourly/daily/etc., last-run timestamp, parameters) are stored in the DB; an OS-level scheduler (cron/systemd/webcron) periodically calls the `Job.execute` API, which checks which jobs are due and runs them. Typical jobs: send scheduled reminders, process CiviMail queue, fetch bounces, refresh smart-group cache, update check. System status shows "Cron running OK" only if `Job.execute` (not individual jobs) is invoked.

**(2) Essential entities/fields.** `civicrm_job` (`name`, `api_entity`, `api_action`, `parameters`, `run_frequency`, `last_run`, `is_active`), `civicrm_job_log` (run history/results).

**(3) Minimal replication.** You already have Redis + a workers/ layer. Use a scheduler (APScheduler in-process, Celery beat, or even an external cron hitting an authed endpoint) to run periodic tasks: refresh any materialized smart groups, send due reminders, fire scheduled CiviRules, run delayed automation actions. A small `job_runs` table (`job_name`, `started_at`, `finished_at`, `status`, `detail`) gives you the job-log equivalent and a health check. No need for a DB-driven job *registry* at this scale — register jobs in code; keep their schedule in config.

---

## 11. ACL / Permissions

**(1) What CiviCRM does.** Two layers: (a) coarse **permissions** (e.g. "access CiviCRM", "edit contacts", "administer") granted to **roles**; (b) fine-grained **ACLs** = a Role + an Operation (View/Edit/Create/Delete/Search) + a Data target (a group of contacts, a custom field group, or a profile). Three special roles: Any (everyone), Authenticated (logged-in), Administrator. ACLs currently constrain only Groups, Custom Groups, and Profiles.

**(2) Essential entities/fields.** `civicrm_acl` (entity_id=role, operation, object_table, object_id=target), `civicrm_acl_entity_role` (which contacts have which ACL role), plus CMS-level role→permission mapping.

**(3) Minimal replication.** The church needs three roles, not an ACL DSL: map directly to **admin / volunteer / viewer**.
- `users` table with a `role` enum (admin/volunteer/viewer).
- Enforce via FastAPI dependencies: `viewer` = read-only (GET), `volunteer` = create/edit contacts, activities, participants, run searches; `admin` = everything incl. import/merge/delete/automation/user management.
- Implement as a permission check decorator/dependency per route (you already auth-gate routers per CLAUDE.md). Skip per-record ACLs unless the church wants "ministry leaders see only their ministry" — if so, add a `scope` (e.g. allowed ministry/community values) on the volunteer user and filter queries by it. Defer that.

---

## 12. API (APIv4 shape) for external automation

**(1) What CiviCRM does.** Uniform `civicrm_api4(Entity, Action, params)` — every entity (Contact, Participant, Activity…) supports the same verbs: `get`, `create`, `update`, `save`, `delete`, plus `getFields`/`getActions` for introspection. `get` takes a `where` array of `[field, operator, value]` clauses, `limit`, `select`, `orderBy`; `create`/`update` take `values`. Supports `:name`/`:label` pseudo-fields (`gender_id:name`), implicit joins (`employer_id.is_opt_out`), and **chaining** (nest a create inside a create; pass parent fields down with `$`). Exposed over REST.

**(2) Essential shape.** Request: entity + action + `{where, select, values, limit, orderBy, chain}`. Response: a uniform result array of records.

**(3) Minimal replication.** You don't need CiviCRM's generic API kernel — build conventional REST resources in FastAPI (`/contacts`, `/participants`, `/activities`, `/events`, `/groups`) with consistent CRUD + a structured `?filter=` query param mirroring the `[field, op, value]` shape for power users, and Pydantic schemas for validation. For external automation specifically: expose API-key-authenticated endpoints (you already have a JTI/token system) and a bulk endpoint per resource (the bulk participant insert from #4 is the most important). If you want CiviCRM-style ergonomics, a single `POST /api/{entity}/{action}` dispatcher over your services gives the uniform feel cheaply — but plain REST is more idiomatic for FastAPI + React/TanStack Query.

---

## Cross-cutting migration notes (data model summary)

Suggested core tables: `contacts` (+ optional `emails`/`phones`/`addresses`), `groups` + `group_members`, `events` + `participants` (the 33k-row table — index `event_id`, `contact_id`, UNIQUE together), `activities` (+ optional `activity_contacts`, optional `cases`), `profiles`, `automation_rules`, `job_runs`, `users`.

Key simplifications vs CiviCRM that are safe at this scale:
- Replace EAV custom-value tables → **typed columns or one JSONB** on contacts.
- Replace option_group/option_value indirection → **Postgres enums or tiny lookup tables**.
- Replace generic CiviReport/ACL/API kernels → **purpose-built endpoints + 3-role RBAC + REST**.
- The one place to spend real engineering effort is **bulk participant insert/update** (set-based SQL / `ON CONFLICT`), which directly fixes the owner's biggest pain.
- Use FK contact-references for Invited By / Consolidated By / Community Leader to unlock reverse-lookup reports.

---

## Sources
- [Custom fields (user guide)](https://docs.civicrm.org/user/en/latest/organising-your-data/creating-custom-fields/) · [Custom data (dev/APIv4)](https://docs.civicrm.org/dev/en/latest/api/v4/custom-data/) · [Custom groups/fields/values blog](https://civicrm.org/blog/lobo/custom-groups-custom-fields-and-multiple-values)
- [civicrm_contact schema](https://doc.symbiotic.coop/dev/civicrm/v5.20/schema/tables/civicrm_contact.html) · [civicrm_activity schema](https://doc.symbiotic.coop/dev/civicrm/v5.20/schema/tables/civicrm_activity.html) · [civicrm_participant schema](https://doc.symbiotic.coop/dev/civicrm/v5.20/schema/tables/civicrm_participant.html)
- [Profiles](https://docs.civicrm.org/user/en/latest/organising-your-data/profiles/)
- [Activities](https://docs.civicrm.org/user/en/latest/organising-your-data/activities/) · [What is CiviCase](https://docs.civicrm.org/user/en/latest/case-management/what-is-civicase/) · [CiviCase: what you need to know](https://docs.civicrm.org/user/en/latest/case-management/what-you-need-to-know/)
- [Events & participants](https://docs.civicrm.org/user/en/latest/events/keeping-track-of-events-and-participants/) · [Manual/bulk registration](https://docs.civicrm.org/user/en/latest/events/manual-event-registration/)
- [Importing data](https://docs.civicrm.org/user/en/latest/common-workflows/importing-data-into-civicrm/)
- [Deduping and merging](https://docs.civicrm.org/user/en/latest/common-workflows/deduping-and-merging/)
- [Smart groups](https://docs.civicrm.org/user/en/latest/organising-your-data/smart-groups/) · [Searching intro](https://docs.civicrm.org/user/en/latest/the-user-interface/searching/)
- [What is CiviReport](https://docs.civicrm.org/user/en/latest/reporting/what-is-civireport/) · [Menu, dashboard & dashlets](https://docs.civicrm.org/user/en/latest/the-user-interface/menu-dashboard-and-dashlets/)
- [CiviRules intro](https://docs.civicrm.org/civirules/en/latest/) · [Immediate processing](https://docs.civicrm.org/civirules/en/latest/basic-example-immediate-processing/) · [Scheduled processing](https://docs.civicrm.org/civirules/en/latest/basic-example-scheduled-processing/)
- [Scheduled jobs (sysadmin)](https://docs.civicrm.org/sysadmin/en/latest/setup/jobs/)
- [Permissions & access control](https://docs.civicrm.org/user/en/latest/initial-set-up/permissions-and-access-control/) · [Access control (dev)](https://docs.civicrm.org/dev/en/latest/security/access/)
- [APIv4 usage](https://docs.civicrm.org/dev/en/latest/api/v4/usage/) · [APIv4 chaining](https://docs.civicrm.org/dev/en/latest/api/v4/chaining/)