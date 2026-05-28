# Project Seraphim — Agent Guidelines

## Project Structure

```
├── backend/                 # FastAPI Python backend
│   ├── app/
│   │   ├── routers/         # API route handlers
│   │   ├── services/        # Business logic, external clients
│   │   ├── workers/         # Background workers (RTSP, queue consumer)
│   │   ├── utils/           # Auth, helpers
│   │   ├── main.py          # FastAPI app entry
│   │   ├── config.py        # Pydantic settings
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
│   │   ├── components/      # React components (by feature)
│   │   ├── hooks/           # Custom React hooks
│   │   ├── services/        # API clients, SSE
│   │   ├── types/           # TypeScript types
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── tests/               # Vitest suite
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── Dockerfile
├── contracts/               # Shared inter-agent contracts
│   └── schemas.py           # Pydantic schemas used across agents
├── tests/                   # Cross-cutting tests
│   ├── e2e/                 # Playwright E2E tests
│   └── fixtures/            # Test data, mock images
├── docker-compose.yml       # Unraid Docker Compose
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
- `shadcn/ui` for base components
- TailwindCSS for styling
- Mobile-first: all designs start at 375px width
- `zustand` or React Context for state (no Redux)
- `tanstack-query` for server state

### Database
- Alembic migrations for all schema changes
- Never modify migration files after commit
- Use `TIMESTAMP WITH TIME ZONE` for all timestamps
- Foreign keys with `ON DELETE` specified explicitly

### Docker
- All services must have `healthcheck` blocks
- Use `restart: unless-stopped`
- Non-root user where possible
- `.env` file for secrets (never commit)

## Inter-Agent Contracts

Agents communicate via shared files in `contracts/` and via the running API.
- **Never** break a Pydantic schema without updating `contracts/schemas.py`
- **Never** change an API route path without updating this file and notifying other agents
- Backend routers must return consistent error shapes: `{ "detail": "..." }` or `{ "detail": [{"msg":"...","loc":["field"]}] }`

## Testing
- Every backend service function should have a unit test
- Every frontend component with logic should have a component test
- E2E tests cover: login → task confirm → leaderboard → logout
- Mock external services (Compreface, CiviCRM, Google OAuth) in unit tests

## Git Workflow
- Do not run `git commit`, `git push`, or any git mutations unless explicitly asked
- Keep changes minimal and focused
- Follow existing code style
