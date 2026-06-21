I have everything needed. Let me synthesize the research into the final deliverable.

---

# n8n Replacement Research — Project Seraphim

> **⚠️ Superseded note (2026-06-22):** This is early research. Notifications later moved **OFF Mattermost → a single Google Chat incoming-webhook channel** (see `decisions.md` Round 8); the notifier service is `services/google_chat.py`-style, not `services/mattermost.py`. References to "Mattermost notifier" below describe the church's *current* n8n state, not the target design. The authoritative target is `docs/specs/` (S17/S18) + `00-MASTER.md`.

This maps the church's four n8n workflows onto **native FastAPI patterns** that fit the existing stack (FastAPI + SQLAlchemy 2.0 async + Postgres/Redis, an existing `CiviCRMClient`, a `push_status` worker pattern, and a `dynamic_settings` DB-backed config tier). The codebase already contains most of the building blocks; the gap is a public form endpoint, an outbound Mattermost notifier, a Zoom polling client, and a thin scheduler/automation layer.

Key code I examined (absolute paths):
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\services\civicrm.py` — existing CiviCRM v3 REST client (`extern/rest.php`, site_key + api_key, tenacity retry). Already has `push_attendance(contact_id, event_id)`, `sync_events`, `sync_members`, `get_rsvp_list`. **Reuse this — add a `create_contact` method.**
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\routers\attendance.py` — the `push_status` queue pattern (`pending → queued → pushed/failed/dead_letter`, retry, dead-letter) is the template for every outbound integration.
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\services\task_service.py` — `_log_attendance` creates `Attendance(push_status="pending")`; a worker drains it. Mirror this for Mattermost + Zoom.
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\config.py` — `dynamic_settings` is the DB-backed secret store (`get_civicrm_*`); add `get_mattermost_*` / `get_zoom_*` the same way (store in `admin_settings`, mark `sensitive=True`).
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\models.py` — `Attendance`, `Detection`, `CiviCRMEvent`, `CiviCRMMember`, `AdminSetting`. No outbox/automation tables yet.
- `C:\Users\John Atienza\Documents\Project Seraphim\backend\app\main.py` — routers are all `Depends(check_setup_complete)` + auth-gated; a **public** form/webhook endpoint must be registered like `setup`/`auth` (before the setup-check) with its own rate limit + secret.

---

## (A) Proposed internal-automation architecture

Recommendation: **do NOT build a generic visual rules engine.** For four fixed workflows, a clean REST API + a small **event-dispatch ("automation") table** + **scheduled jobs** + a **transactional outbox** gives you 95% of n8n's value with a fraction of the complexity and far better testability/observability. Reserve a config-driven rules table only for the parts that genuinely vary (notification routing, enable/disable, channel selection).

Five layers, all native to the current stack:

1. **Inbound edge — public form/webhook router** (`routers/public.py`, registered like `setup`/`auth`, NOT behind `check_setup_complete`):
   - `POST /public/newcomer` — accepts the newcomer form (workflow 1). Protected by slowapi rate limiting (already wired in `main.py`), a shared form secret/origin check, optional CAPTCHA, and strict Pydantic validation.
   - `POST /webhooks/{source}` — generic inbound webhook (e.g. a future Zoom event webhook) with **HMAC signature verification** (constant-time compare against a per-source secret in `admin_settings`).
   - Inbound requests are validated, persisted, and **acknowledged fast (202)**; heavy work (CiviCRM call) happens async so the public form never blocks on CiviCRM.

2. **Internal automation/event bus (lightweight, not a DSL).** Add an `automation_events` table (outbox) and a tiny in-process dispatcher:
   - Domain code emits a typed event (`newcomer.created`, `event.created`, `attendance.confirmed`, `zoom.attendance.recorded`).
   - A small **rules table** (`automations`: `trigger_type`, `condition_json`, `action_type`, `target_config_json`, `is_active`) maps trigger → action. Conditions stay simple (equality/`in`/threshold on event payload fields) — enough for "if event has attendees, notify channel X." This is the ECA (event-condition-action) pattern, kept declarative and DB-driven so admins toggle/route without code changes, but evaluated by plain Python, not an embedded expression language.
   - This is the only "rules engine" you need; it is essentially a dispatch dict + a filter, backed by Postgres.

3. **Transactional outbox + worker** (generalize the existing `Attendance.push_status` pattern into a reusable `outbox` table, or add per-purpose status columns). Any outbound side effect (CiviCRM write, Mattermost post) is written to the outbox **in the same DB transaction** as the state change, then a background worker drains it with retry/backoff (reuse the `tenacity` config from `civicrm.py`) and a **dead-letter** terminal state (already modeled in `attendance.py`). This guarantees at-least-once delivery and survives restarts — the thing n8n gave you for free.

4. **Scheduled jobs (cron).** For the two polling/periodic flows (Zoom pull; event-report push). Options, simplest first:
   - **APScheduler** `AsyncIOScheduler` started in the `lifespan` context of `main.py` (in-process, fine for a single replica) — or
   - A dedicated worker process driven by **APScheduler/Celery-beat**, or the host's cron hitting an internal admin endpoint. Given the existing worker dir (`backend/app/workers/`), an APScheduler loop in a worker process is the lowest-friction fit. Each job is **idempotent** and uses a DB advisory lock so two replicas don't double-run.

5. **Outbound notifications service** (`services/mattermost.py`): a thin async `httpx` client that POSTs JSON to a Mattermost **incoming webhook URL** (stored in `admin_settings`, `sensitive=True`). Same retry/dead-letter treatment as CiviCRM.

6. **Clean public REST API** for everything that is genuinely request/response (newcomer submit, manual "create event", manual "push report") so the church UI and any external caller hit documented, auth-gated endpoints instead of n8n.

Net: **REST API for user-facing/interactive actions; outbox + scheduled jobs + tiny ECA dispatcher for the background automation.** This matches the standard guidance to use REST for interactions and an event-driven layer for background automation, without adopting a heavyweight engine.

---

## (B) Per-workflow replacement design

### 1. Newcomer form → CiviCRM contact

- **n8n today:** public form trigger → "CiviCRM: create contact" node.
- **Trigger:** HTTP POST from a public web form.
- **Data flow:** browser form → public endpoint → validate → create CiviCRM `Individual` contact (+ email) → optional Mattermost ping to a welcome channel.
- **Native implementation:**
  - New **public** endpoint `POST /public/newcomer` in `routers/public.py`, registered before `check_setup_complete` (like `setup.router`). Strict Pydantic `NewcomerSubmission` schema (first/last name, email, phone, source). Rate-limited via the existing `limiter`; protect with a form secret or CAPTCHA + origin allowlist; honeypot field for spam.
  - Add `async def create_contact(self, first_name, last_name, email, **extra) -> int` to `CiviCRMClient` (mirrors existing `_call`: `Contact.create` then optionally `Email.create`, or `Contact.create` with chained email). Returns new `contact_id`.
  - Write the submission to an outbox row (so a CiviCRM outage doesn't lose the newcomer) and return `202`. Worker performs the CiviCRM call with retry/dead-letter.
  - Emit `newcomer.created` → automation rule can fire a Mattermost "New newcomer: {name}" notification (workflow 2's notifier, reused).
- **Owner inputs needed:** exact CiviCRM **custom fields** the newcomer form maps to (and the contact sub-type / group/tag to add, e.g. "Newcomer"), plus the public form's HTML/field list.

### 2. Event Creator + Attendee notifier → Mattermost

- **n8n today:** create an event, then notify a Mattermost channel about attendees.
- **Trigger:** admin action ("create event") and/or an attendance-confirmation event.
- **Data flow:** create `CiviCRMEvent` → on attendee confirmation, post a formatted message to a Mattermost channel.
- **Native implementation:**
  - **Create event:** extend `routers/events.py` with `POST /events` (admin-gated) that creates the event in CiviCRM (`Event.create` — new client method) and upserts the local `CiviCRMEvent` row (the sync/upsert logic already exists in `sync_events`).
  - **Notifier:** new `services/mattermost.py`:
    ```python
    payload = {
      "channel": channel,          # optional override; webhook has a default channel
      "username": "Seraphim",
      "text": "#### New attendees confirmed",
      "attachments": [{
        "color": "#7CD197",
        "fields": [{"title": "Event", "value": title, "short": True},
                   {"title": "Attendees", "value": str(n), "short": True}]
      }]
    }
    await client.post(webhook_url, json=payload)
    ```
    Mattermost incoming webhooks accept a JSON body with `text` (full Markdown), optional `channel`/`username`/`icon_url`, and Slack-compatible `attachments` (with `color`, `fields[].{title,value,short}`). Default `Content-Type` is `application/json`.
  - **Wiring:** on attendance confirmation (`task_service._log_attendance`) emit `attendance.confirmed`; an automation rule with `action_type=mattermost_notify` and `target_config={channel, template}` fires the post via the outbox (retry + dead-letter). Batch/debounce (e.g. one digest per event per N minutes via a scheduled job) to avoid spamming the channel per face.
- **Owner inputs needed:** the **Mattermost incoming webhook URL**, the **channel name(s)** to post to, desired message format (per-attendee vs. digest), and the CiviCRM event-type/fields used when creating events.

### 3. Event attendance reports → CiviCRM (complex)

- **n8n today:** the complex flow — push event attendance reports into CiviCRM as participants.
- **Trigger:** admin "push" action (already exists) and/or scheduled end-of-event job.
- **Data flow:** confirmed local `Attendance` rows → CiviCRM `Participant.create` (status Attended) per contact, with dedupe and a preview diff.
- **Native implementation — mostly already built:**
  - `POST /attendance/push-preview` (dry-run diff) and `POST /attendance/push` (queues `push_status="queued"`) already exist in `attendance.py`; `CiviCRMClient.push_attendance` already creates the Participant; dead-letter + retry already modeled.
  - **What to add:** the **worker that drains `push_status="queued"`** (the routers only enqueue — confirm/implement the consumer in `backend/app/workers/`), calling `push_attendance` with tenacity retry → `pushed`/`failed`/`dead_letter` and writing `last_push_error`/`push_attempts`. The "complex" bits to port from n8n: **RSVP reconciliation** (`get_rsvp_list` already exists — fill the `missing` list in `push_preview`, currently a TODO), dedupe against existing CiviCRM participants, and mapping multiple cameras/detections to one participant.
  - Optionally a **scheduled job** to auto-push X hours after an event's `end_date` so admins don't have to click.
- **Owner inputs needed:** the n8n workflow JSON for this flow (to capture the exact CiviCRM **Participant status/role IDs**, custom participant fields, and any aggregation/grouping rules), and the rule for matching detected attendees to CiviCRM contacts when no RSVP exists.

### 4. Zoom morning prayers → CiviCRM attendance

- **n8n today:** pull Zoom meeting attendance, record it in CiviCRM.
- **Trigger:** scheduled (e.g. each morning after the recurring prayer meeting ends) — Zoom reports lag a few minutes after a meeting ends, so poll on a delay or via a Zoom `meeting.ended` webhook that then schedules the pull.
- **Data flow:** Zoom S2S OAuth token → fetch past-meeting participants → match Zoom participant (email/name) to CiviCRM contact → create `Participant` (Attended) for the prayer event.
- **Native implementation:**
  - New `services/zoom.py`:
    - **Auth — Server-to-Server OAuth (account_credentials):** `POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ACCOUNT_ID}` with header `Authorization: Basic base64(client_id:client_secret)`. Response is a Bearer token, `expires_in≈3599`, **no refresh token** — cache it (~55 min) and re-request on expiry. Store `account_id`/`client_id`/`client_secret` in `admin_settings` (`sensitive=True`).
    - **Attendance endpoint:** `GET /report/meetings/{meetingId}/participants` (scope `report:read:admin`), or `GET /past_meetings/{meetingUUID}/participants` (scope `meeting:read:admin` / `meeting:read`). Use **report** for richer join/leave + duration data. Paginate via `page_size` + `next_page_token`. **UUID caveat:** if a meeting UUID starts with `/` or contains `//`, it must be **double URL-encoded**; for recurring meetings the bare numeric meeting ID returns only the latest occurrence, so to get a specific morning you must resolve the **UUID** of that occurrence (via `GET /report/users/{userId}/meetings` or the `meeting.ended` webhook payload) and query by UUID.
  - **Scheduled job** (APScheduler in a worker): each morning, resolve the prayer meeting's latest occurrence UUID → fetch participants → upsert into `Attendance` (`push_status="pending"`) keyed to the prayer `CiviCRMEvent` → the existing outbox worker pushes to CiviCRM as Participants. Idempotent on `(contact_id, event_id)` (the `uq_attendance_contact_event` constraint already enforces this).
  - **Matching:** Zoom gives email + display name; match to `CiviCRMMember.email` first, then fuzzy name; unmatched participants go to a review list (mirror the existing PIT/task review concept) rather than being silently dropped.
- **Owner inputs needed:** Zoom **account_id, client_id, client_secret** of a Server-to-Server OAuth app with `report:read:admin` (or `meeting:read:admin`); the **meeting ID** (or host user) for the morning-prayers meeting and whether it's recurring; the CiviCRM **event** that morning prayers should be recorded against; and confirmation that participant **emails** are exposed (Zoom can mask attendee emails depending on account settings/role — affects matching).

---

## (C) Exactly what we need from the owner

**Per workflow / integration:**

1. **All four n8n workflow JSON exports** (n8n → each workflow → "..." menu → Download). These are the single most valuable artifact: they reveal the exact node-by-node field mappings, CiviCRM entity/action names, custom field IDs, Participant status/role IDs, conditionals, and credentials used — letting us replicate behavior 1:1 instead of guessing.

2. **CiviCRM (workflows 1, 3, 4):** confirm the existing `civicrm_url` / `api_key` / `site_key` are correct and have write scope; the **newcomer** contact sub-type + group/tag + custom fields; the **Participant** `status_id`/`role_id` and any custom participant fields used for reports; the CMS path if not WordPress (the client currently hardcodes `wp-content/plugins/civicrm/...`).

3. **Mattermost (workflows 1, 2):** the **incoming webhook URL**, the **target channel name(s)**, bot display name/icon, and whether notifications should be per-attendee or a batched digest.

4. **Zoom (workflow 4):** Server-to-Server OAuth app **account_id + client_id + client_secret** with `report:read:admin` (and/or `meeting:read:admin`); the **morning-prayers meeting ID/host** and whether it recurs; the CiviCRM **event** to record attendance against; account setting confirming attendee **emails** are returned (needed for contact matching).

5. **Public newcomer form (workflow 1):** the form's hosted location/origin (for CORS/origin allowlist), its field list, and whether we may add anti-spam (CAPTCHA/honeypot).

6. **Operational:** how many app replicas run in prod (decides in-process APScheduler vs. a dedicated scheduler + advisory locks), and the desired post-event auto-push delay and Zoom poll time.

**Net engineering scope to add to the repo:** `routers/public.py` (public form + signed webhook), `services/mattermost.py`, `services/zoom.py`, `CiviCRMClient.create_contact`/`create_event`, an `outbox`/`automations` table + Alembic migration, the missing `push_status="queued"` consumer in `backend/app/workers/`, and an APScheduler-based scheduler in the worker process. No heavyweight rules engine — a small DB-backed ECA dispatcher plus scheduled jobs is the right altitude for four fixed flows.

Sources:
- [Mattermost incoming webhooks](https://developers.mattermost.com/integrate/webhooks/incoming/)
- [Mattermost message attachments](https://developers.mattermost.com/integrate/reference/message-attachments/)
- [Zoom Server-to-Server OAuth (internal apps)](https://developers.zoom.us/docs/internal-apps/s2s-oauth/)
- [Zoom Meetings API docs](https://developers.zoom.us/docs/api/meetings/)
- [Zoom report/meetings/{meetingId}/participants (Harvard API portal mirror)](https://portal.apis.huit.harvard.edu/docs/ccs-zoom-api/1/routes/report/meetings/%7BmeetingId%7D/participants/get)
- [Zoom devforum — report participants scope/UUID behavior](https://devforum.zoom.us/t/im-not-receiving-meeting-data-for-past-meetings-with-the-same-id-on-end-point-report-meetings-meetingid-participants/86412)
- [Python rule engine patterns (Django Stars)](https://djangostars.com/blog/python-rule-engine/)
- [Event-driven vs REST APIs — when to use each](https://blog.axway.com/learning-center/apis/basics/event-driven-vs-rest-api-interactions)