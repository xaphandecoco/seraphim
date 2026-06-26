# S15 — RBAC: admin / volunteer / viewer
**Phase:** E — Insight & access · **Depends on:** S03 (Native Contact CRUD + Detail Profile — the first net-new CRM surface whose routes must be role-gated) · **Effort:** M · **Status:** Not started

> Reality check (real code, today): the codebase ships a **two-role** model. `User.role` is a free-text `String(20)` defaulting to `"volunteer"` with an inline comment `# admin | volunteer` (`backend/app/models.py:41-43`). The only role guards are `require_admin` and `require_volunteer` in `backend/app/dependencies.py:45-64`. The frontend models exactly two roles everywhere: `User.role: 'volunteer' | 'admin'` (`frontend/src/types/index.ts:5`), `authStore` exposes a single boolean `isAdmin` (`frontend/src/store/authStore.ts:27`), the only route guards are `ProtectedRoute` (any authed user) and `AdminRoute` (`frontend/src/components/layout/{ProtectedRoute,AdminRoute}.tsx`), and `AddVolunteerRequest.role` is `Literal["volunteer", "admin"]` (`backend/app/schemas.py:69`). JWT tokens already carry `role` in the payload — minted at `auth.py:107` (login), `:212` (refresh), `:505` (Google callback); `get_current_user` returns it at `dependencies.py:41`. `authStore.ts:9-22` already implements `decodeJwtRole` which returns the full role string from the JWT payload. **This sprint introduces the third role `viewer`** and re-derives `role` (not just `isAdmin`) end-to-end.

---

## 1. Goal & rationale

Introduce a third, strictly **read-only** role — **`viewer`** — so the church can give staff and community leaders access to reports and the dashboard without exposing contact records, event edits, the task/PIT queues, or any configuration. This closes the locked decision (decisions.md Round 4): *"viewer = reports/dashboard only (no individual contact records)."*

Concretely:
- **Backend:** make the role taxonomy explicit (`admin | volunteer | viewer`), add a DB-level CHECK constraint against bad role values, and add a `require_viewer` dependency that admits **all three** roles for read-only report/dashboard endpoints, plus a `require_role(*allowed)` factory for precise gating in downstream sprints. `require_admin` (`:45`) and `require_volunteer` (`:56`) keep their current semantics exactly; `viewer` is denied by both because it is not in `("admin", "volunteer")` (`dependencies.py:59`).
- **Frontend:** replace the single `isAdmin` boolean with a first-class `role` field on the auth store (keeping `isAdmin` as a derived convenience for all existing consumers), add a `RoleRoute` component (allow-list of roles) reusing the existing guard pattern from `AdminRoute.tsx`, gate the bottom nav per role, and route `viewer` users straight to a permitted landing page (`/dashboard`) — never to a 403-on-load page.

Rationale: the app is becoming the system of record (framework.md §1). A read-only audience is a CRM table-stakes requirement, and doing it now (Phase E, alongside S14 Reporting Dashboard) means viewers land on a real reports surface. The change is small, security-critical, and must be airtight: a viewer must not be able to mutate anything, even by hand-crafting requests. Backend enforcement is the binding security boundary; frontend gating is UX convenience.

---

## 2. Scope

### In scope
- `User.role` becomes a documented 3-value taxonomy `admin | volunteer | viewer`; add a `CheckConstraint` `ck_users_role` (Postgres + SQLite via batch mode) so invalid roles cannot be stored; update inline comment on `models.py:43`.
- New backend dependency `require_viewer` (admits admin/volunteer/viewer) and generic `require_role(*allowed: str)` factory exported from `dependencies.py`; leave `require_admin` and `require_volunteer` **bit-for-bit unchanged**.
- Re-gate the **read-only report/dashboard** endpoints: all GET routes under `/analytics/*` and the `GET /leaderboard` route change from `require_admin`/`require_volunteer` to `require_viewer`. Everything else remains on its existing gate, which already excludes viewers.
- Schema/validation: `AddVolunteerRequest.role` and any role literal inputs widen to accept `viewer`; `UserResponse.role` is unchanged in shape but the doc/comment acknowledges 3 values; unknown roles rejected 422 at the schema layer before any DB write.
- Frontend: `authStore` gains `role: Role | null`; `isAdmin` remains but is derived from `role`; `RoleRoute` guard component; `BottomNav` gated by role; `viewer` default landing = `/dashboard`; `User.role` TS type widens to include `'viewer'`; `Role` type exported from `types/index.ts`.
- User-management UI: role `<select>` adds a **Viewer** option; role badge renders viewer in the list.
- Tests: backend pytest matrix proving each role's allow/deny on representative endpoints across all routers; frontend vitest for store + guards + nav.
- Docs: update `AGENTS.md` role table comment to 3 roles (doc-only line).

### Out of scope
- **No new per-record / per-field ACLs.** Viewer = "reports + dashboard only" at the **route level**. No object-level permissions, field masking, or per-group visibility.
- **No row-level "viewer can see aggregate but not raw contact rows" enforcement inside report payloads** beyond not exposing the contact-list endpoints to them. (Aggregates may include names where the existing report already returns them; tightening report PII is S14's concern, noted in Open Questions.)
- **No self-service role change / self-registration.** Roles are assigned by admins via `/auth/add-volunteer` at create time; this sprint widens the allowed set at creation but does not add an edit-role endpoint (Open Question 4).
- **No change to the auth/token mechanism.** JWT already carries `role`; refresh already copies it (`auth.py:212-218`). We only consume the claim more granularly.
- **No new `audit_log` writes** here. The `audit_log` table is owned by a later sprint per the canonical data model; RBAC denials are surfaced as 403s only.
- **Cases**, smart-group visibility, and future S09/S12 surfaces are gated when those sprints land using the helpers this sprint ships — not retrofitted here.

---

## 3. Data model changes

### Tables / columns

**`users.role`** (`backend/app/models.py:41-43`) — no type change (stays `String(20)`, `default="volunteer"`), but two modifications:

1. Update the inline comment from `# admin | volunteer` to `# admin | volunteer | viewer`.
2. Add `CheckConstraint` in `__table_args__` (new, not currently present on the `User` model). The constraint is declared in the model so `Base.metadata.create_all` enforces it in test SQLite, and in the migration for Postgres.

```python
# backend/app/models.py — inside class User
from sqlalchemy import CheckConstraint  # add to existing import block (line 4-15)

class User(Base):
    __tablename__ = "users"
    # ... existing columns unchanged ...
    role: Mapped[str] = mapped_column(
        String(20), default="volunteer"
    )  # admin | volunteer | viewer

    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'volunteer', 'viewer')",
            name="ck_users_role",
        ),
    )
```

No new columns, FKs, indexes, or unique constraints. No backfill required — existing rows are all `admin` or `volunteer`, both valid under the new constraint.

### Alembic migration

**New file:** `backend/alembic/versions/a4b5c6d7e8f9_add_users_role_check_constraint.py`

- `revision = 'a4b5c6d7e8f9'`
- `down_revision = 'f3a4b5c6d7e8'` — this is the current head (`f3a4b5c6d7e8_add_task_action_approval_unique.py`). **Run `alembic heads` to confirm before authoring; chain to the actual head if another sprint's migration lands first.**

```python
"""add_users_role_check_constraint

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-21
"""
from alembic import op

revision = 'a4b5c6d7e8f9'
down_revision = 'f3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-flight: sanitise any existing rows with an invalid role value so the
    # constraint cannot fail to apply on dirty data. No-op on a clean DB.
    op.execute("UPDATE users SET role = 'volunteer' WHERE role NOT IN ('admin','volunteer','viewer')")

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite requires a table rebuild to add a CHECK constraint.
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_check_constraint(
                "ck_users_role",
                "role IN ('admin', 'volunteer', 'viewer')",
            )
    else:
        op.create_check_constraint(
            "ck_users_role",
            "users",
            "role IN ('admin', 'volunteer', 'viewer')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_constraint("ck_users_role", type_="check")
    else:
        op.drop_constraint("ck_users_role", "users", type_="check")
```

- **`.with_variant(JSONB)` reminder:** not applicable — no JSON columns touched in this migration. (For reference: any JSON column added in adjacent work must use `JSON().with_variant(JSONB, "postgresql")` per `models.py:22`.)
- The pre-flight UPDATE makes `alembic upgrade head` idempotent on a live database. CI requires clean + idempotent on Postgres per AGENTS.md.

---

## 4. Backend

### Endpoints

This sprint adds **no new endpoints**. It (a) adds reusable dependencies to `dependencies.py` and (b) **re-gates existing read-only report/dashboard endpoints** from `require_admin` to `require_viewer` so viewers can reach them. The table lists every endpoint whose gate changes (marked **[gate change]**) plus a representative sample of unchanged endpoints confirming viewer is denied.

| METHOD | path | role (after S15) | request schema | response schema | notes |
|---|---|---|---|---|---|
| GET | `/analytics/attendance-by-event` | viewer+ (admin/vol/viewer) | — | `[{event_id,title,start_date,count}]` | **[gate change]** `require_admin` → `require_viewer` (`analytics.py:22`) |
| GET | `/analytics/tier-distribution` | viewer+ | — | `[{tier,count}]` | **[gate change]** `analytics.py:49` |
| GET | `/analytics/volunteer-stats` | viewer+ | — | aggregate rows | **[gate change]** `analytics.py:63` |
| GET | `/analytics/queue-health` | viewer+ | — | `[{day,total,resolved}]` | **[gate change]** `analytics.py:97` |
| GET | `/analytics/export/attendance` | viewer+ (default) | `event_id?` | `StreamingResponse` CSV | **[gate change]** `analytics.py:122`. Default viewer-allowed; flip to `require_volunteer` if owner restricts exports — see Open Q 1. |
| GET | `/analytics/export/logs` | viewer+ (default) | — | `StreamingResponse` CSV | **[gate change]** `analytics.py:160`. Same caveat as above. |
| GET | `/leaderboard` | viewer+ | `period?` | `LeaderboardResponse` | **[gate change]** `require_volunteer` → `require_viewer` (`leaderboard.py:9,:39`). Dashboard-class read-only data. |
| GET | `/auth/me` | any authed (unchanged) | — | `UserResponse` | `get_current_user` only; already returns `role` at `auth.py:162`. No change. |
| GET/POST/PUT/DELETE | `/members/*` | volunteer+ | — | member payloads | **unchanged** (`require_volunteer`, `members.py:23,68`). Viewer denied — the locked "no individual contact records" rule. |
| GET/POST | `/events/*` | volunteer+ or admin | — | event payloads | **unchanged**. Viewer denied. |
| GET/POST/DELETE | `/attendance/*` | admin | — | — | **unchanged**. Viewer denied. |
| GET/POST | `/tasks/*`, `/audit/*` | volunteer+ | — | — | **unchanged**. Viewer denied (task work is volunteer duty). |
| `*` | `/settings/*`, `/pit/*`, `/cameras/*`, `/logs`, `/uploads/*` | admin | — | — | **unchanged** (`require_admin`). Viewer denied. |

**Denial contract:** a `viewer` calling a volunteer/admin endpoint receives **HTTP 403** with the existing `detail` text ("Volunteer access required" / "Admin access required"). A `viewer` calling a `require_viewer` endpoint succeeds with **200**. Unauthenticated → **401** (unchanged `get_current_user` behavior at `dependencies.py:13-18`).

### Services / workers / business rules

No workers change. The business logic is entirely in `dependencies.py`.

**0. Absorb the `require_reporting` shim from S14 (per CN-17):** S14 introduces `require_reporting` as a forward-compatible shim (`require_reporting = require_volunteer`, admin+volunteer only). **S15 patches that existing shim in place** to also admit `viewer`, redefining it in terms of the new factory: `require_reporting = require_role("admin", "volunteer", "viewer")`. S15 must **NOT** create a duplicate reporting/viewer dependency alongside it — the shim is the single owner of the reporting gate, and downstream reporting routes keep importing `require_reporting` unchanged.

**1. Add `ROLES`, `require_role`, and `require_viewer` to `backend/app/dependencies.py`:**

```python
# backend/app/dependencies.py — additions (existing functions UNCHANGED)

ROLES = ("admin", "volunteer", "viewer")  # canonical taxonomy

# Privilege sets (high → low for READ scope)
_READ_ALL = frozenset(("admin", "volunteer", "viewer"))
_WRITE = frozenset(("admin", "volunteer"))
_ADMIN_ONLY = frozenset(("admin",))


def require_role(*allowed: str):
    """
    Dependency factory: admits only the listed role(s). Returns 403 otherwise.
    Downstream sprints (S03, S09, S12, etc.) use this for precise per-endpoint gating.

    Usage:
        @router.get("/contacts", dependencies=[Depends(require_role("admin", "volunteer"))])
    Or as a function dep:
        user = Depends(require_role("admin"))
    """
    allowed_set = frozenset(allowed)

    async def _dep(current_user: dict = Depends(get_current_user)) -> dict:
        if current_user["role"] not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    # Give the inner dependency a stable __name__ so FastAPI's OpenAPI schema
    # does not collapse multiple role guards into one anonymous dependency.
    _dep.__name__ = f"require_role({'_'.join(sorted(allowed))})"
    return _dep


async def require_viewer(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """
    Read-only surfaces (reports/dashboard): admits admin, volunteer, AND viewer.
    This is the most-permissive authenticated gate.
    An unknown / future role not in _READ_ALL is denied (fail-closed).
    """
    if current_user["role"] not in _READ_ALL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authentication required",
        )
    return current_user
```

- Keep `require_admin` (`dependencies.py:45-53`) and `require_volunteer` (`dependencies.py:56-64`) **bit-for-bit unchanged**. They already exclude `viewer` because `viewer` is not in `("admin", "volunteer")` — confirmed at `:59`.
- `require_viewer` is the most permissive authenticated gate. The name follows the charter; semantically it means "any logged-in role may read this."

**2. Edge cases and business rules:**

- **Unknown role in a valid JWT** (e.g., a legacy token before this sprint, or a junk role on a tampered token — not feasible without the secret, but defense-in-depth): `require_viewer` checks `role not in _READ_ALL`, so any unexpected string → 403. Fail-closed; no fallback to read access.
- **Token role vs DB role drift:** gates trust the JWT `role` claim, as the codebase does today (`get_current_user` returns `payload["role"]` at `dependencies.py:41`). A role change takes effect on the next access-token mint (login or `/auth/refresh`, which copies `role` from the refresh token's payload at `auth.py:212-218`). This is a known limitation (unchanged by this sprint); documented in §9. Instant revocation would require re-reading role from DB per request — cross-ref Open Question 5.
- **`get_current_user` claim validation** already requires `role` present (`dependencies.py:30`), so no missing-claim path reaches the gates.
- **`require_role` factory stability:** the `_dep.__name__` line prevents FastAPI from collapsing multiple factory-produced guards in the OpenAPI docs. The returned inner function is a normal FastAPI `Depends`-able coroutine.
- **Performance:** all gates are O(1) frozenset membership; negligible overhead.

**3. Schema validation rule (create user):** `AddVolunteerRequest.role` (`schemas.py:69`) widens from `Literal["volunteer", "admin"]` to `Literal["volunteer", "admin", "viewer"]`. Any future edit-role endpoint must validate against `ROLES`. Unknown roles are rejected at the Pydantic schema layer (422) before any DB write or CHECK constraint is reached.

### File-by-file change list (backend)

| File | Action | What changes |
|---|---|---|
| `backend/app/dependencies.py` | MODIFY | Add `ROLES`, `_READ_ALL`, `_WRITE`, `_ADMIN_ONLY` constants; add `require_role(*allowed)` factory; add `require_viewer`. Leave `require_admin`, `require_volunteer`, `get_current_user`, `check_setup_complete` untouched. (Refs: `:45-64`.) |
| `backend/app/models.py` | MODIFY | Add `CheckConstraint` import to the existing `from sqlalchemy import (...)` block (`lines 4-15`); update `User.role` inline comment to `# admin \| volunteer \| viewer` (`:43`); add `__table_args__` with `ck_users_role` CheckConstraint. (Ref: `:41-48`.) |
| `backend/app/schemas.py` | MODIFY | `AddVolunteerRequest.role`: `Literal["volunteer", "admin", "viewer"]` (`:69`). Add module-level `ROLE_VALUES: tuple[str, ...] = ("admin", "volunteer", "viewer")` constant for future reuse. Update `TokenPayload.role` docstring/comment. No shape change to `UserResponse`. |
| `backend/app/routers/analytics.py` | MODIFY | Change import line `from app.dependencies import require_admin` to `from app.dependencies import require_viewer`. Replace `_user=Depends(require_admin)` with `_user=Depends(require_viewer)` on all six GET handlers (`:22,:49,:63,:97,:122,:160`). (Default: viewer-allowed for exports; see Open Q 1.) |
| `backend/app/routers/leaderboard.py` | MODIFY | Change import to `from app.dependencies import require_viewer`; change GET handler dep (`:39`) from `require_volunteer` to `require_viewer`. |
| `backend/alembic/versions/a4b5c6d7e8f9_add_users_role_check_constraint.py` | CREATE | As specified in §3. |

**Do NOT change:** every router using `require_admin` or `require_volunteer` (members, events, attendance, tasks, audit, pit, cameras, settings, logs, uploads) is **intentionally** left to deny viewer. No edits there.

---

## 5. Frontend

### Pages / routes / components

The state model change is the heart of the frontend work. Today the store carries only `isAdmin` (`authStore.ts:27`). We add a canonical `role` and derive `isAdmin` from it so every existing `isAdmin` consumer keeps working without changes.

**`frontend/src/types/index.ts` (MODIFY):**

Add the `Role` union export at the top and widen `User.role`:

```typescript
// Add before the User interface (line 1)
export type Role = 'admin' | 'volunteer' | 'viewer';

export interface User {
  id: number;
  email: string;
  name: string | null;
  role: Role;           // was: 'volunteer' | 'admin'
  is_active: boolean;
}
// rest of file unchanged
```

**`frontend/src/store/authStore.ts` (MODIFY):**

`decodeJwtRole` already exists and returns the full role string (`:9-22`). It just needs to validate against `Role` and return `null` for anything outside the known set:

```typescript
import type { User, Role } from '@/types';

const VALID_ROLES: ReadonlySet<string> = new Set(['admin', 'volunteer', 'viewer']);

function decodeJwtRole(token: string): Role | undefined {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return undefined;
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded));
    const role = payload?.role;
    return (typeof role === 'string' && VALID_ROLES.has(role))
      ? (role as Role)
      : undefined;
  } catch {
    return undefined;
  }
}

interface AuthState {
  user: User | null;
  token: string | null;
  role: Role | null;        // ADD — canonical role; isAdmin derived from it
  isAdmin: boolean;
  isAuthenticated: boolean;
  authReady: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  setUser: (user: User | null) => void;
  setToken: (token: string) => void;
  setAuthReady: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  role: null,              // ADD
  isAdmin: false,
  isAuthenticated: false,
  authReady: false,

  login: (user, token) => {
    set({
      user,
      token,
      role: user.role,
      isAdmin: user.role === 'admin',
      isAuthenticated: true,
      authReady: true,
    });
  },

  logout: () => {
    set({ user: null, token: null, role: null, isAdmin: false, isAuthenticated: false, authReady: true });
  },

  setUser: (user) => {
    set({
      user,
      role: user?.role ?? null,
      isAdmin: user?.role === 'admin',
      isAuthenticated: !!user,
    });
  },

  setToken: (token) => {
    const role = decodeJwtRole(token) ?? null;
    set({ token, role, isAdmin: role === 'admin', isAuthenticated: true });
  },

  setAuthReady: () => {
    set({ authReady: true });
  },
}));
```

Key invariants:
- `isAdmin` is always `role === 'admin'` — never set independently. Existing `s.isAdmin` consumers keep working with zero changes.
- An unknown/tampered role in the JWT → `role = null`, `isAdmin = false`, `isAuthenticated = true` (the user is authed but has no permissions). They will fail `RoleRoute` checks and be redirected to `/login` (no token) or appropriate page.
- `setToken` is called during the silent refresh path (`useAuth.ts`) before `login` is called; the role stored here is overwritten by `login` on success.

**`frontend/src/components/layout/RoleRoute.tsx` (CREATE):**

```tsx
import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';
import type { Role } from '@/types';

interface RoleRouteProps {
  children: React.ReactNode;
  /** Roles that may render children. */
  allow: Role[];
}

export function RoleRoute({ children, allow }: RoleRouteProps) {
  const token = useAuthStore((s) => s.token);
  const role = useAuthStore((s) => s.role);
  const authReady = useAuthStore((s) => s.authReady);

  // Wait for the initial /auth/refresh attempt before deciding (same as ProtectedRoute / AdminRoute)
  if (!authReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <div
          className="h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary"
          aria-label="Loading"
        />
      </div>
    );
  }

  if (!token) {
    return <Navigate to="/login" replace />;
  }

  if (!role || !allow.includes(role)) {
    // Never dump a user on a 403 surface.
    // Viewer is redirected to their primary landing; others to the task queue.
    return <Navigate to={role === 'viewer' ? '/dashboard' : '/'} replace />;
  }

  return (
    <div className="pb-16">
      {children}
      <BottomNav />
    </div>
  );
}
```

`AdminRoute` can be left intact (it already behaves as `allow={['admin']}` semantically) or thinned to a re-export:

```tsx
// AdminRoute.tsx — optional thin re-export (keeps behavior identical, reduces drift)
import { RoleRoute } from './RoleRoute';
export function AdminRoute({ children }: { children: React.ReactNode }) {
  return <RoleRoute allow={['admin']}>{children}</RoleRoute>;
}
```

Either approach is acceptable; the thin re-export is preferred to eliminate drift between the two guard implementations.

**`frontend/src/App.tsx` (MODIFY):**

```tsx
import { RoleRoute } from '@/components/layout/RoleRoute';

// Route changes (lines 54-64):
// /dashboard: was AdminRoute → RoleRoute allow all (viewer primary landing)
<Route path="/dashboard" element={<RoleRoute allow={['admin','volunteer','viewer']}><DashboardPage /></RoleRoute>} />

// /ranking: leaderboard is viewer-accessible (read-only)
<Route path="/ranking" element={<RoleRoute allow={['admin','volunteer','viewer']}><RankingPage /></RoleRoute>} />

// Working routes — viewer redirected to /dashboard by RoleRoute
<Route path="/" element={<RoleRoute allow={['admin','volunteer']}><TasksPage /></RoleRoute>} />
<Route path="/audit" element={<RoleRoute allow={['admin','volunteer']}><AuditPage /></RoleRoute>} />
<Route path="/events" element={<RoleRoute allow={['admin','volunteer']}><EventsPage /></RoleRoute>} />
<Route path="/attendees" element={<RoleRoute allow={['admin','volunteer']}><AttendeesPage /></RoleRoute>} />

// Admin-only (unchanged semantics — use RoleRoute for consistency or keep AdminRoute)
<Route path="/settings" element={<RoleRoute allow={['admin']}><SettingsPage /></RoleRoute>} />
<Route path="/settings/users" element={<RoleRoute allow={['admin']}><UserManagementPage /></RoleRoute>} />
<Route path="/settings/attendance" element={<RoleRoute allow={['admin']}><AttendancePage /></RoleRoute>} />
<Route path="/pit" element={<RoleRoute allow={['admin']}><PitPage /></RoleRoute>} />
<Route path="/logs" element={<RoleRoute allow={['admin']}><LogsPage /></RoleRoute>} />
```

**Viewer default landing:** a viewer navigating to `/` hits `<RoleRoute allow={['admin','volunteer']}>` which redirects them to `/dashboard` (the `role === 'viewer'` branch in `RoleRoute`). No separate index-redirect component is needed.

**`frontend/src/components/layout/BottomNav.tsx` (MODIFY):**

```tsx
// Add to imports:
import { LayoutDashboard } from 'lucide-react';
import type { Role } from '@/types';

// Change line 20 to also read role:
const role = useAuthStore((s) => s.role);
const isAdmin = useAuthStore((s) => s.isAdmin);  // keep for adminTabs gate

// Viewer tab set:
const viewerTabs = [
  { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { path: '/ranking',   label: 'Ranking',   icon: Trophy },
];

// Replace the hardcoded mainTabs usage:
const tabs = role === 'viewer' ? viewerTabs : mainTabs;

// In the render: replace `mainTabs.map(...)` with `tabs.map(...)`
// The pending badge block (line 94) is already guarded by `tab.path === '/'`
// which viewers never see → no leak.
// The admin "More" sheet (lines 41-72, 102-114) stays gated on isAdmin — unchanged.
```

The existing tab loop at `BottomNav.tsx:79-99` uses `tab.path` for the badge check, so swapping `mainTabs` for `tabs` safely hides the badge from viewer.

**`frontend/src/pages/UserManagementPage.tsx` (MODIFY):**

- Local `UserRecord.role` type (`:10`): widen to `'admin' | 'volunteer' | 'viewer'`.
- Local `AddForm.role` type (`:20`): widen to `'volunteer' | 'admin' | 'viewer'`.
- Role `<select>` options: add `<option value="viewer">Viewer</option>` after the existing Volunteer option (`:127`).
- Role badge rendering (`:175-181`): add a third branch for `viewer`, e.g. `Eye` icon (`lucide-react`) and a muted tint (`text-foreground/60`).

```tsx
// Role badge helper (extend existing pattern):
const roleBadge = (role: 'admin' | 'volunteer' | 'viewer') => {
  if (role === 'admin') return <span className="flex items-center gap-1 text-primary"><Shield size={14} /> Admin</span>;
  if (role === 'viewer') return <span className="flex items-center gap-1 text-foreground/60"><Eye size={14} /> Viewer</span>;
  return <span className="flex items-center gap-1 text-foreground/40"><User size={14} /> Volunteer</span>;
};
```

Import `Eye` from `lucide-react` alongside existing `Shield` and `User` imports.

### State (TanStack Query keys, Zustand)

- Zustand: `authStore` gains `role`; all existing `s.isAdmin` selectors continue working. No new query keys — RBAC is client state, not server state. Existing report queries (S14) are reused as-is; viewers simply now have API access to them.
- **SSE viewer-aware filter (per CN-19).** Viewers may open the SSE stream but must not receive face/contact PII events. Add a viewer-aware filter in the `main.py` SSE generator: for a viewer session, only `system.*` and `job.*` events are yielded; `detection.*` and `task.*` events (face/contact PII) are filtered out **before yielding**. Concretely, inside the generator, when `payload["role"] == "viewer"`, skip any event whose type does not start with `system.` or `job.`. (This supersedes any blanket "deny viewer / return 403" approach — viewers stay subscribed but receive a PII-free event subset.)

### UX states (loading / empty / error), design tokens, mobile-first

- **Loading:** `RoleRoute` reuses the exact spinner markup from `AdminRoute.tsx:15-20` (`bg-background` / `border-t-primary` tokens). No hex.
- **Denied (client):** `RoleRoute` always redirects to a permitted route (viewer → `/dashboard`). No raw 403 page is ever shown. If a viewer's API call 403s anyway (stale link, direct URL), the calling page should surface `err.response?.data?.detail` via `sonner` toast. For 403 specifically: toast "You don't have access to this." (using the server `detail` when present). This is the existing axios interceptor convention at `services/api.ts` — add the 403 case alongside the existing 401 handler.
- **Empty / error:** report/dashboard empty states are S14's responsibility; unchanged here.
- **Design tokens:** all new UI (viewer badge, viewer nav tabs) uses `bg-card`, `text-foreground`, `text-foreground/60`, `bg-background`, `text-primary` — no hardcoded hex (AGENTS.md). Dark mode automatic via tokens.
- **Mobile-first:** the viewer bottom-nav set (2 tabs) distributes with the existing `flex-1` layout (`BottomNav.tsx:78`) and works at 375px. The admin "More" sheet and badge are not visible to viewers.

### File-by-file change list (frontend)

| File | Action | What changes |
|---|---|---|
| `frontend/src/types/index.ts` | MODIFY | Export `Role` type; widen `User.role` to `Role`. (`:1-7`.) |
| `frontend/src/store/authStore.ts` | MODIFY | Add `role: Role \| null` to state; validate decoded role against `VALID_ROLES`; derive `isAdmin` from `role` in every setter. Existing `decodeJwtRole` refactored to return `Role \| undefined`. (`:9-60`.) |
| `frontend/src/components/layout/RoleRoute.tsx` | CREATE | Allow-list guard component (pattern from `AdminRoute.tsx`). |
| `frontend/src/components/layout/AdminRoute.tsx` | MODIFY | Thin re-export via `<RoleRoute allow={['admin']}>` (optional but recommended). |
| `frontend/src/App.tsx` | MODIFY | Import `RoleRoute`; repoint routes per §5 table. (`:54-64`.) |
| `frontend/src/components/layout/BottomNav.tsx` | MODIFY | Read `role` from store; conditionally render viewer tabs (Dashboard + Ranking) vs working tabs; keep admin sheet gated on `isAdmin`. (`:18-120`.) |
| `frontend/src/pages/UserManagementPage.tsx` | MODIFY | Widen role types; add Viewer select option; render viewer badge with `Eye` icon. (`:10,:20,:127,:175-181`.) |
| `frontend/src/services/api.ts` | MODIFY | Add 403 response handling in the interceptor: `toast.error(error.response?.data?.detail ?? "You don't have access to this.")` alongside the existing 401 handler. |

**Note:** every existing `s.isAdmin` consumer (`BottomNav`, `AdminRoute`, `UserManagementPage`, `SettingsPage`, etc.) keeps working because `isAdmin` remains a derived field on the store. No mass rename required; that is the deliberate compatibility design.

---

## 6. Migration / data

- **Schema:** one migration (`a4b5c6d7e8f9`) adding the `ck_users_role` CHECK constraint (§3). Idempotent via the pre-flight UPDATE.
- **Data:** no backfill of role values needed — existing users are `admin` or `volunteer`, both valid. No `viewer` rows exist until an admin creates one via User Management.
- **Seed / first-admin:** unchanged (`/setup` still mints the first admin at `setup.py`). The setup path does not need to know about `viewer`.
- **Downgrade safety:** `downgrade` drops the CHECK constraint only; role column values are untouched (a `viewer` row survives as plain text, which is acceptable — the old free-text column accepts it). A frontend on an old build renders an unknown role badge gracefully (non-admin styling) and the backend without this sprint denies viewer tokens on all auth routes, so there is no privilege escalation on rollback. Note this asymmetry in the runbook.

---

## 7. Acceptance criteria (numbered, each independently testable)

1. `User.role` accepts exactly `admin`, `volunteer`, `viewer`; inserting any other string (e.g. `"superuser"`) fails with an `IntegrityError` (CHECK constraint) on both SQLite (test `create_all`) and Postgres (migration). `alembic upgrade head` then `downgrade -1` then `upgrade head` is clean and idempotent on Postgres.

2. `require_viewer` returns the user dict (200) for an **admin**, a **volunteer**, and a **viewer** token; returns **403** for a token with any other role string (e.g. `"superuser"`); returns **401** when there is no token.

3. `require_volunteer` returns **403** for a `viewer` token and **200** for admin/volunteer (no regression). `require_admin` returns **403** for both `viewer` and `volunteer`, **200** for admin only.

4. A **viewer** calling `GET /analytics/tier-distribution` (and each other re-gated GET analytics endpoint) receives **200**; before S15 the same call returned **403** from `require_admin`.

5. A **viewer** is denied (**403**) on: `GET /members`, `GET /members/attendees`, all `/tasks/*`, `/audit/*`, `/events` mutations (`POST /events/set-active`, `POST /events/sync`), `/attendance/*`, `/settings/*`, `/pit/*`, `/cameras/*`, `/logs`, `/uploads/*`.

6. `POST /auth/add-volunteer` with `role: "viewer"` (admin auth, valid strong `temporary_password`) creates a user with `role == "viewer"` and returns **200**; with `role: "superuser"` returns **422** (Pydantic validation) and writes no row to the database.

7. `GET /auth/me` for a viewer user returns `{"role": "viewer", ...}`; the access token and refresh token minted at login both carry `role: "viewer"` in their JWT payload; `POST /auth/refresh` for a viewer session returns a new access token also carrying `role: "viewer"` (preserving the value from `auth.py:212-218`).

8. Frontend `authStore`: after `login(VIEWER_USER, VIEWER_TOKEN)`, `role === 'viewer'` and `isAdmin === false`; after `setToken(ADMIN_TOKEN)` then `setToken(VIEWER_TOKEN)`, `role` is `'viewer'` and `isAdmin` is `false` (no stale-admin carryover); after `setToken` with a token bearing `role: "superuser"`, `role === null` and `isAdmin === false` with no exception thrown.

9. `RoleRoute allow={['admin','viewer']}` renders children when `role === 'viewer'`; with `allow={['admin','volunteer']}` and `role === 'viewer'`, the component redirects to `/dashboard`; with no token, it redirects to `/login`; with `authReady === false`, it renders the spinner (`aria-label="Loading"`).

10. A signed-in **viewer** navigating to `/attendees` (or `/`, `/audit`, `/events`) is redirected to `/dashboard`. Navigating to `/dashboard` and `/ranking` renders the page without a redirect.

11. `BottomNav` for a viewer renders exactly **Dashboard** and **Ranking** links (and no Tasks/Members/Events/Audit links and no admin "More" button). For a volunteer: renders the five working tabs (Tasks/Audit/Ranking/Events/Members) and no "More" button. For an admin: renders working tabs plus the "More" button.

12. `UserManagementPage` role select contains `<option value="viewer">Viewer</option>`. Creating a viewer user causes a viewer badge to appear in the user list.

13. `npm run build`, `npm run lint`, `npm run test:run` all pass with no errors or type errors. Backend `pytest tests/ -q` passes with the new RBAC matrix fully green.

---

## 8. Test plan

### Backend pytest

**New file: `backend/tests/test_rbac_roles.py`**

**Add to `backend/tests/conftest.py`:** viewer user and header fixtures (mirror the volunteer fixtures at `:169-200`):

```python
@pytest_asyncio.fixture
async def viewer_user(db_session):
    from app.models import User
    user = User(
        email="viewer@lightnc.org",
        password_hash=hash_password("ViewPass123!"),
        role="viewer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user

@pytest_asyncio.fixture
async def viewer_auth_headers(viewer_user):
    token = make_token(viewer_user.id, viewer_user.email, viewer_user.role, "Viewer")
    return {"Authorization": f"Bearer {token}"}
```

**Tests in `test_rbac_roles.py`:**

```python
# test_require_viewer_admits_all_three_roles
# GET /analytics/tier-distribution with admin/volunteer/viewer → all 200

# test_require_viewer_denies_unauthenticated
# GET /analytics/tier-distribution, no Authorization header → 401

# test_viewer_denied_on_members_list
# GET /members/attendees with viewer headers → 403
# GET /members/attendees with volunteer headers → 200  (proves "no individual contact records" rule)

# test_viewer_denied_on_tasks
# GET /tasks with viewer headers → 403

# test_viewer_denied_on_admin_settings
# GET /settings with viewer headers → 403

# test_volunteer_still_denied_on_admin
# GET /logs with volunteer headers → 403  (no regression)

# test_admin_still_denied_on_viewer_route_with_wrong_test — not applicable (admin is always ⊇)

# test_role_check_constraint_rejects_bad_role
# Create User(role="superuser") via db_session, commit → expect IntegrityError

# test_add_volunteer_accepts_viewer_role
# POST /auth/add-volunteer (admin_auth_headers) with role="viewer" and strong password → 200/201
# Response role == "viewer"; DB row exists with role "viewer"

# test_add_volunteer_rejects_unknown_role
# POST /auth/add-volunteer with role="superuser" → 422; no user created

# test_me_returns_viewer_role
# Login as viewer_user, GET /auth/me → role == "viewer"

# test_refresh_preserves_viewer_role
# Mint a refresh cookie for viewer, POST /auth/refresh, decode new access_token JWT payload → role == "viewer"
# Guards against regression in auth.py:212-218

# Parametrized allow/deny matrix (lock the full grid):
@pytest.mark.parametrize("role,endpoint,method,expected_status", [
    # viewer+ endpoints (require_viewer)
    ("admin",     "/analytics/tier-distribution", "GET", 200),
    ("volunteer", "/analytics/tier-distribution", "GET", 200),
    ("viewer",    "/analytics/tier-distribution", "GET", 200),
    # volunteer+ endpoints (require_volunteer)
    ("admin",     "/members/attendees",            "GET", 200),
    ("volunteer", "/members/attendees",            "GET", 200),
    ("viewer",    "/members/attendees",            "GET", 403),
    # admin-only endpoints (require_admin)
    ("admin",     "/settings",                    "GET", 200),
    ("volunteer", "/settings",                    "GET", 403),
    ("viewer",    "/settings",                    "GET", 403),
])
async def test_rbac_matrix(role, endpoint, method, expected_status, client, ...):
    ...
```

This matrix locks the allow/deny grid for all three roles across the three gate levels.

### Frontend vitest

**Extend `authStore.test.ts`** (existing tests at `:57-187` remain; add):

```typescript
// Viewer identity via setToken
it("setToken with viewer JWT sets role='viewer' and isAdmin=false", () => {
  useAuthStore.getState().setToken(VIEWER_TOKEN);
  expect(useAuthStore.getState().role).toBe('viewer');
  expect(useAuthStore.getState().isAdmin).toBe(false);
  expect(useAuthStore.getState().isAuthenticated).toBe(true);
});

// Viewer identity via login
it("login with viewer user sets role='viewer'", () => {
  useAuthStore.getState().login(VIEWER_USER, VIEWER_TOKEN);
  expect(useAuthStore.getState().role).toBe('viewer');
  expect(useAuthStore.getState().isAdmin).toBe(false);
});

// setUser with viewer
it("setUser with viewer sets role='viewer'; setUser(null) → role=null", () => {
  useAuthStore.getState().setUser(VIEWER_USER);
  expect(useAuthStore.getState().role).toBe('viewer');
  useAuthStore.getState().setUser(null);
  expect(useAuthStore.getState().role).toBeNull();
});

// Stale-state: no carryover
it("switching from admin to viewer token clears isAdmin", () => {
  useAuthStore.getState().setToken(ADMIN_TOKEN);
  expect(useAuthStore.getState().isAdmin).toBe(true);
  useAuthStore.getState().setToken(VIEWER_TOKEN);
  expect(useAuthStore.getState().role).toBe('viewer');
  expect(useAuthStore.getState().isAdmin).toBe(false);
});

// Unknown role → null, no throw
it("setToken with unknown role yields role=null, isAdmin=false, no throw", () => {
  expect(() => useAuthStore.getState().setToken(SUPERUSER_TOKEN)).not.toThrow();
  expect(useAuthStore.getState().role).toBeNull();
  expect(useAuthStore.getState().isAdmin).toBe(false);
});
// Add VIEWER_TOKEN = makeJwt({ sub:'3', role:'viewer', exp: 9999999999 }) etc.
// alongside existing ADMIN_TOKEN/VOLUNTEER_TOKEN helpers.
```

**Create `RoleRoute.test.tsx`:**

```tsx
// Render <RoleRoute allow={['admin','volunteer','viewer']}><p>ok</p></RoleRoute>
// Store: viewer + token + authReady=true → renders "ok"
// Store: viewer + token + authReady=true + allow={['admin','volunteer']} → navigates to /dashboard
// Store: authReady=false → spinner aria-label="Loading" present
// Store: no token + authReady=true → navigates to /login
```

**Create or extend `BottomNav.test.tsx`:**

```tsx
// role='viewer': finds 'Dashboard' link, finds 'Ranking' link,
//   does NOT find 'Tasks'/'Members'/'Events'/'Audit',
//   does NOT find button[aria-label="Admin menu"]
// role='volunteer': finds Tasks/Audit/Ranking/Events/Members,
//   does NOT find Admin menu button
// role='admin': finds working tabs AND Admin menu button
```

**Extend `UserManagementPage` test:**

```tsx
// Assert role <select> contains option with value="viewer"
```

---

## 9. Rollout / rollback / risks

**Rollout order:**
1. Run migration `a4b5c6d7e8f9` (adds CHECK constraint — additive, non-breaking).
2. Deploy backend (new `require_viewer`/`require_role`, re-gated analytics/leaderboard).
3. Deploy frontend (role-aware store/guards/nav).

Steps 2 and 3 can be reversed in order. No existing user is a `viewer` at deploy time, so no existing session is affected. After deploy, an admin creates viewer accounts via the updated User Management page.

**Rollback:**
- `alembic downgrade -1` drops the CHECK constraint. Role column reverts to free-text; `viewer` rows survive harmlessly.
- Reverting frontend to a build without `role` leaves `isAdmin` working (it was always there). Any existing `viewer` user on the old UI is treated as a non-admin (effectively volunteer-level on the client) — but the **backend still denies** them writes (no `viewer` in `("admin","volunteer")`), so there is no privilege escalation on rollback.
- Note the rollback asymmetry in the runbook.

**Risks and mitigations:**

| Risk | Mitigation |
|---|---|
| Privilege escalation via stale token role | Documented limitation (unchanged from today). Short access-token TTL already configured. DB-truth re-check is out of scope here; cross-ref Open Question 5. |
| Missed endpoint (viewer accidentally reaching a write route) | Parametrized pytest matrix asserts deny on a representative endpoint per router. Default-deny posture: only explicitly re-gated GET reports admit viewer. |
| Frontend nav leak (viewer sees a link to a forbidden page) | Nav is role-gated AND `RoleRoute` redirects AND backend 403s — three layers. UI guard is UX; backend is the enforcement boundary. |
| Constraint apply failure on dirty data | Pre-flight `UPDATE` in migration handles any stale/unknown roles before the constraint is applied. |
| CSV export to viewers containing PII | Default is viewer-allowed for read-only aggregates. Owner can flip to `require_volunteer` in one line. Flagged in Open Q 1. |
| `viewers` receiving face/contact PII over SSE | SSE handler in `main.py:121-208` does not currently filter events by role. Add a **viewer-aware filter** in the generator (per CN-19): for a viewer session, yield only `system.*` and `job.*` events and skip `detection.*` / `task.*` (PII) events before yielding. Viewers stay subscribed; they just receive a PII-free subset (not a blanket 403). |

---

## 10. Open questions & pending owner artifacts

1. **CSV / XLSX export for viewers:** should the analytics export endpoints (`/analytics/export/attendance`, `/analytics/export/logs`) be viewer-readable, or volunteer+ only? Default in this spec = **viewer-allowed** (read-only export of aggregates). One-line flip to `require_volunteer` if the owner restricts exports. **(Owner decision.)**

2. **Leaderboard visibility for viewers:** treated here as dashboard-class read-only (viewer-allowed). Confirm the owner is comfortable showing volunteer names and points totals to viewers, or restrict to volunteer+. **(Owner to confirm.)**

3. **Report PII for viewers:** S14 analytics endpoints may return contact names inside aggregate rows (e.g., "attendance-by-event" titles are generic, but "volunteer-stats" returns volunteer names). The locked rule is "no individual contact *records*." Named aggregates are a grey area. Owner to confirm whether viewer report payloads require name masking (would be S14 work, not S15). **(Owner to confirm.)**

4. **Edit-role / demote flow:** this sprint widens create-time role validation but adds no "change an existing user's role" endpoint or UI. If needed, the implementation is: `PATCH /auth/users/{id}/role` (admin only), validates against `ROLES`, updates `user.role`, writes an `audit_log` row once that table exists. Confirm whether this belongs here or in a later user-management sprint. **(Owner decision.)**

5. **Instant revocation:** is the current short-TTL-token expiry acceptable for role demotion (a demoted user retains access until their access token expires), or is immediate revocation required (would need a DB-truth `role` re-check per request, or a user-scoped JTI denylist)? **(Owner decision; would be a token-hardening follow-up if required.)**

6. **SSE viewer filter (implementation note, not a question — per CN-19):** the fix is a viewer-aware event filter in the `main.py` SSE generator: for a viewer session, yield only `system.*` and `job.*` events and filter out `detection.*` / `task.*` (face/contact PII) events before yielding. Implementor should treat this as a mandatory part of this sprint even though it is in `main.py` rather than `dependencies.py`. (This replaces the earlier "deny viewer entirely" framing: viewers stay subscribed but receive only the PII-free event subset.)
