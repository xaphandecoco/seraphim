# Project Seraphim — Claude Code Guide

## Stack
- **Backend**: FastAPI + SQLAlchemy 2.0 async + Alembic + PostgreSQL (asyncpg) / SQLite (tests)
- **Frontend**: React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + Zustand + sonner
- **Cache/Queue**: Redis (slowapi rate-limiting, SSE broadcaster, JTI denylist)
- **Deploy**: Docker Compose + nginx reverse proxy + Cloudflare Tunnel or Caddy TLS

## Key Commands
```bash
# Backend tests (from backend/)
DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test pytest tests/ -q

# Backend lint (from backend/)
ruff check app

# Backend audit (from backend/)
pip-audit -r requirements.txt

# Frontend build + typecheck (from frontend/)
npm run build

# Frontend lint (from frontend/)
npm run lint

# Frontend tests (from frontend/)
npm run test:run
```

## Layout
```
backend/app/       FastAPI app, routers/, services/, utils/, workers/
backend/alembic/   Migrations (never edit after applying)
backend/tests/     pytest suite (conftest.py uses aiosqlite + memory:// Redis)
frontend/src/      React app — pages/, components/, store/, services/, hooks/
docs/              BACKLOG.md, PRODUCTION_RUNBOOK.md
scripts/           deploy.sh, backup.sh, webhook_listener.py
```

## Conventions (see AGENTS.md for full details)
- Use `async`/`await` everywhere I/O bound in Python; type-hint all public functions
- Use TailwindCSS design tokens (`bg-card`, `text-foreground`, `bg-background`) — never hardcoded hex
- Toasts via `sonner`; surface `err.response?.data?.detail` on API failures
- Access token in-memory only (Zustand); refresh token via HttpOnly cookie
- All API routes have no `/api` prefix in FastAPI (nginx strips it); storage router is auth-gated
- Alembic migration for every schema change; JSON columns use `.with_variant(JSONB, "postgresql")`
- Do not run git mutations unless explicitly asked
