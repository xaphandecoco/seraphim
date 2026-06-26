I now have a complete and thorough picture of the frontend and its data model. I have everything needed to produce the report.

---

# Project Seraphim Frontend Map — CRM Migration Recon

App is a mobile-first PWA (face-recognition attendance synced to CiviCRM). Bottom-tab nav, in-memory JWT auth, TanStack Query + axios, Tailwind design tokens. Below is the full inventory plus what's reusable, the role-gating, and gaps for native CRM screens.

## (A) Inventory — Pages, Routes, APIs

Router is flat in `frontend/src/App.tsx` (lines 50-65). No nested layout route; each guard (`ProtectedRoute`/`AdminRoute`) renders `children` + `<BottomNav>`. `SetupCheck` (App.tsx:27-42) hits `GET /api/setup/status` on mount and force-redirects to `/setup` if incomplete.

| Route | Component / file | Guard | APIs called | CRM relevance / CiviCRM-specific UI |
|---|---|---|---|---|
| `/` | `TasksPage` (`pages/TasksPage.tsx`) | Protected | `GET /health/queue` (poll 10s); via `TaskFeed`: `GET /tasks`, SSE `/api/tasks/feed`, `POST /tasks/{id}/confirm|skip|edit|add` | Face-recognition review queue. **Remove/replace** for CRM (becomes cases/activities or contact inbox). Tier/confidence/safe-mode are recognition-specific. |
| `/audit` | `AuditPage` (`pages/AuditPage.tsx`) | Protected | `GET /audit/tasks`, `POST /audit/{id}/confirm|deny|change` | Face-match QA. **Remove** for CRM. Reuses `MemberSearchModal` (contact picker — keep). |
| `/ranking` | `RankingPage` (`pages/RankingPage.tsx`) | Protected | `GET /leaderboard` | Volunteer gamification. Not CRM; drop or repurpose. Good simple list pattern. |
| `/events` | `EventsPage` (`pages/EventsPage.tsx`) | Protected | `GET /events`, `GET /events/active-event-id`, `POST /events/sync`, `POST /events/set-active` | **Closest existing CRM screen** — event list. CiviCRM-specific: "Sync from CiviCRM" button, "active event for camera detections" banner (EventsPage.tsx:83-102), LIVE badge. Keep list/card; strip camera/sync semantics; add create/edit + participants. |
| `/attendees` | `AttendeesPage` (`pages/AttendeesPage.tsx`) | Protected | `GET /members/attendees?search&limit=200`, `POST /members/sync` | **Closest to contact list.** Card grid keyed on `contact_id`, debounced-less search input. CiviCRM-specific: "Sync members from CiviCRM" (admin), face thumbnails + `sample_count`. Becomes the CRM contact list. |
| `/settings` | `SettingsPage` (`pages/SettingsPage.tsx`) | **Admin** | `GET /settings`, `GET /cameras`, `PUT /settings`, `POST /settings/safe-mode`, `POST/DELETE /cameras*`, `POST /cameras/{id}/preview|reconnect`, `POST /setup/test-connection`, `POST /uploads/faces` | Mix. Keep: profile, dark-mode toggle, system/DB/Redis editor, navigation hub. **Remove for CRM:** cameras, safe-mode, face-upload, recognition tunables, CompreFace keys. Links to Attendance/CiviCRM push (line 611). |
| `/settings/users` | `UserManagementPage` (`pages/UserManagementPage.tsx`) | **Admin** | `GET /auth/users`, `POST /auth/add-volunteer`, `POST /auth/deactivate/{id}`, `POST /auth/reset-password` | Generic user admin — **fully reusable** for CRM staff/role management. Role select, password-rule checklist, deactivate w/ confirm, reset-link copy. |
| `/settings/attendance` | `AttendancePage` (`pages/AttendancePage.tsx`) | **Admin** | `GET /events`, `GET /attendance/dead-letter`, `POST /attendance/push-preview`, `POST /attendance/push`, `POST /attendance/dead-letter/{id}/retry` | **Heavily CiviCRM-specific** — "Push to CiviCRM" + dead-letter retry. Demonstrates the bulk-preview→confirm→push pattern useful for CSV import/merge. Strip CiviCRM framing. |
| `/dashboard` | `DashboardPage` (`pages/DashboardPage.tsx`) | **Admin** | `GET /analytics/attendance-by-event|tier-distribution|volunteer-stats|queue-health`, `GET /analytics/export/attendance|logs` (blob CSV) | Recharts bar/pie + CSV export. **Reusable shell** for CRM reporting; tier/queue charts are recognition-specific. CSV-download helper (`downloadCsv`, DashboardPage.tsx:9-19) reusable for exports. |
| `/pit` | `PitPage` (`pages/PitPage.tsx`) | **Admin** | `GET /pit`, `POST /pit/{id}/enroll|delete|non-person` | Face-enrollment escalation queue. **Remove** for CRM. |
| `/logs` | `LogsPage` (`pages/LogsPage.tsx`) | **Admin** | `GET /logs?page&page_size=50` | **Only Prev/Next paginated list in the app** (LogsPage.tsx:95-114). Detection logs — content is recognition-specific but the pagination pattern is the seed for CRM tables. |
| `/login` | `LoginPage` | public | `GET /auth/config`, `POST /auth/login`, `GET /auth/me`, redirect `/api/auth/google` | Generic. Reusable. |
| `/auth/callback` | `OAuthCallbackPage` | public | `GET /auth/me` | Google OAuth token handoff. Reusable. |
| `/setup` | `SetupPage` (`pages/SetupPage.tsx`) | public | `GET /setup/status`, `POST /setup/test-connection|test-services`, `POST /setup`, `GET /setup/status` | 5-step wizard (DB/Redis/Compreface/**CiviCRM**/admin/camera). CiviCRM + CompreFace + camera steps are integration-specific. Multi-step wizard pattern reusable for CRM import flows. |

Backend contact search already exists separately: `GET /members?search&limit` (`backend/app/routers/members.py:18-45`) searches first/last/email and returns `MemberResponse` — this is the CRM contact search endpoint `MemberSearchModal` already uses.

CiviCRM-specific UI to remove/rework (concrete refs): branding "LNC Attendance"/"Volunteer Portal" (LoginPage.tsx:64-67, SetupPage.tsx:226-227, vite.config.ts:21-23 PWA manifest); all "Sync from CiviCRM" buttons (AttendeesPage.tsx:43-52, EventsPage.tsx:68-78); active-event-for-detections banner (EventsPage.tsx:83-102); entire AttendancePage CiviCRM-push + dead-letter; SetupPage CiviCRM step (lines 539-575); all face/camera/recognition surfaces (TasksPage, AuditPage, PitPage, face thumbnails, tier badges, CompreFace settings, cameras).

## (B) Reusable Components, Patterns, Tokens for New CRM Screens

**Design tokens** — `frontend/tailwind.config.js` maps Tailwind colors to HSL CSS vars; values in `frontend/src/index.css` (`:root` light, `.dark`). Use semantic classes only: `bg-background`, `bg-card`, `text-foreground`, `text-foreground/50` (muted), `border-border`, `bg-primary`/`text-primary-foreground`, `focus:ring-primary/30`. Also `muted`, `accent`, `destructive`, `popover`, `secondary` exist as tokens but are under-used (most code hardcodes `bg-red-50/text-green-700` etc. for status — a CRM design-system opportunity). Radius: rounded-xl/2xl convention. Dark mode is `class`-based, toggled in SettingsPage and pre-applied in `main.tsx:9-14`.

**Shared UI components**
- `components/ui/StateViews.tsx` — `LoadingState`, `EmptyState`, `ErrorState` (with retry). Underused (most pages inline their own empty/loading); standardize on these for CRM.
- `components/ui/ConfirmDialog.tsx` — accessible `alertdialog`, `destructive` variant, Esc-to-close, focus management. Reuse for deletes/merges.
- `components/ui/ErrorBoundary.tsx` — top-level boundary (wired in `main.tsx`).
- `components/tasks/MemberSearchModal.tsx` — **the contact picker**: debounced (300ms) search against `GET /members`, focus-trap, Esc-close, result list with avatar/name/email. Directly reusable as the CRM contact-link/lookup modal.
- `components/layout/BottomNav.tsx` — tab bar + admin "More" sheet, pending badge. Nav shell to extend (or replace with sidebar for desktop CRM).

**Data-fetching conventions (TanStack Query)** — `QueryClient` in `main.tsx:16-23` with `staleTime: 5min`, `retry: 1`. Pattern everywhere: `useQuery({ queryKey: ['x', search], queryFn })` with array keys including filters (AttendeesPage.tsx:32-35). Mutations are plain `async` handlers calling `api.*` then `queryClient.invalidateQueries({ queryKey })` (AttendeesPage.tsx:20-31) or optimistic `setQueryData` (TaskFeed.tsx:70-79). No `useMutation` used anywhere — opportunity to standardize for CRM writes.

**API client** — `services/api.ts`: axios instance, `baseURL '/api'`, request interceptor injects `Bearer` token from `authStore`, response interceptor does single-flight 401→`POST /auth/refresh`→retry→logout, and 503 "Setup required"→`/setup`. Dev proxy strips `/api` (vite.config.ts:69-78); nginx does the same in prod (CLAUDE.md). All new CRM calls go through `api`.

**Toast / error surfacing** — `sonner` `<Toaster position="top-center" richColors>` mounted in App.tsx:47. Universal idiom: `toast.success('…')` / `toast.error(err.response?.data?.detail || 'fallback')` (e.g. AttendeesPage.tsx:26-28). Form-level errors sometimes use inline red banners (SetupPage `error` state).

**Forms** — no form library; controlled `useState` objects + shared input class strings (`inputClass`/`labelClass` redefined per page, e.g. SettingsPage.tsx:409-411, SetupPage.tsx:200-202). Reusable patterns: password-rule checklist (UserManagementPage.tsx:25-31 + 137-151; SetupPage.tsx:206-212), show/hide password toggles (SetupPage), multi-step wizard with step indicator + skip (SetupPage.tsx:192-276). **No shared `<Input>`/`<Select>`/`<FormField>` component** — a gap for the field-heavy CRM forms.

**Charts/export** — Recharts (Bar/Pie/ResponsiveContainer) in DashboardPage; palette centralized in `lib/chartColors.ts` (literal HSL). Blob-CSV download helper inline in DashboardPage.tsx:9-19.

**Realtime** — `services/sse.ts` `SSEClient` (reconnect/backoff, token via query param). Used only by TaskFeed; available if CRM wants live updates.

## (C) Role-Gating Mechanism

- **Store:** `store/authStore.ts` (Zustand). Holds `user`, in-memory `token` (never localStorage), derived `isAdmin`, `isAuthenticated`, and `authReady` (gates first render until refresh resolves). `isAdmin` is re-derived from the JWT `role` claim on token refresh via `decodeJwtRole` (authStore.ts:9-22, 53-56) — so a silent refresh can't downgrade/escalate silently. Roles are literally `'volunteer' | 'admin'` (`types/index.ts:5`).
- **Bootstrap:** `hooks/useAuth.ts` runs once in `<AuthInit>` (App.tsx:22-25): if no token, `POST /auth/refresh` → `GET /auth/me` → `login()`; always sets `authReady`. Also keeps `api.defaults` Authorization in sync.
- **Route guards:** `components/layout/ProtectedRoute.tsx` (any authenticated user) and `components/layout/AdminRoute.tsx` (requires `isAdmin`, else `<Navigate to="/" >`). Both spin until `authReady`, redirect to `/login` if no token. Wrap children + render `BottomNav`.
- **Inline gating:** components read `useAuthStore((s) => s.isAdmin)` to conditionally show admin controls (AttendeesPage sync button, EventsPage active-event controls, BottomNav admin "More" tab). Backend mirrors with `require_admin` / `require_volunteer` dependencies (members.py:10).

For CRM: the two-role model is the only RBAC. New features (custom-field admin, system status, settings) → `AdminRoute`; contact list/detail/cases/events → `ProtectedRoute`. If CRM needs more granular roles (e.g., read-only, manager), the role union, `isAdmin` derivation, and guards all need extending.

## (D) Gaps for New CRM Features

1. **Contact detail/edit screen** — none exists. `AttendeesPage` is a read-only card grid with no row click, no detail route, no edit form. Need a `/contacts/:id` route, a real edit form (and a reusable `FormField` set, currently absent).
2. **Advanced search / filtering** — only single free-text inputs (`AttendeesPage`, `MemberSearchModal`). No multi-criteria filter UI, no saved searches, no column/field selection. Backend `/members` search is name/email-only and `limit`-capped (no offset).
3. **Big-table infrastructure** — **no data-table component, no virtualization, no server-side pagination beyond `LogsPage`'s Prev/Next.** Lists are `.map()` over cards/divs; `AttendeesPage` fetches `limit:200` with no paging. CRM contact search at scale needs a paginated/virtualized table (TanStack Table/Virtual not present — check `package.json` before adding).
4. **Cases / activities** — no concept, no models, no UI. Entirely new (list, detail, assignment-to-user; reuse `UserManagementPage`'s user data for assignee pickers).
5. **Custom-field admin** — none. `SettingsPage` only edits a fixed `EDITABLE_TUNABLES` array (SettingsPage.tsx:21-33). Need dynamic field-definition CRUD + dynamic form rendering.
6. **Event management** — `EventsPage` is read-only sync-from-CiviCRM; no create/edit/delete, no participant management. Bulk-participant handling has a partial analog in `AttendancePage`'s preview→confirm→push + dead-letter pattern to mine.
7. **CSV/XLSX import** — only `uploads/faces` (image) and CSV *export* (DashboardPage). No import wizard, no column mapping, no XLSX. The `SetupPage` multi-step wizard is a UI template to reuse.
8. **Find & merge duplicates** — nonexistent. `ConfirmDialog` + the preview/diff layout in `AttendancePage` (will-push/missing/duplicates lists, AttendancePage.tsx:174-191) are partial building blocks.
9. **Dashboard/reporting** — `DashboardPage` shell + Recharts + CSV export reusable, but all current metrics are recognition/attendance-specific.
10. **Settings/system-status** — `SettingsPage` has DB/Redis connection editor + test, but no general "system status" health view. `GET /health/queue` exists; a status page would be new.
11. **Mutation consistency** — no `useMutation`, no shared form components, no shared input components, status colors hardcoded rather than tokenized. Worth establishing a small CRM design-system layer (FormField, DataTable, StatusBadge, Pagination) before building 9 screens.
12. **Layout** — mobile-first single-column with `BottomNav` and `h-screen` per page; no desktop sidebar/master-detail shell, which a data-dense CRM typically wants.

**Key file refs:** routes `frontend/src/App.tsx`; auth `frontend/src/store/authStore.ts`, `frontend/src/hooks/useAuth.ts`, `frontend/src/components/layout/{ProtectedRoute,AdminRoute,BottomNav}.tsx`; API `frontend/src/services/api.ts`; tokens `frontend/tailwind.config.js` + `frontend/src/index.css`; reusable `frontend/src/components/ui/{ConfirmDialog,StateViews,ErrorBoundary}.tsx`, `frontend/src/components/tasks/MemberSearchModal.tsx`; closest CRM screens `frontend/src/pages/{AttendeesPage,EventsPage,UserManagementPage,DashboardPage,LogsPage}.tsx`; contact search backend `backend/app/routers/members.py`.