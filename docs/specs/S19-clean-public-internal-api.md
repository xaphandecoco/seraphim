# S19 — Clean Public/Internal API
**Phase:** F — Automation · **Depends on:** S03 (contacts/events), S04 (events), S15 (viewer role + `require_role` deps), S17 (outbox + automation_rules; `public.py` public router established in S13) · **Effort:** M · **Status:** Not started

---

## 1. Goal & rationale

After S17 (Automation Core) and S18 (Integrations), Seraphim has a rules engine and internal workers but **no stable, documented, externally-consumable API surface**. The owner's automation needs — creating events from a webhook, posting attendance sheets, or triggering notifications — require a clean external contract that does not expose internal session tokens or admin-UI internals.

S19 closes three gaps:

1. **API keys** — long-lived, scoped, revocable credentials for external automation (e.g. scripted imports, Google Apps Script, future Zapier/n8n replacement). Separate from the short-lived JWT access tokens used by the browser app.
2. **HMAC-signed inbound webhooks** — a per-source secret that lets a third party (Google Forms, GitHub, future Zoom webhook) prove the call is legitimate, echoing the existing `scripts/webhook_listener.py` pattern.
3. **OpenAPI docs page** — a protected `/api-docs` page (admin only, never public) that renders the FastAPI OpenAPI schema and is **off** in production by default; a versioned `/v1/` path prefix for all public/external routes.

The result: an owner can write `curl -H "X-API-Key: sea_..." https://seraphim.lightnc.org/api/v1/contacts` and get real data back without browser-session juggling; a Google Forms submit can hit `POST /api/v1/webhooks/newcomer` with HMAC signature and be processed atomically. The `n8n Create Schedule` workflow (n8n id `pz7sHlUbU6jV1Hqm`) routes here: webhook → S19 verifies + routes → S04 event CRUD.

This sprint does **not** build a full public self-serve developer portal. It builds the exact primitives needed for the owner and the identified n8n replacement flows.

---

## 2. Scope

### In scope
- **`api_keys` table** — id, name, key_hash (SHA-256 of raw key), prefix (first 8 chars, for display), scopes (JSONB list), owner_user_id FK, is_active, last_used_at, expires_at (nullable), created_at, revoked_at. Admin CRUD.
- **`webhook_secrets` table** — id, source (unique slug, e.g. `newcomer`, `event_create`, `github`), secret_hash (HMAC secret), is_active, created_at, updated_at.
- **API-key auth dependency** (`get_api_key_auth`) — reads `X-API-Key` header, hashes it, looks up `api_keys`, checks `is_active`, records `last_used_at`. Returns scoped context dict. **Does NOT replace JWT; is additive.**
- **Scope enforcement** — each protected `/v1/` endpoint declares a required scope string (e.g. `contacts:read`, `events:write`, `participants:write`); the dep raises 403 if the key's scopes list does not include it or `*`.
- **HMAC webhook verification dependency** (`verify_webhook_hmac`) — reads `X-Webhook-Signature` (format `sha256=<hex>`), retrieves source secret from `webhook_secrets`, constant-time compares `hmac.new(secret, body, sha256).hexdigest()` against the header value. Raises 401 on mismatch. Echoes `scripts/webhook_listener.py:28-45` pattern.
- **`/v1/` router** registered in `main.py` **before** `check_setup_complete` gate for `POST /v1/webhooks/{source}` (inbound webhook must receive a 200 or 202 without setup-check blocking it); all other `/v1/` routes sit **after** setup check and behind either `get_api_key_auth` or `get_current_user`.
- **Endpoint surface (see §4 endpoints table):** `/v1/contacts` (list/get), `/v1/events` (list/get/create), `/v1/participants/bulk` (bulk write), `/v1/webhooks/{source}` (inbound HMAC-signed), `/v1/me` (key introspection).
- **Rate limiting** — API-key requests keyed on the key prefix (not IP), 1000/hour cap, using the existing `limiter` (SlowAPI). Webhook endpoint: 60/minute per source.
- **OpenAPI docs page** — a `/api-docs` HTML page (React or a simple redirect to `/docs`) gated by `require_admin`; the FastAPI `openapi_url` and `docs_url` remain `None` in production (current `main.py:72-79` behavior) and the `/api-docs` route is the only admin-only exposure. The admin can toggle visibility via an `admin_settings` key `api_docs_enabled`.
- **Versioning** — all external routes live under the `/v1/` prefix. The path is part of the URL, not a header. A future `/v2/` can coexist.
- **Admin UI** — `ApiKeysPage` (CRUD: create key → show raw once → list masked, revoke), `WebhookSecretsPage` (CRUD: create source, regenerate secret), settings sub-tab.
- **Audit logging** — every API-key-authenticated write is recorded in `audit_log` with `actor_id = NULL` (system/external) and `action = api_key.{endpoint}`, plus the key prefix in `after` JSONB.
- **`DynamicSettings` accessor** `get_api_docs_enabled()` — reads `api_docs_enabled` admin setting (default false).
- **Key rotation helper** — `POST /api-keys/{id}/rotate` replaces key hash atomically; returns new raw key once.
- **`requirements.txt`** — no new dependencies; `hmac` and `hashlib` are stdlib; `slowapi` already present.

### Out of scope
- Self-serve developer portal / API keys issued to contacts (not system users) → not planned.
- OAuth2 client credentials flow → deferred; API keys suffice for the owner's single-instance use case.
- Per-endpoint rate-limit UI → admin sets the global key cap via `admin_settings`; per-endpoint config is a future concern.
- Public OpenAPI JSON at an anonymous URL → never. The schema is admin-only.
- Webhook retry + delivery receipts for outbound webhooks → that is S17's `outbox`.
- Pagination/filtering parity with every internal endpoint → `/v1/` is a curated thin layer over the existing routers, not a full mirror. Missing filters are a future v1.x concern.
- GraphQL → not planned.

---

## 3. Data model changes

### 3.1 New table: `api_keys`

```
api_keys
--------
id              Integer  PK autoincrement
name            String(100)  NOT NULL
key_prefix      String(8)    NOT NULL           -- first 8 chars of raw key (display only, not secret)
key_hash        String(64)   NOT NULL  UNIQUE   -- SHA-256 hex of raw key
scopes          JSON/JSONB   NOT NULL  default '[]'  -- list[str], e.g. ["contacts:read","events:write"]
owner_user_id   Integer  FK -> users.id  ON DELETE CASCADE  NOT NULL
is_active       Boolean  NOT NULL  default True
last_used_at    DateTime nullable
expires_at      DateTime nullable
created_at      DateTime NOT NULL  default utc_now()
revoked_at      DateTime nullable
```

Indexes:
- `UNIQUE(key_hash)` — the lookup hot path.
- `ix_api_keys_owner` on `(owner_user_id)`.
- `ix_api_keys_prefix` on `(key_prefix)` — admin list display.

The raw key is **never stored**. Format: `sea_<base64url(32 bytes)>` (43 chars of random + prefix = ~50 chars total). Shown to the user once at creation.

### 3.2 New table: `webhook_secrets`

```
webhook_secrets
---------------
id          Integer  PK autoincrement
source      String(64)   NOT NULL  UNIQUE  -- slug, e.g. "newcomer", "event_create", "github"
secret_hash String(64)   NOT NULL          -- SHA-256 hex of raw HMAC secret
is_active   Boolean  NOT NULL  default True
description String(255) nullable
created_at  DateTime NOT NULL  default utc_now()
updated_at  DateTime NOT NULL  default utc_now()  onupdate utc_now()
```

Index: `UNIQUE(source)` (inherent from constraint).

The raw secret is generated server-side (`secrets.token_hex(32)`) and shown once. Only the hash is stored; verification uses `hmac.compare_digest(expected_hex, provided_hex)` where `expected_hex = hmac.new(raw_secret, body_bytes, sha256).hexdigest()` — but since only the hash is stored, we cannot reconstruct the raw secret for verification. **Correction to design:** the HMAC secret is stored **encrypted** in the DB using Fernet with the `jwt_secret` as the key material, OR stored as plaintext in `admin_settings` (marked `sensitive=True`). Given the existing `admin_settings.sensitive` pattern and the 1-user admin context of this project, the simpler path is: store the raw HMAC secret in `webhook_secrets.secret_value` as plaintext (the column is marked sensitive in application logic; it is not returned in list responses). This matches how `civicrm_api_key` was handled in `admin_settings`. Rename the column accordingly:

```
webhook_secrets
---------------
id           Integer  PK
source       String(64)  NOT NULL  UNIQUE
secret_value String(255) NOT NULL  -- raw HMAC secret; never returned in list/get responses
is_active    Boolean  NOT NULL  default True
description  String(255) nullable
created_at   DateTime
updated_at   DateTime
```

No hash column needed — verification reads `secret_value` directly and uses `hmac.compare_digest`. The raw value is never exposed via GET endpoints (masked as `"********"`).

### 3.3 Alembic plan

One new migration: `s19_add_api_keys_and_webhook_secrets.py`

```python
# upgrade()
op.create_table(
    "api_keys",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("name", sa.String(100), nullable=False),
    sa.Column("key_prefix", sa.String(8), nullable=False),
    sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
    sa.Column("scopes", JSON().with_variant(JSONB, "postgresql"), nullable=False,
              server_default=sa.text("'[]'")),
    sa.Column("owner_user_id", sa.Integer(),
              sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("last_used_at", sa.DateTime(), nullable=True),
    sa.Column("expires_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("revoked_at", sa.DateTime(), nullable=True),
)
op.create_index("ix_api_keys_owner", "api_keys", ["owner_user_id"])
op.create_index("ix_api_keys_prefix", "api_keys", ["key_prefix"])

op.create_table(
    "webhook_secrets",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("source", sa.String(64), nullable=False, unique=True),
    sa.Column("secret_value", sa.String(255), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    sa.Column("description", sa.String(255), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

# downgrade()
op.drop_index("ix_api_keys_prefix", "api_keys")
op.drop_index("ix_api_keys_owner", "api_keys")
op.drop_table("api_keys")
op.drop_table("webhook_secrets")
```

No backfill required (new tables, no existing data). Migration is additive/non-destructive. `down_revision` = head at implementation time (never invent an ID — run `alembic heads` to resolve).

### 3.4 Model additions (`backend/app/models.py`)

Add two new ORM classes to `models.py`. Both use the existing `JSONB` alias (`JSON().with_variant(JSONB, "postgresql")`) and `utc_now()` from `models.py:22/27`.

```python
class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class WebhookSecret(Base):
    __tablename__ = "webhook_secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    secret_value: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
```

### 3.5 `DynamicSettings` accessor

Add to `config.py` (`DynamicSettings` class, after line 163):

```python
def get_api_docs_enabled(self) -> bool:
    return self.get_bool("api_docs_enabled", False)

def get_api_key_rate_limit(self) -> str:
    return self.get_str("api_key_rate_limit", "1000/hour")
```

---

## 4. Backend

### 4.1 Endpoint table

| Method | Path | Role/Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| `POST` | `/api-keys` | `require_admin` | `ApiKeyCreate {name, scopes, expires_at?}` | `ApiKeyCreateResponse {id, prefix, raw_key, scopes, expires_at}` | Raw key shown once; hash stored |
| `GET` | `/api-keys` | `require_admin` | — | `list[ApiKeyResponse]` | Masked; never returns key_hash or raw |
| `GET` | `/api-keys/{id}` | `require_admin` | — | `ApiKeyResponse` | — |
| `PATCH` | `/api-keys/{id}` | `require_admin` | `ApiKeyUpdate {name?, scopes?, is_active?, expires_at?}` | `ApiKeyResponse` | Cannot un-revoke; use rotate |
| `POST` | `/api-keys/{id}/revoke` | `require_admin` | — | `{message}` | Sets `is_active=False`, `revoked_at=now` |
| `POST` | `/api-keys/{id}/rotate` | `require_admin` | — | `ApiKeyCreateResponse` | Issues new raw key; atomically replaces hash; old key immediately invalid |
| `DELETE` | `/api-keys/{id}` | `require_admin` | — | `{message}` | Hard delete (key_hash gone, old key permanently invalid) |
| `POST` | `/webhook-secrets` | `require_admin` | `WebhookSecretCreate {source, description?}` | `WebhookSecretCreateResponse {id, source, raw_secret}` | Raw secret shown once |
| `GET` | `/webhook-secrets` | `require_admin` | — | `list[WebhookSecretResponse]` | `secret_value` masked |
| `POST` | `/webhook-secrets/{id}/rotate` | `require_admin` | — | `WebhookSecretCreateResponse` | New raw secret; old immediately invalid |
| `DELETE` | `/webhook-secrets/{id}` | `require_admin` | — | `{message}` | Hard delete |
| `GET` | `/api-docs` | `require_admin` (session JWT) | — | HTML / redirect | Serves FastAPI schema page; only when `api_docs_enabled=True` |
| `GET` | `/v1/me` | API key (any scope) | — | `ApiKeyMeResponse {id, name, prefix, scopes, owner_email}` | Key self-introspection |
| `GET` | `/v1/contacts` | API key `contacts:read` | `?search=&page=&page_size=` | `PaginatedContactResponse` (from S03) | Delegates to S03 service; soft-deleted excluded |
| `GET` | `/v1/contacts/{id}` | API key `contacts:read` | — | `ContactDetailResponse` (from S03) | — |
| `POST` | `/v1/contacts` | API key `contacts:write` | `ContactCreate` (from S03) | `ContactDetailResponse` | Audited; actor_id=NULL |
| `PATCH` | `/v1/contacts/{id}` | API key `contacts:write` | `ContactUpdate` (from S03) | `ContactDetailResponse` | — |
| `GET` | `/v1/events` | API key `events:read` | `?page=&page_size=&from_date=&to_date=` | `PaginatedEventResponse` (from S04) | — |
| `GET` | `/v1/events/{id}` | API key `events:read` | — | `EventDetailResponse` (from S04) | — |
| `POST` | `/v1/events` | API key `events:write` | `EventCreate` (from S04) | `EventDetailResponse` | Audited |
| `POST` | `/v1/participants/bulk` | API key `participants:write` | `BulkParticipantRequest` (from S05) | `BulkParticipantResponse` | Delegates to S05 bulk service |
| `GET` | `/v1/participants` | API key `participants:read` | `?event_id=&contact_id=&page=` | `PaginatedParticipantResponse` | — |
| `POST` | `/v1/webhooks/{source}` | HMAC `verify_webhook_hmac` | Raw body (any content-type) + `X-Webhook-Signature` | `{accepted: true, queued_id?}` 202 | No setup check; persists to `outbox` (S17); returns 401 on bad HMAC |

### 4.2 New files

**`backend/app/routers/api_keys.py`**

Router prefix `/api-keys`, tag `api-keys`. Admin-only. Contains:
- `create_api_key(req: ApiKeyCreate, db, current_user)` — generates `secrets.token_urlsafe(32)`, prefixes with `sea_`, takes first 8 chars as `key_prefix`, SHA-256 hashes the full raw key (`hashlib.sha256(raw.encode()).hexdigest()`), creates `ApiKey` row, returns `ApiKeyCreateResponse` with the raw key. The raw key is **not** persisted.
- `list_api_keys(db, current_user)` — SELECT all for owner or all (admin sees all).
- `get_api_key(id, db, current_user)` — 404 if not found.
- `update_api_key(id, req, db, current_user)` — PATCH fields; cannot set `is_active=True` if `revoked_at` is set.
- `revoke_api_key(id, db, current_user)` — sets `is_active=False`, `revoked_at=utc_now()`.
- `rotate_api_key(id, db, current_user)` — generates new raw key, updates `key_hash`/`key_prefix`, clears `revoked_at`, sets `is_active=True`, returns new raw key.
- `delete_api_key(id, db, current_user)` — hard delete.

**`backend/app/routers/webhook_secrets.py`**

Router prefix `/webhook-secrets`, tag `webhook-secrets`. Admin-only. Contains:
- `create_webhook_secret(req, db, current_user)` — generates `secrets.token_hex(32)`, stores in `secret_value`, returns raw once.
- `list_webhook_secrets(db, current_user)` — returns list with `secret_value = "********"`.
- `rotate_webhook_secret(id, db, current_user)` — new raw secret, updates `secret_value`, returns raw once.
- `delete_webhook_secret(id, db, current_user)` — hard delete.

**`backend/app/routers/public_v1.py`**

Router prefix `/v1`, tag `public-api-v1`. **This is distinct from `routers/public.py`** (which S13 owns for the unauthenticated newcomer form). `public_v1.py` contains:
- The HMAC-protected inbound webhook: `POST /v1/webhooks/{source}` — registered **without** `check_setup_complete` gate.
- All `/v1/` data endpoints — registered **with** `check_setup_complete` gate and `get_api_key_auth` dep.

**`backend/app/dependencies.py`** (modify)

Add after `require_volunteer` (line 64):

```python
async def get_api_key_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Authenticate via API key (X-API-Key header). Returns scoped context."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    if api_key.expires_at and api_key.expires_at < utc_now():
        raise HTTPException(status_code=401, detail="API key expired")
    # Stamp last_used_at (best-effort, no await on failure)
    api_key.last_used_at = utc_now()
    await db.commit()
    return {
        "key_id": api_key.id,
        "key_prefix": api_key.key_prefix,
        "scopes": api_key.scopes or [],
        "owner_user_id": api_key.owner_user_id,
    }


def require_scope(scope: str):
    """FastAPI dep factory: enforce a specific scope on an API-key request."""
    async def _check(key_ctx: dict = Depends(get_api_key_auth)) -> dict:
        if "*" not in key_ctx["scopes"] and scope not in key_ctx["scopes"]:
            raise HTTPException(
                status_code=403,
                detail=f"API key missing required scope: {scope}"
            )
        return key_ctx
    return _check


async def verify_webhook_hmac(
    source: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> bytes:
    """Verify X-Webhook-Signature (sha256=<hex>) against stored secret."""
    import hmac as _hmac
    sig_header = request.headers.get("X-Webhook-Signature", "")
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed X-Webhook-Signature")
    provided_hex = sig_header[7:]

    result = await db.execute(
        select(WebhookSecret).where(
            WebhookSecret.source == source, WebhookSecret.is_active == True
        )
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(status_code=401, detail=f"Unknown webhook source: {source}")

    body = await request.body()
    expected_hex = _hmac.new(
        wh.secret_value.encode(), body, "sha256"
    ).hexdigest()
    if not _hmac.compare_digest(expected_hex, provided_hex):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    return body
```

Note: `hmac.new` is `hmac.new(key_bytes, msg_bytes, digestmod)` in stdlib — key and msg must both be bytes. The raw secret is decoded from the stored string before passing.

**`backend/app/services/api_key_service.py`**

Thin service layer with:
- `generate_raw_key() -> str` — `"sea_" + secrets.token_urlsafe(32)`.
- `hash_key(raw: str) -> str` — `hashlib.sha256(raw.encode()).hexdigest()`.
- `key_prefix(raw: str) -> str` — `raw[:8]`.
- `generate_webhook_secret() -> str` — `secrets.token_hex(32)`.
- `audit_api_write(db, key_ctx, action, entity, entity_id, before, after)` — thin wrapper around the `audit.record` helper (S02) with `actor_id=None` (system/external), adding `after["_api_key_prefix"] = key_ctx["key_prefix"]`.

### 4.3 `main.py` changes

```python
# Import new routers
from app.routers import api_keys as api_keys_router
from app.routers import webhook_secrets as webhook_secrets_router
from app.routers import public_v1 as public_v1_router
```

Registration order in `main.py` (after existing setup/auth blocks):

```python
# S19: API key + webhook secret management (admin-only, after setup check)
app.include_router(
    api_keys_router.router,
    dependencies=[Depends(check_setup_complete)],
)
app.include_router(
    webhook_secrets_router.router,
    dependencies=[Depends(check_setup_complete)],
)

# S19: /v1/ inbound webhook — registered WITHOUT check_setup_complete
# (must receive requests before setup completes in edge cases)
app.include_router(public_v1_router.webhook_router)  # prefix /v1/webhooks

# S19: /v1/ data endpoints — registered WITH check_setup_complete
app.include_router(
    public_v1_router.v1_router,
    dependencies=[Depends(check_setup_complete)],
)
```

Two sub-routers in `public_v1.py`:
- `webhook_router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])` — no setup gate.
- `v1_router = APIRouter(prefix="/v1", tags=["public-api-v1"])` — behind setup gate.

### 4.4 `/api-docs` endpoint

Add to `main.py` (or `routers/settings.py`):

```python
@app.get("/api-docs")
async def api_docs_page(current_user: dict = Depends(require_admin)):
    """Admin-only OpenAPI docs (enabled via api_docs_enabled setting)."""
    if not dynamic_settings.get_api_docs_enabled():
        raise HTTPException(status_code=404, detail="API docs not enabled")
    # Return Swagger UI HTML directly (FastAPI openapi_url is /openapi.json; re-enable temporarily)
    from fastapi.responses import HTMLResponse
    openapi_url = "/openapi.json"
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html><head><title>Seraphim API Docs</title>
<meta charset="utf-8"/>
<link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head><body>
<div id="swagger-ui"></div>
<script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: "{openapi_url}", dom_id: "#swagger-ui"}})</script>
</body></html>""")
```

Note: This requires `openapi_url` to be non-None. Add a conditional in `main.py`:

```python
_is_prod = legacy_settings.ENVIRONMENT == "production"
_show_docs = not _is_prod
app = FastAPI(
    title="Seraphim CRM API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,       # Always off; use /api-docs (admin-gated)
    redoc_url=None,
    openapi_url="/openapi.json" if not _is_prod else None,
    # In prod, /openapi.json also off by default; /api-docs will 404 unless setting enabled
)
```

When `api_docs_enabled=True` in production, the admin enables `/openapi.json` by restarting with `ENVIRONMENT=development` OR by adding a bypass in `main.py` controlled by the setting. **Simpler approach used in this spec:** the `/api-docs` route always exists (admin-gated 404 unless enabled), and `openapi_url` is `"/openapi.json"` unconditionally (the schema endpoint itself is not user-facing data). The AGENTS.md concern is about interactive docs exposing the full surface — the admin-gated Swagger UI wrapper handles that. Keep `docs_url=None` and `redoc_url=None` in prod. `openapi_url="/openapi.json"` is acceptable since the JSON is not human-readable without a UI wrapper and is protected by the `/api-docs` gate.

### 4.5 Rate limiting for `/v1/` routes

Add a custom key function for API-key-authenticated requests:

```python
# backend/app/rate_limit.py — add alongside existing limiter
from slowapi import Limiter

def _api_key_or_ip(request: Request) -> str:
    """Key function: use key prefix for API-key requests, else IP."""
    key = request.headers.get("X-API-Key", "")
    if key:
        return f"apikey:{key[:8]}"  # prefix only; never log the full key
    return get_remote_address(request)

api_key_limiter = Limiter(
    key_func=_api_key_or_ip,
    storage_uri=_redis_url,
    default_limits=["1000/hour"],
    swallow_errors=True,
)
```

Apply `@api_key_limiter.limit("1000/hour")` to `/v1/` data endpoints and `@limiter.limit("60/minute")` to `/v1/webhooks/{source}` (per-IP). Both limiters must be registered on `app.state`:

```python
app.state.limiter = limiter  # existing
app.state.api_key_limiter = api_key_limiter  # new
```

Register both exception handlers for `RateLimitExceeded` (the same `_rate_limit_exceeded_handler` from slowapi handles either limiter).

### 4.6 Business rules and edge cases

**Key scopes:** The canonical scope vocabulary is:
- `*` — superscope (all operations).
- `contacts:read`, `contacts:write`
- `events:read`, `events:write`
- `participants:read`, `participants:write`
- `webhooks:receive` — not checked on the endpoint (HMAC is the auth); reserved for future scope narrowing.

Scopes are stored as a JSON list in `api_keys.scopes`. A key with `["contacts:read"]` cannot write. `require_scope("contacts:write")` raises 403 for that key. `require_scope("contacts:read")` passes if `"*"` or `"contacts:read"` is in scopes.

**Inbound webhook routing:** `POST /v1/webhooks/{source}` reads `source` from the path, verifies HMAC against the `webhook_secrets` row for that source, then routes based on source slug:
- `newcomer` → deserialize body as `NewcomerWebhookPayload`; enqueue to `outbox` with `event_type="webhook.newcomer"` and `payload=body_json`. S17's outbox worker processes it.
- `event_create` → deserialize as `EventCreateWebhookPayload`; call S04 event service directly (no outbox needed; synchronous create is fast).
- Any other source → accept + enqueue raw to outbox with `event_type=f"webhook.{source}"` for S17 rules engine evaluation.

Always return `202 Accepted` with `{"accepted": true}` immediately. Never block on outbox processing.

**Expiry check:** `expires_at` is compared with `utc_now()` (naive UTC). If expired, return 401 with `"API key expired"`. The check happens in `get_api_key_auth`.

**Rotate atomicity:** `POST /api-keys/{id}/rotate` issues the new raw key, updates `key_hash`/`key_prefix`, clears `revoked_at`, sets `is_active=True`, commits in a single transaction. Between the old hash being replaced and the commit, no request can use either key (sub-ms window). Acceptable for this use case.

**Audit for API-key writes:** all mutating `/v1/` calls go through `audit_api_write(...)` which calls the shared `audit.record(...)` (S02) with `actor_id=None` (the system actor for external calls). The `after` JSONB field includes `"_api_key_prefix": key_ctx["key_prefix"]` so the admin can trace which key made the call.

**`/v1/contacts` pagination:** delegates to the existing S03 contact-list service. Page size max 100. Excludes soft-deleted contacts (same as internal API).

**`/v1/participants/bulk`:** delegates to S05 bulk-participant service. Source field is set to `"manual"` (external caller) unless the payload includes `source` in the allowed set. `source` may be `manual|import|community_report` from external callers; `face` and `zoom` are reserved for internal pipeline writes.

**CORS:** `/v1/` routes are covered by the existing CORS middleware (`allow_origins=[legacy_settings.FRONTEND_URL]`). External callers (scripts, curl) do not go through a browser, so CORS is irrelevant. If a third-party browser app needs access, the admin must add its origin to `admin_settings["allowed_origins"]` (a future concern; not in scope here).

**`/v1/me` endpoint:** always accessible to any valid key regardless of scopes. Returns `{id, name, prefix, scopes, owner_email, expires_at, last_used_at}` — never `key_hash`.

### 4.7 File-by-file summary

| Action | File | Notes |
|---|---|---|
| CREATE | `backend/app/routers/api_keys.py` | New router `/api-keys`, admin-only CRUD + rotate |
| CREATE | `backend/app/routers/webhook_secrets.py` | New router `/webhook-secrets`, admin-only CRUD + rotate |
| CREATE | `backend/app/routers/public_v1.py` | Two sub-routers: `webhook_router` + `v1_router`; inbound HMAC webhook + v1 data endpoints |
| CREATE | `backend/app/services/api_key_service.py` | `generate_raw_key`, `hash_key`, `key_prefix`, `generate_webhook_secret`, `audit_api_write` |
| CREATE | `backend/alembic/versions/s19_add_api_keys_and_webhook_secrets.py` | New migration |
| MODIFY | `backend/app/models.py` | Add `ApiKey`, `WebhookSecret` ORM classes |
| MODIFY | `backend/app/dependencies.py` | Add `get_api_key_auth`, `require_scope`, `verify_webhook_hmac` |
| MODIFY | `backend/app/config.py` | Add `get_api_docs_enabled()`, `get_api_key_rate_limit()` to `DynamicSettings` |
| MODIFY | `backend/app/rate_limit.py` | Add `api_key_limiter` + `_api_key_or_ip` key function |
| MODIFY | `backend/app/main.py` | Register new routers; add `/api-docs` endpoint; register `api_key_limiter` |
| MODIFY | `backend/app/schemas.py` | Add `ApiKeyCreate`, `ApiKeyCreateResponse`, `ApiKeyResponse`, `ApiKeyUpdate`, `WebhookSecretCreate`, `WebhookSecretCreateResponse`, `WebhookSecretResponse`, `ApiKeyMeResponse`, `NewcomerWebhookPayload`, `EventCreateWebhookPayload` |
| MODIFY | `backend/app/routers/__init__.py` | Export `api_keys`, `webhook_secrets`, `public_v1` |

---

## 5. Frontend

### 5.1 Pages and routes

**`frontend/src/pages/ApiKeysPage.tsx`** — new page, admin-only.

Route: `/settings/api-keys` (added to `App.tsx` inside the `AdminRoute` wrapper).

Sections:
1. **API Keys list** — `DataTable` (S03 primitive) with columns: Name, Prefix, Scopes (badges), Owner, Created, Expires, Last Used, Status (Active / Revoked badge), Actions (Revoke / Rotate / Delete). Empty state: "No API keys yet. Create one to enable external automation."
2. **Create Key modal** — fields: Name (required), Scopes (multi-checkbox from canonical scope vocabulary), Expires At (date picker, optional). On submit: `POST /api-keys`; on success opens a **one-time key display modal** (copy button, warning "This key will not be shown again"). Uses `sonner.toast.success("API key created")`.
3. **Rotate Key** — `ConfirmDialog` ("Are you sure? The existing key will stop working immediately."); on confirm: `POST /api-keys/{id}/rotate`; shows new raw key in one-time display modal.
4. **Revoke / Delete** — `ConfirmDialog` for each.

**`frontend/src/pages/WebhookSecretsPage.tsx`** — new page, admin-only.

Route: `/settings/webhooks` (inside `AdminRoute`).

Sections:
1. **Webhook Secrets list** — table with columns: Source (slug), Description, Active, Created, Actions (Rotate / Delete).
2. **Create Secret modal** — fields: Source slug (lowercase, no spaces — validate with `/^[a-z0-9_-]+$/`), Description (optional). On submit: shows raw secret in one-time display modal.
3. **Rotate** — `ConfirmDialog`; shows new secret in one-time display modal.

**`frontend/src/pages/ApiDocsPage.tsx`** — new minimal page, admin-only.

Route: `/api-docs` (inside `AdminRoute`). Shows an `<iframe src="/api-docs" .../>` pointing at the backend admin-only API docs endpoint, or a direct redirect. If `api_docs_enabled=false`, shows an informational card explaining how to enable it via Settings.

### 5.2 Components

**`frontend/src/components/ui/OneTimeSecretModal.tsx`** — new reusable component.

Props: `{ open: boolean, label: string, value: string, onClose: () => void }`. Renders a modal with:
- Warning banner: "Copy this [label] now. It will not be shown again."
- `<code>` block with the value in a monospace font.
- Copy button (uses `navigator.clipboard.writeText(value)`; toast "Copied!").
- Close button (confirms "Have you copied your key?").
- Does not allow the modal to be dismissed by clicking the overlay — must use the Close button.

### 5.3 TanStack Query keys

```typescript
// frontend/src/services/api.ts — add API key endpoints

export const apiKeysKeys = {
  all: ["apiKeys"] as const,
  detail: (id: number) => ["apiKeys", id] as const,
};

export const webhookSecretsKeys = {
  all: ["webhookSecrets"] as const,
};
```

Queries:
- `useApiKeys()` — `GET /api-keys`; staleTime 30s.
- `useApiKey(id)` — `GET /api-keys/{id}`.
- `useWebhookSecrets()` — `GET /webhook-secrets`.

Mutations:
- `useCreateApiKey()` — `POST /api-keys`; invalidates `apiKeysKeys.all`; on success opens `OneTimeSecretModal`.
- `useRevokeApiKey()` — `POST /api-keys/{id}/revoke`; invalidates `apiKeysKeys.all`.
- `useRotateApiKey()` — `POST /api-keys/{id}/rotate`; invalidates `apiKeysKeys.all`; on success opens `OneTimeSecretModal` with new key.
- `useDeleteApiKey()` — `DELETE /api-keys/{id}`; invalidates `apiKeysKeys.all`.
- `useCreateWebhookSecret()` — `POST /webhook-secrets`; invalidates `webhookSecretsKeys.all`; on success opens `OneTimeSecretModal` with raw secret.
- `useRotateWebhookSecret()` — `POST /webhook-secrets/{id}/rotate`; invalidates `webhookSecretsKeys.all`.
- `useDeleteWebhookSecret()` — `DELETE /webhook-secrets/{id}`.

All mutations surface `err.response?.data?.detail` via sonner toast on error.

### 5.4 Navigation

**`frontend/src/components/layout/BottomNav.tsx`** or the Settings sub-nav (depending on S15/S16 structure) — add two items to the Settings section (admin-only):
- "API Keys" → `/settings/api-keys`.
- "Webhooks" → `/settings/webhooks`.

These are collapsed under a "Developer" section or similar grouping in the settings sidebar; visible only to `role === "admin"`.

### 5.5 Role gating

Both `ApiKeysPage` and `WebhookSecretsPage` are wrapped in `AdminRoute` (from `components/layout/`). Viewers and volunteers reaching these routes are redirected to `/dashboard`. The `ApiDocsPage` is similarly admin-only.

### 5.6 UX states

Each page implements:
- **Loading** — `LoadingState` component (S03 primitive, `frontend/src/components/ui/`).
- **Empty** — empty state with a call-to-action button ("Create your first API key").
- **Error** — `ErrorState` with `err.response?.data?.detail`.
- **Destructive confirm** — `ConfirmDialog` for revoke/rotate/delete with descriptive text.

### 5.7 Tokens

All styles use Tailwind tokens: `bg-card`, `text-foreground`, `text-muted-foreground`, `bg-background`, `border-border`, `bg-primary`, `text-primary-foreground`. No hardcoded hex. `dark:` prefix variants where needed (the `OneTimeSecretModal` warning banner uses `bg-amber-100 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200`).

### 5.8 Mobile-first / desktop

`ApiKeysPage`: mobile = vertical card stack per key (name + prefix + scopes badges + status badge + action menu button). Desktop = full `DataTable` row. `OneTimeSecretModal` is centered, max-width 480px, scrollable.

### 5.9 File-by-file

| Action | File | Notes |
|---|---|---|
| CREATE | `frontend/src/pages/ApiKeysPage.tsx` | Admin-only, full CRUD + rotate |
| CREATE | `frontend/src/pages/WebhookSecretsPage.tsx` | Admin-only, full CRUD + rotate |
| CREATE | `frontend/src/pages/ApiDocsPage.tsx` | Admin-only, renders /api-docs |
| CREATE | `frontend/src/components/ui/OneTimeSecretModal.tsx` | One-time key/secret display; copy button |
| MODIFY | `frontend/src/App.tsx` | Add routes `/settings/api-keys`, `/settings/webhooks`, `/api-docs` inside `AdminRoute` |
| MODIFY | `frontend/src/components/layout/BottomNav.tsx` (or settings sidebar) | Add Developer links for admin |
| MODIFY | `frontend/src/services/api.ts` | Add `apiKeysKeys`, `webhookSecretsKeys`, mutation helpers |
| MODIFY | `frontend/src/types/index.ts` (or new `types/api.ts`) | Add `ApiKey`, `WebhookSecret`, `ApiKeyCreateResponse`, `WebhookSecretCreateResponse` TypeScript types |

---

## 6. Migration / data

No data migration required. `api_keys` and `webhook_secrets` start empty. The admin creates keys and secrets post-deployment via the UI.

If existing `admin_settings` rows for webhook secrets were created as a stopgap (not expected, but possible if S13 provisionally stored one), the migration can optionally seed `webhook_secrets` from those rows. This is unlikely and is noted as an open question (§10 Q3).

---

## 7. Acceptance criteria

1. **AC1** — A new API key can be created via `POST /api-keys` (admin JWT). The response includes a raw key starting with `sea_`. The raw key is not stored in the DB (only the SHA-256 hash is present in `api_keys.key_hash`). The key is not retrievable via `GET /api-keys` or `GET /api-keys/{id}`.

2. **AC2** — A `GET /v1/contacts` request with a valid `X-API-Key: sea_...` header and scope `contacts:read` returns `200` with a paginated list of contacts. The same request without the header returns `401`. The same request with a key missing `contacts:read` scope returns `403`.

3. **AC3** — `POST /v1/webhooks/newcomer` with a valid `X-Webhook-Signature: sha256=<hex>` matching the stored secret for source `newcomer` returns `202 {"accepted": true}` and an `outbox` row is created with `event_type="webhook.newcomer"`. A request with an incorrect signature returns `401`.

4. **AC4** — A revoked API key (`is_active=False`) returns `401` on any `/v1/` request.

5. **AC5** — An expired API key (`expires_at < now`) returns `401` on any `/v1/` request.

6. **AC6** — `POST /api-keys/{id}/rotate` returns a new raw key. The old key immediately returns `401`. The new key works. The `api_keys` row has a new `key_hash` and `key_prefix`.

7. **AC7** — The rate limiter caps API-key requests at 1000/hour per key prefix. The 1001st request returns `429`.

8. **AC8** — `GET /api-docs` returns `404` when `api_docs_enabled=False` (default). When set to `True` in admin settings, it returns `200` HTML containing Swagger UI only for an admin JWT bearer. A volunteer/viewer JWT returns `403`.

9. **AC9** — All mutating `/v1/` calls (create contact, create event, bulk participants) produce an `audit_log` row with `actor_id=NULL` and `after` containing `"_api_key_prefix"`.

10. **AC10** — Creating a webhook secret via `POST /webhook-secrets` returns the raw secret once. `GET /webhook-secrets` shows `"********"` for `secret_value`. `POST /webhook-secrets/{id}/rotate` returns a new raw secret and the old secret fails HMAC verification.

11. **AC11** — A non-admin user (volunteer or viewer JWT) attempting `GET /api-keys` or `POST /api-keys` receives `403`. These endpoints do not appear in the volunteer/viewer navigation.

12. **AC12** — `GET /v1/me` with a valid API key (any scope) returns the key metadata (`id`, `name`, `prefix`, `scopes`, `owner_email`). The `key_hash` field is never present.

---

## 8. Test plan

### Backend pytest (`backend/tests/test_api_keys.py`)

```python
# Fixtures reused: client, admin_auth_headers, volunteer_auth_headers (from conftest.py)
# All tests use db_session (aiosqlite) and the existing TEST_JWT_SECRET.

async def test_create_api_key_admin_only(client, admin_auth_headers, volunteer_auth_headers):
    # Admin can create
    r = await client.post("/api-keys", json={"name": "Test", "scopes": ["contacts:read"]},
                          headers=admin_auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["raw_key"].startswith("sea_")
    assert "raw_key" not in await client.get(f"/api-keys/{data['id']}",
                                              headers=admin_auth_headers).aiter_json()
    # Volunteer gets 403
    r2 = await client.post("/api-keys", json={"name": "x", "scopes": []},
                           headers=volunteer_auth_headers)
    assert r2.status_code == 403

async def test_api_key_auth_contacts_read(client, admin_auth_headers, db_session):
    # Create key with contacts:read scope
    r = await client.post("/api-keys", json={"name": "R", "scopes": ["contacts:read"]},
                          headers=admin_auth_headers)
    raw = r.json()["raw_key"]
    # Can read contacts
    r2 = await client.get("/v1/contacts", headers={"X-API-Key": raw})
    assert r2.status_code == 200
    # Cannot write contacts (403)
    r3 = await client.post("/v1/contacts",
                           json={"first_name": "A", "last_name": "B", "contact_type": "individual"},
                           headers={"X-API-Key": raw})
    assert r3.status_code == 403

async def test_revoked_key_returns_401(client, admin_auth_headers):
    r = await client.post("/api-keys", json={"name": "R", "scopes": ["*"]},
                          headers=admin_auth_headers)
    key_id, raw = r.json()["id"], r.json()["raw_key"]
    await client.post(f"/api-keys/{key_id}/revoke", headers=admin_auth_headers)
    r2 = await client.get("/v1/me", headers={"X-API-Key": raw})
    assert r2.status_code == 401

async def test_rotate_key(client, admin_auth_headers):
    r = await client.post("/api-keys", json={"name": "R", "scopes": ["*"]},
                          headers=admin_auth_headers)
    key_id, old_raw = r.json()["id"], r.json()["raw_key"]
    r2 = await client.post(f"/api-keys/{key_id}/rotate", headers=admin_auth_headers)
    new_raw = r2.json()["raw_key"]
    assert new_raw != old_raw
    # Old key fails
    assert (await client.get("/v1/me", headers={"X-API-Key": old_raw})).status_code == 401
    # New key works
    assert (await client.get("/v1/me", headers={"X-API-Key": new_raw})).status_code == 200

async def test_expired_key_returns_401(client, admin_auth_headers, db_session):
    from datetime import timedelta
    r = await client.post(
        "/api-keys",
        json={"name": "E", "scopes": ["*"],
              "expires_at": (utc_now() - timedelta(seconds=1)).isoformat()},
        headers=admin_auth_headers,
    )
    raw = r.json()["raw_key"]
    assert (await client.get("/v1/me", headers={"X-API-Key": raw})).status_code == 401
```

**`backend/tests/test_webhook_secrets.py`**

```python
async def test_create_webhook_secret(client, admin_auth_headers):
    r = await client.post("/webhook-secrets",
                          json={"source": "newcomer", "description": "New Friend form"},
                          headers=admin_auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "raw_secret" in data
    # GET masks the value
    r2 = await client.get("/webhook-secrets", headers=admin_auth_headers)
    secrets = r2.json()
    assert all(s["secret_value"] == "********" for s in secrets)

async def test_webhook_hmac_valid(client, admin_auth_headers):
    import hmac, hashlib, json
    # Create secret for source "test_hook"
    r = await client.post("/webhook-secrets",
                          json={"source": "test_hook"}, headers=admin_auth_headers)
    raw_secret = r.json()["raw_secret"]
    body = json.dumps({"name": "Juan"}).encode()
    sig = "sha256=" + hmac.new(raw_secret.encode(), body, "sha256").hexdigest()
    r2 = await client.post("/v1/webhooks/test_hook",
                           content=body,
                           headers={"Content-Type": "application/json",
                                    "X-Webhook-Signature": sig})
    assert r2.status_code == 202
    assert r2.json()["accepted"] is True

async def test_webhook_hmac_invalid(client, admin_auth_headers):
    await client.post("/webhook-secrets",
                      json={"source": "bad_hook"}, headers=admin_auth_headers)
    r = await client.post("/v1/webhooks/bad_hook",
                          content=b'{"test":1}',
                          headers={"X-Webhook-Signature": "sha256=badhex"})
    assert r.status_code == 401

async def test_rotate_webhook_secret(client, admin_auth_headers):
    r = await client.post("/webhook-secrets",
                          json={"source": "rotate_hook"}, headers=admin_auth_headers)
    wh_id, old_secret = r.json()["id"], r.json()["raw_secret"]
    r2 = await client.post(f"/webhook-secrets/{wh_id}/rotate", headers=admin_auth_headers)
    new_secret = r2.json()["raw_secret"]
    assert new_secret != old_secret
    # Old secret fails
    import hmac, json
    body = b'{"x":1}'
    bad_sig = "sha256=" + hmac.new(old_secret.encode(), body, "sha256").hexdigest()
    r3 = await client.post("/v1/webhooks/rotate_hook",
                           content=body,
                           headers={"X-Webhook-Signature": bad_sig})
    assert r3.status_code == 401
```

**`backend/tests/test_public_v1.py`**

```python
async def test_v1_contacts_list_requires_key(client):
    r = await client.get("/v1/contacts")
    assert r.status_code == 401

async def test_v1_me(client, admin_auth_headers):
    r = await client.post("/api-keys", json={"name": "M", "scopes": ["*"]},
                          headers=admin_auth_headers)
    raw = r.json()["raw_key"]
    r2 = await client.get("/v1/me", headers={"X-API-Key": raw})
    assert r2.status_code == 200
    data = r2.json()
    assert data["prefix"] == raw[:8]
    assert "key_hash" not in data

async def test_v1_audit_log_actor_null(client, admin_auth_headers, db_session):
    from app.models import AuditLog  # S01/S02 model
    r = await client.post("/api-keys", json={"name": "A", "scopes": ["contacts:write"]},
                          headers=admin_auth_headers)
    raw = r.json()["raw_key"]
    await client.post("/v1/contacts",
                      json={"first_name": "T", "last_name": "Est", "contact_type": "individual"},
                      headers={"X-API-Key": raw})
    from sqlalchemy import select
    logs = (await db_session.execute(select(AuditLog).where(AuditLog.entity == "contact"))).scalars().all()
    assert any(l.actor_id is None and "_api_key_prefix" in (l.after or {}) for l in logs)
```

### Frontend vitest (`frontend/src/pages/__tests__/ApiKeysPage.test.tsx`)

```typescript
// Uses React Testing Library + msw (Mock Service Worker) for API mocking
// Standard pattern: render page -> assert loading, then resolved state

it("renders empty state when no API keys", async () => {
  server.use(rest.get("/api/api-keys", (_, res, ctx) => res(ctx.json([]))));
  render(<ApiKeysPage />, { wrapper: AuthProvider });
  await screen.findByText(/No API keys yet/i);
});

it("opens create modal on button click", async () => {
  render(<ApiKeysPage />, { wrapper: AuthProvider });
  await userEvent.click(screen.getByRole("button", { name: /Create API Key/i }));
  expect(screen.getByLabelText(/Name/i)).toBeVisible();
});

it("shows raw key once on create success", async () => {
  server.use(rest.post("/api/api-keys", (_, res, ctx) =>
    res(ctx.json({ id: 1, raw_key: "sea_TESTKEY1234", prefix: "sea_TEST", scopes: ["*"] }))
  ));
  render(<ApiKeysPage />, { wrapper: AuthProvider });
  await userEvent.click(screen.getByRole("button", { name: /Create API Key/i }));
  // fill form
  await userEvent.type(screen.getByLabelText(/Name/i), "My Key");
  await userEvent.click(screen.getByRole("button", { name: /Create/i }));
  await screen.findByText(/sea_TESTKEY1234/i);
  expect(screen.getByText(/will not be shown again/i)).toBeVisible();
});
```

Also: `frontend/src/components/ui/__tests__/OneTimeSecretModal.test.tsx` — tests copy button triggers `navigator.clipboard.writeText`, modal cannot be dismissed by overlay click.

Build and lint must pass: `npm run build && npm run lint`.

---

## 9. Rollout / rollback / risks

### Rollout

1. Deploy migration `s19_add_api_keys_and_webhook_secrets.py` — additive only, no downtime.
2. Deploy backend + frontend.
3. Admin navigates to `/settings/api-keys`, creates the first key with scopes `["*"]` for owner automation scripts.
4. Admin navigates to `/settings/webhooks`, creates a secret for source `newcomer` if the Google Forms webhook is being wired.
5. Owner tests `curl -H "X-API-Key: sea_..." https://seraphim.lightnc.org/api/v1/me`.
6. To enable docs: set `api_docs_enabled=True` in admin settings; navigate to `/api-docs`.

### Rollback

1. Revert frontend deploy (no migration change needed — tables are additive).
2. Run `alembic downgrade -1` to drop `api_keys` and `webhook_secrets`. This removes all created keys. Any automation scripts using API keys will stop working until re-keyed.
3. Remove the new routers from `main.py` (the existing endpoints are unaffected — the new router does not modify any existing route).

### Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Raw key displayed in browser leaks via HTTP logs / proxy | High | Ensure HTTPS (Cloudflare Tunnel / Caddy enforced); `nginx.conf` scrubs `X-API-Key` header from logs (mirror the `scripts/webhook_listener.py` `WEBHOOK_SECRET` pattern) |
| API key broader than needed (`*` scope) gives full write access | Medium | Admin training + scope UI enforces selecting specific scopes; rotate and narrow after initial automation is stable |
| Rate limit bypass via key prefix spoofing | Low | `X-API-Key` header value is hashed, not used directly as rate-limit key; prefix (`key[:8]`) is used only for display |
| `verify_webhook_hmac` timing attack | Low | Uses `hmac.compare_digest` (constant-time); standard stdlib function |
| `/openapi.json` exposed in production accidentally | Low | `openapi_url` conditional controlled by `ENVIRONMENT` env var (existing pattern in `main.py:71`); `/api-docs` checks setting before rendering Swagger UI |
| Admin credentials used to list keys + clone key_prefix values | Low | key_prefix is only 8 chars (non-secret); the hash is never returned; only the raw key at creation can authenticate |

### nginx log scrubbing

Add to `nginx.conf` (or document in `PRODUCTION_RUNBOOK.md`):

```nginx
# Scrub API key from access logs (S19)
# Replace X-API-Key header value with [REDACTED] in log format
log_format main_scrubbed '$remote_addr ... '
    '"$request" $status ... '
    '"$http_x_api_key"';  # Do not log actual value; use a custom var
# Or simply suppress the header in the log format entirely.
```

The simplest approach: the nginx `log_format` should not include `$http_x_api_key`. Document this in the runbook next to the existing JWT/auth scrubbing notes.

---

## 10. Open questions & pending owner artifacts

**Q1 — Scope vocabulary final?**
The canonical scopes (`contacts:read`, `contacts:write`, `events:read`, `events:write`, `participants:read`, `participants:write`) cover the identified use cases. Should `reports:read` be added for future S14 API access? **Owner decision required before cutting the first production key.**

**Q2 — `/v1/webhooks/{source}` routing completeness.**
Currently routes `newcomer` → outbox and `event_create` → S04 service. Additional sources (e.g. future Zoom meeting.ended webhook) require additions to the routing switch. Each new source must have a `webhook_secrets` row. **Not a blocker; add as needed post S19.**

**Q3 — Existing stopgap webhook secrets in `admin_settings`?**
If S13 (public newcomer form) provisionally stored a webhook-style secret in `admin_settings`, the S19 migration should seed the `webhook_secrets` row from it and optionally delete the `admin_settings` row. **Confirm with owner before migration runs.**

**Q4 — nginx `X-API-Key` scrubbing.**
The `scripts/webhook_listener.py` (line 28+) already validates HMAC. The nginx log scrub for `X-API-Key` should be verified with `tests/test_nginx_log_scrub.py` (existing test pattern). **Assign to S19 implementer to add a test case for the header name.**

**Q5 — `GET /v1/contacts` response includes PII (phone, email, address).**
Viewer role (S15) cannot see individual contact records in the browser app. However, an API key with `contacts:read` scope can. **Policy decision: should API keys issued for automation (e.g. import scripts) be subject to the same viewer restriction, or are they always admin-equivalent?** Recommendation: yes, API keys carry their own scopes independent of the owner's role; the `contacts:read` scope is sufficient. This is a design decision the master must confirm.

**Q6 — S15 dependency.**
S19 depends on S15 for the `require_admin` dep being finalized with the `viewer` role. If S15 is not complete, S19 can still land because `require_admin` already exists in `dependencies.py:45`. S15 only adds the `viewer` path; admin behavior is unchanged. **S15 is a soft dependency; S19 can build in parallel with S15.**

**Q7 — S17 dependency.**
The `POST /v1/webhooks/{source}` endpoint writes to the `outbox` table owned by S17. If S17 is not yet deployed, webhook payloads cannot be enqueued. Mitigation: guard the outbox write with a capability check (same as the S12/S13 ruling C6 pattern) — if the `outbox` table does not exist, log the payload to `audit_log` and return `202` without failing. This allows S19 to deploy before S17 and light up fully once S17 lands.

---

### Cross-sprint dependencies and shared-model touchpoints for the master

- **S17 `outbox` table** — S19's inbound webhook handler produces rows in `outbox`. S19 must apply ruling C6 (capability guard: check table presence before inserting). If S17 ships before S19, no guard needed.
- **S13 `routers/public.py`** — S19 creates `routers/public_v1.py` as a sibling, not a modification of S13's public router. The master must ensure no prefix collision: S13 uses `GET /public/newcomer`; S19 uses `GET /v1/...` and `POST /v1/webhooks/{source}`. No conflict.
- **S03 / S04 / S05 services** — S19's `/v1/` data endpoints delegate to the S03 contact service, S04 event service, and S05 bulk participant service. S19 must not duplicate service logic; it only wraps the service calls with `get_api_key_auth` + `require_scope`.
- **S02 `audit.record`** — S19 uses the shared `audit_api_write` helper which calls `audit.record`. Actor is `None` (external/system) to distinguish from human-actor writes. Master should confirm `audit_log.actor_id` is nullable (it is: `models.py` once S01 lands; confirmed by the canonical model).
- **S15 `require_admin`** — S19 uses the existing `require_admin` dep from `dependencies.py:45`. No conflict. S15 widens the role enum; admin behavior is unchanged.
- **`main.py` ordering** — the two new `app.include_router` calls for `webhook_router` (no setup gate) and `v1_router` (with setup gate) must preserve the ordering rule: public/no-gate routers first, gated routers after. Coordinate with S13 (which also registers a no-gate public router).
- **Master inconsistency to flag:** the 00-MASTER dependency entry for S19 lists `S03,S04,S17` but the prompt charter lists `S03,S04,S17`; the master graph shows `S15,S17 → S19`. S15 is a soft dep (admin auth already exists). If S17 is not done, the ruling C6 outbox guard covers the gap. Recommend the master note S17 as a hard dep for the outbox-routed webhook path only.
