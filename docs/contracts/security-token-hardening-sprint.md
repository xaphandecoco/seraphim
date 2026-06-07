# API Contract — Security Token-Hardening Sprint + Gap-Closing

**Branch:** `security/token-hardening-sprint`
**Scope:** Token-type lockdown, refresh rotation + JTI denylist, Attendees "Sync now" (I4), proxy-aware rate-limit regression test (D5).
**Convention:** FastAPI routers carry **no `/api` prefix**; nginx strips `/api` before forwarding. Paths below are the **backend** paths. The frontend calls them through `api` (axios baseURL `/api`), so the browser hits `/api/<path>` and nginx rewrites to `<path>`.
**Auth model:** access token = in-memory Bearer (no `type` claim); refresh token = HttpOnly cookie `refresh_token` (carries `type:"refresh"` + `jti`). The 401 interceptor in `api.ts` auto-calls `POST /auth/refresh`.

This contract is precise enough that frontend and backend can be built independently against it. **No endpoint signatures change in this sprint** — the contract documents the existing, verified surface that the sprint hardens and that I4 consumes. The only *new* artifact is a backend test (`test_rate_limit_proxy.py`); it adds no HTTP surface.

---

## Conventions

- **Content-Type:** `application/json` for all request/response bodies unless noted.
- **Auth header:** `Authorization: Bearer <access_token>` on protected routes. Refresh tokens are **rejected** on every Bearer / `?_t=` path (`verify_token(..., require_type="refresh")` returns `None` → 401). They are accepted **only** on the same-origin HttpOnly cookie path (storage `<img>`, SSE EventSource fallback).
- **Error envelope:** FastAPI default — `{ "detail": <string | array> }`. Frontend surfaces `err.response?.data?.detail`.
- **Roles:** `admin` | `volunteer`. `require_admin` → 403 `{"detail":"Admin access required"}` for non-admins; missing/invalid/refresh token → 401.

---

## 1. `POST /members/sync` — CiviCRM member sync (consumed by I4)

Force a sync of members from CiviCRM into the local DB.

| | |
|---|---|
| **Method / Path** | `POST /members/sync` (browser: `POST /api/members/sync`) |
| **Auth** | Bearer access token, **admin only** (`Depends(require_admin)`) |
| **Request body** | none |
| **Query / path params** | none |

**Success — `200 OK`**
```json
{ "message": "Synced 42 members", "synced_count": 42 }
```
- `message`: human string `"Synced {n} members"`.
- `synced_count`: integer count of upserted members.

**Errors**

| Status | When | Body (`detail`) |
|--------|------|-----------------|
| `401 Unauthorized` | missing/invalid/expired access token, **or a refresh token presented as Bearer** | `"Not authenticated"` / `"Invalid or expired token"` |
| `403 Forbidden` | authenticated non-admin | `"Admin access required"` |
| `503 Service Unavailable` | CiviCRM not configured | `"CiviCRM not configured: <reason>"` |
| `500 Internal Server Error` | sync failure (other) | server-defined detail |

**Frontend (I4) usage:** on `200`, `queryClient.invalidateQueries({ queryKey: ['attendees'] })` (prefix-invalidates all `['attendees', search]` variants) + `toast.success`. On any error, `toast.error(err.response?.data?.detail || 'Sync failed')`. Button rendered **only when** `isAdmin`.

---

## 2. `GET /members/attendees` — attendee list (refreshed by I4)

Backing query for the Attendees page; invalidated after a successful sync.

| | |
|---|---|
| **Method / Path** | `GET /members/attendees` (browser: `GET /api/members/attendees`) |
| **Auth** | Bearer access token (authenticated user) |
| **Query params** | `search` (string, optional, default `""`); `limit` (int, default `200`) |

**Success — `200 OK`** — JSON array of `AttendeeResponse`:
```json
[
  {
    "contact_id": 1234,
    "first_name": "Jane",
    "last_name": "Cruz",
    "email": "jane@example.org",
    "nickname": "Janie",
    "face_thumbnail_path": "/storage/faces/subject-1234/0.jpg",
    "sample_count": 3
  }
]
```
- `email`, `nickname`, `face_thumbnail_path` are nullable.
- `face_thumbnail_path`, when present, is a storage path the frontend renders as `\`/api${attendee.face_thumbnail_path}\`` (served by `GET /storage/{path}`).

**Errors:** `401` (missing/invalid/refresh-as-Bearer token).

**Frontend query key:** `['attendees', search]`. I4 invalidates the `['attendees']` prefix.

---

## 3. `POST /auth/refresh` — rotate refresh token + issue access token (S6, hardened this sprint)

| | |
|---|---|
| **Method / Path** | `POST /auth/refresh` (browser: `POST /api/auth/refresh`) |
| **Auth** | HttpOnly cookie `refresh_token` (sent automatically, same-origin) |
| **Request body** | none |

**Behavior (hardened):** validates the cookie token and requires `payload.type == "refresh"`; if its `jti` is denylisted → 401; otherwise **denylists the old `jti`** (rotation, best-effort/fail-open under `memory://`), mints a **new access token** (body) and a **new refresh token with a new `jti`** (Set-Cookie). Legacy tokens without `jti` still rotate (skip old-deny step).

**Success — `200 OK`**
```json
{ "access_token": "<jwt>", "token_type": "bearer" }
```
**`Set-Cookie: refresh_token=<new-jwt>; HttpOnly; SameSite=Lax; Max-Age=...`** (`Secure` only when the request arrived over HTTPS, i.e. `request.url.scheme == "https"`).

**Errors**

| Status | When | `detail` |
|--------|------|----------|
| `401` | no `refresh_token` cookie | `"Missing refresh token"` |
| `401` | token invalid/expired or `type != "refresh"` | `"Invalid refresh token"` |
| `401` | `jti` is denylisted (rotated-away / logged-out) — **Redis present** | `"Refresh token has been revoked"` |

> **Degradation:** under `REDIS_URL=memory://` the denylist is a no-op (fail-open) — rotation still issues a fresh cookie, but a rotated/revoked token is not hard-rejected. Documented, acceptable pre-prod.

---

## 4. `POST /auth/login` — local login (rate-limited)

| | |
|---|---|
| **Method / Path** | `POST /auth/login` (browser: `POST /api/auth/login`) |
| **Auth** | none |
| **Rate limit** | `5/minute` per client IP (keyed by `get_remote_address` → `request.client.host`; behind the proxy this is the XFF-rewritten client IP — see D5) |
| **Request body** | `{ "email": "user@example.org", "password": "..." }` (`LoginRequest`) |

**Success — `200 OK`**: `{ "access_token": "<jwt>", "token_type": "bearer" }` + `Set-Cookie: refresh_token=...; HttpOnly; SameSite=Lax`.

**Errors**

| Status | When | `detail` |
|--------|------|----------|
| `401` | bad email/password | `"Invalid email or password"` |
| `403` | inactive account | `"Account deactivated"` |
| `429 Too Many Requests` | >5 attempts/min from one client key | SlowAPI default rate-limit body |

> **D5 (rate-limit keying):** `get_remote_address` reads `request.client.host` only — **not** `X-Forwarded-For`. The XFF→client rewrite is performed by uvicorn's **`ProxyHeadersMiddleware`** (server-level, enabled by `--proxy-headers --forwarded-allow-ips=*`), which is **absent from the FastAPI app object**. The D5 regression test wraps an app in `ProxyHeadersMiddleware(trusted_hosts="*")` to prove distinct `X-Forwarded-For` values key to distinct buckets and identical values share a bucket. No change to `rate_limit.py`. With `*`-trust the leftmost XFF is client-spoofable — accepted tradeoff for this single-tunnel/LAN deployment (escalation path recorded in the runbook).

---

## 5. `POST /auth/logout` — clear cookie + best-effort revoke (hardened this sprint)

| | |
|---|---|
| **Method / Path** | `POST /auth/logout` (browser: `POST /api/auth/logout`) |
| **Auth** | reads `refresh_token` cookie if present (not required) |
| **Request body** | none |

**Behavior:** if a `refresh_token` cookie is present and valid, **denylists its `jti`** (best-effort / fail-open), then deletes the cookie.

**Success — `200 OK`**: `{ "message": "Logged out" }` + `Set-Cookie` deleting `refresh_token`.

---

## 6. Token-replay-protected resource paths (lockdown surface — unchanged signatures)

These accept Bearer **or** `?_t=` **or** the HttpOnly cookie. Bearer / `?_t=` **reject `type:"refresh"`**; the cookie path is permissive (browser `<img>` / EventSource send cookies automatically and cannot set headers).

| Endpoint | Method | Auth paths | Success | Refresh-token-as-Bearer / `?_t=` |
|----------|--------|-----------|---------|----------------------------------|
| `/storage/{file_path:path}` | `GET` | Bearer · `?_t=` · cookie | `200` image bytes (`image/jpeg`/`image/png`) | `401 "Not authenticated"` |
| `/tasks/feed` | `GET` | Bearer · `?_t=` · cookie | `200 text/event-stream` (SSE; frames are `json.dumps(separators=(",",":"))` — injection-safe) | `401` |

Path-containment on `/storage`: outside-root → `403 "Access denied"`; missing file → `404 "File not found"`.

---

## Status-code summary (quick reference)

| Code | Meaning in this contract |
|------|--------------------------|
| 200 | Success (sync done, refresh issued, file served, SSE opened) |
| 401 | Missing/invalid/expired token, refresh token presented on a Bearer/`?_t=` path, revoked refresh `jti` (Redis present) |
| 403 | Authenticated but wrong role (non-admin on admin route); `/storage` path traversal |
| 404 | `/storage` file not found |
| 429 | Login rate limit (5/min per client IP) exceeded |
| 503 | CiviCRM not configured (`/members/sync`) |
| 500 | Unhandled sync/server error |

---

*Generated as part of the contract-first sprint plan for `security/token-hardening-sprint`. No endpoint signatures change; this documents the verified surface the sprint hardens and that the I4 frontend work consumes.*
