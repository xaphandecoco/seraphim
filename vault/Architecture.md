# System Architecture

## High-Level Data Flow

```
┌─────────────────┐
│  RTSP Cameras   │  (Tapo C260, RTSP URLs configured in UI)
│  (up to N)      │
└────────┬────────┘
         │
         │ FFmpeg frame capture @ configurable FPS
         ↓
┌──────────────────────────────┐
│  RTSP Worker Container       │  (`seraphim-rtsp-worker`)
│  (app.workers.rtsp_capture)  │  Pulls frames, detects faces,
│                              │  resolves CompreFace matches,
│  - Reads active_event_id     │  maps subject_id → contact_id
│  - Detections table INSERT   │
│  - Redis queue push          │
│  - 30s pHash dedup buffer    │
│  - Tier mapping (confidence) │
│  - Paced by queue size       │
│  - Safe mode respected       │
└──────────────────────────────┘
         │
         │ Detection + matched_name + tier + face snapshot
         ↓
    [Redis Queue]
         │
         │ Async consume (≤1s poll)
         ↓
┌──────────────────────────────┐
│  Queue Worker Container      │  (`seraphim-queue-worker`)
│  (app.workers.queue_consumer)│  Consumes detections,
│                              │  decides action by tier:
│  - Tier 100 (≥0.98)         │    - Tier-100 → auto-log
│    → auto-log to attendance  │    - Tier-91-99 → Task (1 approval)
│    (if active_event_id set)  │    - Below-90 → Task (2 approvals)
│  - Tier-91-99, below-90      │    - Unknown → Task (2 approvals)
│    → create Task record      │
│  - Task linked to Detection  │
│  - SSE broadcast new task    │
└──────────────────────────────┘
         │
         │ Task created + broadcast
         ↓
    [React Frontend]
         │
         │ SSE event stream
         ├─────────────────────────────────────┐
         │ (Optional: fallback polling)         │
         ↓                                      ↓
┌─────────────────────────────┐     ┌──────────────────────────┐
│  Volunteer Review UI        │     │  Task Card Component     │
│  (TasksPage + TaskFeed)     │     │  - Face thumbnail       │
│                             │     │  - Matched member name  │
│  Actions:                   │     │  - Confidence tier      │
│  - Confirm (1 action max)   │     │  - Action buttons       │
│  - Edit to different member │     │  - Skip with reason     │
│  - Add unknown to member    │     │  - 3s cooldown timer    │
│  - Skip (max 2/volunteer)   │     │  - Dual-approval status │
└─────────────────────────────┘     └──────────────────────────┘
         │
         │ POST /tasks/{id}/confirm|edit|add|skip
         ↓
┌──────────────────────────────┐
│  Backend API Route Handler   │  (FastAPI route, async)
│  (app.routers.tasks)         │  - Verify volunteer token
│                              │  - Load task + detection
│  - Task service business     │  - Load for update (locking)
│    logic (dual-approval      │  - Action not duplicated?
│    guard, PIT escalation,    │  - Increment approvals
│    skip counter)             │  - If task resolved:
│  - Create TaskAction record  │    - Create Attendance
│  - Publish SSE event         │    - Broadcast resolution
└──────────────────────────────┘
         │
         │ Task action + approval tally
         ↓
    [Detection + Task Tables]
         │
         │ Task resolved → Attendance created
         │   (or PIT escalated if disputed)
         ↓
┌──────────────────────────────┐
│  Admin Attendance Push UI    │  (AttendancePage)
│  (react-query integration)   │  - Dry-run preview
│                              │  - Batch push to CiviCRM
│  POST /attendance/push-preview│
│  POST /attendance/push       │  - Marks attendance
│                              │    push_status → "queued"
└──────────────────────────────┘
         │
         │ Attendance records in "queued" state
         ↓
┌──────────────────────────────┐
│  Queue Worker (background)   │  Async task processor
│  (attendance push loop)      │  - Polls queued attendance
│                              │  - Makes CiviCRM API calls
│  - Batch POST to CiviCRM     │  - Updates push_status
│    REST v3 API               │  - Dead-letter on failure
│  - Retry backoff             │
│  - Dead-letter queue on      │
│    persistent failure        │
└──────────────────────────────┘
         │
         │ push_status → "pushed" or "dead_letter"
         ↓
    [CiviCRM Event Participant]
         │
         │ (External system — REST API)
         │
    [Attendance Confirmed]
```

---

## Component Breakdown

### 1. RTSP Worker (`seraphim-rtsp-worker`)

**Container**: `Dockerfile.worker` (Python 3.11, ffmpeg, opencv)  
**Command**: `python -m app.workers.rtsp_capture`  
**Process**: Continuous loop per camera

| Step | Logic |
|------|-------|
| 1. Connect RTSP | ffmpeg pull frame @ FPS rate |
| 2. Detect faces | OpenCV face detection on frame |
| 3. Recognize | For each face: call CompreFace → get subject_id + similarity |
| 4. Resolve contact | Map subject_id → CompreFace subject → contact_id (or None for unknown) |
| 5. Tier map | similarity → `"100"` / `"91-99"` / `"below90"` / `"unknown"` |
| 6. Store snapshot | Save face crop to `/app/storage/faces/{date}/{id}.jpg` |
| 7. Detection INSERT | DB: `INSERT detections(camera_id, image_path, confidence, tier, matched_name, event_id, ...)` |
| 8. Queue push | `LPUSH seraphim:detection:queue detection_id` (Redis) |
| 9. Check limits | If queue ≥ 500: pause ingestion; resume if < 400 |

**Rate limiting**: Respects `safe_mode` admin toggle (stops all frame ingestion if true).

**Resilience**: Health check via `pgrep` process alive; restarts on failure.

---

### 2. Queue Worker (`seraphim-queue-worker`)

**Container**: `Dockerfile` (Uvicorn, but runs as worker script)  
**Command**: `python -m app.workers.queue_consumer`  
**Process**: Continuous async loop

| Tier | Similarity | Action | Required Approvals |
|------|-----------|--------|-------------------|
| `100` | ≥ 0.98 | Auto-log (+ SSE "auto-logged") | None |
| `91-99` | 0.91–0.979 | Create Task | 1 volunteer confirmation |
| `below90` | < 0.91 | Create Task | 2 volunteer confirmations |
| `unknown` | No match / Error | Create Task | 2 volunteer confirmations |

**Special handling**:
- **Active event required**: Only auto-log Tier-100 if `active_event_id` is set
- **Deduplication**: Skip detection if `(contact_id, event_id)` already exists in attendance (no duplicate rows)
- **Task expiry**: Auto-expire if unresolved after 31 days
- **PIT escalation**: If skip count ≥ 4 from ≥ 2 volunteers, move task to admin pit queue

**Broadcast**: On Task creation, publish SSE event so frontend re-fetches task list in real-time.

---

### 3. FastAPI Backend (`seraphim-backend`)

**Port**: 8000 (exposed as 3001 in production)  
**Entry**: `app/main.py` → FastAPI app with lifespan, CORS, security middleware, rate-limiting

**Routers** (all registered without `/api` prefix; nginx strips it):

| Router | Prefix | Purpose |
|--------|--------|---------|
| `auth` | `/auth` | Login, OAuth, token refresh, user CRUD |
| `setup` | `/setup` | First-boot wizard (locked after completion) |
| `tasks` | `/tasks` | Task list, SSE feed, actions (confirm/edit/add/skip/override) |
| `attendance` | `/attendance` | Attendance list, CiviCRM push (preview + batch), dead-letter |
| `members` | `/members` | Contact CRUD, paginated list, attendance history |
| `events` | `/events` | CiviCRM event list, sync, set-active |
| `cameras` | `/cameras` | Camera CRUD, preview, reconnect |
| `analytics` | `/analytics` | Dashboards, leaderboard, CSV export |
| `audit` | `/audit` | Quality audit tasks (for volunteers during queue downtime) |
| `pit` | `/pit` | Admin disputed/multi-skip queue |
| `logs` | `/logs` | Audit log viewer |
| `health` | `/health` | DB/Redis/CompreFace status + safe-mode flag |
| `storage` | `/storage/{path}` | Authenticated face image serving |

**Middleware**:
- **CORS**: Restricted to `FRONTEND_URL` only
- **SecurityHeaders**: CSP, X-Frame-Options, X-Content-Type-Options
- **Rate limiter**: slowapi (keyed on client IP; fails open if Redis down)
- **Cooldown**: 3-second action cooldown per volunteer per task

**Auth**:
- Access token: 15-min lifetime, in-memory only (Zustand on frontend)
- Refresh token: 7-day lifetime, HttpOnly + Secure + SameSite=lax cookie
- Token rotation: Every `/auth/refresh` rotates the refresh token; old JTI deny-listed in Redis
- Token-type lockdown: Bearer + `?_t=` paths reject `type="refresh"` tokens (API replay prevented)

---

### 4. Frontend SPA (`seraphim-frontend`)

**Port**: 5173 (dev) / 3000 (production via nginx)  
**Framework**: React 18 + TypeScript + Vite + PWA

**Pages**:
- `LoginPage`: Email/password + optional Google OAuth
- `SetupPage`: 5-step first-boot wizard
- `TasksPage`: Main volunteer queue (SSE + polling fallback)
- `AuditPage`: Quality audit (empty queue only)
- `ContactsPage`: Paginated directory with search + filter
- `ContactDetailPage`: Profile, custom fields, attendance, face summary
- `DashboardPage`: Analytics charts (admin)
- `AttendancePage`: CiviCRM push UI (admin)
- `SettingsPage`: Admin tunables, cameras, dark mode
- etc.

**State**:
- **Auth** (Zustand): Access token in-memory, refresh cookie via `useAuth` hook
- **Tasks** (Zustand): Task UI state
- **Server state** (TanStack Query): Caching, refetch, mutation hooks

**Styling**: TailwindCSS with design tokens (primary, background, foreground, card, border).  
**Dark mode**: Class-based (`darkMode: 'class'`), persisted to localStorage, applied before first paint.  
**Toasts**: sonner (not `alert()`/`confirm()`).  
**PWA**: Workbox service worker, precache app shell, network-first for `/api`, installable on mobile.

**API integration**:
- Axios client with 401 → refresh → retry interceptor
- SSE endpoint (`/tasks/feed`) fetched with token via `?_t=<token>` query param (EventSource can't set headers)

---

### 5. PostgreSQL Database

**Tables**: 14 core + supporting (see [[Data Model]])

**Key constraints**:
- `tasks(status, required_approvals, current_approvals)`: Dual-approval guard via unique partial index `uq_task_action_approval` on `(task_id, volunteer_id)` excluding `skip` actions
- `attendance(contact_id, event_id)`: Unique constraint (one attendance per member per event)
- `detections(status, tier, confidence)`: Indexed for query performance
- **Timestamps**: Naive UTC (no timezone)
- **JSON columns**: `.with_variant(JSONB, "postgresql")` for Postgres production + SQLite tests

---

### 6. Redis

**Use cases**:
- **Task queue**: `seraphim:detection:queue` (LPUSH/RPOP for FIFO)
- **SSE pub/sub**: Broadcaster (publish/subscribe) for real-time task updates
- **JTI denylist**: Token revocation (ex: after logout or rotation)
- **Rate limiter**: slowapi token buckets

**Failover behavior**:
- **Rate limiter**: Fails open (swallows errors) so auth keeps working
- **JTI denylist**: Fails open (rotation still works, revocation degrades under Redis outage)
- **SSE broadcaster**: Fails — connection drops if Redis unavailable

---

### 7. CompreFace (External)

**Role**: Face detection + recognition engine (not bundled; configured via admin UI)

**API calls** (by RTSP Worker):
1. **Detection**: POST `/api/v1/detection/detect` → get face boxes + landmarks
2. **Recognition**: POST `/api/v1/recognition/recognize` → get subject_id + similarity score (if recognized)
3. **Subject enroll**: POST `/api/v1/enrollment/enroll` → add face samples to a subject (admin)
4. **Health check**: GET `/api/v1/health` → status

**Configuration**:
- URL + API key + separate detection/recognition keys (optional)
- Dual-key support: one key for detection, one for recognition (backward-compat with single-key installs)

---

### 8. CiviCRM (External)

**Role**: Church member database + event management (optional integration)

**API calls** (by Queue Worker):
1. **Event sync**: GET `/api/v3/Event` → fetch events, cache in DB
2. **Attendance push**: POST `/api/v3/ParticipantPayment` → log attendance as event participant
3. **Contact lookup**: GET `/api/v3/Contact` (optional, for enrichment)

**Failure handling**:
- Batch push with automatic retry + backoff
- Failed records moved to dead-letter queue for manual review/retry

---

## Data Dependencies

```
┌─────────────┐
│  Contacts   │  (14 core fields + custom_data JSONB)
│  (members)  │
└──────┬──────┘
       │ 1:N
       ↓
┌──────────────────┐     ┌────────────────┐
│  CompreFace      │────→│  Detections    │
│  Subjects        │     │  (face + score)│
└──────────────────┘     └────────┬───────┘
                                  │ 1:N
                                  ↓
                          ┌────────────────┐
                          │  Tasks         │
                          │  (review queue)│
                          └────────┬───────┘
                                   │ 1:N
                                   ↓
                          ┌────────────────────┐
                          │  TaskActions       │
                          │  (audit trail)     │
                          └────────────────────┘
                                   
                          ┌────────────────┐
Detections ──(resolved)──→│  Attendance    │
                          │  (logged &     │
                          │   pushed to    │
CiviCRM Events ◄──sync────│  CiviCRM)      │
                          └────────────────┘
```

---

## Security & Reliability

| Concern | Mitigation |
|---------|-----------|
| **JWT secret exposure** | Fail-fast on startup if setup complete but secret missing/short |
| **Access token theft** | In-memory only (never persisted) |
| **Refresh token replay** | Rotated on every refresh; old JTI deny-listed |
| **Refresh token compromise** | Logout deny-lists JTI; `/storage/*` checks denylist before serving face images |
| **SSRF on setup endpoints** | Locked (410 GONE) after setup completion |
| **Dual-approval bypass** | Partial unique index on `(task_id, volunteer_id)` excluding skips; IntegrityError → 400 |
| **SQL injection** | SQLAlchemy ORM (parameterized queries) exclusively |
| **Command injection** | `create_subprocess_exec` with list args; RTSP URL scheme validated |
| **Face image leakage** | Authenticated `/storage/{path}` route with path-containment check |
| **Biometric data at rest** | On trusted on-prem Postgres (plaintext); consider encryption if volume untrusted |
| **Rate-limit bypass** | Keyed on real client IP (configure `--forwarded-allow-ips=*` behind proxy); fails open |
| **Queue saturation DoS** | Hard limit 500 tasks → pause ingestion; manual safe-mode toggle |
| **Dependency CVE** | Regular audits; PyJWT ≥2.13.0, react-router-dom 6.30.4 patched |
| **Container privilege escalation** | `privileged: true` removed from RTSP worker; no capability escalation needed |

---

## Deployment Paths (Production)

See [[Deployment]] for detailed runbook.

1. **Cloudflare Tunnel** (no inbound ports)
   - HTTPS via Cloudflare edge
   - Named tunnel token in `.env`
   - SSE heartbeat (15s) survives ~100s idle timeout

2. **Caddy auto-TLS** (Let's Encrypt, ports 80+443)
   - HTTPS via Caddy container
   - Domain in `.env`
   - Full wildcard/subdomain support

3. **Plain HTTP on LAN** (private Unraid)
   - No HTTPS; cookies set per-request (Secure flag only over HTTPS)
   - For internal networks only

All three paths support:
- Docker Compose stack (postgres, redis, backend, frontend, workers)
- Alembic migrations (auto-run on backend startup)
- Setup wizard (complete → bootstrap.json + app reload)
