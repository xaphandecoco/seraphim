# Coding Conventions

Extracted from CLAUDE.md and AGENTS.md. All new code must follow these patterns.

---

## Python Backend

### Async & I/O

- **Always use `async`/`await`** for all I/O-bound operations (DB queries, HTTP calls, file I/O, Redis)
- **Type-hint all public functions** with argument types and return types
- **Import style**: `from app.database import async_session` for session management
- **Session management**: Use context manager pattern
  ```python
  from app.database import async_session
  async with async_session() as db:
      result = await db.execute(...)
  ```

### SQLAlchemy 2.0 Async

- **ORM only**: No raw SQL strings (parameterized via ORM)
- **Async execution**: `await session.execute(...)` / `await session.scalars(...)`
- **Explicit refresh**: `await session.refresh(obj)` to load lazy-loaded relations
- **Transactions**: Default to automatic commit on context exit; use `session.rollback()` on error
- **Locking**: Use `with_for_update()` on SELECT when row contention expected
  ```python
  task = await db.scalar(select(Task).where(...).with_for_update())
  ```

### Pydantic v2

- **All request/response models** as Pydantic schemas (not bare dicts)
- **Validation**: Custom validators using `field_validator` / `model_validator`
- **JSON serialization**: `model_dump()`, `model_dump_json()`
- **Config**: Set `from_attributes = True` to allow ORM → Pydantic mapping

### Database Migrations (Alembic)

- **One migration per schema change** (never combine unrelated changes)
- **Never edit a migration after it is applied** (create a new one instead)
- **JSON columns**: Always use `.with_variant(JSONB, "postgresql")` for Postgres native JSONB + SQLite compatibility
  ```python
  skip_reasons = Column(JSON().with_variant(JSONB, "postgresql"), default=list)
  ```
- **Naming**: `def upgrade()` / `def downgrade()` with clear docstrings

### Models & Relationships

- **Timestamps**: Use `utc_now()` (naive UTC, no timezone)
- **Defaults**: `default=` only applies at flush; always initialize explicitly before `+=` (e.g., counters)
  ```python
  if stat.tasks_confirmed is None:
      stat.tasks_confirmed = 0
  stat.tasks_confirmed += 1
  ```
- **Foreign keys**: Always specify `ON DELETE` behavior explicitly
  ```python
  camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="SET NULL"))
  ```
- **Indexes**: Index hot/growing columns explicitly
  ```python
  __table_args__ = (
      Index("ix_detections_status_created", "status", "created_at"),
  )
  ```
- **Dual-approval guard**: Use partial unique index to enforce one action per volunteer per task
  ```python
  __table_args__ = (
      Index("uq_task_action_approval", "task_id", "volunteer_id",
            postgresql_where=and_(action != "skip"),
            sqlite_where=and_(action != "skip"),
            unique=True),
  )
  ```

### Error Handling

- **Explicit HTTPExceptions**: Raise `HTTPException(status_code=..., detail=...)` for API errors
- **IntegrityError on dual-approval**: Catch & convert to `HTTPException(400, "Already acted on this task")`
- **Logging**: Use Python `logging` module with descriptive messages

### Testing

- **Conftest pattern**: Use `conftest.py` with async fixtures
- **Database**: `async_session` with auto-create/drop per test (SQLite)
- **Redis**: `REDIS_URL=memory://` in tests
- **Mocks**: Mock CompreFace, CiviCRM, Google OAuth (no external calls in CI)

---

## React Frontend

### Component Structure

- **Functional components only** (no class components)
- **Hooks pattern**: `useAuth`, `useQuery`, `useMutation`, `useState`, etc.
- **File organization**: `components/`, `pages/`, `services/`, `store/`, `hooks/`, `types/`
- **TypeScript**: Every component must be typed
  ```typescript
  interface TaskCardProps {
    task: Task;
    onAction: (taskId: number, action: string) => Promise<void>;
  }
  export function TaskCard({ task, onAction }: TaskCardProps) { ... }
  ```

### Styling with TailwindCSS

- **Design tokens only** — never hardcoded hex values or inline styles
- **Token colors**: `bg-primary`, `text-foreground`, `border-border`, `bg-card`, `bg-background`
- **Opacity**: Use `bg-primary/20`, `text-foreground/50` (with `<alpha-value>` in tailwind.config)
- **Dark mode**: Every component must work under `.dark` class
  - Use `dark:bg-card` prefix variants OR rely on token colors which auto-adapt
  - Test by toggling dark mode in Settings

**Token mapping** (from `index.css`):
| Token | Light | Dark | Purpose |
|-------|-------|------|---------|
| `primary` | HSL 48 90% 62% (yellow) | Dark yellow | Church brand (60%) |
| `background` | HSL 45 40% 96% (cream) | Dark background | Page background (30%) |
| `foreground` | HSL 220 15% 15% (charcoal) | Light text | Text (10%) |
| `card` | HSL 45 30% 98% | Dark card | Card backgrounds |
| `border` | HSL 45 20% 88% | Dark border | Dividers, borders |

### State Management

- **Auth** (Zustand): In-memory access token only (never localStorage)
  ```typescript
  const { token, setToken } = useAuthStore();
  // On login: setToken(accessToken)
  // On logout: setToken(null)
  // On page load: useAuth hook calls /auth/refresh to re-issue
  ```
- **Task state** (Zustand): UI-level state (filter, sort, pagination)
- **Server state** (TanStack Query): All API-derived data (`useQuery`, `useMutation`)
  - Automatic cache invalidation on mutation
  - Retry on error + exponential backoff
  - Fallback polling when SSE unavailable

### User Feedback

- **Toasts**: sonner only (no `alert()`, `confirm()`, or browser dialogs)
  ```typescript
  import { toast } from "sonner";
  toast.success("Task confirmed!");
  toast.error(err.response?.data?.detail ?? "An error occurred");
  ```
- **Loading states**: `LoadingState` component (not ad-hoc "Loading…" text)
- **Error states**: `ErrorState` component with retry button
- **Empty states**: `EmptyState` component with clear explanation
- **Confirmations**: `ConfirmDialog` component (not `confirm()`)

### Async Operations

Every async operation must have 3 states:
```typescript
const { data, isLoading, error } = useQuery({
  queryKey: ["tasks"],
  queryFn: async () => { ... },
});

return (
  <>
    {isLoading && <LoadingState />}
    {error && <ErrorState error={error} />}
    {data && data.length === 0 && <EmptyState />}
    {data && data.map(task => <TaskCard key={task.id} task={task} />)}
  </>
);
```

### Mobile-First Design

- **Baseline**: 375 px width (iPhone SE)
- **Touch targets**: ≥ 44 px height/width
- **Safe areas**: Safe-area insets for notches + home indicators
- **Viewport**: `user-scalable=no` removed (allow pinch zoom for accessibility)
- **Bottom nav**: Safe-area padding + "More" sheet for admin tabs at 375 px

### Dark Mode Implementation

- **Tailwind config**: `darkMode: 'class'` (not `media`)
- **Persistence**: `localStorage.seraphim-theme` (light | dark | auto)
- **Application**: Before first paint in `main.tsx`
  ```typescript
  const theme = localStorage.getItem("seraphim-theme") ?? "light";
  if (theme === "dark" || (theme === "auto" && matchMedia("prefers-color-scheme: dark").matches)) {
    document.documentElement.classList.add("dark");
  }
  ```
- **Toggle**: Settings → Appearance → `setTheme("dark" | "light")`

### HTTP Client (Axios)

- **Base config**: Set default headers (Content-Type, auth)
- **Interceptors**: 
  - Request: Add `Authorization: Bearer {token}` if token exists
  - Response: On 401, call `/auth/refresh` → retry original request once → redirect to `/login` if still 401
  - Error: Expose `err.response?.data?.detail` for toasts
  ```typescript
  api.interceptors.response.use(
    (res) => res,
    async (err) => {
      if (err.response?.status === 401) {
        // Try refresh once
        try {
          await api.post("/auth/refresh");
          return api(err.config);
        } catch {
          window.location.href = "/login";
        }
      }
      throw err;
    }
  );
  ```

### SSE (Server-Sent Events)

- **Endpoint**: `GET /tasks/feed` (WebSocket alternative for real-time task updates)
- **Auth**: Token passed via `?_t=<token>` query param (EventSource can't set headers)
- **Fallback**: Polling via `/tasks` query if SSE unavailable
- **Heartbeat**: Backend sends `: keepalive` comment every 15s to survive CDN idle timeouts

### Linting & Formatting

- **ESLint**: Check with `npm run lint`
- **Prettier**: Format with `npm run format` (if configured)
- **TypeScript**: `npm run build` includes type checking

---

## API & Authentication

### Token Handling

| Token | Storage | Lifetime | Scope |
|-------|---------|----------|-------|
| Access | Zustand (in-memory) | 15 min | API calls (reject if `type="refresh"`) |
| Refresh | HttpOnly cookie | 7 days | Token re-issue (rotated on every refresh, old JTI deny-listed) |

**Key rules**:
- Access token never persisted (localStorage forbidden)
- Refresh cookie `Secure` in HTTPS, plain HTTP on LAN (per-request flag)
- On logout: `/auth/logout` deny-lists JTI, clears cookie
- On 401: Attempt single `/auth/refresh` before redirect to `/login`

### Route Conventions

- **No `/api` prefix** in FastAPI router definitions (nginx strips it on proxy)
- **Auth dependency**: `require_volunteer` / `require_admin` / `check_setup_complete`
- **Status codes**:
  - 200: Success (GET, PATCH, POST non-creation)
  - 201: Resource created (POST)
  - 204: No content (DELETE)
  - 400: Bad request (validation, dual-approval conflict)
  - 401: Unauthorized (missing/invalid token)
  - 403: Forbidden (insufficient role)
  - 404: Not found
  - 410: Gone (setup test endpoints after setup complete)
  - 429: Rate limited
  - 500: Server error

### Pagination

All list endpoints paginate by default:
```json
{
  "items": [...],
  "total": 1234,
  "page": 1,
  "page_size": 20
}
```

**Query params**: `?page=N&page_size=20` (default 20, clamped to 100)

### Response Models

- **Exclude internals**: Use `response_model` Pydantic schema to exclude push-error fields, sensitive data
- **Resolve names**: `matched_name: "member:123"` resolved to `display_name: "John Doe"` in API responses
- **Timestamps**: ISO 8601 strings (UTC)

---

## Security Rules

### JWT

- **Never use empty secret** (`or ""` fallback forbidden)
- **Fail-fast on startup** if setup complete but secret missing/short (< 32 chars)
- **Lazy initialization**: JWT serializer built per-request (not at module import) to support secret reload

### URLs & Schemes

- **RTSP validation**: `rtsp://` or `rtsps://` scheme only (prevent file://, http://, command injection)
- **Relative URLs**: Preferred over absolute to prevent SSRF

### File Serving

- **Face images**: Authenticated `/storage/{path}` route only
- **Path containment**: Prevent `../` traversal (validate path against configured storage root)

### Setup & Testing Endpoints

- **One-time bootstrap**: `/setup` locked forever after completion (410 GONE on subsequent calls)
- **Test endpoints**: `/setup/test-connection`, `/setup/test-services` → 410 after setup

### Input Validation

- **Upload limits**: 10 MB byte size + 25 MP pixel dimensions (for face images)
- **Password strength**: ≥12 chars, uppercase, lowercase, number, special char
- **String constraints**: Email format, URL scheme, JSONB structure

### Cookies

- **HttpOnly**: Always set on refresh token (prevent XSS theft)
- **Secure**: Only in production (ENVIRONMENT=production) — allows HTTP on LAN
- **SameSite**: `lax` (prevent CSRF)
- **Per-request flag**: `_cookie_secure()` sets Secure flag based on request protocol (HTTPS only)

---

## Docker & Deployment

### Container Best Practices

- **No `privileged: true`** — use `cap_add` for specific capabilities if needed (RTSP worker needs none)
- **Healthchecks**: Every service must have a `healthcheck` block
- **Restart policy**: `unless-stopped` (restart on failure, but not if manually stopped)
- **Resource limits**: Set `deploy.resources.limits` for memory-heavy services (queue-worker: 2 CPU, 2 GB)
- **Volumes**: Persistent data (postgres, redis, storage, config) mounted from host

### Environment Variables

- **No secrets in git**: `.env` and `.env.production` are gitignored
- **Template**: `.env.template` documents all required + optional vars
- **Generation**: Use `scripts/generate-secrets.sh` to mint JWT_SECRET, WEBHOOK_SECRET, DB_PASSWORD
- **Override per compose**: `docker-compose.override.yml` for dev (exposes ports, hot-reload)

### Migrations

- **Automatic on startup**: Backend entrypoint runs `alembic upgrade head`
- **Idempotent**: Same migration run twice must succeed (database design requirement)
- **No down migrations**: Always forward-only; rollback via separate new migration if needed

### CI/CD

- **Test database**: SQLite + asyncpg support (use `.with_variant(JSONB, "postgresql")` for JSON columns)
- **Python 3.11**: Match production runtime
- **Linting**: `ruff check app`, `black --check`, `mypy` if applicable
- **Dependency audit**: `pip-audit -r requirements.txt`
- **Frontend**: `npm run build` (typecheck + build), `npm run lint`

---

## Naming & Style

### Python

- **Functions/variables**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `SCREAMING_SNAKE_CASE`
- **Private**: Prefix with `_` (e.g., `_helper_func`)
- **Async functions**: No special prefix (just `async def func_name`)

### TypeScript/React

- **Components**: `PascalCase` files and exports (`TaskCard.tsx`, `export TaskCard`)
- **Hooks**: `useXxx` pattern (`useAuth`, `useTask`)
- **Services/utils**: `camelCase` (`taskService.ts`, `export async getTask()`)
- **Interfaces/types**: `PascalCase` (`interface Task`, `type TaskStatus`)
- **Enums**: `PascalCase` (`enum Role { Admin, Volunteer }`)

### SQL

- **Table names**: `snake_case`, plural (`users`, `tasks`, `detections`)
- **Columns**: `snake_case` (e.g., `created_at`, `matched_name`)
- **Indexes**: Prefix `ix_` (e.g., `ix_detections_status_created`)
- **Constraints**: Prefix `uq_`, `fk_`, `ck_`

---

## Common Patterns

### Business Logic Service Pattern

```python
class TaskService:
    """Encapsulate task-related business logic."""
    
    async def confirm_task(self, db, task_id: int, volunteer_id: int) -> None:
        """Confirm a task; raises HTTPException on conflict."""
        task = await db.scalar(select(Task).where(Task.id == task_id).with_for_update())
        
        # Dual-approval guard
        existing = await db.scalar(
            select(TaskAction).where(
                TaskAction.task_id == task_id,
                TaskAction.volunteer_id == volunteer_id,
            )
        )
        if existing:
            raise HTTPException(400, "You have already acted on this task")
        
        # Update & commit
        task.current_approvals += 1
        await db.commit()
        
        # Broadcast
        await broadcaster.publish("task_update", json.dumps({"id": task_id}))
```

### React Query Pattern

```typescript
function TaskList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["tasks"],
    queryFn: async () => (await api.get("/tasks")).data,
  });

  const confirmMutation = useMutation({
    mutationFn: async (taskId: number) =>
      (await api.post(`/tasks/${taskId}/confirm`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      toast.success("Task confirmed!");
    },
    onError: (err) => {
      toast.error(err.response?.data?.detail ?? "Failed");
    },
  });

  if (isLoading) return <LoadingState />;
  if (error) return <ErrorState error={error} />;
  if (data?.length === 0) return <EmptyState />;

  return (
    <div>
      {data?.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          onConfirm={() => confirmMutation.mutate(task.id)}
          isLoading={confirmMutation.isPending}
        />
      ))}
    </div>
  );
}
```

---

## Documentation & Comments

- **Docstrings**: All public functions in Python (Google or NumPy style)
- **TypeScript JSDoc**: For public functions and complex logic
- **Inline comments**: Only for non-obvious logic (why, not what)
- **Migration docstrings**: Clear description of schema change and rationale
- **README/AGENTS.md**: Keep updated with architectural changes

---

## Code Review Checklist

Before submitting a PR:
- [ ] All public functions type-hinted (Python) or typed (TypeScript)
- [ ] All async operations have loading/error/empty states (React)
- [ ] No hardcoded hex colors (TailwindCSS tokens only)
- [ ] Dark mode tested (React)
- [ ] JSONB columns use `.with_variant()` (Python models)
- [ ] No `alert()` or `confirm()` (sonner toasts only)
- [ ] No access token in localStorage (Zustand in-memory)
- [ ] Alembic migration created for schema changes
- [ ] Tests pass (`pytest` backend, `npm run test:run` frontend)
- [ ] Linting passes (`ruff`, `eslint`)
- [ ] No git secrets (no `.env` files committed)
