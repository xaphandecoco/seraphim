# LNC Attendance System

**Light of the World Worldwide Ministries — North Caloocan (LNC)**

A facial recognition-based church attendance tracking system. Captures RTSP camera feeds, identifies members via AI, and logs attendance to CiviCRM. Built for ~600 active members with volunteer verification workflows.

---

## Quick Start

```bash
# 1. Clone and enter directory
cd "Project Seraphim"

# 2. Create environment file
cp .env.template .env
# Edit .env with your credentials

# 3. Create data directories
mkdir -p .local-data/postgres .local-data/redis .local-data/storage .local-data/config

# 4. Start development stack
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d

# 5. Initialize database
docker compose exec seraphim-backend alembic upgrade head

# 6. Open http://localhost:5173 and complete the setup wizard
```

---

## Table of Contents

- [System Overview](#system-overview)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Authentication & Authorization](#authentication--authorization)
- [Recognition Pipeline](#recognition-pipeline)
- [Docker Services](#docker-services)
- [Environment Variables](#environment-variables)
- [Development Setup](#development-setup)
- [Production Deployment (Unraid)](#production-deployment-unraid)
- [Security](#security)
- [Branding & UI](#branding--ui)
- [Troubleshooting](#troubleshooting)

---

## System Overview

```
RTSP Camera → FFmpeg Frame Capture → Compreface Recognition → Tier Classification
    → Redis Queue → Volunteer Review UI → Attendance Record → CiviCRM Push
```

**Key Design Principles:**
- Privacy-first: all AI processing on-premise via Docker
- Dual-approval: low-confidence matches require multiple volunteer confirmations; each volunteer acts only once per task
- Audit trail: every action logged; admin pit queue for disputed faces; quality audit for enrolled faces
- Gamification: volunteer leaderboard with accuracy scoring
- Graceful degradation: mock camera pipeline for testing without hardware
- PWA: installable on mobile home screens; offline app-shell; network-first API caching

### Service Architecture

| Service | Role | Port (Dev) |
|---------|------|-----------|
| postgres | Primary database (14 tables) | 5432 |
| redis | Task queue, cache, SSE pub/sub | 6379 |
| seraphim-backend | FastAPI application server | 8000 / 3001 |
| seraphim-frontend | React Vite SPA (nginx in prod) | 5173 / 3000 |
| seraphim-rtsp-worker | FFmpeg frame capture from RTSP | internal |
| seraphim-queue-worker | Redis queue consumer → task creation | internal |
| mock-camera-1 | FFmpeg test pattern RTSP stream | 8554 |

> **Note:** Compreface is an **external service** (not bundled in Docker Compose). Configure its URL and API key during the setup wizard or in admin settings.

---

## Technology Stack

### Backend
| Category | Technology | Version |
|----------|------------|---------|
| Runtime | Python | 3.11 |
| Web Framework | FastAPI | 0.115.5 |
| Server | Uvicorn | 0.32.1 |
| Database | SQLAlchemy (async) | 2.0.36 |
| DB Driver | asyncpg | 0.30.0 |
| Migrations | Alembic | 1.14.0 |
| Auth | PyJWT + bcrypt | — |
| HTTP Client | httpx | 0.28.1 |
| Cache | redis-py | 5.2.1 |
| Validation | Pydantic v2 | 2.10.3 |
| Image Processing | OpenCV (headless) + Pillow | 4.10.0 + 11.0.0 |
| Rate Limiting | slowapi | 0.1.9 |
| Testing | pytest + pytest-asyncio | 8.3.4 |

### Frontend
| Category | Technology | Version |
|----------|------------|---------|
| Framework | React | 18.3 |
| Language | TypeScript | 5.4 |
| Build Tool | Vite | 5.2 |
| PWA | vite-plugin-pwa | 0.20 |
| Styling | TailwindCSS (dark mode: class) | 3.4 |
| State | Zustand | 4.5 |
| Server State | TanStack Query | 5.40 |
| HTTP Client | Axios | 1.7 |
| Toasts | Sonner | 1.5 |
| Charts | Recharts | 2.12 |
| Icons | Lucide React | 0.379 |
| Testing | Vitest + Testing Library | 1.6 |

### Infrastructure
| Category | Technology |
|----------|------------|
| Containerization | Docker + Docker Compose |
| Host OS (Prod) | Unraid (Linux) |
| Dev OS | Windows + Docker Desktop |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Face Recognition | Compreface (Exadel) |
| Reverse Proxy | Nginx (frontend prod — proxies `/api` to backend) |

---

## Project Structure

```
Project Seraphim/
├── backend/                    # FastAPI Python backend
│   ├── app/
│   │   ├── routers/            # API route handlers
│   │   │   ├── analytics.py    # Aggregate stats + CSV export (admin)
│   │   │   ├── attendance.py   # Attendance records + CiviCRM push + dead-letter
│   │   │   ├── audit.py        # Quality audit tasks (prefix: /audit)
│   │   │   ├── auth.py         # Login, OAuth, password reset, user management
│   │   │   ├── cameras.py      # CRUD + preview
│   │   │   ├── events.py       # CiviCRM event sync
│   │   │   ├── health.py       # Health checks + queue metrics + safe_mode flag
│   │   │   ├── leaderboard.py  # Gamification rankings (display names)
│   │   │   ├── logs.py         # Audit log viewer
│   │   │   ├── members.py      # CiviCRM member search + attendees
│   │   │   ├── pit.py          # Admin dispute queue
│   │   │   ├── settings.py     # Admin settings + safe mode
│   │   │   ├── setup.py        # First-boot wizard (locked after completion)
│   │   │   ├── storage.py      # Authenticated file serving for /storage/**
│   │   │   ├── tasks.py        # Task CRUD + actions (resolves member names)
│   │   │   └── uploads.py      # Face upload (10 MB / 25 MP limits)
│   │   ├── services/           # Business logic, external clients
│   │   │   ├── civicrm.py      # CiviCRM REST v3 client
│   │   │   ├── compreface.py   # Compreface API client
│   │   │   ├── dedup.py        # Perceptual hash deduplication
│   │   │   ├── face_pipeline.py
│   │   │   ├── face_storage.py # Face snapshot storage
│   │   │   ├── queue_manager.py# Redis queue operations
│   │   │   ├── rtsp.py         # RTSP stream handling
│   │   │   └── task_service.py # Business logic for tasks (dual-approval guards)
│   │   ├── workers/            # Background workers
│   │   │   ├── queue_consumer.py
│   │   │   ├── queue_producer.py
│   │   │   └── rtsp_capture.py
│   │   ├── middleware/
│   │   │   ├── cooldown.py     # 3-second action cooldown
│   │   │   └── security_headers.py
│   │   ├── utils/
│   │   │   └── auth.py         # Password hashing, JWT, tokens
│   │   ├── config.py           # 3-tier settings (Bootstrap/Dynamic/Legacy)
│   │   ├── database.py         # SQLAlchemy engine + sessions
│   │   ├── dependencies.py     # Auth deps + setup check
│   │   ├── main.py             # FastAPI app + lifespan (fail-fast JWT check)
│   │   ├── models.py           # 14 SQLAlchemy tables
│   │   ├── schemas.py          # Pydantic request/response models
│   │   └── sse.py              # Server-Sent Events broadcaster
│   ├── alembic/                # Database migrations
│   │   └── versions/           # inc. d1e2f3a4b5c6_add_name_to_users.py
│   ├── tests/                  # pytest suite
│   ├── mock_camera/            # FFmpeg mock RTSP camera
│   ├── Dockerfile
│   ├── Dockerfile.dev
│   ├── Dockerfile.worker
│   ├── requirements.txt
│   └── alembic.ini
│
├── frontend/                   # React Vite frontend (PWA)
│   ├── src/
│   │   ├── components/
│   │   │   ├── layout/         # BottomNav (More sheet for admins), ProtectedRoute, AdminRoute
│   │   │   ├── tasks/          # TaskCard, TaskFeed, MemberSearchModal (focus-trapped)
│   │   │   └── ui/             # StateViews: LoadingState, EmptyState, ErrorState
│   │   ├── hooks/
│   │   │   └── useAuth.ts      # In-memory token + refresh cookie hydration on mount
│   │   ├── pages/
│   │   │   ├── AttendancePage.tsx    # CiviCRM push + dead-letter queue (admin)
│   │   │   ├── AttendeesPage.tsx
│   │   │   ├── AuditPage.tsx         # Quality audit (volunteer, when queue empty)
│   │   │   ├── DashboardPage.tsx     # Analytics charts + CSV export (admin)
│   │   │   ├── EventsPage.tsx        # + Sync button (admin)
│   │   │   ├── LogsPage.tsx
│   │   │   ├── LoginPage.tsx         # Google button conditional on OAuth config
│   │   │   ├── OAuthCallbackPage.tsx # Reads ?token= from backend redirect
│   │   │   ├── PitPage.tsx
│   │   │   ├── RankingPage.tsx       # Period selector; shows display names
│   │   │   ├── SettingsPage.tsx      # Dark mode, tunables editor, camera preview
│   │   │   ├── SetupPage.tsx         # 5-step wizard; Next gated on validation
│   │   │   ├── TasksPage.tsx         # Safe Mode + Queue banners (no /settings poll)
│   │   │   └── UserManagementPage.tsx# Add/deactivate/reset volunteers (admin)
│   │   ├── services/
│   │   │   ├── api.ts          # Axios; 401 → refresh → retry before redirect
│   │   │   ├── connectionUrl.ts# Shared Postgres/Redis URL builders
│   │   │   └── sse.ts          # SSEClient; passes token via ?_t= query param
│   │   ├── store/
│   │   │   ├── authStore.ts    # In-memory token (no localStorage); setToken()
│   │   │   └── taskStore.ts
│   │   ├── types/index.ts
│   │   ├── App.tsx             # Routes incl. /audit, /settings/users, /settings/attendance, /dashboard
│   │   ├── index.css           # Design tokens (light + .dark blocks)
│   │   └── main.tsx            # Applies persisted dark mode class before first paint
│   ├── index.html              # PWA meta tags; no user-scalable=no
│   ├── nginx.conf              # Proxies /api/* → backend; SSE headers
│   ├── tailwind.config.js      # darkMode:'class'; token colors with <alpha-value>
│   ├── vite.config.ts          # vite-plugin-pwa: manifest, SW, maskable icons
│   └── package.json
│
├── scripts/
│   ├── deploy.sh
│   ├── rollback.sh
│   └── webhook_listener.py     # Gitea push webhook (127.0.0.1; WEBHOOK_SECRET ≥32 chars)
│
├── .env.template
├── docker-compose.yml          # Production compose (no privileged; ENVIRONMENT=production)
├── docker-compose.override.yml # Development overrides
├── docker-compose.unraid.yml
├── AGENTS.md
└── USER_GUIDE.md
```

---

## Database Schema

### Core Tables

#### `users` — Authentication Accounts
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| email | String(255) | unique, not null |
| **name** | String(255) | nullable |
| password_hash | String(255) | not null |
| auth_provider | String(20) | default `"local"` (local \| google) |
| role | String(20) | default `"volunteer"` (admin \| volunteer) |
| is_active | Boolean | default `True` |
| password_reset_token | String(255) | nullable |
| password_reset_expires_at | DateTime | nullable |
| created_at | DateTime | default `utc_now` |

#### `cameras` — RTSP Camera Configuration
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| name | String(100) | not null |
| rtsp_url | Text | not null; validated `rtsp://` or `rtsps://` scheme |
| zone_label | String(100) | nullable |
| fps | Integer | default `1` |
| enable_health_check | Boolean | default `True` |
| status | String(20) | default `"streaming"` |
| offline_since | DateTime | nullable |
| created_at | DateTime | default `utc_now` |

#### `detections` — Face Detection Results
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| camera_id | Integer | FK → cameras.id (ON DELETE SET NULL) |
| timestamp | DateTime | default `utc_now` |
| image_path | Text | not null |
| confidence | Numeric(5,3) | nullable |
| tier | String(20) | nullable (100 \| 91-99 \| below90 \| unknown) |
| status | String(20) | default `"pending"` |
| matched_name | String(255) | nullable (resolved to display name in API responses) |
| compreface_subject_id | String(255) | nullable |
| event_id | Integer | FK → civicrm_events.event_id |
| is_enrolled | Boolean | default `False` |
| deleted_at | DateTime | nullable |
| created_at | DateTime | default `utc_now` |

#### `tasks` — Volunteer Review Queue
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| detection_id | Integer | FK → detections.id (ON DELETE CASCADE) |
| status | String(20) | default `"pending"` |
| required_approvals | Integer | default `1` (1 \| 2) |
| current_approvals | Integer | default `0` |
| skip_count | Integer | default `0` |
| skip_reasons | JSONB | default `list` (reassigned, not mutated in-place) |
| pit_status | String(20) | nullable |
| expiry_date | DateTime | default `utc_now + 31 days` |
| created_at | DateTime | default `utc_now` |

#### `task_actions` — Audit Trail of Volunteer Actions
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| task_id | Integer | FK → tasks.id (ON DELETE CASCADE) |
| volunteer_id | Integer | FK → users.id (ON DELETE SET NULL) |
| action | String(30) | confirm \| edit \| add \| skip \| admin_override \| audit_confirm \| audit_deny \| audit_edit |
| reason | String(255) | nullable |
| created_at | DateTime | default `utc_now` |

#### `attendance` — Confirmed Attendance Records
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| contact_id | Integer | FK → civicrm_members.contact_id |
| event_id | Integer | FK → civicrm_events.event_id |
| detection_id | Integer | FK → detections.id (ON DELETE SET NULL) |
| status | String(20) | default `"pending"` |
| push_status | String(20) | default `"pending"` (pending \| queued \| pushed \| failed \| dead_letter) |
| push_attempts | Integer | default `0` |
| last_push_error | Text | nullable |
| created_at | DateTime | default `utc_now` |

**Unique Constraint**: `contact_id + event_id` (one attendance per member per event)

#### `volunteer_stats` — Gamification Metrics
| Column | Type | Constraints |
|--------|------|-------------|
| volunteer_id | Integer | FK → users.id (ON DELETE CASCADE), PK |
| month | String(7) | PK (YYYY-MM) |
| tasks_confirmed | Integer | default `0` |
| tasks_edited | Integer | default `0` |
| tasks_added | Integer | default `0` |
| accuracy_score | Numeric(5,2) | default `100.0` |
| total_points | Integer | default `0` |

#### `logs` — System Event Log
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| detection_id | Integer | FK → detections.id |
| face_snapshot_path | Text | nullable |
| timestamp | DateTime | default `utc_now` |
| camera_id | Integer | FK → cameras.id |
| matched_name | String(255) | nullable |
| confidence | Numeric(5,3) | nullable |
| tier | String(20) | nullable |
| action | String(30) | not null |
| volunteer_id | Integer | FK → users.id |
| second_volunteer_id | Integer | FK → users.id |
| event_id | Integer | FK → civicrm_events.event_id |
| push_status | String(20) | nullable |
| created_at | DateTime | default `utc_now` |

### Supporting Tables

#### `civicrm_members`, `civicrm_events`, `compreface_subjects`, `pit_queue`, `admin_settings`
Schema unchanged from initial design — see inline Pydantic schemas for current field details.

---

## API Reference

> All routes are served without an `/api` prefix in FastAPI.
> The nginx production config and Vite dev proxy both strip `/api` before forwarding.

### Auth Endpoints (`/auth`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/auth/config` | Public | OAuth feature flags (`google_oauth_enabled`) |
| POST | `/auth/login` | Public | Email/password login → JWT access token |
| POST | `/auth/logout` | Authenticated | Clears HttpOnly refresh token cookie |
| GET | `/auth/me` | Authenticated | Current user profile |
| POST | `/auth/refresh` | Public (cookie) | Issue new access token from refresh cookie |
| GET | `/auth/users` | Admin | List all user accounts |
| POST | `/auth/add-volunteer` | Admin | Create volunteer account |
| POST | `/auth/deactivate/{user_id}` | Admin | Deactivate account (retains history) |
| POST | `/auth/reset-password` | Admin | Generate password reset link |
| POST | `/auth/reset-password/confirm` | Public | Confirm password reset (rate-limited) |
| GET | `/auth/google` | Public | Initiate Google OAuth (503 if disabled) |
| GET | `/auth/google/callback` | Public | OAuth callback → redirect with `?token=` |

### Task Endpoints (`/tasks`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/tasks` | Volunteer | Paginated list; `matched_name` resolved to display name |
| GET | `/tasks/next` | Volunteer | Next available pending task (resolved name) |
| GET | `/tasks/feed` | Authenticated | SSE stream; auth via Bearer or `?_t=<token>` |
| POST | `/tasks/{id}/confirm` | Volunteer | Confirm match; one action per volunteer (3 s cooldown) |
| POST | `/tasks/{id}/edit` | Volunteer | Change to different member; one action per volunteer |
| POST | `/tasks/{id}/add` | Volunteer | Assign unknown face to member; one action per volunteer |
| POST | `/tasks/{id}/skip` | Volunteer | Skip with reason (max 2 skips per volunteer per task) |
| POST | `/tasks/{id}/override` | Admin | Instant-resolve dual-approval task |

### Attendance Endpoints (`/attendance`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/attendance` | Volunteer | List records (response model excludes push internals) |
| POST | `/attendance/push-preview` | Admin | Dry-run CiviCRM push diff |
| POST | `/attendance/push` | Admin | Queue records for background push |
| GET | `/attendance/dead-letter` | Admin | List permanently-failed push records |
| POST | `/attendance/dead-letter/{id}/retry` | Admin | Reset failed record for retry |

### Analytics Endpoints (`/analytics`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/analytics/attendance-by-event` | Admin | Attendance count per event (last 20) |
| GET | `/analytics/tier-distribution` | Admin | Detection counts by confidence tier |
| GET | `/analytics/volunteer-stats` | Admin | Aggregated volunteer performance |
| GET | `/analytics/queue-health` | Admin | Daily detection/resolution counts (14 days) |
| GET | `/analytics/export/attendance` | Admin | Stream attendance CSV |
| GET | `/analytics/export/logs` | Admin | Stream logs CSV (up to 5 000 rows) |

### Camera Endpoints (`/cameras`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/cameras` | Admin | List all cameras |
| POST | `/cameras` | Admin | Create camera (rtsp_url validated: `rtsp://` or `rtsps://`) |
| PUT | `/cameras/{id}` | Admin | Update camera |
| DELETE | `/cameras/{id}` | Admin | Delete camera |
| GET | `/cameras/{id}/preview` | Admin | Grab single JPEG frame |
| POST | `/cameras/{id}/reconnect` | Admin | Force reconnect |

### Other Endpoints

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/events` | Volunteer | List cached CiviCRM events |
| POST | `/events/sync` | Admin | Force sync from CiviCRM |
| GET | `/members` | Volunteer | Search members by name/email |
| POST | `/members/sync` | Admin | Force sync from CiviCRM |
| GET | `/pit` | Admin | List admin pit queue |
| POST | `/pit/{id}/enroll` | Admin | Enroll face to member |
| POST | `/pit/{id}/delete` | Admin | Mark as trash |
| POST | `/pit/{id}/non-person` | Admin | Mark as false detection |
| GET | `/audit/tasks` | Volunteer | Quality audit cards (empty queue only) |
| POST | `/audit/{id}/confirm` | Volunteer | Confirm enrolled match correct |
| POST | `/audit/{id}/deny` | Volunteer | Flag match incorrect |
| POST | `/audit/{id}/change` | Volunteer | Change to different contact |
| GET | `/leaderboard` | Volunteer | Rankings; period: `this_month\|previous_month\|all_time` |
| GET | `/logs` | Admin | Filtered logs with pagination |
| GET | `/settings` | Admin | Read all settings (sensitive values masked) |
| PUT | `/settings` | Admin | Update settings + reload in-memory cache |
| POST | `/settings/safe-mode` | Admin | Toggle safe mode |
| GET | `/setup/status` | Public | Check if setup completed |
| POST | `/setup/test-connection` | Public (pre-setup only) | Test Postgres/Redis (locked after setup) |
| POST | `/setup/test-services` | Public (pre-setup only) | Test Compreface/CiviCRM (locked after setup) |
| POST | `/setup` | Public (one-time) | Initial bootstrap (permanently locks after success) |
| GET | `/health` | Public | DB, Redis, Compreface status |
| GET | `/health/queue` | Public | Queue metrics + `safe_mode` flag |
| GET | `/storage/{path}` | Authenticated | Authenticated face image serving (path-contained) |
| POST | `/uploads/faces` | Admin | Upload image; detect + pipeline all faces (10 MB / 25 MP limit) |

---

## Authentication & Authorization

### 3-Tier Configuration

1. **BootstrapConfig** (file): Immutable config read from `bootstrap.json` on startup
2. **DynamicSettings** (DB): Runtime-mutable settings loaded from `admin_settings` table; reloaded in-memory after setup wizard and after any `PUT /settings`
3. **LegacySettings** (env): Backwards-compatible environment variables (reads `JWT_SECRET`)

### Startup Fail-Fast

If setup is complete (`setup_complete = true` in the DB) but the JWT secret is missing or shorter than 32 characters, the app **refuses to start** with a `RuntimeError`. This prevents silent token-forgery risk.

### First-Boot Setup

On first startup, the setup wizard at `/setup`:
1. Creates the admin user account
2. Populates all `admin_settings` rows (including `jwt_secret`)
3. Writes `bootstrap.json` to disk
4. **Immediately reloads** in-memory settings so the app is usable without a restart
5. Returns **410 GONE** permanently for all `/setup/*` endpoints

### Login Methods

- **Primary**: Local email/password (bcrypt). Login form is the default UI.
- **Google OAuth**: Enabled via `enable_google_oauth` admin setting. The frontend only shows the Google button when `/auth/config` confirms it is enabled. The callback receives `?token=<access_token>` and calls `/auth/me` to hydrate the user.
- **No self-registration**: Only admins can create volunteer accounts.

### Token Architecture

| Token | Storage | Lifetime | Notes |
|-------|---------|----------|-------|
| Access token | Zustand (in-memory only) | 15 min (configurable) | Never written to localStorage |
| Refresh token | HttpOnly cookie | 7 days (configurable) | `Secure` in production, `SameSite=lax` |

On app load, `useAuth` silently calls `/auth/refresh` to re-issue an access token from the cookie. On 401, the Axios interceptor attempts a single refresh before redirecting to `/login`. Logout calls `POST /auth/logout` to clear the server-side cookie.

### SSE Authentication

`GET /tasks/feed` accepts authentication via:
1. `Authorization: Bearer <token>` header (dev/Postman)
2. `?_t=<token>` query parameter (browser `EventSource`, which cannot set headers)

### Roles & Permissions

| Feature | Volunteer | Admin |
|---------|-----------|-------|
| Review tasks | ✓ | ✓ |
| Quality audit | ✓ | ✓ |
| Leaderboard / Events / Members | ✓ | ✓ |
| User management | — | ✓ |
| Attendance push to CiviCRM | — | ✓ |
| Analytics dashboard + CSV export | — | ✓ |
| Settings / cameras | — | ✓ |
| Logs / Pit queue | — | ✓ |

### Password Policy

All password fields (setup admin, add-volunteer, reset-confirm) require:
- ≥ 12 characters
- At least one uppercase letter
- At least one lowercase letter
- At least one number
- At least one special character (`!@#$%^&*()_+-=[]{}|;':\",./<>?`)

---

## Recognition Pipeline

### Confidence Tier Mapping

| Similarity Score | Tier | Action | Volunteer Approvals |
|-----------------|------|--------|---------------------|
| ≥ 0.98 | `"100"` | Auto-logged to attendance | None |
| 0.91 – 0.979 | `"91-99"` | Task created, 1 volunteer confirms | 1 |
| < 0.91 | `"below90"` | Task created, 2 volunteers confirm | 2 |
| No match / Error | `"unknown"` | Task created, 2 volunteers confirm | 2 |

### Dual-Approval Guard

Each volunteer can act on a given task **exactly once** (confirm, edit, or add). The `task_service` checks `TaskAction` for a prior action by the same volunteer and raises 400 if found. This closes the bypass where a single volunteer could edit→edit to self-resolve a 2-approval task.

### Skip Logic

- **Per-volunteer limit**: Max 2 skips per task per volunteer
- **PIT trigger**: 4 total skips from ≥ 2 different volunteers → task moves to admin PIT queue
- **skip_reasons**: JSONB array — always reassigned (not mutated) to ensure SQLAlchemy detects the change

### Queue Management

- **Hard limit**: 500 tasks → frame ingestion pauses
- **Resume limit**: Falls below 400 → ingestion resumes
- **Deduplication**: 30-second pHash ring buffer prevents duplicate detections
- **Task expiry**: 31 days → auto-expired if unresolved
- **Safe mode**: Admin toggle — stops all recognition instantly; visible to volunteers via `/health/queue`

---

## Docker Services

### Production (`docker-compose.yml`)

```yaml
Services:
  postgres:             no host port (internal only)
  redis:                no host port (internal only)
  seraphim-backend:     port 3001:8000; ENVIRONMENT=production
  seraphim-frontend:    port 3000:80 (nginx proxies /api/ + serves PWA)
  seraphim-rtsp-worker: no privileged mode
  seraphim-queue-worker: 2 CPU, 2 GB RAM limit
```

> The RTSP worker previously ran with `privileged: true`. This has been removed — FFmpeg pulling RTSP over the network requires no host privileges.

### Development (`docker-compose.override.yml`)

```yaml
Services:
  postgres:           port 5432 host
  redis:              port 6379 host
  seraphim-backend:   port 8000 host, hot-reload, ./backend:/app mount
  seraphim-frontend:  port 5173 host, hot-reload, ./frontend:/app mount
  seraphim-rtsp-worker:  ./backend:/app mount
  seraphim-queue-worker: ./backend:/app mount
  mock-camera-1:      port 8554 host
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL async connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `JWT_SECRET` | Pre-setup fallback | JWT signing secret (≥ 32 bytes). Superseded by DB value after setup. |
| `COMPREFACE_URL` | No | Compreface API base URL |
| `COMPREFACE_API_KEY` | No | Compreface API key |
| `CIVICRM_URL` | No | CiviCRM REST API base URL |
| `CIVICRM_API_KEY` | No | CiviCRM API key |
| `CIVICRM_SITE_KEY` | No | CiviCRM site key |
| `GOOGLE_CLIENT_ID` | No | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | No | Google OAuth client secret |
| `ADMIN_EMAILS` | No | Comma-separated admin emails |
| `STORAGE_PATH` | Yes | Path for face snapshots (e.g. `/app/storage`) |
| `ENVIRONMENT` | Yes (prod) | Set to `production` to enable secure cookies + disable SQL echo |
| `BOOTSTRAP_CONFIG_PATH` | No | Override path for `bootstrap.json` (default `/app/config/bootstrap.json`) |
| `WEBHOOK_SECRET` | Yes (if webhook used) | HMAC secret for Gitea webhook (≥ 32 chars) |

> All sensitive settings (Compreface key, CiviCRM keys, JWT secret) can be set or updated via the admin Settings UI after setup. The env vars are only the startup fallback.

---

## Development Setup

### Prerequisites
- Docker Desktop (Windows) or Docker Engine (Linux)
- Git

### First-Time Setup

```bash
# 1. Clone repository
git clone <repo-url>
cd "Project Seraphim"

# 2. Create .env from template
cp .env.template .env
# Edit DB_PASSWORD and JWT_SECRET at minimum

# 3. Create data directories
mkdir -p .local-data/postgres .local-data/redis .local-data/storage .local-data/config

# 4. Start all services
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d

# 5. Run initial database migration
docker compose exec seraphim-backend alembic upgrade head

# 6. Complete setup wizard via browser at http://localhost:5173
```

### Access Points (Development)

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| Backend (alt) | http://localhost:3001 |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |

### Useful Commands

```bash
# Run backend tests
docker compose exec seraphim-backend pytest tests/ -v

# Run a single migration
docker compose exec seraphim-backend alembic upgrade head

# Database shell
docker compose exec postgres psql -U seraphim -d seraphim_attendance

# Frontend install (after adding a new package)
docker compose exec seraphim-frontend npm install

# View backend logs
docker compose logs -f seraphim-backend

# Rebuild a single service
docker compose build --no-cache seraphim-backend
```

---

## Production Deployment (Unraid)

### File Transfer
Copy project files to Unraid via USB/SMB.

### Unraid Configuration
1. Install **Docker Compose Manager** plugin
2. Create `/mnt/user/appdata/seraphim/` directory
3. Copy `docker-compose.yml` (and optionally `docker-compose.unraid.yml`), `.env`, and backend/frontend directories
4. Create data directories:
   ```
   /mnt/user/appdata/seraphim/storage/
   /mnt/user/appdata/seraphim/config/
   ```
5. Update `.env` with production secrets — especially `ENVIRONMENT=production`
6. Run `docker compose up -d`
7. Run `docker compose exec seraphim-backend alembic upgrade head`
8. Complete setup wizard at `http://unraid-ip:3000`

### Production URLs
| Service | URL |
|---------|-----|
| Frontend (nginx, proxies /api) | http://unraid-ip:3000 |
| Backend API (direct) | http://unraid-ip:3001 |

> For HTTPS, put a reverse proxy (Nginx Proxy Manager, Traefik, Cloudflare Tunnel) in front of port 3000. Set `ENVIRONMENT=production` so cookies get the `Secure` flag.

---

## Security

### Current Posture

| Category | Status |
|----------|--------|
| JWT secret | Fail-fast on startup if setup complete and secret missing/short; no empty-string fallback |
| Token storage | Access token in-memory only (Zustand); refresh token in HttpOnly cookie |
| Authentication | bcrypt + JWT; refresh cookie; role-based access |
| SQL injection | SQLAlchemy ORM used exclusively |
| Command injection | `create_subprocess_exec` with list args (no shell); RTSP URL scheme validated |
| SSRF | Setup test endpoints locked (410) after setup completes |
| Dual-approval | Per-volunteer "already acted" guard on confirm/edit/add |
| Biometric images | Served only through authenticated `/storage/{path}` route with path containment |
| Upload limits | 10 MB byte limit + 25 MP pixel cap on face uploads |
| CSP | `default-src 'self'; object-src 'none'; frame-ancestors 'none'` |
| CORS | Restricted to `FRONTEND_URL` only |
| Webhook | Empty/short `WEBHOOK_SECRET` → process refuses to start; binds `127.0.0.1` |
| Container privilege | `privileged: true` removed from RTSP worker |

### Known Gaps

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| High | No HTTPS enforcement | Deploy behind reverse proxy with HTTPS termination |
| Medium | Rate limiting keyed on socket IP | Configure `--forwarded-allow-ips` when behind a proxy |
| Low | Password reset link in query string | Deliver token out-of-band if higher security needed |

---

## Branding & UI

### LNC Visual Identity

The frontend uses the **60-30-10 design principle** with all colours expressed as Tailwind CSS design tokens (no hardcoded hex in components):

| Token | CSS var | Role | Value |
|-------|---------|------|-------|
| `primary` | `--primary` | Church yellow (60%) | HSL 48 90% 62% |
| `background` | `--background` | Warm cream (30%) | HSL 45 40% 96% |
| `foreground` | `--foreground` | Deep charcoal (10%) | HSL 220 15% 15% |
| `card` | `--card` | Near-white cards | HSL 45 30% 98% |
| `border` | `--border` | Warm tan borders | HSL 45 20% 88% |

All tokens have a dark-mode counterpart in `.dark { }` in `index.css`. Tailwind is configured with `darkMode: 'class'` and `<alpha-value>` support so opacity modifiers (`bg-primary/20`, `text-foreground/50`) work correctly.

### Dark Mode

Dark mode is toggled from **Settings → Appearance**. The choice is persisted to `localStorage` as `seraphim-theme`. On first visit the device's `prefers-color-scheme` is respected.

### Mobile-First & PWA

- Baseline width: 375 px
- Bottom tab navigation with safe-area insets
- Touch targets minimum 44 px
- Pinch-zoom enabled (`user-scalable=no` removed)
- Full PWA: `manifest.webmanifest`, maskable icons, Workbox service worker (precache app shell, network-first for `/api`)
- Installable on Android (Chrome) and iPhone/iPad (Safari)

---

## Troubleshooting

### App refuses to start after setup
Check the backend logs. If you see `JWT secret is missing or too short`, the `admin_settings` table may be missing the `jwt_secret` row. Re-run the setup wizard (delete `bootstrap.json` from the config volume first).

### Setup wizard 410 error
A stale `bootstrap.json` may exist from a partial setup. Delete it:
```bash
docker compose exec seraphim-backend rm /app/config/bootstrap.json
```
Then retry the wizard.

### No tasks appearing
- Check the Safe Mode banner — recognition may be paused by an admin
- Check the Queue Saturated banner — ingestion may be paused
- Verify cameras are streaming in Settings → Cameras

### Face thumbnails not loading
Images are served through the authenticated `/storage/` route. If thumbnails show broken icons, check that the backend container has access to the storage volume and that the user is authenticated.

### Google login not appearing
The Google button only shows when `GET /auth/config` returns `google_oauth_enabled: true`. Enable it in Settings → Recognition & Queue Tunables → Enable Google OAuth, and ensure `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` are configured.

### Backend build timeout
The backend image includes ffmpeg and ~250 Debian dependencies (~680 MB). First build may take 30+ minutes. Subsequent builds use Docker layer cache.

### Compreface not responding
```bash
curl "http://your-compreface-url:port/api/v1/health"
```
Check the URL in Settings → System.

### Attendance not in CiviCRM
1. Verify CiviCRM credentials in Settings
2. Check the Dead Letter Queue in Settings → Attendance & CiviCRM Push
3. Check Logs for push errors

---

## Appendix A: TypeScript Frontend Types

```typescript
export interface Task {
  id: number;
  detection_id: number;
  tier: '100' | '91-99' | 'below90' | 'unknown';
  face_thumbnail_path: string | null;
  matched_name: string | null;   // resolved display name (not "member:123")
  confidence: number | null;
  camera_name: string | null;
  detected_at: string | null;
  status: string;
  required_approvals: number;
  current_approvals: number;
  skip_count: number;
  enrollment_progress: string | null;
}

export interface Member {
  contact_id: number;
  first_name: string;
  last_name: string;
  display_name?: string;
  email?: string;
}

export interface ChurchEvent {
  event_id: number;
  title: string;
  start_date: string;
  end_date?: string;
}
```

## Appendix B: Compreface Tier Mapping

```python
def _map_tier(similarity: float | None) -> Literal["100", "91-99", "below90", "unknown"]:
    if similarity is None:
        return "unknown"
    if similarity >= 0.98:
        return "100"
    if similarity >= 0.91:
        return "91-99"
    return "below90"
```

## Appendix C: Contact

- **Developer**: John Atienza
- **Church**: Light of the World Worldwide Ministries — North Caloocan (lightnc.org)
- **Deployment Target**: Unraid home server
- **Live CiviCRM**: REST v3 API available
- **Hardware**: Tapo C260 cameras (RTSP)
