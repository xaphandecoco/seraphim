# Frontend Architecture

React 18 + TypeScript + Vite + TailwindCSS + TanStack Query + Zustand + sonner.

## Routing & Pages

**Public routes:**
- `/login` — OAuth login page
- `/auth/callback` — OAuth callback handler
- `/setup` — Initial setup wizard (checks `GET /api/setup/status`)

**Protected routes (auth required):**
- `/` (default) — **Tasks** page (list pending tasks)
- `/ranking` — **Ranking/Leaderboard** (member rankings & metrics)
- `/events` — **Events** list & management
- `/events/:id` — **Event Detail** (single event, participants, attendance)
- `/contacts` — **Contacts** (member directory, search, filters)
- `/contacts/new` — **Contact Form** (create mode)
- `/contacts/:id` — **Contact Detail** (view, attendance history, custom fields)
- `/contacts/:id/edit` — **Contact Form** (edit mode)
- `/audit` — **Audit** (activity log)
- `/community-reports` — **Community Reports** list
- `/community-reports/new` — **Community Report Form**
- `/community-reports/:id` — **Community Report Detail**

**Admin-only routes:**
- `/dashboard` — **Dashboard** (stats & admin overview)
- `/settings` — **Settings** (primary admin hub)
- `/settings/users` — **User Management**
- `/settings/custom-fields` — **Custom Fields** (schema editor)
- `/settings/attendance` — **Attendance Settings**
- `/settings/event-series` — **Event Series** (recurring templates)
- `/settings/migration` — **Migration** (data import batch runner)
- `/settings/migration/:batchId` — **Migration Report** (import results)
- `/settings/fr-transition/orphans` — **FR Orphan Review** (face recognition cleanup)
- `/bulk-upload` — **Bulk Photo Upload**
- `/name-match/review` — **Name Match Review** (resolve fuzzy-name collisions)
- `/logs` — **Logs** (system events)
- `/pit` — **Pit** (debugging/internal tools)

Route guards: `<ProtectedRoute>` checks `authReady` and redirects to `/login`; `<AdminRoute>` checks `isAdmin` and redirects to `/contacts`.

## State Management (Zustand)

**`useAuthStore`:**
- `user: User | null` — current logged-in user
- `token: string | null` — JWT access token (in-memory only, never localStorage)
- `isAdmin: boolean` — decoded from `token.role === 'admin'`
- `isAuthenticated: boolean` — true if user is set
- `authReady: boolean` — false until initial `/auth/refresh` completes; route guards wait on this
- Methods: `login(user, token)`, `logout()`, `setUser()`, `setToken()` (decodes role inline), `setAuthReady()`

**`useTaskStore`:**
- `pendingCount: number` — count of active tasks
- `setPendingCount(count)` — update count

Both stores are initialized via `create()` from `zustand`.

## Data Fetching (TanStack Query)

**Query Client setup** (main.tsx):
- Default `staleTime: 5 min`, `retry: 1`
- Integrated via `<QueryClientProvider>`

**API client** (services/api.ts):
- Axios instance with `baseURL: '/api'` (nginx strips `/api` prefix before FastAPI)
- Request interceptor: adds `Authorization: Bearer <token>` header
- Response interceptor: on `401` and no retry, attempts `POST /auth/refresh`, updates token in-memory, and retries; if refresh fails, logs out and redirects to `/login`
- On `503` with "Setup required" detail, redirects to `/setup`

**Service layer** (services/{contacts,events,...}.ts):
- Grouped API calls per resource (e.g., `contactsApi.list()`, `contactsApi.get(id)`, etc.)
- Also export named functions (F06 spec) alongside objects
- Types: `ContactCreate`, `ContactUpdate`, `ContactFilters`, `Paginated<T>`, etc.

**React Query hooks** (hooks/useContacts.ts, useContact.ts, etc.):
- **Query hooks** (read):
  - `useContactList(params)` — `queryKey: ['contacts', params]`, `staleTime: 30s`, `placeholderData` for pagination
  - `useContact(id)` — enabled only if `id != null`
  - `useContactAttendance(id, params)` — attendance per contact
- **Mutation hooks** (write):
  - `useCreateContact()` — invalidates `['contacts']` on success
  - `useUpdateContact(id)` — invalidates `['contacts']` and `['contact', id]`
  - `useDeleteContact(id)`, `useRestoreContact(id)` — similar invalidation
- All mutations use `useQueryClient()` for cache management

## Design Tokens (TailwindCSS)

**Base colors (semantic):**
- `bg-background` — page background
- `bg-card` — card/panel background
- `text-foreground` — primary text
- `text-foreground/50`, `text-foreground/60`, etc. — opacity variants (secondary, tertiary)

**Conventions:**
- Never hardcoded hex colors; always use Tailwind classes
- Theme: light/dark stored in `localStorage['seraphim-theme']`; auto-applied to `<html class="dark">` before render
- Uses `prefers-color-scheme` media query as fallback if theme not set

## Toast Notifications (sonner)

- Import `from 'sonner'`
- `<Toaster position="top-center" richColors />` rendered in `<App>`
- Usage: `toast.success('msg')`, `toast.error('msg')`, `toast.promise(promise, { loading, success, error })`
- Convention: surface API errors via `err.response?.data?.detail` on 4xx/5xx responses

## Auth Flow

1. App mounts → `<AuthInit>` runs `useAuth()` hook
2. `useAuth()` checks if token already in memory (same session); if not, attempts `POST /auth/refresh` (uses HttpOnly refresh cookie)
3. On refresh success, fetches user via `GET /auth/me` with new token, calls `login(user, token)`
4. On refresh failure (no valid cookie), calls `setAuthReady()` so route guards can show `/login`
5. Access token stays in memory only (Zustand); refresh token is HttpOnly + SameSite cookie (backend sets)
6. `<ProtectedRoute>` redirects to `/login` if `!authReady || !isAuthenticated`
7. `<AdminRoute>` further checks `isAdmin` and redirects to `/contacts` if false

## Component Patterns

- **State views**: `<LoadingState>`, `<ErrorState>`, `<EmptyState>` for async states
- **Tables**: `<DataTable>` with column config, `<Pagination>` for paging
- **Forms**: `<FormField>` wrapper, `<CustomFieldRenderer>` for schema-driven fields
- **Modals/Dialogs**: `<ConfirmDialog>`, `<ResolveMatchModal>`, etc.
- **Status**: `<StatusBadge>`, `<EventTypeBadge>`, `<SessionTimeBadge>` for semantic badges

## Build & Tooling

- **Vite** dev server & build
- **TypeScript** strict mode, no `any`
- **ESLint** + Prettier (via npm scripts: `lint`, `format`, `build`)
- **Tests**: Vitest (unit & integration), run via `npm run test:run`

---

See [[Architecture]], [[Backend API]], [[Home]].
