# Project Seraphim — Agent Guidelines

## Project Structure

```
├── backend/                 # FastAPI Python backend
│   ├── app/
│   │   ├── routers/         # API route handlers
│   │   │   ├── analytics.py # Aggregate stats + CSV export (admin)
│   │   │   ├── attendance.py
│   │   │   ├── audit.py     # Quality audit queue (prefix: /audit)
│   │   │   ├── auth.py      # Login, OAuth, user management
│   │   │   ├── cameras.py
│   │   │   ├── events.py
│   │   │   ├── health.py    # Includes safe_mode in /health/queue
│   │   │   ├── leaderboard.py
│   │   │   ├── logs.py
│   │   │   ├── members.py
│   │   │   ├── pit.py
│   │   │   ├── settings.py
│   │   │   ├── setup.py
│   │   │   ├── storage.py   # Authenticated file serving for /storage/**
│   │   │   ├── tasks.py
│   │   │   └── uploads.py
│   │   ├── services/        # Business logic, external clients
│   │   ├── workers/         # Background workers (RTSP, queue consumer)
│   │   ├── utils/           # Auth, helpers
│   │   ├── main.py          # FastAPI app entry
│   │   ├── config.py        # 3-tier settings (Bootstrap/Dynamic/Legacy)
│   │   ├── database.py      # SQLAlchemy engine & sessions
│   │   ├── models.py        # SQLAlchemy ORM models
│   │   └── schemas.py       # Pydantic request/response models
│   ├── alembic/             # Database migrations
│   ├── tests/               # pytest suite
│   ├── Dockerfile           # Main backend image
│   ├── Dockerfile.worker    # RTSP worker image
│   └── requirements.txt
├── frontend/                # React Vite frontend
│   ├── src/
│   │   ├── components/
│   │   │   ├── layout/      # BottomNav, ProtectedRoute, AdminRoute
│   │   │   ├── tasks/       # TaskCard, TaskFeed, MemberSearchModal
│   │   │   └── ui/          # Shared: LoadingState, EmptyState, ErrorState, ConfirmDialog
│   │   ├── hooks/           # useAuth (in-memory token + refresh)
│   │   ├── pages/           # Route-level pages (see App.tsx for full list)
│   │   ├── services/
│   │   │   ├── api.ts       # Axios client with 401→refresh interceptor
│   │   │   ├── connectionUrl.ts  # Shared Postgres/Redis URL builders
│   │   │   └── sse.ts       # SSEClient (passes token via ?_t= query param)
│   │   ├── store/
│   │   │   ├── authStore.ts # In-memory token; no localStorage
│   │   │   └── taskStore.ts
│   │   ├── types/           # TypeScript types
│   │   ├── App.tsx          # Routes (includes /audit, /settings/users, /settings/attendance, /dashboard)
│   │   ├── index.css        # Design tokens (light + dark)
│   │   └── main.tsx         # Applies persisted dark mode before first paint
│   ├── index.html
│   ├── vite.config.ts       # Includes vite-plugin-pwa
│   ├── tailwind.config.js   # darkMode:'class'; token colors with <alpha-value>
│   └── Dockerfile
├── scripts/
│   ├── webhook_listener.py  # Gitea push webhook (binds 127.0.0.1; requires WEBHOOK_SECRET ≥32 chars)
│   └── deploy.sh
├── docker-compose.yml       # Unraid Docker Compose (no privileged on RTSP worker)
└── .env.template            # Environment variable template
```

## Coding Standards

### Backend (Python)
- Python 3.11+
- `async`/`await` everywhere I/O bound
- SQLAlchemy 2.0 async session pattern
- Pydantic v2 for all request/response validation
- Type hints on all public functions
- `black` formatting, `ruff` linting
- pytest with `pytest-asyncio`

### Frontend (TypeScript/React)
- React 18 functional components + hooks
- TailwindCSS with **design tokens** — always use token classes (`bg-primary`, `text-foreground`, `border-border`, `bg-background`, `bg-card`) instead of hardcoded hex values
- Dark mode is wired: every new component must work under `.dark` (use `dark:` prefix variants or token classes which adapt automatically)
- Mobile-first: all designs start at 375px width
- `zustand` for auth/task state; `tanstack-query` for server state
- Toasts via `sonner` — never use `alert()` or `confirm()`; always surface server `detail` messages
- Destructive confirmations use the `ConfirmDialog` component (`frontend/src/components/ui/ConfirmDialog.tsx`)

### Auth / Token handling
- Access token lives **in-memory only** (Zustand) — never in localStorage
- Refresh cookie is HttpOnly; the 401 interceptor in `api.ts` attempts `/auth/refresh` before redirecting to `/login`
- SSE endpoint (`/tasks/feed`) receives the access token via `?_t=` query param since `EventSource` cannot send headers

### API prefix convention
- All routers are registered without an `/api` prefix in FastAPI (the Vite dev proxy and nginx production proxy strip `/api` before forwarding)
- Exception: the `/storage` router is also prefix-free and auth-gated
- `check_setup_complete` dependency no longer has a hard-coded allow-list — setup and auth routes are registered before it is applied

### Database
- Alembic migrations for all schema changes
- Never modify migration files after they are applied
- JSON columns use `JSON().with_variant(JSONB, "postgresql")` (jsonb in prod, JSON for SQLite tests) — see `models.py`
- Timestamps are naive UTC (`utc_now()`); query code must compare with naive `datetime.now(timezone.utc).replace(tzinfo=None)`
- Index hot/growing columns (see migration `e2f3a4b5c6d7`); foreign keys with `ON DELETE` specified explicitly
- SQLAlchemy `default=0` only applies at flush — initialize counters explicitly before `+=` (see `VolunteerStat`)

### Domain rules
- **Active event** — camera detections are tagged with `dynamic_settings.get_active_event_id()`; attendance is only logged when it's set. Set via `POST /events/set-active`.
- **Member resolution** — the pipeline maps CompreFace `subject_id` → `ComprefaceSubject.contact_id` and stores `matched_name="member:{id}"`; resolve to a display name in API responses.
- **List endpoints** — always paginate/clamp (`/tasks`, `/logs`, `/attendance`, `/members`).

### Docker
- All services must have `healthcheck` blocks
- Use `restart: unless-stopped`
- `privileged: true` is **not** permitted — use `cap_add` if a specific capability is required
- `.env` file for secrets (never commit)
- Set `ENVIRONMENT=production` in production compose

## Key Security Rules

- **JWT secret** — always read from `dynamic_settings.get_jwt_secret()` only; never fall back to an empty string. The app refuses to serve (fails `lifespan`) if setup is complete but the secret is empty or shorter than 32 chars.
- **RTSP URLs** — validated to start with `rtsp://` or `rtsps://` in schemas; never passed to ffmpeg as shell strings.
- **Setup test endpoints** — `/setup/test-connection` and `/setup/test-services` return 410 after setup is complete.
- **Webhook listener** — requires `WEBHOOK_SECRET` ≥ 32 chars to start; binds to `127.0.0.1` by default.
- **Face images** — served only through the authenticated `/storage/{path}` route with path-containment checks.

## Testing
- Run inside the backend image (Python 3.11, matches prod): `docker compose run --rm seraphim-backend pytest tests/ -q`
- `conftest.py` uses the app's own engine over a file SQLite DB (so HTTP + pipeline sessions share one DB), creates/drops schema per test, sets `REDIS_URL=memory://`, and pre-loads a ≥32-char test JWT secret
- CI (`.github/workflows/ci.yml`) runs pytest (+ Redis service), frontend build/lint, and `pip-audit`/`npm audit`
- Mock external services (CompreFace, CiviCRM, Google OAuth) in unit tests
- When changing behavior, update the tests that assert it (don't leave them asserting old behavior)

## Git Workflow
- Do not run `git commit`, `git push`, or any git mutations unless explicitly asked
- Keep changes minimal and focused
- Follow existing code style
