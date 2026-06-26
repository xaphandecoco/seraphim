# Auth and Security

FastAPI + JWT + HttpOnly cookies + Redis JTI denylist. Roles: `admin`, `volunteer` (plus S15 `viewer` shim). No self-registration — admins only.

## Authentication Flow

### Login (Local or Google OAuth)

**POST /auth/login** (rate-limited: 5/minute)
1. User submits email + password
2. Server looks up User record, verifies bcrypt hash
3. On success, server creates two tokens:
   - **Access Token** (JWT, 15 min default): Claims = {sub: user_id, email, name, role}. Sent in response body.
   - **Refresh Token** (JWT, 7 days default): Contains {sub, email, name, role, type: "refresh", jti: random}. Set as HttpOnly cookie.

**Browser state:**
- Access token stored in-memory (Zustand). Cleared on page reload (forces refresh-token rotation or re-login).
- Refresh token persisted in browser via HttpOnly cookie (auto-sent on every request).

### Refresh Flow (S6 — Token Rotation)

**POST /auth/refresh**
1. Client has stale access token; requests new one with refresh cookie
2. Server validates refresh token signature + expiry
3. Server checks Redis denylist for old JTI (revocation guard)
4. If valid, server:
   - Adds old JTI to Redis denylist (with TTL = remaining lifetime)
   - Issues new access token
   - Issues new refresh token (new JTI)
   - Sets new refresh cookie
5. Returns new access token in body

Rotation works **without Redis** (memory:// falls back); denylist just degrades to advisory.

### Logout

**POST /auth/logout**
1. Client sends request with refresh cookie
2. Server extracts JTI + expiry from refresh token
3. Server adds JTI to Redis denylist (TTL = exp - now)
4. Server deletes refresh cookie
5. Frontend clears in-memory access token

Next refresh attempt finds JTI in denylist → 401 Unauthorized.

### Token Validation

All API endpoints require Bearer token:
```
Authorization: Bearer <access_token>
```

**Dependency: `get_current_user()`** (in dependencies.py)
1. Extracts Bearer token from header
2. Calls `verify_token(token, secret, reject_type="refresh")`
   - Decodes JWT signature (HS256)
   - Rejects if `type == "refresh"` (refresh tokens can only be used at /auth/refresh)
   - Checks expiry
3. Returns payload {sub, email, name, role}

Role-based access via:
- `require_admin()`: Checks role == "admin"
- `require_volunteer()`: Checks role in ("admin", "volunteer")
- `require_viewer()`: Checks role in ("admin", "volunteer", "viewer") — S15 shim

## Token Management

### Tokens (app/utils/auth.py)

| Function | Purpose |
|----------|---------|
| `create_access_token(data, secret, expires_delta)` | JWT with HS256; no `type` or `jti` claim (stateless) |
| `create_refresh_token(data, secret, expires_delta)` | JWT with `type: "refresh"` and `jti: secrets.token_urlsafe(16)` |
| `verify_token(token, secret, reject_type=None)` | Decode + validate; fail-open on errors (return None) |
| `hash_password(plain)` | bcrypt with 72-byte limit enforcement |
| `verify_password(plain, hash)` | bcrypt constant-time check |
| `generate_reset_token()` | `secrets.token_urlsafe(32)` for password reset |

### JTI Denylist (app/utils/token_denylist.py)

Redis-backed blacklist for logged-out refresh tokens.

| Function | Purpose | Behavior |
|----------|---------|----------|
| `deny_jti(jti, exp)` | Add JTI to denylist with TTL | TTL = exp - now; no-op if <= 0 or Redis unavailable (fail-open) |
| `is_jti_denied(jti)` | Check if JTI revoked | Returns False if Redis unavailable (fail-open) |
| `aclose_redis()` | Close singleton client | Call from FastAPI lifespan shutdown hook |

**Design:**
- Singleton Redis client (lazy-init on first call, reused for all subsequent calls)
- Never explicitly `aclose()` per call (kills pool permanently); only on shutdown
- Falls back gracefully: if Redis URL is `memory://` or unreachable, denylist becomes a no-op but auth still works
- Storage key: `jti_denylist:{jti}` with auto-expiry (Redis TTL)

## Rate Limiting (slowapi)

**Limiter config** (app/rate_limit.py):
```python
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=redis_url or "memory://",
    default_limits=["100/minute"],
    swallow_errors=True,  # Fail-open: if Redis down, allow request
)
```

**Per-endpoint limits:**
- **POST /auth/login**: `@limiter.limit("5/minute")` — brute-force protection
- **POST /auth/reset-password/confirm**: `@limiter.limit("3/minute")` — reset token brute-force
- **GET /auth/google/callback**: `@limiter.limit("10/minute")` — OAuth callback rate-limit

Rate-limit state stored in Redis (shared across all Uvicorn workers); falls back to in-memory if Redis unavailable.

## Password Reset

**Admin initiates:**
1. **POST /auth/reset-password** (admin only): Admin supplies volunteer email
2. Server generates reset token (`secrets.token_urlsafe(32)`)
3. Server stores hash of token + 24-hour expiry in User.password_reset_token + password_reset_expires_at
4. Server returns reset link: `/reset-password?token={plain_token}&email={email}`
5. Admin shares link securely with volunteer

**Volunteer resets:**
1. **POST /auth/reset-password/confirm** (rate-limited: 3/minute): Volunteer supplies token + new password
2. Server verifies token hash + expiry (must be < 24h old, password strength: ≥12 chars, uppercase, lowercase, digit, special char)
3. Server clears reset token from User record
4. Server updates password_hash, returns success

**Generic messages** prevent user enumeration (e.g., "If that email exists, a reset link has been generated").

## Google OAuth (Optional)

**POST /auth/google** → **GET /auth/google/callback**

1. Server generates PKCE pair (code_verifier, code_challenge S256)
2. Server redirects to Google OAuth with state + PKCE challenge
3. State and verifier stored in HttpOnly, Secure (HTTPS only), SameSite=Lax cookies (10-min expiry)
4. Google redirects back with auth code
5. Server validates state cookie (CSRF guard), verifies PKCE, exchanges code for ID token
6. Server creates or finds User (by email), issues tokens, sets refresh cookie
7. Server deletes state + verifier cookies, redirects to frontend with access_token in URL query param

**Domain gate** via `check_allowed_domain(email, allowed_domain)` — defaults to `lightnc.org`. Configured in admin_settings.

## Setup Wizard

**GET /setup** (must run before auth is available):
1. Frontend calls GET /setup
2. Server checks if `setup_complete == True` in admin_settings; if yes, returns 503 Service Unavailable
3. If not, frontend presents wizard with steps:
   - Test database connection (POST /setup/test-connection)
   - Test Redis connection (POST /setup/test-connection)
   - Test CompreFace connection (POST /setup/test-service)
   - Collect admin credentials (email, password, name)
   - Optionally configure cameras (list of CameraCreateRequest)
   - Submit SetupRequest (database_url, redis_url, compreface_url, admin creds, cameras, jwt_secret optional)

4. **POST /setup** (not auth-gated): Server creates admin User, seeds AdminSetting, populates Cameras, sets `setup_complete = True`
5. Middleware check_setup_complete fires on all routes: if `setup_complete == False`, return 503 "Setup required"
6. Once complete, normal auth flow resumes

## Password Strength

All password fields validated:
```python
def _validate_password_strength(v: str) -> str:
    assert len(v) >= 12, "Password must be at least 12 characters"
    assert any(c.isupper() for c in v), "Must contain uppercase"
    assert any(c.islower() for c in v), "Must contain lowercase"
    assert any(c.isdigit() for c in v), "Must contain digit"
    assert any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v), "Must contain special char"
```

Applies to:
- Initial admin password (setup)
- Volunteer password reset
- Temporary password on add-volunteer

## Roles & Permissions

| Role | Capabilities |
|------|--------------|
| **admin** | Full system access: setup, user mgmt, settings, can initiate password resets, approve tasks, view all audit logs |
| **volunteer** | Task approval, community reports, can view own leaderboard rank, no access to settings/user mgmt |
| **viewer** | Read-only access (S15 shim); no task approvals; deprecated in favor of volunteer + RBAC |

Access checked via:
- `require_admin()` → 403 if not admin
- `require_volunteer()` → 403 if not admin or volunteer
- `require_viewer()` → 403 if not admin, volunteer, or viewer
- `get_current_user()` → 401 if not authenticated

## Cookie Security

Refresh cookie set with:
- `httponly=True` — prevents JavaScript access (XSS protection)
- `secure=True` — HTTPS only (checked at request.url.scheme == "https"; behind Cloudflare Tunnel or TLS proxy, X-Forwarded-Proto honored)
- `samesite="lax"` — CSRF protection (allows same-site cross-origin requests)
- `max_age=604800` (7 days default) — matches refresh token TTL

On local LAN (plain HTTP), `secure=False` so cookie still works; over HTTPS, `secure=True`.

## Configuration

All auth config stored in DynamicSettings (admin_settings table) + Tier 1 bootstrap:

| Setting Key | Type | Default | Mutable | Notes |
|-------------|------|---------|---------|-------|
| `jwt_secret` | str | "" (required) | Yes | HMAC secret; set at setup; rotate by updating key (new refresh tokens require old secret to decode) |
| `access_token_expire_minutes` | int | 15 | Yes | Access token lifetime |
| `refresh_token_expire_days` | int | 7 | Yes | Refresh token lifetime; JTI denylist TTL aligns |
| `allowed_domain` | str | "lightnc.org" | Yes | Email domain gate for Google OAuth |
| `enable_google_oauth` | bool | False | Yes | Toggle Google OAuth on/off |
| `google_client_id` | str | "" | Yes | OAuth client ID (from Google Console) |
| `google_client_secret` | str | "" | Yes | OAuth client secret (sensitive, masked in UI) |
| `redis_url` | str | "redis://redis:6379/0" | Yes | Redis URL for denylist + rate-limiter (memory:// in tests) |

**DynamicSettings** (app/config.py):
- Singleton that loads from admin_settings on startup
- Can be reloaded at runtime (admin endpoint)
- Provides typed getters: `get_str()`, `get_int()`, `get_bool()`, `get_float()`

---
**Related:**
- [[Backend API]] — /auth endpoints
- [[Backend Services]] — token lifecycle, refresh rotation
- [[Home]] — project overview

**Specs:**
- S1 (Design B) — Bearer-only path must reject refresh tokens
- S6 — Token rotation on refresh (old JTI denylisted)
- S15 — viewer role shim
- S24 — Biometric consent stub
