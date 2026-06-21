I have everything required for the reconnaissance report.

---

# Project Seraphim Backend — CiviCRM Decoupling Reconnaissance

This app treats CiviCRM as the source of truth in three ways: (1) it **pulls** members/events into local mirror tables `civicrm_members` / `civicrm_events`, (2) it **pushes** confirmed attendance back to CiviCRM via a worker state machine, and (3) the local PKs (`contact_id`, `event_id`) are **CiviCRM's own IDs** wired into FK constraints across the whole schema. Below is the full map.

---

## (A) Every CiviCRM-coupled file/function — remove or rewire

### Pure-CiviCRM, can be deleted entirely
- **`backend/app/services/civicrm.py`** — the whole `CiviCRMClient`. Key methods: `_rest_url` (civicrm.py:44, hardcodes the WordPress plugin path `/wp-content/plugins/civicrm/civicrm/extern/rest.php`), `_call` (civicrm.py:69, API v3 envelope with `api_key`+`site_key`), `sync_members` (civicrm.py:95), `sync_events` (civicrm.py:110), `push_attendance` (civicrm.py:124), `get_rsvp_list` (civicrm.py:139, currently unused — only referenced by a TODO in attendance.py:108).

### Routers that call CiviCRM (rewire to be native CRUD)
- **`routers/members.py`**
  - `sync_members` endpoint (members.py:118-162) — calls `CiviCRMClient().sync_members()`. **Remove**; replace with native member create/update/list/delete endpoints.
  - Import `from app.services.civicrm import CiviCRMClient` (members.py:13) — remove.
  - `search_members` (members.py:18) and `list_attendees` (members.py:63) are pure local-DB reads — **reusable as-is** once the model is renamed.
- **`routers/events.py`**
  - `sync_events` endpoint (events.py:80-141) — calls `CiviCRMClient().sync_events()`, parses CiviCRM date formats. **Remove**; replace with native event CRUD.
  - Import (events.py:12) — remove.
  - `set_active_event` (events.py:35) error message at events.py:53 says "Sync events from CiviCRM first." — reword.
  - `list_events` (events.py:17), `get_active_event_id` (events.py:29), `set_active` (events.py:35) — **reusable**.
- **`routers/attendance.py`**
  - `push_preview` (attendance.py:57-117) — dry-run diff of "what would push to CiviCRM"; TODO at attendance.py:108 for `get_rsvp_list`. **Remove or repurpose** (no external target anymore).
  - `push_attendance` endpoint (attendance.py:120-154) — flips records to `push_status="queued"` to feed the push worker. **Remove** (no external system to push to).
  - `list_dead_letter` (attendance.py:162) and `retry_dead_letter` (attendance.py:195) — only meaningful for the push pipeline. **Remove** with the push machine.
  - `list_attendance` (attendance.py:19) — **reusable**.
- **`routers/setup.py`**
  - `test_services` (setup.py:83-143) — the connectivity check probes `{civicrm_url}/extern/rest.php?entity=System&action=check` (setup.py:115-134). **Remove the CiviCRM branch.**
  - `create_setup` writes `civicrm_url`, `civicrm_api_key`, `civicrm_site_key` into `admin_settings` (setup.py:192-194) and marks the latter two sensitive (setup.py:216-225). **Remove these three keys.**

### Worker / service coupling
- **`services/queue_manager.py`** — `_process_civicrm_push` (queue_manager.py:53-161) is the entire push state machine (detailed in section B). It is one of four jobs in the worker loop (`run`, queue_manager.py:38). Imports `CiviCRMClient` (queue_manager.py:14). **Remove the job and the call at queue_manager.py:38.** `MAX_PUSH_ATTEMPTS` (queue_manager.py:51) goes with it. `_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup` are CiviCRM-free and **reusable**.
- **`workers/queue_consumer.py`** — logs `civicrm_url` at startup (queue_consumer.py:23). Cosmetic; reword. The consumer host itself is **reusable**.

### Config
- **`config.py`** — `DynamicSettings.get_civicrm_url` (config.py:100), `get_civicrm_api_key` (config.py:103), `get_civicrm_site_key` (config.py:106). **Remove.** No `civicrm` entry in `LegacySettings`, so nothing there.

### Schemas (CiviCRM-named DTOs)
- **`schemas.py`**: `civicrm_url`/`civicrm_api_key`/`civicrm_site_key` on `ServiceTestRequest`/`SetupRequest` (schemas.py:103, 214-216); `civicrm_ok`/`civicrm_message` on `ServiceTestResponse` (schemas.py:109-110); `DeadLetterRecord`/`DeadLetterListResponse`/`DeadLetterRetryResponse` and `PushDiff`/`AttendeeSummary` (the push DTOs around schemas.py:261, 395-416). `MemberResponse` (schemas.py:366, `contact_id`), `EventResponse` (schemas.py:354, `event_id`), `AttendeeResponse` (schemas.py:374) — rewire field names if you re-key.

### Models (the deepest coupling — section D)
- **`models.py`**: `CiviCRMMember` (models.py:66), `CiviCRMEvent` (models.py:77), and every FK pointing at them: `ComprefaceSubject.contact_id` (models.py:93), `Detection.event_id` (models.py:123), `Attendance.contact_id`/`event_id` (models.py:191-194), `Log.event_id` (models.py:241), plus `Attendance.push_status`/`push_attempts`/`last_push_error` (models.py:203-209).

### Tests referencing CiviCRM
`tests/test_civicrm.py`, `tests/test_members_sync.py`, `tests/test_setup_connectivity.py`, `tests/test_attendance.py`, and `tests/conftest.py` all exercise the above and must be rewritten.

---

## (B) Attendance-PUSH mechanism in detail

The push pipeline is the **only** code that writes back to CiviCRM. State lives on `Attendance.push_status` (enum in models.py:203-205) with `push_attempts` and `last_push_error` (models.py:206-209).

**State machine** (`push_status`: `pending | queued | pushed | failed | expired | dead_letter`):

1. **`pending`** — set at attendance creation in three places:
   - `face_pipeline.process_face_crop` for tier-100 auto-logged attendance (face_pipeline.py:144).
   - `task_service._log_attendance` when a task resolves (task_service.py:393).
   - `attendance.retry_dead_letter` reset (attendance.py:226).
2. **`queued`** — two enqueue paths:
   - Manual admin: `POST /attendance/push` flips all `pending` records for an event to `queued` (attendance.py:145-146).
   - Automatic: the worker itself flips `pending`/`failed` records to `queued` before processing (queue_manager.py:84-86).
3. **Worker loop** — `QueueManager.run` (queue_manager.py:28) calls `_process_civicrm_push` every cycle (queue_manager.py:38). It:
   - Bails immediately if `civicrm_url` is unset (queue_manager.py:64-67).
   - Selects up to 10 records where `push_status IN ('pending','failed') AND push_attempts < MAX_PUSH_ATTEMPTS (=3)` (queue_manager.py:72-79).
   - For each: validates `contact_id`+`event_id` present (queue_manager.py:91), then calls `CiviCRMClient.push_attendance(contact_id, event_id)` → CiviCRM `Participant.create` with `status_id="Attended", role_id="Attendee"` (civicrm.py:124-137).
   - On success → **`pushed`** (queue_manager.py:111).
   - On failure (returned-false, exception, or missing IDs) → increment `push_attempts`, store `last_push_error`; if attempts ≥ 3 → **`dead_letter`** (queue_manager.py:95/119/140), else → **`failed`** (queue_manager.py:104/128/149) which will be retried next cycle.
4. **Retry** — two layers: (a) `CiviCRMClient._call` has tenacity retry on transient HTTP (429/500/502/503/timeouts), 3 attempts, exponential backoff (civicrm.py:25-31, 62-68); (b) the worker-level `failed`→retry loop bounded by `MAX_PUSH_ATTEMPTS`.
5. **Dead-letter handling** — admin-only: `GET /attendance/dead-letter` lists them (attendance.py:162), `POST /attendance/dead-letter/{id}/retry` resets `push_status="pending"`, `push_attempts=0`, `last_push_error=None` (attendance.py:226-228).
6. **`expired`** — declared in the enum (models.py:205) but **never set anywhere** in the codebase (dead value).

**Decoupling impact:** The entire B section becomes dead once this app is the source of truth — attendance is already authoritative the moment it's written. Remove `push_status`/`push_attempts`/`last_push_error`, `_process_civicrm_push`, the `/push`, `/push-preview`, `/dead-letter*` endpoints, and `CiviCRMClient.push_attendance`. Note `Log.push_status` (models.py:244) and the analytics CSV export columns (analytics.py:131,144,165,173) also reference it.

---

## (C) Face → Attendance flow in detail

Two entry points feed the **same** pipeline function `face_pipeline.process_face_crop` (face_pipeline.py:36): the RTSP worker and the manual upload endpoint.

**RTSP path** (`workers/rtsp_capture.py`):
1. `main` (rtsp_capture.py:98) loads cameras with `enable_health_check=True` and starts an `FFmpegCapture` per camera (rtsp_capture.py:140-147).
2. `_on_frame` (rtsp_capture.py:68): encodes frame → `ComprefaceClient.detect` to find all face boxes (rtsp_capture.py:73) → spawns `_process_face` per face (rtsp_capture.py:83).
3. `_process_face` (rtsp_capture.py:40): crops the box and **stamps the active event** via `dynamic_settings.get_active_event_id()` (rtsp_capture.py:58), then calls `process_face_crop` (rtsp_capture.py:59).

**Upload path** (`routers/uploads.py`): `POST /uploads/faces` (admin) decodes the image, detects faces, and calls `process_face_crop` with `event_id` from a query param (uploads.py:136-142).

**`process_face_crop` core** (face_pipeline.py:36-183):
1. **Quality gate** — `FaceQualityGate.check` rejects faces <100px or blur-variance <100 (quality_gate.py:11-20); failures persist a `Detection(status="skipped")` (face_pipeline.py:65-77).
2. **Recognition** — `ComprefaceClient.recognize` (compreface.py:107) returns `(subject_id, similarity, tier)`. Tier mapping `_map_tier` (compreface.py:32): `≥0.98 → "100"`, `≥0.91 → "91-99"`, else `below90`/`unknown`, with thresholds from `dynamic_settings` (compreface.py:138-142).
3. **Dedup** — `DedupCache.is_duplicate` (dedup.py:16): 60s window keyed by subject_id, or perceptual 8×8 hash for unknown faces (face_pipeline.py:92).
4. **Subject→member link** — `_find_member_id` (face_pipeline.py:26) queries `ComprefaceSubject.contact_id WHERE compreface_subject_id == subject_id` (face_pipeline.py:29-33). **This single join is how a recognized face becomes a member.** If found, `matched_name` is set to the synthetic string `f"member:{member_id}"` (face_pipeline.py:110).
5. **Detection insert + routing** (face_pipeline.py:112-164):
   - Tier `"100"` → `Detection(status="auto_logged")`; if `member_id` and `event_id` both present and no existing `(contact_id,event_id)` row, insert `Attendance(status="confirmed", push_status="pending")` (face_pipeline.py:127-146). If no active event, it's logged and skipped (face_pipeline.py:147-151).
   - Tier `"91-99"` → `Task(required_approvals=1)`; tier `below90`/`unknown` → `Task(required_approvals=2)` (face_pipeline.py:154-164).
6. **SSE broadcast** to refresh the volunteer queue (face_pipeline.py:168-181).

**Volunteer approval → Attendance** (`services/task_service.py`, exposed by `routers/tasks.py`):
- `confirm_task` (task_service.py:54), `edit_task` (task_service.py:128, sets `matched_name="member:{id}"`), `add_task` (task_service.py:202, also sets `is_enrolled=True`), `admin_override` (task_service.py:337). When `current_approvals >= required_approvals`, the task is `resolved` and `_log_attendance` runs (task_service.py:101/184/259/353).
- `_log_attendance` (task_service.py:364): reads the detection, parses `member:{id}` out of `matched_name` (task_service.py:382-386), and inserts `Attendance(contact_id=member_id, event_id=detection.event_id, status="confirmed", push_status="pending")` + a `Log` row (task_service.py:388-408). **Note:** `contact_id` can be `None` here if `matched_name` wasn't a `member:` string — the Postgres FK and NOT NULL would reject it.

**The enrollment gap (critical for the rewrite):** No endpoint in `app/` ever **creates** a `ComprefaceSubject` row or **sets its `contact_id`**. `queue_manager._process_enrollment` (queue_manager.py:163) only flips an *existing* pending subject to `active` and uploads a sample to CompreFace; `pit.enroll_pit_task` (pit.py:54) and `task_service.add_task` only stamp `detection.matched_name`/`is_enrolled` — they do **not** create the subject↔contact link. The `compreface_subjects` rows (and their `contact_id`) are seeded outside the visible code (DB seed/external script). A native CRM must add a first-class "enroll member" flow that creates `ComprefaceSubject(subject_name, compreface_subject_id, contact_id)`.

---

## (D) The active-event mechanism

- Stored as the `admin_settings` row keyed `active_event_id`, read via `dynamic_settings.get_active_event_id()` (config.py:157-164; returns `None` for null/empty/0).
- **Set/cleared** by `POST /events/set-active` (admin) — validates the event exists in `civicrm_events` (events.py:48-54), upserts the setting, then calls `dynamic_settings.reload` (events.py:56-68).
- **Read** by: `GET /events/active-event-id` (events.py:29), the health endpoint (health.py:74), and crucially the RTSP worker's `_process_face` (rtsp_capture.py:58) which stamps every detection. The worker reloads settings every 60s (`_reload_settings_loop`, rtsp_capture.py:86; `SETTINGS_RELOAD_INTERVAL`, rtsp_capture.py:19) and the queue worker reloads each cycle (queue_manager.py:35), so changing the active event takes effect without restart.
- There is exactly **one** global active event — no per-camera or time-window selection. Detections with no active event get no attendance (tier-100 warning at face_pipeline.py:148).

**Decoupling impact:** Mechanism is sound and CiviCRM-independent except that `set_active_event` validates against `civicrm_events` (events.py:49) — that just becomes the native events table.

---

## (E) Cleanly reusable vs. must-change

**Reusable as-is (CiviCRM-independent):**
- Face pipeline internals: `services/compreface.py`, `services/quality_gate.py`, `services/dedup.py`, `services/face_storage.py`, `services/face_cleanup.py`.
- Worker host + 3 of 4 jobs: `services/queue_manager.py` minus `_process_civicrm_push` (`_process_enrollment`, `_process_expired_tasks`, `_process_face_cleanup` stay).
- RTSP capture (`workers/rtsp_capture.py`), upload pipeline (`routers/uploads.py`).
- Volunteer task lifecycle: `services/task_service.py` (except it parses `member:{id}` and FK-references `CiviCRMMember` at task_service.py:121) and `routers/tasks.py`, `routers/pit.py`, `routers/audit.py`.
- Active-event mechanism (`routers/events.py` set/get/list).
- Auth, settings infra, rate-limiting, SSE, storage router, analytics queries (minus push columns).
- `workers/queue_producer.py` is already a deprecated no-op (queue_producer.py:1-26).

**Must change — the source-of-truth / ID-minting problem:**
- **`contact_id` and `event_id` are CiviCRM's PKs, not ours.** `civicrm_members.contact_id` and `civicrm_events.event_id` are `Integer` PKs **with no autoincrement/sequence** — they're populated by copying CiviCRM's `id` (members.py:129, events.py:93). Once this app mints its own IDs, those PKs must become identity/serial columns (or you adopt surrogate keys). Every dependent FK must follow: `ComprefaceSubject.contact_id` (models.py:93 / migration 84), `Detection.event_id` (models.py:123 / migration 102), `Attendance.contact_id`+`event_id` (models.py:191-194 / migration 124-126), `Log.event_id` (models.py:241 / migration 147). Confirmed in the initial migration `74e9ab60ea7e_initial.py` (lines 35-49, 76-128).
- **Tables/models should be renamed** `CiviCRMMember`→`Member`, `CiviCRMEvent`→`Event` (cosmetic but pervasive; needs an Alembic rename migration — note CLAUDE.md says never edit applied migrations, so add a new one).
- **The `member:{id}` synthetic-name convention** (face_pipeline.py:110, task_service.py:178/252/382, audit.py:27, attendance.py:86, tasks.py:25, pit.py:73) is brittle but CRM-agnostic; it can stay, or be replaced with a proper `Detection.contact_id` FK column when you own the schema.
- **New native write paths required (currently absent):** member create/update/delete, event create/update/delete, and an **enroll-member-face** flow that actually creates `ComprefaceSubject` with `contact_id` + calls CompreFace `add_subject`/`add_example` (the building blocks exist in compreface.py:151-162; only the orchestration/endpoint is missing).
- **Drop entirely:** `push_status`/`push_attempts`/`last_push_error` columns + dead-letter machinery + `/attendance/push*` + `/dead-letter*` + `_process_civicrm_push` + `CiviCRMClient` + civicrm config keys + the CiviCRM branch of setup connectivity. The `expired` push_status value is already dead code.

**One data-integrity note for the migration:** `task_service._log_attendance` (task_service.py:388) can insert `Attendance.contact_id = None` when a resolved task's detection lacks a `member:` name; today the Postgres FK/NOT-NULL would reject it. When you own the schema, decide whether unidentified-but-confirmed attendance is allowed (nullable contact_id) or must be blocked at resolve time.