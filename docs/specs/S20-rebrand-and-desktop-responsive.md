# S20 — Rebrand & Desktop-Responsive Pass
**Phase:** H — Cutover · **Depends on:** S03 (ContactsPage + DataTable + Pagination + ContactDetailPage), S04 (EventsPage CRM rework), S14 (DashboardPage CRM reporting) · **Effort:** M · **Status:** Not started

> **Charter:** Replace every "LNC Attendance" / "Volunteer Portal" / "LNC" brand string and every CiviCRM-era description with "Seraphim" across the PWA manifest, `index.html`, `LoginPage`, `SetupPage`, and all `<title>`/`<meta>` references. Then do a desktop-responsive pass on the three data-dense CRM views (Contacts, Events, Reports/Dashboard) by introducing a two-column `AppShell` sidebar/master-detail layout that activates at `lg:` breakpoint while fully preserving the mobile-first BottomNav at smaller widths. Pure UI and branding — no new endpoints, no new tables, no Alembic migration.

---

## 1. Goal & rationale

After S01–S19 the CRM is feature-complete but still ships with artefacts from the pre-CRM era:

**Branding rot (every string is an exact file:line citation from the real code):**
- `frontend/index.html:14` — `<title>LNC Attendance</title>`
- `frontend/index.html:12` — `apple-mobile-web-app-title` = `"LNC Attendance"`
- `frontend/index.html:13` — description = `"Light of the World NC — facial recognition attendance system"`
- `frontend/vite.config.ts:21` — PWA manifest `name: 'LNC Attendance'`
- `frontend/vite.config.ts:22` — PWA manifest `short_name: 'LNC'`
- `frontend/vite.config.ts:23` — PWA manifest `description: 'Light of the World NC — facial recognition attendance system'`
- `frontend/src/pages/LoginPage.tsx:63-66` — heading `"LNC Attendance"`, subheading `"Volunteer Portal"`
- `frontend/src/pages/LoginPage.tsx:59` — `alt="LNC Logo"`
- `frontend/src/pages/LoginPage.tsx:144` — footer `"Light of the World Worldwide Ministries — North Caloocan"` (factually correct church name — **keep as footnote**, rephrase so it describes the organization, not the app)
- `frontend/src/pages/SetupPage.tsx:226` — heading `"LNC Attendance"`
- `frontend/src/pages/SetupPage.tsx:227` — subheading `"Initial Setup"` (keep)
- `frontend/src/pages/SetupPage.tsx:224` — `alt="LNC Logo"`
- `frontend/src/main.tsx:10` — theme key `'seraphim-theme'` (already correct — no change needed)

**Layout deficit (identified in `docs/crm-research/frontend.md §D.12`):**
The mobile-first single-column `h-screen` pages with `BottomNav` are appropriate for the recognition queue workflow but undersized for a 1,440-contact CRM. At `lg:` (1024 px +) a persistent left sidebar with navigation items and a wider content area dramatically improves information density on the three heaviest pages:
- **Contacts** (`/contacts`, `/contacts/:id`) — the DataTable + side-panel detail
- **Events** (`/events`, `/events/:id/participants`) — the event list + participant grid
- **Dashboard** (`/dashboard`) — the multi-tile reporting view

The bottom nav remains on `< lg` devices (phones, small tablets) unchanged. This avoids regressions in the recognition-queue screens (Tasks, Audit, PIT, Ranking) which are genuinely optimised for a portrait phone.

**No backend work is required by this sprint.** There are no new endpoints, no schema changes, no Alembic migration. This is entirely a frontend + config change.

---

## 2. Scope

### In scope
- **Rebrand all string literals** listed above to "Seraphim" across: `index.html`, `vite.config.ts`, `LoginPage.tsx`, `SetupPage.tsx`. No other string changes.
- **New PWA icons** — `public/logo.png` is the existing icon asset; manifest continues to reference it. No new icon required (owner may swap the PNG asset separately; spec does not dictate artwork). Update the manifest `name`, `short_name`, `description` only.
- **`AppShell` layout component** (`frontend/src/components/layout/AppShell.tsx`) — renders a responsive wrapper:
  - `< lg`: identical to current behaviour — children fill the screen, `BottomNav` fixed at bottom (rendered by `ProtectedRoute`/`AdminRoute` as today).
  - `>= lg`: two-column flex: a fixed-width `SideNav` (240 px) on the left + a scrollable main area occupying the remainder; `BottomNav` is hidden.
- **`SideNav` component** (`frontend/src/components/layout/SideNav.tsx`) — desktop-only sidebar navigation, visible only at `>= lg`. Mirrors `BottomNav` link targets with icons + labels. Shows the "Seraphim" wordmark + role badge at the top. Admin links (Settings, Users, System) in a dedicated section. Active-route highlight uses `location.pathname`. Collapses to icon-only at `lg:` if layout space is tight (optional — see §10).
- **`DesktopRoute` wrapper** (`frontend/src/components/layout/DesktopRoute.tsx`) — thin wrapper that composes `AppShell` around `ProtectedRoute`/`AdminRoute` children. Used only by the three data-dense views.
- **Three page-level responsive upgrades** using `DesktopRoute`:
  - **`ContactsPage.tsx`** (introduced by S03 at `/contacts`) — at `lg:` wrap the page in `AppShell`; the `DataTable` (from S03) gains wider columns and the contact detail side-panel opens inline to the right (`lg:grid lg:grid-cols-[1fr_400px]`) instead of as a bottom sheet / full-screen route.
  - **`EventsPage.tsx`** (reworked by S04 at `/events`) — at `lg:` the event list is a narrower left column (360 px); the participant grid for the selected event fills the right panel.
  - **`DashboardPage.tsx`** (reworked by S14 at `/dashboard`) — at `lg:` the tile grid switches from single-column to `grid-cols-2` or `grid-cols-3` (based on tile size hints) so the full analytics suite is navigable without excessive scrolling.
- **`BottomNav` hide on desktop** — add `lg:hidden` to `BottomNav`'s `<nav>` element (and remove the `pb-16` offset in `ProtectedRoute`/`AdminRoute` wrappers at `lg:`).
- **`ProtectedRoute` / `AdminRoute` responsive padding** — replace the hardcoded `pb-16` div wrapper with a Tailwind responsive class `pb-16 lg:pb-0` so desktop layouts are not padded for a bottom nav that is hidden.
- **`authStore` `isViewer` derivative** — S15 adds `isViewer`; S20 should forward-declare the `SideNav` viewer guard so it only shows permitted nav items. If S15 has not landed, gate on `!isViewer` (treat missing `isViewer` as false via optional chaining in the store selector). See §10.
- **Vitest snapshot/render tests** for `AppShell`, `SideNav`, `LoginPage` rebrand strings.
- **`npm run build` + `npm run lint`** must stay green.

### Out of scope
- Any new backend endpoint, schema column, Alembic migration, Pydantic schema, or service.
- Artwork / icon redesign. The existing `public/logo.png` is used as-is.
- Responsive upgrades for recognition-queue pages: `TasksPage`, `AuditPage`, `PitPage`, `RankingPage`, `LogsPage`. Those are phone-native workflows and are not touched.
- Responsive upgrades for `SettingsPage`, `UserManagementPage`, `SetupPage`. These are low-frequency admin screens; acceptable as single-column.
- TanStack Virtual / windowing. Row virtualisation (needed at 1,440+ contacts) was specified in S03 (DataTable) or S09. S20 only adjusts layout breakpoints.
- Dark-mode palette changes. The existing `index.css` tokens are already correct.
- Contact or event quick-create flows inside the sidebar (belongs to S03/S04 drawers, already scoped there).
- PWA icon artwork changes. Updating `logo.png` itself is a design/owner task outside the sprint.
- Multi-pane drag-resizing of the sidebar/content split. Fixed widths only.

---

## 3. Data model changes

**None.** S20 is purely frontend + config. No new tables, columns, indexes, FKs, or Alembic migration are required or permitted.

---

## 4. Backend

**None.** No new endpoints, no changed service logic, no config additions. The backend is untouched by this sprint.

For completeness: `backend/app/config.py`, `backend/app/main.py`, and all routers are **not modified** by S20.

---

## 5. Frontend

### 5.1 Pages / routes / components new + modified

| File | Action | Summary |
|---|---|---|
| `frontend/index.html` | MODIFY | Title, apple-mobile-web-app-title, description meta tag |
| `frontend/vite.config.ts` | MODIFY | PWA manifest name / short_name / description |
| `frontend/src/pages/LoginPage.tsx` | MODIFY | Heading, subheading, img alt, footer text |
| `frontend/src/pages/SetupPage.tsx` | MODIFY | Heading on the wizard card header, img alt |
| `frontend/src/components/layout/AppShell.tsx` | CREATE | Responsive two-column wrapper |
| `frontend/src/components/layout/SideNav.tsx` | CREATE | Desktop left sidebar nav |
| `frontend/src/components/layout/DesktopRoute.tsx` | CREATE | Thin ProtectedRoute + AppShell composition |
| `frontend/src/components/layout/BottomNav.tsx` | MODIFY | Add `lg:hidden` to the `<nav>` element |
| `frontend/src/components/layout/ProtectedRoute.tsx` | MODIFY | `pb-16` → `pb-16 lg:pb-0` |
| `frontend/src/components/layout/AdminRoute.tsx` | MODIFY | Same `pb-16 lg:pb-0` change |
| `frontend/src/pages/ContactsPage.tsx` | MODIFY | Switch to `DesktopRoute`; add `lg:` master-detail grid |
| `frontend/src/pages/EventsPage.tsx` | MODIFY | Switch to `DesktopRoute`; add `lg:` two-column split |
| `frontend/src/pages/DashboardPage.tsx` | MODIFY | Switch to `DesktopRoute`; upgrade tile grid to `lg:grid-cols-2 xl:grid-cols-3` |
| `frontend/src/App.tsx` | MODIFY | Wrap `/contacts`, `/events`, `/dashboard` routes with `DesktopRoute` |

### 5.2 Rebrand string changes (file-by-file, exact old → new)

#### `frontend/index.html`
```html
<!-- OLD line 12 -->
<meta name="apple-mobile-web-app-title" content="LNC Attendance" />
<!-- NEW -->
<meta name="apple-mobile-web-app-title" content="Seraphim" />

<!-- OLD line 13 -->
<meta name="description" content="Light of the World NC — facial recognition attendance system" />
<!-- NEW -->
<meta name="description" content="Seraphim — Church CRM for Light North Caloocan" />

<!-- OLD line 14 -->
<title>LNC Attendance</title>
<!-- NEW -->
<title>Seraphim</title>
```

#### `frontend/vite.config.ts` (inside the `VitePWA` manifest object, lines 21-23)
```ts
// OLD
name: 'LNC Attendance',
short_name: 'LNC',
description: 'Light of the World NC — facial recognition attendance system',
// NEW
name: 'Seraphim',
short_name: 'Seraphim',
description: 'Seraphim — Church CRM for Light North Caloocan',
```

#### `frontend/src/pages/LoginPage.tsx`
```tsx
// OLD line 59 (img alt)
alt="LNC Logo"
// NEW
alt="Seraphim"

// OLD lines 63-66 (heading + subheading)
<h1 className="text-3xl font-extrabold tracking-tight text-foreground">
  LNC Attendance
</h1>
<p className="mt-1 text-sm font-medium text-foreground/70">
  Volunteer Portal
</p>
// NEW
<h1 className="text-3xl font-extrabold tracking-tight text-foreground">
  Seraphim
</h1>
<p className="mt-1 text-sm font-medium text-foreground/70">
  Light North Caloocan CRM
</p>

// OLD line 144 (footer — keep church name, drop app branding)
Light of the World Worldwide Ministries — North Caloocan
// NEW
Light of the World Worldwide Ministries · North Caloocan
```
(The footer is factually correct; it merely drops its implicit role as an app tagline. The `·` separator is a cosmetic micro-clean.)

#### `frontend/src/pages/SetupPage.tsx`
```tsx
// OLD line 224 (img alt)
alt="LNC Logo"
// NEW
alt="Seraphim"

// OLD line 226 (wizard card heading)
<h1 className="text-2xl font-extrabold text-foreground">LNC Attendance</h1>
// NEW
<h1 className="text-2xl font-extrabold text-foreground">Seraphim</h1>
```

### 5.3 New component: `AppShell`

**`frontend/src/components/layout/AppShell.tsx`**

```tsx
import { SideNav } from './SideNav';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Desktop sidebar — hidden below lg */}
      <div className="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:flex lg:w-60 lg:flex-col">
        <SideNav />
      </div>
      {/* Main content — offset on desktop */}
      <main className="lg:pl-60">
        {children}
      </main>
    </div>
  );
}
```

**Key constraints:**
- `lg:fixed` sidebar ensures the nav stays put while the content area scrolls independently.
- `lg:pl-60` (= 240 px = Tailwind `w-60`) exactly offsets the sidebar width; `pl-0` at `< lg`.
- No JS state for sidebar open/close — desktop sidebar is always visible at `>= lg`; mobile uses `BottomNav` exclusively. This avoids hydration complexity and keeps the component stateless.
- Dark mode: all colours are token-based (`bg-card`, `border-border`, etc.) — auto-adapts.

### 5.4 New component: `SideNav`

**`frontend/src/components/layout/SideNav.tsx`**

Renders:
1. **Wordmark area** at top: Seraphim logo (`/logo.png`, 28 px) + `"Seraphim"` text + role badge (`admin` / `volunteer` / `viewer` from `useAuthStore((s) => s.role ?? s.user?.role)`).
2. **Primary nav links** (visible to all authenticated roles unless noted):
   - Contacts (`/contacts`, `Users` icon) — volunteer + admin
   - Events (`/events`, `Calendar` icon) — volunteer + admin
   - Activities (`/activities`, `ClipboardList` icon) — volunteer + admin (S12)
   - Reports (`/dashboard`, `BarChart2` icon) — all roles (viewer-accessible)
   - Leaderboard (`/ranking`, `Trophy` icon) — volunteer + admin
3. **Admin section** (rendered only when `isAdmin`, exact mirror of `BottomNav`'s `adminTabs`):
   - Face Queue (`/pit`, `AlertTriangle` icon)
   - System Logs (`/logs`, `FileText` icon)
   - Settings (`/settings`, `Settings` icon)
4. **Bottom slot**: user avatar initial + name + email + Sign Out button (calls `authStore.logout()` + `api.post('/auth/logout').catch(()=>{})`)

Active link: `location.pathname.startsWith(tab.path)` → `bg-primary/10 text-primary font-semibold`. Inactive: `text-foreground/60 hover:bg-background hover:text-foreground`.

**Viewer gating in `SideNav`:** items that viewers cannot reach are hidden rather than shown disabled, to avoid confusion. Check `role !== 'viewer'` for Contacts, Events, Activities, Leaderboard admin nav. Viewer sees only Reports in primary + no admin section.

**Forward-compat note:** if S15 has not landed, `authStore` has no `role` field — the selector `s.role ?? s.user?.role` falls back to `s.user?.role` which is always `'volunteer' | 'admin'`; viewer-gating `role !== 'viewer'` evaluates to `true` for all existing roles, so all nav items appear — correct behaviour until S15 activates viewer accounts.

```tsx
import { Link, useLocation } from 'react-router-dom';
import { Users, Calendar, ClipboardList, BarChart2, Trophy, AlertTriangle, FileText, Settings, LogOut } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { api } from '@/services/api';

const primaryLinks = [
  { path: '/contacts',   label: 'Contacts',    icon: Users,          roles: ['admin', 'volunteer'] as const },
  { path: '/events',     label: 'Events',      icon: Calendar,       roles: ['admin', 'volunteer'] as const },
  { path: '/activities', label: 'Activities',  icon: ClipboardList,  roles: ['admin', 'volunteer'] as const },
  { path: '/dashboard',  label: 'Reports',     icon: BarChart2,      roles: ['admin', 'volunteer', 'viewer'] as const },
  { path: '/ranking',    label: 'Leaderboard', icon: Trophy,         roles: ['admin', 'volunteer'] as const },
] as const;

const adminLinks = [
  { path: '/pit',      label: 'Face Queue',   icon: AlertTriangle },
  { path: '/logs',     label: 'System Logs',  icon: FileText },
  { path: '/settings', label: 'Settings',     icon: Settings },
] as const;

export function SideNav() {
  const location = useLocation();
  const { user, isAdmin, logout } = useAuthStore((s) => ({
    user: s.user,
    isAdmin: s.isAdmin,
    logout: s.logout,
  }));
  // Forward-compat: S15 adds role to authStore; fall back to user.role until then
  const role: string = (useAuthStore as any)((s: any) => s.role) ?? user?.role ?? 'volunteer';

  const handleLogout = async () => {
    try { await api.post('/auth/logout'); } catch { /* best-effort */ }
    logout();
  };

  return (
    <aside className="flex h-full flex-col border-r border-border bg-card">
      {/* Wordmark */}
      <div className="flex items-center gap-3 px-5 py-5">
        <img src="/logo.png" alt="Seraphim" className="h-7 w-auto" />
        <span className="text-lg font-extrabold tracking-tight text-foreground">Seraphim</span>
      </div>

      {/* Role badge */}
      <div className="px-5 pb-3">
        <span className="inline-block rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-bold uppercase text-primary">
          {role}
        </span>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-2" aria-label="Main navigation">
        {primaryLinks
          .filter((link) => (link.roles as readonly string[]).includes(role))
          .map(({ path, label, icon: Icon }) => {
            const active = location.pathname === path || location.pathname.startsWith(path + '/');
            return (
              <Link
                key={path}
                to={path}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                  active
                    ? 'bg-primary/10 font-semibold text-primary'
                    : 'font-medium text-foreground/60 hover:bg-background hover:text-foreground'
                }`}
              >
                <Icon size={18} aria-hidden="true" />
                {label}
              </Link>
            );
          })}

        {isAdmin && (
          <>
            <div className="mx-1 my-2 border-t border-border" />
            <p className="px-3 pb-1 text-[10px] font-bold uppercase tracking-wider text-foreground/40">Admin</p>
            {adminLinks.map(({ path, label, icon: Icon }) => {
              const active = location.pathname === path || location.pathname.startsWith(path + '/');
              return (
                <Link
                  key={path}
                  to={path}
                  aria-current={active ? 'page' : undefined}
                  className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                    active
                      ? 'bg-primary/10 font-semibold text-primary'
                      : 'font-medium text-foreground/60 hover:bg-background hover:text-foreground'
                  }`}
                >
                  <Icon size={18} aria-hidden="true" />
                  {label}
                </Link>
              );
            })}
          </>
        )}
      </nav>

      {/* User slot */}
      <div className="border-t border-border px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/20 text-sm font-bold text-primary">
            {user?.name?.charAt(0).toUpperCase() ?? 'U'}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-foreground">{user?.name ?? 'User'}</p>
            <p className="truncate text-xs text-foreground/50">{user?.email}</p>
          </div>
          <button
            onClick={handleLogout}
            aria-label="Sign out"
            className="shrink-0 rounded-lg p-1.5 text-foreground/40 transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <LogOut size={16} aria-hidden="true" />
          </button>
        </div>
      </div>
    </aside>
  );
}
```

### 5.5 New component: `DesktopRoute`

**`frontend/src/components/layout/DesktopRoute.tsx`**

Thin composition wrapper for routes that benefit from the desktop layout. Wraps the auth guard with `AppShell` so the sidebar appears on desktop, while `ProtectedRoute`/`AdminRoute` still handles the auth check and renders `BottomNav` (which is `lg:hidden`).

```tsx
import { AppShell } from './AppShell';
import { ProtectedRoute } from './ProtectedRoute';

interface DesktopRouteProps {
  children: React.ReactNode;
  admin?: boolean;
}

export function DesktopRoute({ children, admin = false }: DesktopRouteProps) {
  // Conditionally import AdminRoute to avoid circular deps
  const Guard = admin
    ? require('./AdminRoute').AdminRoute
    : ProtectedRoute;
  return (
    <Guard>
      <AppShell>{children}</AppShell>
    </Guard>
  );
}
```

> **Implementation note:** use static imports (not `require`) in the actual file. The inline above uses `require` for clarity; the real file imports both `ProtectedRoute` and `AdminRoute` at the top, then conditionally branches by prop.

### 5.6 Modified: `BottomNav.tsx`

**Change:** add `lg:hidden` to the outer `<nav>` wrapper element (line 74 in current file). Also add `lg:hidden` to the admin "More" sheet overlay div (line 41). No other changes.

```tsx
// OLD (line 74)
<nav aria-label="Main navigation" className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card/95 backdrop-blur-sm">
// NEW
<nav aria-label="Main navigation" className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-card/95 backdrop-blur-sm lg:hidden">

// OLD (line 41 — admin sheet overlay)
<div className="fixed inset-0 z-40" onClick={() => setShowMore(false)}>
// NEW
<div className="fixed inset-0 z-40 lg:hidden" onClick={() => setShowMore(false)}>
```

### 5.7 Modified: `ProtectedRoute.tsx` and `AdminRoute.tsx`

Both currently wrap children in `<div className="pb-16">` to add bottom-nav clearance. This must not apply on desktop where the nav is hidden.

**`ProtectedRoute.tsx`** (line 27):
```tsx
// OLD
<div className="pb-16">
// NEW
<div className="pb-16 lg:pb-0">
```

**`AdminRoute.tsx`** — apply the identical change wherever it renders the children wrapper `pb-16` div.

### 5.8 Modified: `ContactsPage.tsx` (introduced by S03)

At `>= lg`, ContactsPage switches from a full-screen list to a master-detail split:

```
+--sidebar(SideNav 240px)--+--master(contacts table)--+--detail(contact panel)--+
                            ^  ~540-600px               ^  ~400px
                            [  responsive flex-1       ] [  lg:w-[400px]         ]
```

**Pattern:** a URL-driven detail panel. When no contact is selected, the right column shows an `EmptyState` prompt ("Select a contact to view details"). When `/contacts/:id` is visited, the detail panel opens inline rather than replacing the full page.

**Approach:** use `useParams` / `useMatch` to detect if `:id` is present. At `< lg` the detail page navigates as a separate full-screen route (existing S03 behaviour). At `>= lg` the detail renders as a right panel alongside the list. Implement this with a layout flag hook:

```tsx
// inside ContactsPage
const isDesktop = useMediaQuery('(min-width: 1024px)');
// ... on row click:
if (isDesktop) {
  setSelectedId(id); // shows inline panel
} else {
  navigate(`/contacts/${id}`); // full-screen route (mobile)
}
```

`useMediaQuery` is a small hook added at `frontend/src/hooks/useMediaQuery.ts`:

```ts
import { useState, useEffect } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}
```

**TanStack Query key:** `['contact-detail', selectedId]` (same key as S03's `/contacts/:id` page — cache is shared; no double-fetch on navigation).

**Layout diff (ContactsPage):**
```tsx
<div className="flex h-screen flex-col lg:flex-row lg:h-screen">
  {/* Master list */}
  <div className="flex flex-col flex-1 min-w-0 lg:max-w-[640px] lg:border-r lg:border-border">
    {/* existing header + search + DataTable */}
  </div>
  {/* Detail panel — desktop only */}
  {isDesktop && (
    <div className="hidden lg:flex lg:flex-col lg:w-[400px] lg:overflow-y-auto">
      {selectedId
        ? <ContactDetailPanel contactId={selectedId} onClose={() => setSelectedId(null)} />
        : <EmptyState icon={Users} title="Select a contact" description="Click a row to view details" />
      }
    </div>
  )}
</div>
```

`ContactDetailPanel` is a component extracted from S03's `ContactDetailPage` that renders the same profile cards (core info, custom fields, derived badges, attendance slot, face slot, etc.) but inside a scrollable panel rather than a full page. It receives `contactId` as a prop and issues the same `GET /members/:id` query.

**UX: loading / empty / error** (tokens, no hex):
- Loading: `<LoadingState message="Loading contact…" />` (from `components/ui/StateViews.tsx`).
- Empty (no selection): `<EmptyState icon={Users} title="Select a contact" />`.
- Error: `<ErrorState message={err.response?.data?.detail || 'Failed to load contact'} onRetry={refetch} />`.

### 5.9 Modified: `EventsPage.tsx` (reworked by S04)

At `>= lg`, a two-column layout with the event list (scrollable, fixed-width left column) and the participant grid (right column) for the currently-selected event.

```
+--SideNav 240px--+--event list 360px--+--participant grid flex-1--+
```

Implementation pattern mirrors §5.8: `isDesktop` hook + `selectedEventId` state. On `< lg` the participant grid opens as a bottom-sheet or separate route (per S04 spec). On `>= lg` it renders inline right.

**TanStack Query key:** `['event-participants', selectedEventId, page]` — same key as S04's participant grid endpoint `GET /events/:id/participants`; cache shared.

**Layout change in `EventsPage`:**
```tsx
<div className="flex h-screen flex-col lg:flex-row">
  <div className="flex flex-col lg:w-[360px] lg:border-r lg:border-border lg:overflow-y-auto">
    {/* header, filter bar, event list */}
  </div>
  {isDesktop && (
    <div className="hidden lg:flex lg:flex-1 lg:flex-col lg:overflow-y-auto">
      {selectedEventId
        ? <EventParticipantPanel eventId={selectedEventId} />
        : <EmptyState icon={Calendar} title="Select an event" description="Click an event to manage participants" />
      }
    </div>
  )}
</div>
```

### 5.10 Modified: `DashboardPage.tsx` (reworked by S14)

The tile grid switches from single-column to multi-column at `lg:` and `xl:`:

```tsx
// OLD (from current DashboardPage main area):
<main className="flex-1 overflow-y-auto space-y-4 px-3 pt-3 pb-4">

// NEW:
<main className="flex-1 overflow-y-auto px-3 pt-3 pb-4">
  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
    {/* tiles */}
  </div>
</main>
```

Tiles that are wide by design (attendance grids, SMA time-series) receive `lg:col-span-2` or `xl:col-span-3` to span full width as appropriate. Tile components from S14 receive an optional `colSpan?: 'full' | 'half'` prop for this purpose.

**DashboardPage on desktop also loses the `ArrowLeft` back-button** (line 48-52 current file — it navigates back to `/settings` which is a recognition-era artifact). On desktop, navigation is via `SideNav`. Replace with a descriptive `<h1>` only; keep the back-button only on `< lg` (`block lg:hidden`).

### 5.11 Modified: `App.tsx`

Replace the three data-dense routes with `DesktopRoute`:

```tsx
// OLD
<Route path="/contacts" element={<ProtectedRoute><ContactsPage /></ProtectedRoute>} />
<Route path="/events" element={<ProtectedRoute><EventsPage /></ProtectedRoute>} />
<Route path="/dashboard" element={<AdminRoute><DashboardPage /></AdminRoute>} />

// NEW (S15 may gate dashboard to viewer too — use require_viewer logic from S15)
<Route path="/contacts" element={<DesktopRoute><ContactsPage /></DesktopRoute>} />
<Route path="/events" element={<DesktopRoute><EventsPage /></DesktopRoute>} />
<Route path="/dashboard" element={<DesktopRoute admin><DashboardPage /></DesktopRoute>} />
```

The `admin` prop on `DesktopRoute` routes to `AdminRoute` internally. Once S15 adds `viewer` role, `dashboard` should use a viewer-permitted guard; that adjustment belongs to S15 and is noted in §10.

### 5.12 TanStack Query keys

S20 introduces no new query keys. It re-uses:
- `['contact-detail', id]` — S03
- `['event-participants', id, page]` — S04
- `['analytics-*']` — S14

The `useMediaQuery` hook does not interact with TanStack Query.

### 5.13 Role gating summary

| Component / route | Mobile (`< lg`) | Desktop (`>= lg`) | Viewer access |
|---|---|---|---|
| `ContactsPage` | `ProtectedRoute` (via `DesktopRoute`) | `SideNav` visible; admin + volunteer only | Hidden from SideNav; 403 if route hit |
| `EventsPage` | `ProtectedRoute` | `SideNav` visible; admin + volunteer | Hidden from SideNav; 403 if route hit |
| `DashboardPage` | `AdminRoute` (until S15; then viewer-permitted) | `SideNav` visible to all roles | Viewer: Reports link visible in SideNav |
| `SideNav` admin section | n/a | `isAdmin === true` only | Not shown to viewer |
| `BottomNav` | Shown (all roles) | `lg:hidden` — never shown | n/a |

### 5.14 Mobile-first preservation checklist

The following existing mobile behaviours must remain unchanged after S20:
- Bottom tab bar (`BottomNav`) visible and functional on `< lg`.
- Touch scroll on all pages (`overflow-y-auto`, `-webkit-tap-highlight-color: transparent`, `overscroll-behavior-y: none` from `index.css`).
- `safe-area-inset-bottom` spacer in `BottomNav` (line 116 current file) — preserved.
- `h-screen flex flex-col` page structure for recognition-queue pages (Tasks, Audit, PIT, Ranking, Logs) — **not touched by S20**.
- `pb-16` bottom padding on mobile still applies (the `lg:pb-0` change only removes it at desktop).

---

## 6. Migration / data

Not applicable. S20 makes no data model changes and no data migration is required.

---

## 7. Acceptance criteria

1. The browser tab title reads "Seraphim" on every page (verified in `<title>` of `index.html`).
2. `LoginPage` heading is "Seraphim"; subheading is "Light North Caloocan CRM". No occurrence of "LNC Attendance" or "Volunteer Portal" remains anywhere in `LoginPage.tsx`.
3. `SetupPage` wizard card heading is "Seraphim". No occurrence of "LNC Attendance" remains anywhere in `SetupPage.tsx`.
4. PWA manifest (`vite.config.ts`) `name` = "Seraphim", `short_name` = "Seraphim", `description` = "Seraphim — Church CRM for Light North Caloocan".
5. `index.html` has `apple-mobile-web-app-title` = "Seraphim" and description = "Seraphim — Church CRM for Light North Caloocan".
6. `npm run build` exits 0. `npm run lint` exits 0. `npm run test:run` exits 0.
7. At viewport width 375 px (mobile): `BottomNav` is visible; `SideNav` is not rendered in the DOM (confirmed by `lg:hidden` on nav + `hidden lg:flex` on SideNav wrapper). `ContactsPage`, `EventsPage`, `DashboardPage` render as single-column full-screen pages identical to pre-S20 at narrow widths.
8. At viewport width 1280 px (desktop): `SideNav` is visible with "Seraphim" wordmark. `BottomNav` is not visible. `ContactsPage` renders as a master-detail split. `EventsPage` renders as a two-column event-list + participant-panel split. `DashboardPage` renders tiles in a multi-column grid (`lg:grid-cols-2`).
9. Clicking a contact row on desktop opens the contact detail in the right panel without navigating away from the list (URL does not change; or navigates to `/contacts/:id` and both columns remain visible).
10. Clicking an event on desktop opens the participant grid inline in the right column without full-page navigation.
11. `SideNav` active-link highlight matches the current `location.pathname`.
12. Viewer role (post-S15): only "Reports" is visible in `SideNav` primary links; Contacts/Events/Activities/Leaderboard are absent; Admin section is absent.
13. Admin role: all primary links + admin section visible in `SideNav`.
14. Volunteer role: all primary links visible; admin section absent.
15. Sign-out button in `SideNav` bottom slot calls `POST /auth/logout` (best-effort) and `authStore.logout()` then redirects to `/login`.
16. No hardcoded hex colour added in any new component; all colours use Tailwind token classes.
17. Dark mode (`document.documentElement.classList.add('dark')`) renders all new components correctly — `SideNav`, `AppShell`, `ContactDetailPanel`, `EventParticipantPanel` use only token classes.
18. `useMediaQuery` hook cleans up `MediaQueryList` listener on unmount (no memory leak).

---

## 8. Test plan

### 8.1 Backend pytest

No backend changes → no new backend tests required. Existing suite must remain green (`pytest tests/ -q` from `backend/`).

### 8.2 Frontend vitest (`npm run test:run`)

**New test file: `frontend/src/components/layout/AppShell.test.tsx`**
- `renders children on mobile (< lg)` — render `<AppShell><div data-testid="content" /></AppShell>` in a 375 px jsdom env; assert `content` present; assert no `[data-testid="sidenav"]`.
- `renders SideNav on desktop (>= lg)` — mock `window.matchMedia` to return `true` for `(min-width: 1024px)`; render; assert `[data-testid="sidenav"]` present.

**New test file: `frontend/src/components/layout/SideNav.test.tsx`**
- `shows Seraphim wordmark` — render with admin user in store; assert `img[alt="Seraphim"]` present; assert text "Seraphim" visible.
- `shows admin section when isAdmin=true` — assert "Admin" heading and "Settings" link present.
- `hides admin section when isAdmin=false` — set role=volunteer; assert "Admin" heading absent.
- `viewer role hides Contacts link` — set role=viewer; assert "Contacts" link absent; assert "Reports" link present.
- `active link gets primary style` — mock `useLocation` to return `{ pathname: '/contacts' }`; render; assert the Contacts `<a>` has `bg-primary/10` in className.
- `sign-out calls logout and api.post` — spy on `authStore.logout` and `api.post`; click Sign out button; assert both called.

**New test file: `frontend/src/hooks/useMediaQuery.test.ts`**
- `returns true when media matches` — mock `window.matchMedia` to return `{ matches: true, addEventListener: jest.fn(), removeEventListener: jest.fn() }`; render hook; assert `result.current === true`.
- `updates on media change event` — fire `change` event on the mock MQL with `matches=false`; assert hook re-renders with `false`.
- `cleans up listener on unmount` — assert `removeEventListener` called after unmount.

**Modified test: `frontend/src/pages/LoginPage.test.tsx`** (create if absent, or extend existing)
- `renders Seraphim heading` — render `<LoginPage />`; assert `screen.getByRole('heading', { name: /Seraphim/i })` present.
- `does not render LNC Attendance` — assert `screen.queryByText(/LNC Attendance/i)` is null.
- `does not render Volunteer Portal` — assert `screen.queryByText(/Volunteer Portal/i)` is null.

**Modified test: `frontend/src/components/layout/BottomNav.test.tsx`** (create if absent)
- `nav has lg:hidden class` — render `<BottomNav />`; assert the `<nav>` element's className includes `lg:hidden`.

**Build + lint gate:**
```bash
npm run build   # must exit 0 (TypeScript + Vite)
npm run lint    # must exit 0 (ESLint)
npm run test:run  # all tests green
```

---

## 9. Rollout / rollback / risks

### Rollout
S20 is pure frontend config + UI. The backend is not deployed. Deploy steps:
1. Run `npm run build` — verify exit 0.
2. Run `npm run lint` + `npm run test:run` — verify all green.
3. Deploy the new `frontend/dist/` bundle to the Docker container (the `frontend` service in `docker-compose.unraid.yml`). The app is a Vite SPA served by nginx; no DB migration, no service restart beyond `docker compose up -d --no-deps --build frontend`.
4. Smoke-check: open the app in a browser, verify `<title>Seraphim</title>` in the page source; verify `SideNav` visible at ≥ 1024 px.
5. On mobile (or narrow viewport): verify `BottomNav` still functional on all existing pages.

### Rollback
Revert the frontend container to the previous image tag. No data is at risk; no DB migration to reverse. `docker compose up -d --no-deps frontend` with the old image tag.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `BottomNav lg:hidden` removes nav on a tablet that is exactly at the `lg:` breakpoint and has no SideNav visible due to CSS specificity | Low | Medium | Test at 1024 px exactly; use `min-width: 1024px` consistently |
| `AppShell` `lg:pl-60` misaligns content if SideNav is conditionally unmounted | Low | Low | SideNav is always in the DOM at `>= lg`; the `lg:pl-60` on the main element is always applied |
| The `ContactDetailPanel` re-fetches when clicking rows fast (race condition) | Low | Low | TanStack Query caches `['contact-detail', id]`; staleTime 5 min prevents duplicate requests |
| S15 not landed when S20 ships — `isViewer`/`role` missing from authStore | Medium | Low | Forward-compat guard: `s.role ?? s.user?.role`; viewer-hiding evaluates false for all pre-S15 roles (all links show) — safe degradation |
| PWA cached under old name "LNC Attendance" on existing installs | Medium | Low | `VitePWA registerType: 'autoUpdate'` triggers cache invalidation on service-worker update; old installs will update within one app open |
| `useMediaQuery` SSR mismatch (not applicable here — Vite SPA only) | n/a | n/a | n/a |
| `require` dynamic import in `DesktopRoute` confuses bundler | Low | Low | Use static imports with conditional prop logic instead; noted in §5.5 |

---

## 10. Open questions & pending owner artifacts

1. **Logo / artwork:** Does the owner want a new `logo.png` (e.g., a Seraphim-branded icon vs the current LNC logo)? Spec assumes the existing `public/logo.png` is retained; replacing it is a design task outside this sprint and can be dropped in as a static asset swap without a code change.

2. **`DashboardPage` viewer-gating:** S14 gates the dashboard to `AdminRoute`. S15 adds viewer role with "reports/dashboard only" access. S20's `DesktopRoute admin` prop mirrors `AdminRoute` and will lock out viewers until S15 widens the guard. The master must ensure S15's `DesktopRoute viewer-plus` variant lands before or in tandem with S15. **Owner action:** confirm whether viewer dashboard access should ship with S15 or wait for a dedicated S20 follow-up patch.

3. **`SideNav` collapse to icon-only at `lg:` (1024–1280 px):** The 240 px sidebar may feel wide at exactly 1024 px. A collapse-to-icons mode (48 px wide, tooltips on hover) would improve this. Deferred as optional: if owner's real usage is primarily on a desktop browser at 1440 px+, full-width sidebar is fine. Flag for a quick follow-up if usability feedback from the cutover pilot indicates the sidebar crowds the content area at 1024 px.

4. **`ContactDetailPanel` vs navigating to `/contacts/:id`:** On desktop, S20 renders the detail in a right panel and does not navigate to `/contacts/:id`. This means the URL does not update with the selected contact's ID, making it impossible to link/bookmark a specific contact detail from the list view. Options: (a) update the URL via `useNavigate` to `/contacts/:id` even on desktop and render the layout conditionally; (b) accept the current stateful approach. Option (a) is cleaner but requires S03 to have wired up a `ContactDetailPage` that can also be used as a panel. **Recommend option (a); flag for the implementer.** The `isDesktop` branch in S03's router can render the list+panel shell when the route is `/contacts/:id` at desktop widths.

5. **`/activities` route in `SideNav`:** S12 (Activities) is the source of the `/activities` route. If S12 has not landed when S20 ships, the Activities link in SideNav will 404. Guard it: only render the Activities link when `import.meta.env.VITE_S12_LANDED === 'true'` (a build-time env flag), or simply move the link population to a feature-flags constant array that the master can prune before S20 ships. Simpler alternative: add it as an `isAdmin || isVolunteer` guard-only route that renders an `EmptyState("Activities coming soon")` until S12 lands.

6. **`/ranking` on the SideNav vs `/pit` gamification:** The Leaderboard (`/ranking`) is listed as a primary nav item visible to volunteer+admin. After the CiviCRM excision (S01) the leaderboard tracks volunteer recognition-queue accuracy. In CRM context the leaderboard may need to evolve (e.g., activities completed, contacts engaged). Out of scope for S20; note for the master to flag whether `/ranking` belongs in the CRM nav at cutover.

7. **`DashboardPage` back-button removal:** The existing `DashboardPage` has an `<ArrowLeft>` button that navigates to `/settings` (line 48-52). On desktop, SideNav replaces this need. On mobile, the `BottomNav` does not include a direct `/dashboard` tab today (it was reachable only via the admin "More" sheet → Settings → Analytics Dashboard link). Post-S20, `/dashboard` is a first-class SideNav item. The mobile UX gap (how do viewers reach `/dashboard` on mobile after S15 adds viewer role) should be addressed: add `/dashboard` to the `BottomNav` mainTabs for viewer-only, or add it to the admin "More" sheet for admin/volunteer. **Recommendation:** add a "Reports" tab to `BottomNav.mainTabs` visible to all roles, pointing to `/dashboard`. This is a small change that can be bundled in S20 or patched in S15.
