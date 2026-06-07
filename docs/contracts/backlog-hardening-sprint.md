# API Contract — Backlog Hardening Sprint (`chore/backlog-hardening`)

**Status:** Frozen for this sprint. **Wire-format change budget: ZERO.**

This sprint is a behavior-preserving hardening sweep. Every endpoint below is **already shipped**;
this contract is the regression fence that lets frontend and backend be verified independently. The
only frontend-observable change in the entire sprint is **L7** (client-side `isAdmin` re-derivation
after silent refresh) — and even that is a pure client-state fix that consumes the *unchanged*
`POST /auth/refresh` response. No request shape, response shape, status code, header, or cookie
attribute defined here may change. If an implementation forces a change to this document, stop and
escalate — the change is out of scope.

Conventions (project-wide, unchanged):
- **No `/api` prefix on FastAPI routes.** nginx strips `/api/` before proxying. The frontend axios
  client (`frontend/src/services/api.ts`) sets `baseURL: '/api'`, so a frontend call to `/auth/refresh`
  hits backend route `POST /auth/refresh`. Paths below are given as **backend route** with the
  **frontend/nginx path** noted where they differ.
- **Access token:** in-memory only (Zustand `authStore.token`), sent as `Authorization: Bearer <jwt>`.
  Carries no `type` claim.
- **Refresh token:** `HttpOnly`, `SameSite=Lax`, `Secure` **only** when the request arrived over HTTPS
  (`_cookie_secure()` keys off `request.url.scheme`, which reflects `X-Forwarded-Proto`). Carries
  `type: "refresh"` + a `jti`. Max-Age = `refresh_token_expire_days * 86400`.
- **Error envelope:** all errors are FastAPI's `{"detail": "<message>"}`. The frontend surfaces
  `err.response?.data?.detail`.
- **Token-type lockdown (S1):** `verify_token(..., reject_type="refresh")` (renamed from `require_type`
  this sprint — internal Python kwarg only, **not** part of the wire contract) returns `None` for any
  token whose `type` claim equals the rejected value. Bearer and `?_t=` query-param paths reject
  refresh tokens; the HttpOnly-cookie path is permissive.

---

## 1. `POST /auth/refresh`  *(frontend: `POST /api/auth/refresh`)*

The 401→refresh interceptor and the app-boot auth probe both call this. **L7 depends on this response
being unchanged.** **L4** removes the unused `response: Response` handler parameter — the endpoint
already builds its own `Response` locally; this is invisible on the wire.

- **Auth:** none in headers. **Requires** the `refresh_token` HttpOnly cookie.
- **Request body:** none.
- **Request cookies:** `refresh_token=<jwt>` (`type: "refresh"`, with `jti`).
- **Success — `200 OK`:**
  - Body: `{ "access_token": "<jwt>" }`  (`TokenResponse`; `token_type` is **not** emitted — do not add it).
  - `Set-Cookie: refresh_token=<NEW jwt>; HttpOnly; SameSite=Lax[; Secure]; Max-Age=<days*86400>`
    (rotation: a new `jti` is issued and the old `jti` is deny-listed best-effort).
- **Errors:**
  - `401 {"detail": "Missing refresh token"}` — no cookie present.
  - `401 {"detail": "Invalid refresh token"}` — cookie fails `verify_token` or `payload.type != "refresh"`.
  - `401 {"detail": "Refresh token has been revoked"}` — `jti` is in the denylist (logged-out / already-rotated).
- **L7 client contract:** the access-token JWT payload contains `role` (`"admin"` | `"volunteer"`).
  `authStore.setToken()` MUST base64url-decode the payload and set `isAdmin = (payload.role === "admin")`.
  Malformed/undecodable token → `isAdmin = false` (never throw). This does **not** change the response;
  it changes how the client derives state from it.

## 2. `POST /auth/logout`  *(frontend: `POST /api/auth/logout`)*

**L4** rewrites the handler to build its **own** `Response(...)` and call `delete_cookie` on it, then
drops the injected `response: Response` parameter — Set-Cookie behavior identical to today.

- **Auth:** none required (best-effort revocation of whatever cookie is present).
- **Request body:** none.
- **Request cookies:** `refresh_token=<jwt>` (optional; if present and decodable, its `jti` is deny-listed).
- **Success — `200 OK`:**
  - Body: `{ "message": "Logged out" }`
  - `Set-Cookie: refresh_token=; Max-Age=0` (cookie deletion).
- **Errors:** none expected; revocation failures are swallowed (fail-open) and the cookie is still cleared.

## 3. `POST /auth/login`  *(frontend: `POST /api/auth/login`)* — unchanged, fence only

- **Request body:** `{ "email": string, "password": string }` (`LoginRequest`).
- **Success — `200 OK`:** `{ "access_token": "<jwt>" }` + `Set-Cookie: refresh_token=...HttpOnly...`.
- **Errors:** `401 {"detail": "Invalid email or password"}`; `403 {"detail": "Account deactivated"}`.
- **Rate limit:** `5/minute` (slowapi) → `429` with slowapi's default detail on exceed.

## 4. `GET /auth/me`  *(frontend: `GET /api/auth/me`)* — unchanged, fence only

- **Auth:** `Authorization: Bearer <access jwt>` (refresh tokens rejected — `reject_type="refresh"`).
- **Success — `200 OK`:** `{ "id": int, "email": string, "name": string|null, "role": "admin"|"volunteer", "is_active": true }` (`UserResponse`).
- **Errors:** `401 {"detail": "Not authenticated"}` (no creds); `401 {"detail": "Invalid or expired token"}`;
  `401 {"detail": "Invalid token claims"}` (missing `sub`/`email`/`role`). All carry `WWW-Authenticate: Bearer`.

## 5. `GET /tasks/feed`  *(frontend/EventSource: `GET /api/tasks/feed?_t=<access jwt>`)* — unchanged, fence only

SSE stream. **L6** adds nginx log scrubbing on the parent `/api/` and `/` blocks (defense-in-depth);
the dedicated `location = /api/tasks/feed` block already scrubs and is **unchanged**.

- **Auth (three accepted paths):**
  1. `Authorization: Bearer <access jwt>` — refresh tokens **rejected** (`reject_type="refresh"`).
  2. `?_t=<access jwt>` query param (EventSource cannot set headers) — refresh tokens **rejected**.
  3. `refresh_token` HttpOnly cookie — **permissive** (any valid token; this is the browser path).
- **Success — `200 OK`, `Content-Type: text/event-stream`:** frames `data: {json}\n\n` and keepalive
  `: keepalive\n\n` (~15s, `sse_heartbeat_seconds`).
- **Errors:** `401 {"detail": "Not authenticated"}` when all three auth paths fail.

## 6. `GET /storage/{file_path:path}`  *(frontend `<img>`: `GET /api/storage/<path>?_t=<access jwt>`)* — unchanged, fence only

Biometric face-image serving. **L6** adds log scrubbing on parent blocks; the dedicated
`location /api/storage/` block already scrubs and is **unchanged**. **L5** changes the *internal* Redis
client lifecycle behind `is_jti_denied` (singleton + fail-open) with **no** observable change here.

- **Auth (three accepted paths), identical policy to §5:**
  1. `Authorization: Bearer <access jwt>` — refresh tokens **rejected**.
  2. `?_t=<access jwt>` — refresh tokens **rejected**.
  3. `refresh_token` HttpOnly cookie — **permissive**, **but** a deny-listed `jti` is rejected
     (revocation parity with `/auth/refresh`; fail-open under `memory://` → treated as not-denied).
- **Success — `200 OK`:** `FileResponse`; `Content-Type` `image/jpeg` | `image/png` | `application/octet-stream`.
- **Errors:** `401 {"detail": "Not authenticated"}`; `403 {"detail": "Access denied"}` (path-traversal
  containment failure); `404 {"detail": "File not found"}`.

---

## Endpoints explicitly NOT touched

`GET /auth/config`, `GET /auth/google`, `GET /auth/google/callback`, `POST /auth/reset-password`,
`POST /auth/reset-password/confirm`, `GET /auth/users`, `POST /auth/add-volunteer`,
`POST /auth/deactivate/{user_id}`, and all `/members/*`, `/tasks` (non-feed), `/events/*`, etc. They
are listed only to make the zero-wire-change boundary explicit.

## Verification matrix (independent build/verify)

| Contract item | Backend proof | Frontend proof |
|---|---|---|
| §1 refresh shape + rotation + revoke | `tests/test_auth_full.py`, `tests/test_token_type_lockdown.py` | `authStore.test.ts` (decode→`isAdmin`); `AttendeesPage.test.tsx` (admin-gated Sync button consumes `isAdmin`) |
| §2 logout cookie-clear (L4) | `tests/test_auth_full.py` (logout) | n/a |
| §4/§5/§6 reject_type lockdown (M2) | `tests/test_token_type_lockdown.py`, `tests/test_storage_auth.py` | n/a |
| §6 denylist parity (L5) | `tests/test_storage_auth.py` (patches `app.routers.storage.is_jti_denied`) | n/a |
| §5/§6 nginx scrub (L6) | `tests/test_deploy_artifacts.py` (if it inspects nginx) | n/a |

🤖 Generated with [Claude Code](https://claude.com/claude-code)
