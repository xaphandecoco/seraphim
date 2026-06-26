# Project Seraphim Wiki

**LNC Attendance System** — Facial recognition-based church attendance tracking for Light of the World Worldwide Ministries — North Caloocan (~600 active members).

## Quick Links

- [[Architecture]] — System components, request flow, tech stack, data dependencies
- [[Backend API]] — FastAPI routes, auth, token management
- [[Backend Services]] — Business logic layer (face pipeline, contact service, queue manager)
- [[Data Model]] — 14 SQLAlchemy tables, schema, relationships
- [[Frontend]] — React pages, components, state management, dark mode
- [[Deployment]] — Docker Compose, production options (Caddy, Cloudflare Tunnel, LAN HTTP)
- [[Face Recognition Pipeline]] — CompreFace integration, tier mapping, dual-approval workflow
- [[Conventions]] — Coding standards, patterns, security rules

## What It Does

```
Cameras (RTSP)
    ↓ FFmpeg frame capture (RTSP Worker)
    ↓ Face detection/recognition (CompreFace)
Redis Queue
    ↓ Queue consumer (Queue Worker)
    ↓ Task creation by confidence tier
Volunteer Review UI
    ↓ Confirm/Edit/Add/Skip (dual-approval for low confidence)
Attendance Record
    ↓ Push to CiviCRM API (Admin)
Live sync across 600 members
```

## Key Features

- **Dual-approval workflow**: Low-confidence matches (< 91%) require 2 volunteer confirmations; each volunteer acts once
- **Tiered routing**: Tier-100 (≥98% match) auto-logged; Tier-91-99 needs 1 volunteer; below-90 needs 2
- **Active event**: Detections tagged with the current active event; attendance only logged if event is set
- **Gamification**: Volunteer leaderboard with accuracy scoring (this month / previous month / all-time)
- **Quality audit**: Volunteers audit enrolled faces during queue downtime
- **Admin pit queue**: Disputed/multi-skipped tasks escalated for manual review
- **CiviCRM sync**: Batch push to REST v3 API; dead-letter queue for failures
- **Custom fields**: Extensible contact schema via admin UI
- **PWA**: Mobile app installable on home screen; offline app shell, network-first API caching

## Tech Stack

- **Backend**: Python 3.11, FastAPI, SQLAlchemy 2.0 async, Alembic, PostgreSQL (asyncpg)
- **Frontend**: React 18, TypeScript, Vite, TailwindCSS, Zustand, TanStack Query, sonner (toasts)
- **Cache/Queue**: Redis 7 (slowapi rate-limiting, SSE broadcaster, JTI denylist)
- **Recognition**: CompreFace (Exadel) — external service
- **CMS**: CiviCRM REST v3 API (optional)
- **Deploy**: Docker Compose, nginx (production frontend), Caddy or Cloudflare Tunnel (TLS)

## Recent Production Readiness Audit (✓ Complete)

All major security, UX, and operational gaps resolved:
- JWT secret fail-fast on startup if setup complete
- Access token in-memory only; refresh token HttpOnly + rotated on every refresh
- Token-type lockdown: API rejects refresh tokens
- Secure cookies + `SameSite=lax` in production
- `/storage/*` biometric images authenticated + path-contained
- Setup wizard reloads in-memory settings post-completion
- Dark mode wired (class-based, persistent)
- PWA manifest + Workbox service worker
- Analytics dashboard + CSV export
- Safe mode toggle stops recognition instantly
- Rate-limiting documented (keyed on real client IP behind proxy)

## File Structure

```
backend/               FastAPI app
├── app/routers/      Handlers (tasks, attendance, auth, members, events, etc.)
├── app/services/     Business logic (face pipeline, contact service, queue manager)
├── app/workers/      RTSP capture, queue consumer
├── app/models.py     14 SQLAlchemy tables
├── app/schemas.py    Pydantic validation
└── alembic/          Migrations (never edit post-apply)

frontend/             React Vite app (PWA)
├── src/components/   Layout, UI, tasks, contacts
├── src/pages/        Dashboard, tasks, contacts, audit, settings, login
├── src/services/     Axios client, SSE, API functions
├── src/store/        Zustand (auth, tasks)
└── tailwind.config   Design tokens (light + .dark)

docker-compose.yml    Production stack (no privileged; ENVIRONMENT=production)
```

## Central Conventions

See [[Conventions]] for full detail. Highlights:

- **Python**: `async`/`await` everywhere I/O bound, type-hint all public functions
- **React**: TailwindCSS design tokens (never hardcoded hex), dark mode wired, mobile-first (375px)
- **Auth**: Access token in-memory (Zustand), refresh via HttpOnly cookie, 401 → refresh interceptor
- **Database**: Alembic migration for every schema change, JSONB columns use `.with_variant(JSONB, "postgresql")`
- **API**: No `/api` prefix in FastAPI (nginx strips it); all paginated list endpoints
- **Toasts**: sonner only (no `alert()` or `confirm()`)

## Running Locally

```bash
# 1. Environment
cp .env.template .env
# Edit JWT_SECRET, DB_PASSWORD, etc.

# 2. Data directories
mkdir -p .local-data/postgres .local-data/redis .local-data/storage .local-data/config

# 3. Stack
docker compose -f docker-compose.yml -f docker-compose.override.yml up -d

# 4. Migrate
docker compose exec seraphim-backend alembic upgrade head

# 5. Complete setup wizard at http://localhost:5173
```

## Key Contacts

- **Developer**: John Atienza
- **Church**: Light of the World Worldwide Ministries — North Caloocan (lightnc.org)
- **Deployment**: Unraid home server (Linux)
