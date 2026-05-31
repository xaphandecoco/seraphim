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
- Dual-approval: low-confidence matches require multiple volunteer confirmations
- Audit trail: every action logged; admin pit queue for disputed faces
- Gamification: volunteer leaderboard with accuracy scoring
- Graceful degradation: mock camera pipeline for testing without hardware

### Service Architecture

| Service | Role | Port (Dev) |
|---------|------|-----------|
| postgres | Primary database (13 tables) | 5432 |
| redis | Task queue, cache, SSE pub/sub | 6379 |
| seraphim-backend | FastAPI application server | 8000 / 3001 |
| seraphim-frontend | React Vite SPA | 5173 / 3000 |
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
| Web Framework | FastAPI | 0.111.0 |
| Server | Uvicorn | 0.30.0 |
| Database | SQLAlchemy (async) | 2.0.30 |
| DB Driver | asyncpg | 0.29.0 |
| Migrations | Alembic | 1.13.1 |
| Auth | PyJWT + bcrypt | — |
| HTTP Client | httpx | 0.27.0 |
| Cache | redis-py | 5.0.4 |
| Validation | Pydantic v2 | 2.7.2 |
| Image Processing | OpenCV (headless) + Pillow | 4.9.0 + 10.3.0 |
| Testing | pytest + pytest-asyncio | 8.2.1 |

### Frontend
| Category | Technology | Version |
|----------|------------|---------|
| Framework | React | 18.3 |
| Language | TypeScript | 5.4 |
| Build Tool | Vite | 5.2 |
| Styling | TailwindCSS | 3.4 |
| State | Zustand | 4.5 |
| Server State | TanStack Query | 5.40 |
| HTTP Client | Axios | 1.7 |
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
| Reverse Proxy | Nginx (frontend production) |

---

## Project Structure

```
Project Seraphim/
├── backend/                    # FastAPI Python backend
│   ├── app/
│   │   ├── routers/            # API route handlers
│   │   │   ├── auth.py         # Login, OAuth, password reset
│   │   │   ├── tasks.py        # Task CRUD + actions
│   │   │   ├── attendance.py   # Attendance records + CiviCRM push
│   │   │   ├── cameras.py      # CRUD + preview
│   │   │   ├── events.py       # CiviCRM event sync
│   │   │   ├── members.py      # CiviCRM member search
│   │   │   ├── pit.py          # Admin dispute queue
│   │   │   ├── audit.py        # Quality audit tasks
│   │   │   ├── leaderboard.py  # Gamification rankings
│   │   │   ├── logs.py         # Audit log viewer
│   │   │   ├── settings.py     # Admin settings + safe mode
│   │   │   ├── setup.py        # First-boot wizard
│   │   │   └── health.py       # Health checks + queue metrics
│   │   ├── services/           # Business logic, external clients
│   │   │   ├── compreface.py   # Compreface API client
│   │   │   ├── civicrm.py      # CiviCRM REST v3 client
│   │   │   ├── queue_manager.py# Redis queue operations
│   │   │   ├── task_service.py # Business logic for tasks
│   │   │   ├── dedup.py        # Perceptual hash deduplication
│   │   │   ├── face_storage.py # Face snapshot storage
│   │   │   └── rtsp.py         # RTSP stream handling
│   │   ├── workers/            # Background workers
│   │   │   ├── rtsp_capture.py # RTSP frame capture
│   │   │   ├── queue_consumer.py# Queue processor
│   │   │   └── queue_producer.py# Queue publisher
│   │   ├── utils/
│   │   │   └── auth.py         # Password hashing, JWT, tokens
│   │   ├── middleware/
│   │   │   └── cooldown.py     # 3-second action cooldown
│   │   ├── main.py             # FastAPI app + lifespan
│   │   ├── config.py           # 3-tier settings (Bootstrap/Dynamic/Legacy)
│   │   ├── database.py         # SQLAlchemy engine + sessions
│   │   ├── dependencies.py     # Auth deps + setup check
│   │   ├── models.py           # 13 SQLAlchemy tables
│   │   ├── schemas.py          # Pydantic request/response models
│   │   └── sse.py              # Server-Sent Events broadcaster
│   ├── alembic/                # Database migrations
│   ├── tests/                  # pytest suite
│   ├── mock_camera/            # FFmpeg mock RTSP camera
│   ├── Dockerfile              # Production backend image
│   ├── Dockerfile.dev          # Development image (hot reload)
│   ├── Dockerfile.worker       # RTSP worker image
│   ├── requirements.txt
│   └── alembic.ini
│
├── frontend/                   # React Vite frontend
│   ├── src/
│   │   ├── components/         # React components by feature
│   │   │   ├── layout/         # BottomNav, ProtectedRoute, AdminRoute
│   │   │   └── tasks/          # TaskCard, TaskFeed, MemberSearchModal
│   │   ├── hooks/              # Custom React hooks
│   │   ├── pages/              # Route-level pages
│   │   │   ├── LoginPage.tsx
│   │   │   ├── SetupPage.tsx
│   │   │   ├── TasksPage.tsx
│   │   │   ├── RankingPage.tsx
│   │   │   ├── EventsPage.tsx
│   │   │   ├── AttendeesPage.tsx
│   │   │   ├── SettingsPage.tsx
│   │   │   ├── PitPage.tsx
│   │   │   ├── LogsPage.tsx
│   │   │   └── OAuthCallbackPage.tsx
│   │   ├── services/           # API clients, SSE
│   │   ├── store/              # Zustand auth state
│   │   ├── types/              # Shared TypeScript types
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.css           # CSS custom properties (LNC theme)
│   ├── public/
│   │   └── logo.png            # LNC logo (LOGOME.png)
│   ├── tests/                  # Vitest suite
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── package.json
│
├── contracts/                  # Shared inter-agent contracts
│   └── schemas.py              # Pydantic schemas used across agents
│
├── tests/                      # Cross-cutting tests
│   ├── e2e/                    # Playwright E2E tests
│   └── fixtures/               # Test data, mock images
│
├── .local-data/                # Dev data volumes
│   ├── postgres/
│   ├── redis/
│   ├── storage/
│   └── config/
│
├── .env                        # Environment variables (not committed)
├── .env.template               # Environment variable template
├── docker-compose.yml          # Production compose (Unraid)
├── docker-compose.override.yml # Development overrides
├── docker-compose.unraid.yml   # Production volume overrides
└── AGENTS.md                   # Agent coding guidelines
```

---

## Database Schema

### Core Tables

#### `users` — Authentication Accounts
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| email | String(255) | unique, not null |
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
| rtsp_url | Text | not null |
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
| matched_name | String(255) | nullable |
| compreface_subject_id | String(255) | nullable |
| event_id | Integer | FK → civicrm_events.event_id |
| is_enrolled | Boolean | default `False` |
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
| skip_reasons | JSONB | default `list` |
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
| push_status | String(20) | default `"pending"` |
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

#### `civicrm_members` — Cached CiviCRM Contacts
| Column | Type | Constraints |
|--------|------|-------------|
| contact_id | Integer | PK |
| first_name | String(255) | not null |
| last_name | String(255) | not null |
| nickname | String(255) | nullable |
| email | String(255) | nullable |
| last_synced_at | DateTime | nullable |

#### `civicrm_events` — Cached CiviCRM Events
| Column | Type | Constraints |
|--------|------|-------------|
| event_id | Integer | PK |
| title | String(255) | not null |
| start_date | DateTime | not null |
| end_date | DateTime | nullable |
| last_synced_at | DateTime | nullable |

#### `compreface_subjects` — Enrolled Face Subjects
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| subject_name | String(255) | not null |
| compreface_subject_id | String(255) | unique |
| contact_id | Integer | FK → civicrm_members.contact_id |
| enrollment_status | String(20) | default `"pending"` |
| sample_count | Integer | default `0` |
| created_at | DateTime | default `utc_now` |

#### `pit_queue` — Admin Dispute Queue
| Column | Type | Constraints |
|--------|------|-------------|
| id | Integer | PK |
| task_id | Integer | FK → tasks.id (ON DELETE CASCADE) |
| admin_action | String(20) | nullable (enroll \| delete \| non_person) |
| admin_note | Text | nullable |
| resolved_at | DateTime | nullable |
| created_at | DateTime | default `utc_now` |

#### `admin_settings` — Runtime Configuration
| Column | Type | Constraints |
|--------|------|-------------|
| key | String(100) | PK |
| value | JSONB | default `dict` |
| category | String(50) | default `"general"` |
| description | Text | nullable |
| requires_restart | Boolean | default `False` |
| sensitive | Boolean | default `False` |
| updated_at | DateTime | default `utc_now`, onupdate |
| updated_by | Integer | FK → users.id |

---

## API Reference

### Auth Endpoints (`/auth`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| POST | `/auth/login` | Public | Email/password login → JWT access token |
| POST | `/auth/logout` | Public | Clears refresh token cookie |
| GET | `/auth/me` | Authenticated | Current user profile |
| POST | `/auth/refresh` | Public (cookie) | Refresh access token |
| POST | `/auth/reset-password` | Admin | Generate password reset link |
| POST | `/auth/reset-password/confirm` | Public | Confirm password reset |
| POST | `/auth/add-volunteer` | Admin | Create volunteer account |
| POST | `/auth/deactivate/{user_id}` | Admin | Deactivate account |
| GET | `/auth/google` | Public | Initiate Google OAuth |
| GET | `/auth/google/callback` | Public | OAuth callback |

### Task Endpoints (`/tasks`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/tasks` | Volunteer | Paginated list (filter: status, tier, volunteer_id) |
| GET | `/tasks/next` | Volunteer | Next available pending task |
| POST | `/tasks/{id}/confirm` | Volunteer | Confirm match (3s cooldown) |
| POST | `/tasks/{id}/edit` | Volunteer | Change to different member (3s cooldown) |
| POST | `/tasks/{id}/add` | Volunteer | Assign unknown face to member (3s cooldown) |
| POST | `/tasks/{id}/skip` | Volunteer | Skip with reason (3s cooldown) |
| POST | `/tasks/{id}/override` | Admin | Instant-resolve dual-approval task |

### Attendance Endpoints (`/attendance`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/attendance` | Volunteer | List records (filter: event_id, date, status) |
| POST | `/attendance/push-preview` | Admin | Dry-run CiviCRM push diff |
| POST | `/attendance/push` | Admin | Queue records for background push |

### Camera Endpoints (`/cameras`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/cameras` | Admin | List all cameras |
| POST | `/cameras` | Admin | Create camera |
| PUT | `/cameras/{id}` | Admin | Update camera |
| DELETE | `/cameras/{id}` | Admin | Delete camera |
| GET | `/cameras/{id}/preview` | Admin | Grab JPEG preview frame |
| POST | `/cameras/{id}/reconnect` | Admin | Force reconnect |

### Event Endpoints (`/events`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/events` | Volunteer | List cached CiviCRM events |
| POST | `/events/sync` | Admin | Force sync from CiviCRM |

### Member Endpoints (`/members`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/members` | Volunteer | Search by name/email |
| POST | `/members/sync` | Admin | Force sync from CiviCRM |

### Pit Queue Endpoints (`/pit`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/pit` | Admin | List admin pit queue |
| POST | `/pit/{id}/enroll` | Admin | Enroll face to member |
| POST | `/pit/{id}/delete` | Admin | Mark as trash |
| POST | `/pit/{id}/non-person` | Admin | Mark as false detection |

### Audit Endpoints (`/api/audit`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/api/audit/tasks` | Volunteer | Quality audit cards (only when main queue empty) |
| POST | `/api/audit/{id}/confirm` | Volunteer | Confirm enrolled match correct |
| POST | `/api/audit/{id}/deny` | Volunteer | Flag match incorrect |
| POST | `/api/audit/{id}/change` | Volunteer | Change to different contact |

### Leaderboard Endpoints (`/leaderboard`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/leaderboard?period={this_month\|previous_month\|all_time}` | Volunteer | Rankings |

### Log Endpoints (`/logs`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/logs` | Admin | Filtered logs with pagination |

### Settings Endpoints (`/settings`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/settings` | Admin | Read all settings |
| PUT | `/settings` | Admin | Update settings |
| POST | `/settings/safe-mode` | Admin | Toggle safe mode |

### Setup Endpoints (`/setup`)

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/setup/status` | Public | Check if setup completed |
| POST | `/setup` | Public (one-time) | Initial bootstrap (permanently locks after success) |

### Health & SSE

| Method | Path | Access | Description |
|--------|------|--------|-------------|
| GET | `/health` | Public | DB, Redis, Compreface status |
| GET | `/health/queue` | Public | Queue saturation metrics |
| GET | `/tasks/feed` | Authenticated | SSE stream for real-time task updates |

---

## Authentication & Authorization

### First-Boot Setup

The system uses a 3-tier configuration system:
1. **BootstrapConfig** (file): Immutable config read from `bootstrap.json` on startup
2. **DynamicSettings** (DB): Runtime-mutable settings loaded from `admin_settings` table
3. **LegacySettings** (env): Backwards-compatible environment variables

On first startup, the setup wizard at `/setup` creates:
- Admin user account
- `admin_settings` table entries
- Initial cameras (if provided)
- `bootstrap.json` file

After successful setup, the endpoint returns **410 GONE** permanently.

### Login Methods

- **Primary**: Local email/password (bcrypt). Login form is the default UI.
- **Future**: Google OAuth via `ENABLE_GOOGLE_OAUTH` setting.
- **No self-registration**: Only admins can create volunteer accounts.

### Roles & Permissions

| Feature | Volunteer | Admin |
|---------|-----------|-------|
| Scrolling tasks | Yes | Yes |
| Leaderboard | Yes | Yes |
| Events view | Yes | Yes |
| Push attendance to CiviCRM | No | Yes |
| Settings (API keys, cameras) | No | Yes |
| Log viewer / Pit queue | No | Yes |
| Account management | No | Yes |

### JWT Configuration

- Algorithm: HS256
- Access token expiry: 15 minutes (configurable)
- Refresh token expiry: 7 days (stored in HttpOnly cookie)
- Cookie flags: Secure in production, SameSite=lax

---

## Recognition Pipeline

### Confidence Tier Mapping

| Similarity Score | Tier Value | Action | Volunteer Approvals |
|-----------------|------------|--------|---------------------|
| >= 0.98 | `"100"` | Auto-logged to attendance | None |
| 0.91 – 0.979 | `"91-99"` | Task created, 1 volunteer confirms | 1 |
| < 0.91 | `"below90"` | Task created, 2 volunteers confirm | 2 |
| No match / Error | `"unknown"` | Task created, 2 volunteers confirm | 2 |

### Task Resolution Flow

```
Detection → Compreface Recognition → Tier Classification
    │
    ├── tier="100" → Auto-log attendance → Done
    │
    └── tier="91-99" / "below90" / "unknown"
            │
            ▼
        Create Task → Redis Queue → Volunteer Portal
            │
            ├── Volunteer clicks "Confirm"
            │       └── current_approvals += 1
            │       └── If >= required_approvals → resolved → attendance record
            │
            ├── Volunteer clicks "Edit" (change member)
            │       └── Updates detection.matched_name
            │       └── current_approvals += 1
            │
            ├── Volunteer clicks "Add" (assign new member)
            │       └── Updates detection.matched_name + is_enrolled=True
            │       └── current_approvals += 1
            │
            └── Volunteer clicks "Skip"
                    └── skip_count += 1
                    └── If skip_count >= 4 from >=2 volunteers → PIT queue
```

### Skip Logic

- **Per-volunteer limit**: Max 2 skips per task per volunteer
- **PIT trigger**: 4 total skips from >=2 different volunteers → task goes to admin PIT queue
- **Skip reasons**: "Face unclear", "Person unknown", "Not a face", "Will ask later"
- **PIT actions**: Admin can enroll, delete, or mark as non-person

### Scoring (Leaderboard)

| Action | Points |
|--------|--------|
| Confirm or Add | +1 |
| Edit (name change) | +2 |
| Incorrect confirmation (admin audit finds error) | -3 |

### Queue Management

- **Hard limit**: 500 tasks → frame ingestion pauses
- **Resume limit**: Falls below 400 → ingestion resumes
- **Deduplication**: 30-second pHash ring buffer prevents duplicate detections
- **Task expiry**: 31 days → auto-expired if unresolved
- **Safe mode**: Admin toggle stops all recognition instantly

### Data Retention

- **Untrained face snapshots**: Deleted after 31 days
- **Trained embeddings**: Stored indefinitely in Compreface
- **Attendance records**: Permanent
- **Logs**: Permanent

---

## Docker Services

### Development (`docker-compose.override.yml`)

```yaml
Services:
  postgres:     port 5432 host, volume ./.local-data/postgres
  redis:        port 6379 host, volume ./.local-data/redis
  seraphim-backend:  port 8000 host, hot-reload, ./backend:/app mount
  seraphim-frontend: port 5173 host, hot-reload, ./frontend:/app mount
  seraphim-rtsp-worker:  ./backend:/app mount
  seraphim-queue-worker: ./backend:/app mount
  mock-camera-1:    port 8554 host
```

### Production (`docker-compose.yml`)

```yaml
Services:
  postgres:           no host port (internal only)
  redis:              no host port (internal only)
  seraphim-backend:        port 3001:8000
  seraphim-frontend:       port 3000:80 (nginx static)
  seraphim-rtsp-worker:    privileged mode
  seraphim-queue-worker:   2 CPU, 2GB RAM limit
```

> **Note:** Compreface runs as an **external service** (not in Docker Compose). Point the system to your Compreface instance via the setup wizard or admin settings.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `COMPREFACE_URL` | Yes | Compreface API base URL |
| `COMPREFACE_API_KEY` | Yes | Compreface API key |
| `JWT_SECRET` | Yes | JWT signing secret (min 32 bytes) |
| `CIVICRM_URL` | No | CiviCRM REST API base URL |
| `CIVICRM_API_KEY` | No | CiviCRM API key |
| `CIVICRM_SITE_KEY` | No | CiviCRM site key |
| `GOOGLE_CLIENT_ID` | No | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | No | Google OAuth client secret |
| `ADMIN_EMAILS` | Yes | Comma-separated admin emails |
| `STORAGE_PATH` | Yes | Path for face snapshots |

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
# View logs
docker compose logs -f seraphim-backend

# Run backend tests
docker compose exec seraphim-backend pytest tests/ -v

# Database shell
docker compose exec postgres psql -U seraphim -d seraphim_attendance

# Rebuild a single service
docker compose build --no-cache seraphim-backend

# Stop everything
docker compose down
```

---

## Production Deployment (Unraid)

### File Transfer
Copy project files to Unraid via USB/SMB.

### Unraid Configuration
1. Install **Docker Compose Manager** plugin
2. Create `/mnt/user/appdata/seraphim/` directory
3. Copy `docker-compose.yml`, `.env`, and backend/frontend directories
4. Create data directories:
   ```
   /mnt/user/appdata/seraphim/storage/
   /mnt/user/appdata/seraphim/config/
   ```
5. Update `.env` with production secrets
6. Run `docker compose up -d`
7. Run `docker compose exec seraphim-backend alembic upgrade head`
8. Complete setup wizard

### Production URLs
| Service | URL |
|---------|-----|
| Frontend | http://unraid-ip:3000 |
| Backend API | http://unraid-ip:3001 |

---

## Security

### Current Posture: GOOD

| Category | Status |
|----------|--------|
| Authentication | bcrypt + JWT, refresh tokens, role-based access |
| Authorization | Admin/volunteer separation, setup lock |
| Input Validation | Pydantic v2 on all endpoints |
| SQL Injection | SQLAlchemy ORM used exclusively |
| Command Injection | `create_subprocess_exec` with list args (no shell) |
| Information Disclosure | Fixed — generic error messages |
| Secrets Management | No hardcoded secrets, all via env/config |
| CORS | Restricted to `FRONTEND_URL` only |
| Session Security | HttpOnly, Secure flag in production, SameSite=lax |

### Known Gaps

| Priority | Issue | Recommendation |
|----------|-------|----------------|
| High | No HTTPS enforcement | Deploy behind reverse proxy (nginx/traefik) with HTTPS termination |
| Medium | Weak password complexity | Add complexity rules or generate strong passwords in setup wizard |
| Medium | No dedicated security audit log | Add security event logging for compliance |

### Data Protection

- **Passwords**: bcrypt hashed with salt (adaptive)
- **JWT secrets**: Stored in config, never in code
- **Face images**: Stored on filesystem with date-derived paths; 31-day retention for untrained
- **Database**: No plaintext passwords; parameterized queries throughout

---

## Branding & UI

### LNC Visual Identity

The frontend implements the **60-30-10 design principle** using LNC church colors:

| Token | Role | HSL Value | Hex |
|-------|------|-----------|-----|
| Primary (60%) | Church yellow | `48 90% 62%` | `#F5D547` |
| Background (30%) | Warm cream | `45 40% 96%` | `#FBF8F0` |
| Foreground (10%) | Deep charcoal | `220 15% 15%` | `#1F2128` |

### CSS Custom Properties

```css
:root {
  --background: 45 40% 96%;
  --foreground: 220 15% 15%;
  --card: 45 30% 98%;
  --primary: 48 90% 62%;
  --primary-foreground: 220 15% 15%;
  --secondary: 45 30% 92%;
  --border: 45 20% 88%;
  --ring: 48 90% 50%;
  --radius: 0.5rem;
}
```

### Logo

The LNC logo (`LOGOME.png`) is served at `/logo.png` and displayed on:
- Login page (centered, 96px height)
- Setup wizard header
- Browser favicon and PWA icons

### Mobile-First Design

- Baseline width: 375px
- Bottom tab navigation with safe-area insets
- Touch targets minimum 44px
- `user-scalable=no` for app-like feel
- PWA-capable with `apple-mobile-web-app-capable`

---

## Troubleshooting

### Backend build timeout
The backend image includes ffmpeg and ~250 Debian dependencies (~680MB). First build may take 30+ minutes. Subsequent builds use Docker layer cache.

### Compreface not responding
Ensure your external Compreface instance is running and accessible. Check the connection from admin settings or test via:
```bash
curl "http://your-compreface-url:port/api/v1/health"
```

### Frontend proxy errors
The Vite dev server proxies `/api` to `http://seraphim-backend:8000` (Docker internal DNS). If the backend container name changes, update `vite.config.ts`.

### Database connection failures
Ensure postgres healthcheck passes before backend starts. The `depends_on` with `condition: service_healthy` handles this, but on slow systems you may need to restart the backend container manually.

### Setup wizard 410 error
If setup fails partway through, a stale `bootstrap.json` may be written. Delete `.local-data/config/bootstrap.json` and retry.

---

## Appendix A: TypeScript Frontend Types

```typescript
export interface Task {
  id: number;
  detection_id: number;
  tier: '100' | '91-99' | 'below90' | 'unknown';
  face_thumbnail_path: string | null;
  matched_name: string | null;
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
