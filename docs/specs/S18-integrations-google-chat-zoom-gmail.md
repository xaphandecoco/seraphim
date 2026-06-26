# S18 — Integrations: Google Chat, Zoom & Gmail

**Phase:** G — Automation · **Depends on:** S16 (APScheduler + `job_runs`), S17 (outbox + automation_rules), S22 (name-matching service + `name_match_review_queue`) · **Effort:** L · **Status:** Not started

---

## 1. Goal & rationale

This sprint replaces the four relevant n8n workflows (`sJ7EgCZkk9wKSj3D` Morning Prayer / Zoom; `fKkPUolayRyZrjao` scheduler notifiers; `jdVHzcMWXdANwG8R` newcomer notify; `3yaS8JZfct8BVe8Z` community-report notify) with production-grade, in-process Python services wired through the S17 outbox and APScheduler (S16). It eliminates the dependency on Google Sheets, Mattermost, and OpenRouter by:

1. Posting to a **single Google Chat incoming-webhook channel** (not Mattermost) for all church-wide notifications.
2. Sending **Gmail** transactional emails (attendance summaries, newcomer welcome) via Gmail SMTP/OAuth, keeping the richly formatted email templates visible in the n8n workflow.
3. Pulling **Zoom** morning-prayer attendees via Server-to-Server OAuth (`account_id=QtCxnKWNTFyqxRN8Y0s3wQ` from n8n JSON), pushing name-matched participants into `participants` via the S22 matching service.
4. Scheduling six attendance-notification cadences (Sun 09:30, 12:00, 17:00 for 8AM/10AM/3PM services; Wed 21:00 for Powerhouse; Zoom pull at ≈23:00 daily; community-report notify on submission) through the S16 `job_runs`-logged APScheduler.

The sprint assumes the rules engine (`automation_rules` + `outbox`) is live from S17 — the integration services are **consumers of the outbox** (they drain `outbox` rows tagged with `event_type` values they own) and **producers** of new rows for downstream notification.

Pending owner artifact (needed to go live, not to spec): Google Chat webhook URL + Zoom S2S client_id and client_secret.

---

## 2. Scope

### In scope
- `services/google_chat.py` — thin async httpx poster to a single incoming-webhook URL stored in `admin_settings`.
- `services/gmail.py` — async Gmail SMTP wrapper (port 587 STARTTLS) using credentials stored in `admin_settings`; sends the HTML attendance-summary emails visible in the n8n Morning Prayer workflow.
- `services/zoom.py` — Zoom Server-to-Server OAuth: token-fetch + 55-minute bearer cache, `GET /report/users/{host}/meetings` (date window), `GET /report/meetings/{uuid}/participants` (page_size=100, next_page_token pagination); UUID double-encoding when uuid starts with `/` or contains `//`; filter logic (topic contains "Prayer" OR "Dawn" OR "Family Altar" OR "Global" OR "Open"; participant_count > 5; start_time after Asia/Manila midnight of current day).
- `workers/outbox_worker.py` — APScheduler-driven drainer that reads `outbox` rows with `status=pending` and `run_at <= now`, dispatches by `event_type`, applies at-least-once retry (max 3 attempts → dead_letter), logs to `job_runs`.
- New `automation_rules` seed rows and corresponding `outbox` producer hooks for:
  - Sunday attendance notifiers: 09:30 (8AM service), 12:00 (10AM service), 17:00 (3PM service).
  - Wednesday 21:00 Powerhouse attendance notifier.
  - Daily 23:00 Zoom Morning Prayer attendance pull-and-ingest.
  - Newcomer intake notify (triggered by S13 `POST /public/newcomer` → `automation_rules`).
  - Community-report submission notify (triggered by S22 community-report save).
- Admin settings CRUD for integration credentials: `google_chat_webhook_url`, `zoom_account_id`, `zoom_client_id`, `zoom_client_secret`, `zoom_host_email`, `gmail_smtp_host`, `gmail_smtp_port`, `gmail_smtp_user`, `gmail_smtp_password`, `gmail_from_address`, `gmail_notification_recipients`.
- Frontend: **Integrations** sub-page under Settings (admin only) to configure credentials + test + view recent job_runs for these jobs.
- Google Chat message format: plain text with bold/newline formatting (Card v1 not required; structured text is sufficient).
- Gmail HTML template: close reproduction of n8n template (LNC logo header, Session Details card, Database Match card, footer compliance notice), implemented as a Python string template (Jinja2 or f-string).
- `routers/integrations.py` — admin endpoints to test Google Chat, test Gmail, trigger Zoom pull manually, list recent outbox rows for integration event_types.

### Out of scope
- Finance reports (Liquidation/BRF inside Gforms AI V2) — excluded per n8n-analysis.md.
- IT/Network workflows (Speedtest, MikroTik) — excluded.
- Per-community Mattermost channel routing (replaced wholesale by single Google Chat channel).
- Google Sheets / Google Drive (no longer needed; all state lives in Seraphim DB).
- Gotenberg/PDF generation (finance scope).
- Zoom webhooks (`meeting.ended` push) — S19 scope; this sprint polls on schedule.
- Community-report form frontend — S22 scope; this sprint only wires the notification leg.
- Name-matching implementation — S22 scope; this sprint calls `NameMatchService` from S22.
- New Friend V2 data-entry frontend — S13 scope; this sprint only wires the Google Chat/Gmail notify triggered by S13.
- The visual rules-engine admin UI — S17 scope; this sprint seeds automation_rules rows via migration.

---

## 3. Data model changes

### 3.1 `admin_settings` keys — consumed by S18, seeded by S16

**Ownership change (design doc 2026-06-22):** All `admin_settings` keys for integrations are seeded by S16's migration (`s16_job_runs_and_settings_engine`). S18 does **not** seed any keys and does **not** call `dynamic_settings.get_str(...)` directly. S18 reads credentials exclusively through `settings_service.get(db, key)` at call time, so credential changes take effect immediately without restart.

Keys S18 reads (all seeded by S16 — see S16 §3.3 for full table):

| key | sensitive | S18 usage |
|---|---|---|
| `google_chat_webhook_url` | yes | `google_chat.py` webhook poster |
| `zoom_account_id` | no | Zoom S2S token exchange |
| `zoom_client_id` | no | Zoom S2S token exchange |
| `zoom_client_secret` | yes | Zoom S2S token exchange (Fernet-decrypted by settings_service) |
| `zoom_host_email` | no | `GET /report/users/{host}/meetings` |
| `gmail_smtp_host` | no | SMTP connection |
| `gmail_smtp_port` | no | SMTP connection |
| `gmail_smtp_user` | yes | SMTP AUTH |
| `gmail_smtp_password` | yes | SMTP AUTH (Fernet-decrypted by settings_service) |
| `gmail_from_address` | no | From header |
| `gmail_notification_recipients` | no | TO list |
| `gmail_notification_cc` | no | CC list |

**Zoom bearer token cache:** stored in-process on `ZoomClient` instance (not DB); re-fetched when `expires_at - 60s` is past. No `admin_settings` key needed.

**Missing integration behaviour:** If `settings_service.get(db, "google_chat_webhook_url")` returns `""`, the service logs a warning and writes `status="failed"` / `detail="google_chat_webhook_url not configured"` to `job_runs`. Other jobs continue unaffected.

### 3.2 New `outbox` event_type values (seeds only — no schema change)

The `outbox` table is created by S17. S18 uses these `event_type` strings:

| event_type | produced by | consumed by |
|---|---|---|
| `google_chat.attendance_summary` | APScheduler jobs (notifiers) | `outbox_worker.py` → `google_chat.py` |
| `google_chat.newcomer_notify` | S13 newcomer intake handler | `outbox_worker.py` → `google_chat.py` |
| `google_chat.community_report_notify` | S22 community-report save handler | `outbox_worker.py` → `google_chat.py` |
| `gmail.attendance_summary` | APScheduler jobs (notifiers) | `outbox_worker.py` → `gmail.py` |
| `gmail.newcomer_notify` | S13 newcomer intake | `outbox_worker.py` → `gmail.py` |
| `zoom.morning_prayer_pull` | APScheduler job (nightly 23:00) | `outbox_worker.py` → `zoom.py` + S22 matching |

### 3.3 New `automation_rules` seed rows

Owner: S17 schema. S18 migration seeds six rows (admin can edit/disable from S17 UI post-go-live):

| name | trigger | action_type | schedule (cron) | notes |
|---|---|---|---|---|
| `sunday_8am_notify` | `scheduled` | `google_chat.attendance_summary` + `gmail.attendance_summary` | `30 9 * * 0` | 8AM Sunday service summary |
| `sunday_10am_notify` | `scheduled` | same | `0 12 * * 0` | 10AM service |
| `sunday_3pm_notify` | `scheduled` | same | `0 17 * * 0` | 3PM service |
| `powerhouse_notify` | `scheduled` | same | `0 21 * * 3` | Wednesday Powerhouse |
| `zoom_morning_prayer_pull` | `scheduled` | `zoom.morning_prayer_pull` | `0 23 * * *` | Nightly Zoom pull |
| `newcomer_notify` | `event:newcomer.created` | `google_chat.newcomer_notify` + `gmail.newcomer_notify` | (event-driven) | On newcomer intake |

### 3.4 Alembic migration

**File:** `backend/alembic/versions/<rev>_s18_integration_settings_and_seeds.py`

The migration has **no DDL changes** — it only inserts seed rows for the `automation_rules` rows in §3.3. `admin_settings` keys are **not seeded here** (owned by S16). Downgrade deletes automation_rules rows by name.

```python
# Upward: seed automation_rules rows only
# Downward: delete by rule name (safe — leaves other rules intact)
```

**Important:** If S17 has not been applied (automation_rules table does not exist), the migration skips the automation_rules seeds gracefully using `op.get_bind().execute("SELECT to_regclass('automation_rules')")`. In practice, S18 must be sequenced after S17 in `alembic heads`.

---

## 4. Backend

### 4.1 Endpoint table

| METHOD | path | role | request body | response | notes |
|---|---|---|---|---|---|
| `POST` | `/integrations/test-google-chat` | admin | `{"message": str}` | `{"ok": bool, "detail": str}` | Sends a test message to the configured webhook |
| `POST` | `/integrations/test-gmail` | admin | `{"to": str}` | `{"ok": bool, "detail": str}` | Sends a test email |
| `POST` | `/integrations/zoom/pull` | admin | `{"date": str \| null}` | `{"job_run_id": int}` | Manually trigger Zoom pull for a given date (defaults to today); enqueues outbox row |
| `GET` | `/integrations/outbox` | admin | `?event_type=&status=&limit=50&offset=0` | `OutboxListResponse` | Recent outbox rows for integration event_types; filters by type/status |
| `GET` | `/integrations/job-runs` | admin | `?job_name=&limit=20` | `JobRunListResponse` | Recent `job_runs` for integration jobs |
| ~~`GET /integrations/settings`~~ | ~~admin~~ | — | — | **Removed** — use `GET /settings?category=integrations` (owned by S16) |
| ~~`PUT /integrations/settings`~~ | ~~admin~~ | — | — | **Removed** — use `PUT /settings/{key}` (owned by S16, includes Fernet encryption + audit) |

All endpoints under `/integrations` are gated by `check_setup_complete` and `require_admin`.

### 4.2 Services

#### `backend/app/services/google_chat.py` (CREATE)

```python
# Public interface
async def post_message(text: str, *, webhook_url: str | None = None) -> bool:
    """POST a text message to the Google Chat incoming webhook.
    Falls back to dynamic_settings.get_str('google_chat_webhook_url').
    Returns True on HTTP 2xx. Raises GoogleChatError on failure.
    """

async def post_attendance_summary(
    event_title: str,
    service_label: str,
    attendance_count: int,
    unique_count: int,
    top_names: list[str],
    *,
    webhook_url: str | None = None,
) -> bool:
    """Format and post an attendance summary card."""
```

**Message format (Google Chat simple text card):**

```
*Seraphim Attendance Report — {event_title}*
Service: {service_label}
Total check-ins: {attendance_count}
Unique attendees: {unique_count}
──────────────────────
{top_names_list or "(details in Seraphim)"}
```

Uses `httpx.AsyncClient` (not `requests`). Timeout: 10s. Raises `GoogleChatError(message, status_code)` on non-2xx. The outbox worker catches and increments `attempts`.

No credentials needed on the server side beyond the webhook URL (incoming webhooks are URL-authenticated by Google Chat).

#### `backend/app/services/gmail.py` (CREATE)

```python
async def send_html_email(
    to: list[str],
    subject: str,
    html_body: str,
    *,
    cc: list[str] | None = None,
    from_address: str | None = None,
) -> bool:
    """Send HTML email via SMTP STARTTLS using admin_settings credentials.
    Runs smtplib in a thread executor (asyncio.get_event_loop().run_in_executor)
    to avoid blocking the event loop.
    """

def render_attendance_email(
    topic: str,
    start_time: str,
    end_time: str,
    duration_minutes: int,
    attendees_in_out: int,
    match_summary: str,
) -> str:
    """Render the HTML email body matching the n8n Morning Prayer template.
    Returns a self-contained HTML string.
    """
```

**Template structure** (faithful reproduction of the n8n `Send a message` node body):
- Header: LNC logo banner + subtitle "Information Technology Ministries / Data Analytics - Attendance Monitoring".
- Section: "Session Details" card (Start Time, End Time, Duration, Attendees In/Out).
- Section: "Database Match" card (`match_summary` plain text, `white-space: pre-wrap`).
- Footer: GDPR/RA10173 compliance notice + contact email.
- All colors in inline CSS only (email-safe; no external stylesheets except Google Fonts `Inter`).

SMTP implementation: `smtplib.SMTP(host, port)` → `starttls()` → `login(user, password)` → `sendmail`. Run in `asyncio.to_thread`. Timeout: 30s. Raises `GmailError` on SMTP exceptions.

#### `backend/app/services/zoom.py` (CREATE)

```python
class ZoomClient:
    """Zoom Server-to-Server OAuth client.
    
    Credentials stored in admin_settings:
      zoom_account_id, zoom_client_id, zoom_client_secret, zoom_host_email.
    Bearer token is cached in-process; refreshed when < 60s from expiry.
    """

    async def get_bearer_token(self) -> str:
        """POST https://zoom.us/oauth/token?grant_type=account_credentials&account_id={id}
        with Basic auth (client_id:client_secret). Caches token and expires_at.
        Returns Bearer string.
        """

    async def get_meetings_today(
        self,
        host_email: str | None = None,
        *,
        date_override: str | None = None,
    ) -> list[dict]:
        """GET /report/users/{host}/meetings?from={yesterday}&to={today} (Asia/Manila TZ).
        Returns list of meeting dicts.
        Applies topic filter: topic must contain one of
          ("Prayer", "Dawn", "Family Altar", "Global", "Open").
        Applies participant_count filter: participants_count > 5.
        Applies time filter: start_time > Asia/Manila midnight of current day.
        Returns the LAST occurrence per topic (n8n Limit node behavior).
        """

    async def get_meeting_participants(
        self, meeting_uuid: str, page_size: int = 100
    ) -> list[dict]:
        """GET /report/meetings/{uuid}/participants?page_size={n}
        Double-URL-encodes uuid when it starts with '/' or contains '//'.
        Paginates via next_page_token until exhausted.
        Returns flat list of participant dicts [{name, email, user_id, duration, ...}].
        """
```

**UUID encoding logic** (from Zoom API docs and n8n behavior):
```python
def _encode_meeting_uuid(uuid: str) -> str:
    if uuid.startswith("/") or "//" in uuid:
        return urllib.parse.quote(urllib.parse.quote(uuid, safe=""), safe="")
    return urllib.parse.quote(uuid, safe="")
```

**Recurring-occurrence handling:** the n8n workflow calls `GET /report/users/{host}/meetings` with `from` = yesterday (Manila) and `to` = today (Manila). The `Limit` node keeps only the last item. This sprint replicates: fetch all meetings for the window, apply filters, sort by `start_time` descending, take index 0. The UUID from this response is used for the participants call.

#### `backend/app/workers/outbox_worker.py` (CREATE)

This is a new APScheduler job (`IntervalTrigger(seconds=30)`) registered in `app/main.py` lifespan alongside existing workers (or in `queue_consumer.py`'s host). It drains `outbox` rows where `status='pending'` and `run_at <= utcnow()`, processes up to 20 rows per cycle.

```python
async def drain_outbox(session_factory: async_sessionmaker) -> bool:
    """Read up to 20 pending outbox rows due for processing.
    For each row: dispatch by event_type, update status/attempts.
    Returns True if work was done.
    """
```

**Dispatch table:**

| event_type prefix | handler |
|---|---|
| `google_chat.*` | `google_chat.post_message` or type-specific helper |
| `gmail.*` | `gmail.send_html_email` with payload-rendered template |
| `zoom.morning_prayer_pull` | `_handle_zoom_morning_prayer_pull(payload, session)` |

**Retry logic:**
- On success: `status='sent'`.
- On any exception: `attempts += 1`, `last_error = str(e)`.
- If `attempts >= 3`: `status='dead_letter'` (no more retries; admin sees it in `/integrations/outbox?status=dead_letter`).
- All transitions committed in the same transaction as the handler side-effects (for `zoom.morning_prayer_pull`, the `participants` upserts and the outbox update are in one transaction; Google Chat/Gmail are best-effort — committed before the HTTP call so a restart doesn't re-send).

#### `_handle_zoom_morning_prayer_pull` (inside `outbox_worker.py`)

This is the most complex handler. It runs when `event_type='zoom.morning_prayer_pull'`.

**Algorithm:**
1. Instantiate `ZoomClient` (reads creds from `dynamic_settings`).
2. Call `zoom_client.get_meetings_today(date_override=payload.get("date"))`.
3. For each qualifying meeting (usually 1–2: Morning Prayer + optionally LY Breaking Dawn):
   a. Resolve or create the `events` row for today's occurrence:
      - Look up `events` where `event_type='Prayer Meeting'` AND `occurrence_date=today` (Manila) AND `title ILIKE '%Prayer%'` (or the Zoom topic). Use `title=f"Prayer {weekday} {MMDDYY}"` from the n8n `Edit Fields1` pattern.
      - If not found: create a new `events` row (title derived from topic + Manila date, `event_type='Prayer Meeting'`, `session_time=NULL`, `source='zoom_auto'`). Log to `job_runs`.
   b. Call `zoom_client.get_meeting_participants(meeting_uuid)`.
   c. Aggregate participants: de-duplicate by `user_id` (Zoom can return multiple in/out entries per person). Keep the row with max `duration`.
   d. For each participant: call S22's `NameMatchService.match(raw_name=p["name"], email=p.get("email"), event_id=event.id)`.
      - Returns `MatchResult(contact_id, confidence, method)` or `None` (unmatched → queued in `name_match_review_queue` by S22).
      - On match: upsert `participants(contact_id, event_id, status='attended', source='zoom', created_at=now)` with `ON CONFLICT(event_id, contact_id) DO NOTHING`.
   e. Collect `matched_count`, `unmatched_names`, `total_participants`.
4. Build `match_summary` string (same format as n8n Comparison Agent output):
   ```
   Added on Seraphim:
   name 1
   name 2
   
   Not Added (review queue):
   name A
   name B
   ```
5. Produce two outbox rows: `google_chat.attendance_summary` and `gmail.attendance_summary` with the match_summary in `payload`.
6. Log a `job_runs` row: `job_name='zoom_morning_prayer_pull'`, `detail={"meeting_count": n, "matched": m, "unmatched": u}`.

**Idempotency:** the `UNIQUE(event_id, contact_id)` constraint on `participants` absorbs re-runs. A second run of the same Zoom pull for the same day produces `ON CONFLICT DO NOTHING` — no duplicates.

#### APScheduler job registrations (modify `backend/app/main.py` or `queue_consumer.py`)

Add to the `lifespan` (or APScheduler config in S16's scheduler host):

```python
from apscheduler.triggers.cron import CronTrigger

scheduler.add_job(
    enqueue_attendance_notifier,
    CronTrigger(day_of_week="sun", hour=9, minute=30, timezone="Asia/Manila"),
    id="sunday_8am_notify",
    kwargs={"service_label": "8AM"},
)
scheduler.add_job(
    enqueue_attendance_notifier,
    CronTrigger(day_of_week="sun", hour=12, minute=0, timezone="Asia/Manila"),
    id="sunday_10am_notify",
    kwargs={"service_label": "10AM"},
)
scheduler.add_job(
    enqueue_attendance_notifier,
    CronTrigger(day_of_week="sun", hour=17, minute=0, timezone="Asia/Manila"),
    id="sunday_3pm_notify",
    kwargs={"service_label": "3PM"},
)
scheduler.add_job(
    enqueue_attendance_notifier,
    CronTrigger(day_of_week="wed", hour=21, minute=0, timezone="Asia/Manila"),
    id="powerhouse_notify",
    kwargs={"service_label": "Powerhouse"},
)
scheduler.add_job(
    enqueue_zoom_pull,
    CronTrigger(hour=23, minute=0, timezone="Asia/Manila"),
    id="zoom_morning_prayer_pull",
)
```

`enqueue_attendance_notifier(service_label)`: queries `events` for today's event matching the service_label/session_time, computes attendance counts from `participants`, writes two `outbox` rows (`google_chat.attendance_summary`, `gmail.attendance_summary`). Falls back gracefully if no active event for that service time (logs warning to `job_runs`, does not post).

`enqueue_zoom_pull()`: writes one `outbox` row (`zoom.morning_prayer_pull`, `payload={"date": today_manila_str}`).

### 4.3 Business rules and edge cases

**Google Chat:**
- If `google_chat_webhook_url` is empty: `post_message` raises `GoogleChatConfigError` immediately (no HTTP attempt). The outbox worker logs the error and increments `attempts` as normal — will hit dead_letter after 3 attempts. Admin sees this in the integrations page.
- Message length: Google Chat webhooks reject payloads > 4,096 bytes for text fields. Truncate `top_names` list to 50 entries max; use `"(... and N more)"` suffix.
- Rate limit: Google Chat incoming webhooks are rate-limited at ~1 msg/second/webhook. The outbox drainer processes at most 20 rows per 30s cycle so this is well within limits.

**Gmail:**
- If SMTP credentials are empty: raises `GmailConfigError`. Same dead-letter behavior.
- `gmail_notification_recipients` empty → skip Gmail send (no error; log debug). Useful during testing when only Google Chat is configured.
- All SMTP calls run in `asyncio.to_thread` to avoid blocking the event loop. Thread timeout: 30s.
- The `render_attendance_email` function must embed all styles inline (no `<style>` block) for maximum email-client compatibility. Exception: the current n8n template uses a `<style>` block — keep it as-is for fidelity; most modern clients render it.

**Zoom:**
- Bearer token: cached on `ZoomClient._token` / `_expires_at`. If two concurrent outbox rows trigger the handler simultaneously, both will try to refresh. Add a `threading.Lock` / `asyncio.Lock` around the refresh to prevent double-calls. Practical risk is low (zoom pull is one row/day) but must be correct.
- `participants_count > 5` filter: match n8n exactly. Do not ingest zero-attendance meetings or test calls.
- Topic filter (OR logic): `any(kw in meeting["topic"] for kw in ("Prayer", "Dawn", "Family Altar", "Global", "Open"))`. Case-sensitive per n8n `caseSensitive: true`. This matches the n8n `Prayer Meeting Filters` node.
- Time filter: `meeting["start_time"] > Asia/Manila midnight of current calendar day`. Convert Zoom UTC ISO times to Manila before comparison.
- Per-participant de-duplication: Zoom report API can return multiple entries per `user_id` (join/leave cycles). Collapse by `user_id`, keeping the entry with the highest `duration`. If `user_id` is absent (external/phone participants), collapse by name (case-insensitive).
- `page_size=100` is the Zoom API maximum for `/report/meetings/{uuid}/participants`. Paginate via `next_page_token` until the field is absent or empty.
- On `404` from Zoom (meeting not found / too old): log `job_runs` warning, mark outbox `sent` (treat as "no meetings today") rather than retrying.
- On `401` from Zoom: refresh bearer token and retry once before incrementing `attempts`.

**Attendance notifiers:**
- Notifiers query `participants` for the current day's events matching the service label. "Current day" = Asia/Manila calendar day at cron fire time.
- If the active event for that service time has zero participants: post "0 attendees checked in so far" (do not skip entirely — the n8n workflow always posts).
- `unique_count` = `SELECT COUNT(DISTINCT contact_id) FROM participants WHERE event_id=... AND status='attended'`.
- `total_count` = `SELECT COUNT(*) FROM participants WHERE event_id=... AND status='attended'` (some contacts may have multiple sources — `ON CONFLICT DO NOTHING` prevents true duplicates, so these should be equal; but keep both for reporting accuracy).

**Newcomer notify (triggered from S13):**
- S13's `POST /public/newcomer` handler, after creating the `Contact`, calls `automation_service.emit("newcomer.created", payload={...})` (S17 pattern).
- S18 registers a handler for `event_type='google_chat.newcomer_notify'` in the outbox dispatching that formats: `*New Friend: {first_name} {last_name}*\nPhone: {phone}\nEmail: {email}\nInvited By: {invited_by or "–"}`.
- Also produces `gmail.newcomer_notify` row if `gmail_notification_recipients` is set.

**Community-report notify (triggered from S22):**
- S22's community-report save handler emits `event_type='google_chat.community_report_notify'` into the outbox.
- The outbox worker handler formats: `*Community Report Submitted*\nSubmitted by: {leader_name}\nZone: {zone}\nAttendees: {count}\nTopics: {topics}`.

### 4.4 File-by-file plan

| File | Action | Key symbols |
|---|---|---|
| `backend/app/services/google_chat.py` | CREATE | `GoogleChatClient`, `post_message(text, webhook_url)`, `post_attendance_summary(...)`, `GoogleChatError`, `GoogleChatConfigError` |
| `backend/app/services/gmail.py` | CREATE | `GmailClient`, `send_html_email(to, subject, html_body, cc, from_address)`, `render_attendance_email(...)`, `GmailError`, `GmailConfigError` |
| `backend/app/services/zoom.py` | CREATE | `ZoomClient`, `get_bearer_token()`, `get_meetings_today(host_email, date_override)`, `get_meeting_participants(uuid, page_size)`, `_encode_meeting_uuid(uuid)`, `ZoomError` |
| `backend/app/workers/outbox_worker.py` | CREATE | `drain_outbox(session_factory)`, `_dispatch_outbox_row(row, session)`, `_handle_zoom_morning_prayer_pull(payload, session)`, `_handle_google_chat(row, session)`, `_handle_gmail(row, session)` |
| `backend/app/routers/integrations.py` | CREATE | `router = APIRouter(prefix="/integrations", tags=["integrations"])`, all endpoints in §4.1 |
| `backend/app/config.py` | MODIFY | Add `get_google_chat_webhook_url()`, `get_zoom_account_id()`, `get_zoom_client_id()`, `get_zoom_client_secret()`, `get_zoom_host_email()`, `get_gmail_smtp_host()`, `get_gmail_smtp_port()`, `get_gmail_smtp_user()`, `get_gmail_smtp_password()`, `get_gmail_from_address()`, `get_gmail_notification_recipients()`, `get_gmail_notification_cc()` methods on `DynamicSettings` |
| `backend/app/main.py` | MODIFY | Import and include `integrations.router`; register `drain_outbox` job with APScheduler (IntervalTrigger 30s) and the five cron notifier jobs (§4.2). Requires S16's APScheduler host to be in place. |
| `backend/alembic/versions/<rev>_s18_integration_settings_and_seeds.py` | CREATE | Seed `admin_settings` keys + `automation_rules` rows |
| `backend/requirements.txt` | MODIFY | Ensure `httpx>=0.27` (already likely present), `apscheduler>=3.10` (S16 dep), `pytz>=2024` (timezone handling for Manila). No new major deps needed. |

---

## 5. Frontend

### 5.1 Pages / routes

| Route | Component | Guard | Purpose |
|---|---|---|---|
| `/settings/integrations` | `IntegrationsPage` | AdminRoute | Configure Google Chat, Gmail, Zoom credentials; test; view recent outbox + job_runs |

Add to `frontend/src/App.tsx`: `<Route path="/settings/integrations" element={<AdminRoute><IntegrationsPage /></AdminRoute>} />`.

### 5.2 Components

#### `frontend/src/pages/IntegrationsPage.tsx` (CREATE)

Tabs (or accordion sections — mobile-first):

**Tab 1: Google Chat**
- Input field: `google_chat_webhook_url` (type=`url`, placeholder `https://chat.googleapis.com/v1/spaces/...`).
- Save button → `PUT /integrations/settings`.
- Test button → `POST /integrations/test-google-chat` with `{"message": "Seraphim test message"}`. Shows toast success/error.
- Status indicator: green/red badge based on last test result.

**Tab 2: Gmail**
- Fields: `gmail_smtp_host`, `gmail_smtp_port` (number), `gmail_smtp_user`, `gmail_smtp_password` (type=password, with show/hide toggle), `gmail_from_address`, `gmail_notification_recipients` (textarea, one per line or comma-separated), `gmail_notification_cc`.
- Save + Test (sends test email to `gmail_smtp_user`).

**Tab 3: Zoom**
- Fields: `zoom_account_id`, `zoom_client_id`, `zoom_client_secret` (password + toggle), `zoom_host_email`.
- Save button.
- Manual Pull button → `POST /integrations/zoom/pull` → shows `job_run_id` in toast.

**Tab 4: Recent Activity**
- `GET /integrations/outbox?limit=50` — table: event_type, status, attempts, last_error, run_at.
- `GET /integrations/job-runs?job_name=zoom_morning_prayer_pull,sunday_8am_notify,...&limit=20` — table: job_name, started_at, status, detail JSON preview.
- Manual refresh button. Auto-refresh every 30s while tab is open (using `useQuery` with `refetchInterval`).

#### TanStack Query keys

```typescript
['integrations', 'settings']                          // GET /integrations/settings
['integrations', 'outbox', { status, event_type }]    // GET /integrations/outbox
['integrations', 'job-runs', { job_name }]            // GET /integrations/job-runs
```

Mutations: `PUT /integrations/settings`, `POST /integrations/test-google-chat`, `POST /integrations/test-gmail`, `POST /integrations/zoom/pull` — all use plain `async` handlers calling `api.*` then `toast.success/error`.

#### Navigation

Add "Integrations" link to the Settings nav (under the existing settings menu in `BottomNav` or `SettingsPage` link list, admin-only). Exact location: add a `<Link to="/settings/integrations">` entry in the settings links card on `SettingsPage.tsx` (after the Users link).

### 5.3 UX details

**Loading state:** `LoadingState` component from `components/ui/StateViews.tsx` while initial settings fetch is in flight.

**Empty state:** if no outbox rows, show `EmptyState` with text "No integration activity yet."

**Error state:** `ErrorState` with retry on query failure.

**Sensitive field masking:** settings response from the API returns `"***"` for non-empty sensitive fields. The frontend shows a placeholder password field with value `"***"` and a "Change" button that clears the field for editing. On save, if the field value is still `"***"`, omit it from the PUT payload (do not overwrite with the mask string).

**Mobile-first layout:** tabs collapse to an accordion on screens < 640px (`sm:hidden` to hide tab bar + show accordion headers).

**Dark mode:** all inputs use `bg-card text-foreground border-border` tokens. Status badges use `bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400` for success and `bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400` for error.

**Toast on save:** `toast.success("Integration settings saved.")`. On error: `toast.error(err.response?.data?.detail || "Failed to save settings.")`.

### 5.4 File-by-file

| File | Action |
|---|---|
| `frontend/src/pages/IntegrationsPage.tsx` | CREATE |
| `frontend/src/App.tsx` | MODIFY — add route `/settings/integrations` |
| `frontend/src/pages/SettingsPage.tsx` | MODIFY — add Integrations link card in the settings nav block |
| `frontend/src/types/index.ts` | MODIFY — add `IntegrationSettings`, `OutboxRow`, `JobRun` types |

---

## 6. Migration / data

No contact or participant data migration in this sprint. The `admin_settings` keys are seeded empty (or with safe defaults like `zoom_host_email = "lightnorthcaloocan@gmail.com"`) by the Alembic migration. The owner must populate `google_chat_webhook_url`, `zoom_account_id`, `zoom_client_id`, `zoom_client_secret`, and Gmail SMTP credentials via the Integrations UI before the scheduled jobs will function.

**Zoom account_id pre-fill:** the account_id `QtCxnKWNTFyqxRN8Y0s3wQ` is visible in the n8n JSON (`Get Bearer Tokens on Zoom` node URL). The migration **can** pre-seed this value as a safe default (it is not a secret; it is the Zoom account identifier). The migration seeds it with a `# TODO confirm with owner` comment.

---

## 7. Acceptance criteria

1. `POST /integrations/test-google-chat` with a valid webhook URL responds `{"ok": true}` and a test message appears in the Google Chat space within 5 seconds.
2. `POST /integrations/test-gmail` with valid SMTP credentials sends a test email and responds `{"ok": true}`.
3. `POST /integrations/zoom/pull` with a valid date (or no date) creates an `outbox` row with `event_type='zoom.morning_prayer_pull'` and returns `{"job_run_id": <int>}`.
4. The outbox drainer processes the zoom pull row: calls Zoom API, matches participants via S22 `NameMatchService`, upserts `participants` rows with `source='zoom'`, marks outbox `sent`, writes a `job_runs` row with `status='completed'`.
5. For a Zoom meeting with 10 participants where 7 are matched by S22 and 3 are unmatched, the unmatched names appear in `name_match_review_queue` (S22) and the summary shows `7 Added / 3 Not Added`.
6. The Sunday 09:30 cron job fires within 60s of 09:30 Asia/Manila on a Sunday; it produces two outbox rows (`google_chat.attendance_summary` + `gmail.attendance_summary`) for the 8AM service; the drainer sends the Google Chat message and Gmail email; both `outbox` rows end with `status='sent'`.
7. The Wednesday 21:00 Powerhouse cron job produces and delivers the same notification pair for Powerhouse.
8. Newcomer intake via S13 `POST /public/newcomer` produces a `google_chat.newcomer_notify` outbox row; the drainer posts the newcomer card to Google Chat.
9. S22 community-report save produces a `google_chat.community_report_notify` outbox row; the drainer posts the community-report summary to Google Chat.
10. Failed Google Chat post (webhook URL wrong / HTTP 400) increments `outbox.attempts`; after 3 attempts the row is `status='dead_letter'`; `GET /integrations/outbox?status=dead_letter` lists it with `last_error`.
11. `GET /integrations/settings` returns sensitive fields as `"***"` (non-empty) or `""` (empty); no plaintext secrets in the response.
12. `PUT /integrations/settings` with field value `"***"` does NOT overwrite the stored setting.
13. Zoom UUID with leading `/` or `//` is double-encoded before the API call; the test asserts the encoded URL is used.
14. Zoom bearer token is cached; a second call within 55 minutes does NOT re-request a new token.
15. Zero-participant meetings (participant_count ≤ 5) are skipped by the Zoom pull.
16. `IntegrationsPage` renders all four tabs (Google Chat, Gmail, Zoom, Recent Activity) and passes `npm run build` + `npm run lint` without errors.
17. Admin can navigate to `/settings/integrations` from the Settings page; non-admin users are redirected.

---

## 8. Test plan

### Backend (pytest)

**File: `backend/tests/test_google_chat.py`**
```python
async def test_post_message_success():
    # Mock httpx.AsyncClient.post to return status 200
    # Assert GoogleChatClient.post_message returns True

async def test_post_message_config_error():
    # With empty webhook URL in dynamic_settings
    # Assert raises GoogleChatConfigError

async def test_post_message_http_error():
    # Mock returns 400
    # Assert raises GoogleChatError with status_code=400
```

**File: `backend/tests/test_gmail.py`**
```python
async def test_send_html_email_success():
    # Mock smtplib.SMTP context manager
    # Assert send_html_email returns True

async def test_send_html_email_config_error():
    # Empty SMTP user/password → GmailConfigError

def test_render_attendance_email_contains_required_sections():
    # Call render_attendance_email with known args
    # Assert output contains "Session Details", "Database Match", "LNC", start_time
```

**File: `backend/tests/test_zoom.py`**
```python
async def test_get_bearer_token_caches():
    # Two calls: assert httpx.post called only once (cache hit on second)

async def test_get_meetings_today_filters_topic():
    # Mock response includes 3 meetings: "Morning Prayer", "Test Meeting", "LY Breaking Dawn"
    # Assert only 2 returned (matching keywords)

async def test_get_meetings_today_filters_participant_count():
    # Meeting with participants_count=3 is excluded (≤5)

async def test_get_meeting_participants_paginates():
    # First page: next_page_token="abc"; second page: no token
    # Assert both pages concatenated in result

def test_encode_meeting_uuid_leading_slash():
    # uuid = "/AbcDef==" → assert double-encoded

async def test_zoom_pull_upserts_participants(db, mock_s22_name_match_service):
    # Create event in DB; mock Zoom returns 3 participants; S22 matches 2
    # After _handle_zoom_morning_prayer_pull:
    #   participants table has 2 rows with source='zoom'
    #   name_match_review_queue has 1 row (unmatched)

async def test_zoom_pull_idempotent(db, mock_s22):
    # Run _handle_zoom_morning_prayer_pull twice with same data
    # participants table still has same 2 rows (ON CONFLICT DO NOTHING)
```

**File: `backend/tests/test_outbox_worker.py`**
```python
async def test_drain_outbox_sends_google_chat(db, mock_google_chat):
    # Seed pending outbox row event_type='google_chat.attendance_summary'
    # Call drain_outbox; assert mock_google_chat.post_message called
    # Assert outbox row status='sent'

async def test_drain_outbox_retries_on_failure(db, mock_google_chat_fails):
    # mock raises GoogleChatError on first call
    # After first drain: attempts=1, status='pending'
    # After three drains: attempts=3, status='dead_letter'

async def test_drain_outbox_skips_run_at_future(db):
    # Seed row with run_at = utcnow() + 1hour
    # drain_outbox processes nothing (returns False)
```

**File: `backend/tests/test_integrations_router.py`**
```python
async def test_test_google_chat_admin_only(client, volunteer_token):
    resp = await client.post("/integrations/test-google-chat", 
                             headers={"Authorization": f"Bearer {volunteer_token}"},
                             json={"message": "test"})
    assert resp.status_code == 403

async def test_test_google_chat_success(client, admin_token, mock_google_chat):
    resp = await client.post("/integrations/test-google-chat",
                             headers={"Authorization": f"Bearer {admin_token}"},
                             json={"message": "test"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

async def test_get_settings_masks_sensitive(client, admin_token, seeded_zoom_creds):
    resp = await client.get("/integrations/settings",
                            headers={"Authorization": f"Bearer {admin_token}"})
    data = resp.json()
    assert data["zoom_client_secret"] == "***"  # non-empty sensitive field masked

async def test_put_settings_ignores_mask(client, admin_token, seeded_zoom_creds):
    # PUT with zoom_client_secret="***" should NOT overwrite
    # GET after PUT still returns masked value (original secret intact)
    ...

async def test_zoom_pull_enqueues_outbox(client, admin_token, db):
    resp = await client.post("/integrations/zoom/pull",
                             headers={"Authorization": f"Bearer {admin_token}"},
                             json={})
    assert resp.status_code == 200
    assert "job_run_id" in resp.json()
    # Check outbox row exists
    ...
```

### Frontend (vitest)

**File: `frontend/src/pages/IntegrationsPage.test.tsx`**
```typescript
test('renders all four tabs', () => {
  render(<IntegrationsPage />);
  expect(screen.getByText('Google Chat')).toBeInTheDocument();
  expect(screen.getByText('Gmail')).toBeInTheDocument();
  expect(screen.getByText('Zoom')).toBeInTheDocument();
  expect(screen.getByText('Recent Activity')).toBeInTheDocument();
});

test('save button calls PUT /integrations/settings', async () => {
  // Mock api.put
  // Click Save; assert api.put called with correct payload
});

test('sensitive field with mask value omitted from PUT payload', async () => {
  // Set field to "***"; click Save; assert api.put payload does NOT include that key
});

test('test button shows success toast on OK response', async () => {
  // Mock api.post returns {ok: true}
  // Click Test; assert toast.success called
});
```

Build and lint commands (frontend):
```bash
npm run build   # must pass with 0 errors
npm run lint    # must pass with 0 warnings on new files
```

---

## 9. Rollout / rollback / risks

### Rollout

1. Apply Alembic migration (seeds only; no DDL; instant, zero downtime).
2. Deploy backend + frontend.
3. In the Integrations UI: enter Google Chat webhook URL → click Test (verify message appears in space).
4. Enter Gmail SMTP credentials → click Test (verify email arrives).
5. Enter Zoom credentials → click "Trigger Manual Pull" for yesterday's date → verify participants appear in Seraphim with `source='zoom'`.
6. Monitor `/integrations/outbox` and `/integrations/job-runs` for any dead-letter rows.
7. On first natural Sunday after deploy, verify 09:30/12:00/17:00 cron fires produce Google Chat and Gmail notifications.

### Rollback

- If Google Chat / Gmail fail: set credentials to empty in the Integrations UI — the drainer will hit `GoogleChatConfigError` / `GmailConfigError`, and after 3 attempts the rows go dead_letter. No disruption to other app functions.
- If the Zoom pull causes DB errors: check `job_runs` and `name_match_review_queue`; participants are idempotently inserted so no data corruption.
- Full rollback (code): revert the release; run `alembic downgrade -1` to remove the seeded admin_settings rows.

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Google Chat webhook URL not yet provided by owner | High (pending artifact) | Integration ships but jobs silently no-op (config error, dead-letter) until URL is provided. S16/S17 must be stable first. |
| Zoom S2S creds expired/revoked | Medium | Bearer token refresh auto-handles expiry. If client_id/secret is wrong → 401 → dead_letter → admin alert via integrations page. |
| S22 not landed when S18 ships | High (dependency) | `_handle_zoom_morning_prayer_pull` can fail gracefully: if `NameMatchService` import fails, fall back to logging all names to `name_match_review_queue` directly with `status='pending'` (S22 table exists as part of S22 migration). However, S22 must be shipped first per the dependency graph. |
| Gmail App Password vs OAuth | Medium | Gmail SMTP with App Password is simpler and sufficient for single-sender use; no OAuth refresh token to manage. If the church migrates to Google Workspace policies that disable App Passwords, switch to Gmail API (scope: `gmail.send`). Document this risk in the integrations settings page as a help text. |
| APScheduler timezone handling | Low | All jobs use `timezone="Asia/Manila"` (`Asia/Manila` = UTC+8, no DST). Test with a Manila-timezone `datetime` to confirm cron fires at the correct UTC time. |
| Zoom UUID double-encoding regression | Low | Covered by unit test `test_encode_meeting_uuid_leading_slash`. |

---

## 10. Open questions & pending owner artifacts

### Pending owner artifacts (required before go-live, not blocking spec)

1. **Google Chat webhook URL** — the single central space webhook URL. This is the only thing needed to activate Google Chat notifications.
2. **Zoom S2S credentials** — `client_id` and `client_secret` for the Server-to-Server OAuth app in the `QtCxnKWNTFyqxRN8Y0s3wQ` account. The `account_id` is already known from the n8n JSON.
3. **Gmail App Password** — an App Password (not the Google account password) generated for `john.atienza@lightnc.org` or the data-analytics Gmail account used in the n8n workflow (sender: `IT - Data Analytics Ministry`).
4. **Confirm Zoom host email** — n8n uses `lightnorthcaloocan@gmail.com` as the Zoom host. Confirm this is correct for the new Zoom S2S app.
5. **Confirm Gmail recipients** — n8n sends to `mannyscorrea@gmail.com`, `lnc.cristinacorrea@gmail.com` with CC `john.atienza@lightnc.org`, `mcv@lightnc.org`, `sophia.abeleda@lightnc.org` (Morning Prayer flow). Confirm if this should be configurable via the integrations UI or hardcoded.

### Open questions for master reconciliation

1. **S17 automation_rules schema lock** — the migration seeds `automation_rules` rows. The `trigger`, `conditions`, `actions` JSONB shapes must match exactly what S17 specifies. If S17 changes the schema after S18 is written, the seed migration must be patched. The master must confirm S17's `automation_rules` schema before S18 implements.
2. **S22 `NameMatchService` interface** — this sprint calls `NameMatchService.match(raw_name, email, event_id)` and expects a `MatchResult(contact_id, confidence, method)` or `None`. If S22 uses a different function signature, `outbox_worker.py` must be patched. The master should lock this interface in S22's spec.
3. **Event auto-creation by Zoom pull** — when no `events` row exists for today's prayer meeting, should the Zoom pull create one? The n8n flow did (via CiviCRM `Event.create`). This sprint does the same (creates an `events` row with `title` derived from Zoom topic + Manila date, `event_type='Prayer Meeting'`, `source='zoom_auto'`). The master should confirm this behavior vs. requiring the event to be pre-created via S04.
4. **`outbox.run_at` semantics for scheduled jobs** — the cron jobs write outbox rows with `run_at = utcnow()` (immediate). Should the APScheduler cron jobs write rows into outbox and let the drainer dispatch, or should they call the notifier services directly? This sprint chooses the outbox path (for at-least-once and auditability) but the master should confirm this is the S17-canonical pattern.
5. **Zoom Bearer token caching across restarts** — current design loses the cached token on restart and re-fetches. This is acceptable (token acquisition takes ~200ms). No DB persistence needed.

---

## Cross-sprint dependencies and touchpoints

- **S16** must be live for APScheduler host and `job_runs` table.
- **S17** must be live for `outbox` and `automation_rules` tables. S18's migration runs after S17's.
- **S22** must be live for `NameMatchService` and `name_match_review_queue`. S18's Zoom pull handler imports and calls S22 code.
- **S13** calls `automation_service.emit("newcomer.created", payload)` which S18 consumes — S13 must ship first or the newcomer notify is simply not produced (no error).
- **S04** owns `events` table; S18 creates `events` rows for auto-generated Zoom prayer events. S18 must respect the `events` schema from S04 (including `event_type`, `session_time`, `recurring_series_id` — there is no `session_type` or `service_time` column, per CN-15).
- **S01** owns `participants` table; S18 inserts rows with `source='zoom'`. The `source` enum in S01 must include `'zoom'` — per the canonical model this is confirmed (§2.1 of 00-MASTER specifies `source` includes `zoom`).
