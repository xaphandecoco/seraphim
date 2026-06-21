# S13 — Profiles & Public Newcomer Form
**Phase:** D — Power features · **Depends on:** S02 (Dynamic Custom-Field Engine: `custom_field_group`/`custom_field_def`, `validate_and_coerce`, `GET /custom-fields/schema`), S03 (Native Contact CRUD: `Contact` ORM, `create_contact(...)` service, `ContactDetailPage`, `/contacts` routes, `FormField`/`DataTable`/`Pagination`/`StatusBadge` primitives), S22 (AI Name-Matching: `name_match.match_name()` service, `NameMatchReviewQueue` model) · **Effort:** L · **Status:** Not started

> **Authoritative ground-truth.** Data model uses canonical names from the master prompt. n8n source is `docs/crm-research/n8n/jdVHzcMWXdANwG8R.json` (New Friend V2, 28 nodes, ID `jdVHzcMWXdANwG8R`). All LLM calls replaced by Claude (Anthropic) via S22's `name_match` service — never OpenRouter. Notifications route to Google Chat + Gmail as per decisions Round 8 (not Mattermost). This sprint assumes S01 renames are in effect (`contacts`, `events`, `participants` tables; `Contact` ORM model; app-minted ids).

---

## 1. Goal & rationale

Two tightly-coupled capabilities that share a single JSON form-schema engine:

### 1a. Profiles (form templates)

A `profiles` table stores a **JSON form schema** that drives a generic `<ProfileFormRenderer>` component for **templated contact creation**. Instead of one monolithic contact form, an admin can define reusable templates — "New Friend", "Community Member", "Volunteer" — that declare which **core scalar fields** and which **custom fields** (from S02's `custom_field_def`) appear, in what order, with per-field label overrides, defaults, and logical section groupings. The renderer is shared by:
- The **internal** "New contact from template" launcher on the contacts list (`ContactsPage`).
- The **public** newcomer self-service form (capacity 1b).

This is the CRM equivalent of CiviCRM Profiles. It fixes the gap documented in `docs/crm-research/frontend.md` §D.11: there is currently no shared form component and every form is hand-rolled per page.

### 1b. Public newcomer intake

A single **unauthenticated, anti-spam** endpoint `POST /public/newcomer` that:
- Validates a submission against the **New Friend** profile schema.
- Creates a `Contact(contact_type="Individual", contact_subtype="New Friend")`.
- Resolves the free-text **Invited By** and **Consolidated By** names through S22's `name_match.match_name()` pipeline and writes the result into `custom_data` (contact-reference fields); unresolved names go to `name_match_review_queue`.
- Classifies the **Prayer Request** text (valid vs. invalid) via Claude before emailing it.
- Enqueues a Google Chat notification + two Gmail emails through the `outbox` table (S17 pattern, wired here directly rather than waiting for S17's full rules engine).
- Returns a user-friendly `201` / `202`.

This **replaces the n8n workflow `jdVHzcMWXdANwG8R` (New Friend V2, 28 nodes)** end-to-end. The current flow: Google Sheets trigger → `Edit Fields` → `Contact Generator` (CiviCRM `Contact.create`) → `Birthday Updater` → `Note Generator` → "Get Invited By" AI agent (CiviCRM search + `custom_26`) → "Get Consolidated By" AI agent (`custom_63`) → Mattermost + Gmail.

The rebuilt flow is fully in-process: public POST → service layer → `contacts` table → `name_match` service → `outbox` rows → background drain (Google Chat webhook + Gmail SMTP/API). No Google Sheets, no CiviCRM, no n8n, no Mattermost.

---

## 2. Scope

### In scope

**Backend**
- New `profiles` table + Alembic migration.
- `Profile` ORM model and full Pydantic schema set.
- Admin CRUD endpoints for profiles (`GET/POST/PUT/DELETE /profiles`, admin-gated).
- Public read of a single published profile: `GET /public/newcomer/profile` (unauthenticated, returns schema only — no contact data).
- Internal profile render: `POST /profiles/{id}/render` (authenticated; returns resolved schema to drive the "new from template" form).
- **Public newcomer intake** `POST /public/newcomer` — rate-limited, honeypot-guarded, idempotent within a short window.
- New **`backend/app/routers/public.py`** router registered directly in `main.py` like `setup.router` and `auth.router` (before `check_setup_complete`).
- Three seed presets: **New Friend**, **Community Member**, **Volunteer** — inserted idempotently via a data migration or Alembic `op.bulk_insert`.
- **Profile JSON-schema contract** documented inline (exact `fields[]` shape, `settings` shape).
- Outbox rows for Google Chat + Gmail notifications written in the same DB transaction as contact creation; relies on the `outbox` table contract (canonical model; rows drained by S17's background worker — S13 only writes the rows and ships a minimal stub drain function that S17 replaces with the full worker).
- Prayer request classification via `anthropic` SDK (`claude-sonnet-4-5` model; classify to `valid|invalid` before emailing).
- Audit log row on every public newcomer creation (`audit_log` table, `action="contact.create"`, `entity="contact"`).
- Pydantic schemas in `backend/app/schemas.py`.
- Tests: `backend/tests/test_profiles.py` + `backend/tests/test_public_newcomer.py`.

**Frontend**
- New **`ProfilesPage`** (`/profiles`) — admin-only list of defined profiles + actions.
- New **`ProfileFormPage`** (`/profiles/new` and `/profiles/:id/edit`) — admin JSON-schema builder.
- Reusable **`<ProfileFormRenderer>`** component — driven entirely by a profile's `fields[]` schema; renders core and custom fields; handles validation; consumed by both internal new-contact flow and the public welcome page.
- **"New from template" launcher** on `ContactsPage` — a dropdown/button that lists active profiles; clicking one opens an inline form pre-populated from that profile schema.
- New **public page** `/welcome` — no auth, no `BottomNav`, no `ProtectedRoute`; renders the New Friend profile form; calls `POST /public/newcomer`; shows a success/error state.
- Tests: frontend vitest for `<ProfileFormRenderer>` rendering + validation + public page happy-path.

### Out of scope (explicit)

- **Full visual automation rules engine** (triggers/conditions/actions/delays UI) → S17. S13 writes outbox rows directly in service code; S17's worker drains them.
- **Google Chat credentials UI** in Settings → S18. S13 reads the webhook URL from `admin_settings` key `google_chat_webhook_url` (settable manually or by S18's settings panel).
- **Gmail SMTP/API credentials UI** → S18. S13 reads `gmail_sender_email` / `gmail_app_password` or `gmail_oauth_token` from `admin_settings`.
- **Community-report public submission** → S22 (community reports require auth in v1).
- **Repeating / multi-record field sets** in profiles → explicitly not supported per decisions Round 3.
- **Full outbox drain worker implementation** → S17. S13 ships a `_drain_outbox_stub()` that it calls inline (synchronous best-effort) and marks the row `sent` or `failed`; S17 replaces this with the full at-least-once async worker.
- **Profile versioning / history** → deferred.
- **Profile assignment to a group or segment** → S09/S17 scope.
- **CAPTCHA integration** (reCAPTCHA / hCaptcha) → S19 (API keys). S13 ships the honeypot + rate-limit; the CAPTCHA hook point is left in the code as a TODO stub.
- **`viewer` role** blocking → S15. S13 endpoints use `require_admin` (profiles CRUD) and `require_volunteer` (internal render); `/public/*` is unauthenticated.

---

## 3. Data model changes

### 3.1 New table: `profiles`

```sql
CREATE TABLE profiles (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    entity      VARCHAR(30)  NOT NULL DEFAULT 'contact',  -- 'contact' only in v1
    fields      JSONB        NOT NULL DEFAULT '[]',
    settings    JSONB        NOT NULL DEFAULT '{}',
    is_public   BOOLEAN      NOT NULL DEFAULT FALSE,
    owner_id    INTEGER      REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_profiles_name ON profiles(name);
```

**ORM model** in `backend/app/models.py`:

```python
class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    entity: Mapped[str] = mapped_column(String(30), nullable=False, default="contact")
    fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
```

Use the module-level `JSONB` alias (`JSON().with_variant(_PG_JSONB, "postgresql")`) already defined at `models.py:22`.

### 3.2 `fields` JSONB schema contract

`fields` is a JSON array of **field descriptor objects**. Each object:

```json
{
  "id": "string — unique within this profile; 'core:first_name' or 'custom:{field_name}'",
  "field_type": "'core' | 'custom'",
  "core_field": "string | null — e.g. 'first_name', 'last_name', 'phone', 'email', 'gender', 'birth_date', 'street_address', 'contact_subtype'",
  "custom_field_name": "string | null — matches custom_field_def.name (S02)",
  "label_override": "string | null — if null, use the field's canonical label",
  "placeholder": "string | null",
  "default_value": "any | null",
  "is_required": "boolean — overrides the custom_field_def.is_required for this profile",
  "weight": "integer — display order",
  "section": "string | null — logical section heading e.g. 'Personal Info', 'Visit Info', 'Connections'"
}
```

`settings` JSONB: top-level profile metadata controlling the renderer:

```json
{
  "contact_subtype_default": "string | null — e.g. 'New Friend'; auto-applied on submission",
  "submit_label": "string — button text e.g. 'Register as New Friend'",
  "success_message": "string — shown after public submit",
  "redirect_after_submit": "string | null — URL for public form redirect",
  "notify_google_chat": "boolean — default true for is_public profiles",
  "notify_gmail": "boolean — default true for is_public profiles",
  "prayer_request_field": "string | null — core_field or custom_field_name that contains the prayer request text; triggers prayer email if valid",
  "invited_by_field": "string | null — custom_field_name to run S22 name-match for Invited By",
  "consolidated_by_field": "string | null — custom_field_name to run S22 name-match for Consolidated By"
}
```

### 3.3 Alembic plan

**New migration file**: `backend/alembic/versions/<rev>_s13_profiles_table.py`

```
def upgrade():
    op.create_table('profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('entity', sa.String(30), nullable=False, server_default='contact'),
        sa.Column('fields', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('is_public', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('owner_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_profiles_name', 'profiles', ['name'], unique=True)

    # Seed three presets (idempotent: skip if name already exists)
    profiles_table = sa.table('profiles',
        sa.column('name', sa.String),
        sa.column('entity', sa.String),
        sa.column('fields', postgresql.JSONB),
        sa.column('settings', postgresql.JSONB),
        sa.column('is_public', sa.Boolean),
        sa.column('created_at', sa.DateTime),
        sa.column('updated_at', sa.DateTime),
    )
    conn = op.get_bind()
    existing = {r[0] for r in conn.execute(sa.text("SELECT name FROM profiles")).fetchall()}
    to_insert = [p for p in _SEED_PROFILES if p['name'] not in existing]
    if to_insert:
        op.bulk_insert(profiles_table, to_insert)

def downgrade():
    op.drop_index('uq_profiles_name', table_name='profiles')
    op.drop_table('profiles')
```

`_SEED_PROFILES` is a module-level list of three dicts defined in the migration file (not imported from app code to keep migrations self-contained):

**New Friend preset** — maps exactly to n8n `jdVHzcMWXdANwG8R.json` `Edit Fields` node output:
- Core fields: `first_name` (required), `last_name` (required), `phone`, `gender`, `birth_date`, `street_address`
- Custom fields (by `name`): `facebook_name`, `new_friend_add_date` (Date Invited), `service_time`, `invited_by` (contact_reference), `consolidated_by` (contact_reference), `prayer_request` (textarea), `season` (text — "What best describes your season?")
- `settings.contact_subtype_default = "New Friend"`, `settings.is_public_flag = true` (profile `is_public = true`), `settings.invited_by_field = "invited_by"`, `settings.consolidated_by_field = "consolidated_by"`, `settings.prayer_request_field = "prayer_request"`, `settings.notify_google_chat = true`, `settings.notify_gmail = true`

**Community Member preset**:
- Core: `first_name`, `last_name`, `phone`, `email`, `gender`, `birth_date`, `street_address`
- Custom: `community_zone`, `community_leader`, `ministry` (multiselect), `pepsol_level`
- `settings.contact_subtype_default = "Community Member"`

**Volunteer preset**:
- Core: `first_name`, `last_name`, `email`, `phone`
- Custom: `ministry` (multiselect), `volunteer_role`
- `settings.contact_subtype_default = "Volunteer"`

> The exact `custom_field_name` values (`facebook_name`, `new_friend_add_date`, `invited_by`, etc.) must match what S02's custom-field seeding creates. The master plan must confirm these names. See §10 Q1.
>
> **Ruling CN-14:** S13's newcomer-form (and all profile-seed) field names **must EXACTLY match S02's seeded `custom_field_def.name` values**. **S02 seeds first; S13 consumes.** S13 **must NOT** define its own field names independently — if a name does not yet exist in S02's seed, S02 is the place to add it, not S13. The owner's CiviCRM custom-field export is the final arbiter; until it arrives, both sprints use the agreed snake_case names listed here and in the master §2.2.

### 3.4 `outbox` table dependency

S13 writes rows to `outbox` (canonical model; the table is created by an earlier sprint or S17). S13 must guard against the table not yet existing during migration. **If `outbox` is not yet created when S13's migration runs, S13's migration creates it** with a minimal schema that S17 extends:

```
outbox(id SERIAL PK, event_type VARCHAR(50), payload JSONB, status VARCHAR(20) DEFAULT 'pending',
       attempts INTEGER DEFAULT 0, last_error TEXT, run_at TIMESTAMP, created_at TIMESTAMP DEFAULT NOW())
```

Migration uses `inspector.has_table('outbox')` to skip creation if it already exists (idempotent).

### 3.5 Downgrade

Drop `profiles` table + index. Remove any `outbox` rows created by S13's seed (not practical to identify; document to truncate manually if rolling back).

---

## 4. Backend

### 4.1 Endpoints

| Method | Path | Role | Request body | Response | Notes |
|---|---|---|---|---|---|
| `GET` | `/profiles` | volunteer | `?entity=contact&is_public=&page=1&page_size=20` | `PaginatedProfileResponse` | Lists all profiles; filtered by entity/is_public |
| `POST` | `/profiles` | admin | `ProfileCreate` | `ProfileResponse` | Creates profile; validates `fields[]` schema |
| `GET` | `/profiles/{id}` | volunteer | — | `ProfileResponse` | |
| `PUT` | `/profiles/{id}` | admin | `ProfileUpdate` | `ProfileResponse` | Full replace of `fields` + `settings` |
| `DELETE` | `/profiles/{id}` | admin | — | `204` | Hard delete; fail 409 if it is the only `is_public` profile |
| `POST` | `/profiles/{id}/render` | volunteer | `{prefill: dict}` optional | `ProfileRenderResponse` | Resolves schema (merges core field metadata + custom_field_def from S02) ready to drive a form; server-side only, not public |
| `GET` | `/public/newcomer/profile` | public | — | `PublicProfileSchema` | Returns the `is_public=true` profile's resolved `fields[]` + `settings`; no auth; registers without `check_setup_complete` |
| `POST` | `/public/newcomer` | public | `NewcomerSubmission` | `NewcomerResult` | Rate-limited; honeypot; idempotent; creates contact; enqueues notifications |

### 4.2 Pydantic schemas (add to `backend/app/schemas.py`)

```python
class ProfileFieldDescriptor(BaseModel):
    id: str
    field_type: Literal["core", "custom"]
    core_field: Optional[str] = None
    custom_field_name: Optional[str] = None
    label_override: Optional[str] = None
    placeholder: Optional[str] = None
    default_value: Optional[Any] = None
    is_required: bool = False
    weight: int = 0
    section: Optional[str] = None

class ProfileSettings(BaseModel):
    contact_subtype_default: Optional[str] = None
    submit_label: str = "Submit"
    success_message: str = "Thank you! Your information has been recorded."
    redirect_after_submit: Optional[str] = None
    notify_google_chat: bool = True
    notify_gmail: bool = True
    prayer_request_field: Optional[str] = None
    invited_by_field: Optional[str] = None
    consolidated_by_field: Optional[str] = None

class ProfileCreate(BaseModel):
    name: str
    entity: str = "contact"
    fields: list[ProfileFieldDescriptor] = []
    settings: ProfileSettings = ProfileSettings()
    is_public: bool = False

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    fields: Optional[list[ProfileFieldDescriptor]] = None
    settings: Optional[ProfileSettings] = None
    is_public: Optional[bool] = None

class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    entity: str
    fields: list[ProfileFieldDescriptor]
    settings: ProfileSettings
    is_public: bool
    owner_id: Optional[int]
    created_at: datetime
    updated_at: datetime

class PaginatedProfileResponse(BaseModel):
    items: list[ProfileResponse]
    total: int
    page: int
    page_size: int

class ProfileRenderResponse(BaseModel):
    profile_id: int
    name: str
    fields: list[dict]   # resolved: core fields merged with custom_field_def metadata
    settings: ProfileSettings

class PublicProfileSchema(BaseModel):
    name: str
    settings: ProfileSettings
    fields: list[dict]   # same resolved shape as ProfileRenderResponse.fields

# --- Newcomer Intake ---

class NewcomerSubmission(BaseModel):
    # Honeypot: must be empty or absent
    website: Optional[str] = None  # honeypot field; reject if non-empty

    # Required core fields
    first_name: str
    last_name: str

    # Optional core fields (matching n8n Edit Fields node)
    phone: Optional[str] = None
    gender: Optional[str] = None           # "Male" | "Female" | other
    birth_date: Optional[str] = None       # YYYY-MM-DD; n8n normalizes M/D/YYYY → YYYY-MM-DD
    street_address: Optional[str] = None

    # Custom field values (all optional; stored in contacts.custom_data)
    facebook_name: Optional[str] = None
    new_friend_add_date: Optional[str] = None   # YYYY-MM-DD "Date Invited"
    service_time: Optional[str] = None           # e.g. "Second Service (10AM)"
    invited_by: Optional[str] = None             # free-text name → S22 resolution
    consolidated_by: Optional[str] = None        # free-text name → S22 resolution
    prayer_request: Optional[str] = None
    season: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

class NewcomerResult(BaseModel):
    contact_id: int
    status: Literal["created", "duplicate"]
    message: str
    invited_by_resolved: Optional[bool] = None    # True=matched, False=queued
    consolidated_by_resolved: Optional[bool] = None
```

### 4.3 New files

#### `backend/app/routers/public.py` (new)

```python
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.rate_limit import limiter
from app.schemas import NewcomerSubmission, NewcomerResult, PublicProfileSchema
from app.services.profile_service import (
    get_public_profile_schema,
    process_newcomer_submission,
)

router = APIRouter(prefix="/public", tags=["public"])

@router.get("/newcomer/profile", response_model=PublicProfileSchema)
async def get_newcomer_profile(db: AsyncSession = Depends(get_db)):
    """Return the public newcomer form schema (no auth)."""
    schema = await get_public_profile_schema(db)
    if schema is None:
        raise HTTPException(status_code=404, detail="No public profile configured")
    return schema

@router.post("/newcomer", response_model=NewcomerResult, status_code=201)
@limiter.limit("5/minute;20/hour")   # 5 submissions/IP/minute, 20/hour
async def submit_newcomer(
    request: Request,
    submission: NewcomerSubmission,
    db: AsyncSession = Depends(get_db),
):
    """Public newcomer intake. Rate-limited, honeypot-protected."""
    # Honeypot check (reject bots that fill the hidden 'website' field)
    if submission.website:
        # Return 201 to fool bots but do nothing
        return NewcomerResult(contact_id=0, status="created",
                              message="Thank you for your submission.")
    return await process_newcomer_submission(submission, db)
```

#### `backend/app/services/profile_service.py` (new)

Business logic only; no router imports. Public API:

```python
async def get_public_profile_schema(db: AsyncSession) -> Optional[PublicProfileSchema]: ...
async def render_profile(profile_id: int, prefill: dict, db: AsyncSession) -> ProfileRenderResponse: ...
async def process_newcomer_submission(
    submission: NewcomerSubmission,
    db: AsyncSession,
) -> NewcomerResult: ...
async def _classify_prayer_request(text: str) -> Literal["valid", "invalid"]: ...
async def _enqueue_notification(
    event_type: str,
    payload: dict,
    db: AsyncSession,
    run_at: Optional[datetime] = None,
) -> None: ...
async def _drain_outbox_stub(db: AsyncSession) -> None: ...
```

**`process_newcomer_submission` algorithm** (replacing n8n workflow logic):

1. **Honeypot** already checked in router; service skips if `submission.website` is set.

2. **Idempotency window (duplicate suppression)**: query `contacts` for an existing non-deleted contact matching `(first_name ILIKE, last_name ILIKE)` AND (`phone = submission.phone` OR `email = submission.email`) created within the last 5 minutes. If found, return `NewcomerResult(contact_id=existing.id, status="duplicate", ...)` without creating another row. This prevents double-submits without exposing a persistent unique constraint (newcomers may share names).

3. **Build `custom_data`**: start with `{}`. Set `service_time`, `new_friend_add_date`, `facebook_name`, `season`, `prayer_request` directly. Leave `invited_by` and `consolidated_by` as `None` initially — they are filled in step 5.

4. **Set `contact_subtype`**: read from the public profile's `settings.contact_subtype_default` (should be `"New Friend"`). Fall back to `"New Friend"` if unset.

5. **Create the Contact** via S03's `create_contact(...)` service:
   ```python
   contact = await create_contact(
       db=db,
       first_name=submission.first_name.strip(),
       last_name=submission.last_name.strip(),
       phone=submission.phone,
       gender=submission.gender,
       birth_date=_parse_date(submission.birth_date),
       street_address=submission.street_address,
       contact_type="Individual",
       contact_subtype=subtype,
       custom_data=custom_data,
   )
   ```
   If S03's `create_contact` writes an `audit_log` row automatically, no additional call is needed; otherwise emit `audit_log(action="contact.create", entity="contact", entity_id=contact.id, after={...})` here.

6. **Name resolution — Invited By**: if `submission.invited_by` is non-empty:
   - Call `await name_match.match_name(raw_name=submission.invited_by, db=db, source="newcomer", event_id=None)`.
   - The service returns `MatchResult(contact_id: Optional[int], status: "matched"|"queued"|"unmatched")`.
   - If `status == "matched"`: set `contact.custom_data["invited_by"] = result.contact_id`; write `await db.commit()`.
   - If `status == "queued"`: a `name_match_review_queue` row already exists (created by S22); `invited_by_resolved = False`.
   - Update the outbox notification payload with the resolution result.

7. **Name resolution — Consolidated By**: same as step 6 with `consolidated_by` field.
   > The n8n workflow runs these serially (Get Invited By → Get Consolidated By). The rebuild can run them concurrently: `asyncio.gather(match_invited_by(...), match_consolidated_by(...))`.

8. **Prayer request classification** (optional, non-blocking): if `submission.prayer_request` is non-empty:
   - Call `await _classify_prayer_request(submission.prayer_request)`.
   - Internally: `anthropic.AsyncAnthropic().messages.create(model="claude-sonnet-4-5", max_tokens=10, ...)` with a system prompt: `"You are a church prayer team assistant. Classify the following as 'valid' (a genuine prayer request like health, family, guidance) or 'invalid' (not a prayer request: N/A, none, good luck, etc.). Reply with exactly one word: valid or invalid."`.
   - On `valid`: enqueue a prayer-request Gmail notification (step 9b).
   - On `invalid` or on any Claude error (catch `anthropic.APIError`): skip the prayer email (fail open, non-blocking).
   - **Important**: use the `anthropic` SDK with `async` client. The `ANTHROPIC_API_KEY` is read from `admin_settings` key `anthropic_api_key` via `dynamic_settings`. If unset, log a warning and skip classification (degrade gracefully).

9. **Enqueue notifications** — in the **same transaction** as contact creation (two rows into `outbox`):

   9a. **Google Chat notification** (`notify_google_chat = true` in profile settings):
   ```python
   await _enqueue_notification(
       event_type="google_chat.new_friend",
       payload={
           "webhook_key": "google_chat_webhook_url",   # admin_settings key
           "text": f"*New Friend: {contact.first_name} {contact.last_name}*\n"
                   f"Invited by: {submission.invited_by or 'N/A'}\n"
                   f"Consolidated by: {submission.consolidated_by or 'N/A'}\n"
                   f"Service: {submission.service_time or 'N/A'}",
       },
       db=db,
   )
   ```
   Format mirrors n8n `Post a message6` + `Post a message7` (merged into one Google Chat message since Google Chat has no "reply-in-thread" concept in webhook mode).

   9b. **Gmail — New Friend Report** (`notify_gmail = true`):
   ```python
   await _enqueue_notification(
       event_type="gmail.new_friend_report",
       payload={
           "to": "attendance.monitoring@lightnc.org",
           "sender_name": "Seraphim CRM",
           "subject": f"New Friend Report: {contact.first_name} {contact.last_name}",
           "template": "new_friend_report",
           "context": { ...submission dict + contact_id + resolution status... },
       },
       db=db,
   )
   ```

   9c. **Gmail — Prayer Request** (only if prayer_request is `valid`):
   ```python
   await _enqueue_notification(
       event_type="gmail.prayer_request",
       payload={
           "to": "attendance.monitoring@lightnc.org",
           "subject": f"New Friend Prayer Request: {contact.first_name} {contact.last_name}",
           "template": "prayer_request_report",
           "context": { ...prayer_request, contact name, service_time... },
       },
       db=db,
   )
   ```
   > **Email addresses** (`attendance.monitoring@lightnc.org`) are from the n8n workflow JSON and must be configurable: store in `admin_settings` key `newcomer_notify_email` (default `attendance.monitoring@lightnc.org`). Prayer request email address is stored in `admin_settings` key `prayer_notify_email`.

   > **Post-S17 follow-up (per CN-10):** the raw outbox INSERT performed by `_enqueue_notification` (steps 9a–9c) is a **pre-S17 stub**. Once S17 lands its canonical outbox + rules engine, this raw INSERT MUST be patched to call `automation_engine.emit()` instead of writing the row directly. Tracked as an S17-landing follow-up; the `has_table('outbox')` migration guard (§3.4) likewise becomes a permanent no-op after S17.

10. **Commit** the full transaction (contact row + outbox rows).

11. **Best-effort stub drain**: call `await _drain_outbox_stub(db)` after commit. This stub immediately attempts to deliver pending `google_chat.new_friend` and `gmail.*` rows synchronously (one HTTP call each with a 5-second timeout via `httpx.AsyncClient`). On success, marks `status="sent"`; on any error marks `status="failed"`. This provides "best-effort" delivery on the request path without requiring S17's full background worker. S17 replaces this with a proper retry loop and moves delivery fully off the request path.

12. **Return** `NewcomerResult(contact_id=contact.id, status="created", message=settings.success_message, invited_by_resolved=..., consolidated_by_resolved=...)`.

**`_drain_outbox_stub` implementation sketch**:
```python
async def _drain_outbox_stub(db: AsyncSession) -> None:
    """Best-effort synchronous drain of pending outbox rows for this request.
    S17 replaces this with a proper background worker."""
    from sqlalchemy import select, update
    from app.config import dynamic_settings
    import httpx, logging
    log = logging.getLogger(__name__)

    result = await db.execute(
        select(Outbox).where(Outbox.status == "pending")
                      .where(Outbox.attempts < 3)
                      .order_by(Outbox.created_at)
                      .limit(10)
    )
    rows = result.scalars().all()
    async with httpx.AsyncClient(timeout=5.0) as client:
        for row in rows:
            try:
                if row.event_type.startswith("google_chat."):
                    webhook_url = dynamic_settings.get("google_chat_webhook_url")
                    if not webhook_url:
                        continue
                    r = await client.post(webhook_url, json={"text": row.payload.get("text", "")})
                    r.raise_for_status()
                elif row.event_type.startswith("gmail."):
                    # Placeholder: full impl in S18
                    log.info("gmail stub: would send %s to %s", row.event_type, row.payload.get("to"))
                    # Mark sent so we don't retry endlessly before S18 lands
                row.status = "sent"
                row.attempts += 1
            except Exception as exc:
                row.status = "failed"
                row.attempts += 1
                row.last_error = str(exc)[:500]
    await db.commit()
```

#### `backend/app/routers/profiles.py` (new)

Admin CRUD for profiles + internal render endpoint. Calls `profile_service.*`. Full detail:

```
GET  /profiles             → list_profiles(entity, is_public, page, page_size, db, user=require_volunteer)
POST /profiles             → create_profile(body: ProfileCreate, db, user=require_admin)
GET  /profiles/{id}        → get_profile(id, db, user=require_volunteer)
PUT  /profiles/{id}        → update_profile(id, body: ProfileUpdate, db, user=require_admin)
DELETE /profiles/{id}      → delete_profile(id, db, user=require_admin)
POST /profiles/{id}/render → render_profile(id, body: {prefill: dict}, db, user=require_volunteer)
```

`render_profile` service logic:
- Load `Profile` by id; 404 if not found.
- For each `field` in `profile.fields` (sorted by `weight`):
  - If `field_type == "core"`: emit the core field metadata dict (data_type, label, placeholder, is_required).
  - If `field_type == "custom"`: query `custom_field_def` by `name = field.custom_field_name`; merge label/options/data_type from S02. Apply `label_override` if set.
- Return `ProfileRenderResponse` with resolved `fields[]`.

**Validation on `ProfileCreate` / `ProfileUpdate`**:
- All `custom_field_name` values in `fields[]` must exist in `custom_field_def` for entity `"contact"`. If not, return `422` with a clear error naming the missing field.
- At most one profile may have `is_public=True` at any time. Enforce with a pre-insert/pre-update query (not a DB unique index, since the business rule is "exactly one public profile, not zero"). On conflict: 409 with `"Another profile is already public. Set is_public=false on that profile first."`.

### 4.4 Registration in `main.py`

```python
# After the existing: app.include_router(auth.router)
from app.routers import public as public_router, profiles as profiles_router
app.include_router(public_router.router)   # no check_setup_complete, no auth
# Profiles: behind setup complete, authenticated
app.include_router(profiles_router.router, dependencies=[Depends(check_setup_complete)])
```

### 4.5 `outbox` ORM model (if not yet defined by S17)

Add `Outbox` class to `backend/app/models.py` if absent:

```python
class Outbox(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
```

S17 extends this model with additional indexes and the full drain worker; S13 only needs basic insert + read by status.

### 4.6 CORS / rate-limit configuration for the public endpoint

The `/public/*` router is already covered by the app-level `CORSMiddleware` (allows `FRONTEND_URL`). For the **public newcomer form** served from a potentially different origin (if hosted separately), add an `admin_settings` key `newcomer_allowed_origins` (JSON array). The public router reads this at request-time and adds the response header:

```python
allowed_origins = dynamic_settings.get_json("newcomer_allowed_origins", ["*"])
# Do NOT use "*" in production when allow_credentials is True; document this.
```

Rate limit `5/minute;20/hour` per IP is set via the `@limiter.limit(...)` decorator on `submit_newcomer`. The existing `limiter` from `app.rate_limit` (slowapi, Redis-backed in prod, in-memory in tests) is used directly.

### 4.7 `requirements.txt` additions

- `anthropic>=0.40.0` — verify not already present; S22 also adds it.
- `httpx>=0.27.0` — for the outbox stub drain; may already be a transitive dep; verify.

---

## 5. Frontend

### 5.1 New pages and routes

| Route | Component | Guard | Purpose |
|---|---|---|---|
| `/profiles` | `ProfilesPage` | `AdminRoute` | List all profiles; link to edit; toggle is_public |
| `/profiles/new` | `ProfileFormPage` | `AdminRoute` | Create new profile |
| `/profiles/:id/edit` | `ProfileFormPage` | `AdminRoute` | Edit existing profile |
| `/welcome` | `WelcomePage` | **public** (no `ProtectedRoute`) | Public newcomer form |

`/welcome` must be added to `App.tsx` **outside** any `<ProtectedRoute>` or `<AdminRoute>` wrapper and must have **no `BottomNav`**. It is served by the same Vite/nginx setup; Cloudflare Tunnel exposes it at the same domain as the rest of the app.

### 5.2 Component tree

```
frontend/src/
  pages/
    ProfilesPage.tsx          (new)
    ProfileFormPage.tsx        (new)
    WelcomePage.tsx            (new — public)
  components/
    profiles/
      ProfileFormRenderer.tsx  (new — reusable renderer)
      ProfileFieldEditor.tsx   (new — drag-to-reorder field list in admin builder)
      SectionRenderer.tsx      (new — renders one section group of fields)
```

### 5.3 TanStack Query keys

```typescript
// Admin profiles list
['profiles', { entity, is_public, page, page_size }]

// Single profile
['profiles', id]

// Rendered schema for internal form
['profiles', id, 'render', prefill]

// Public newcomer schema (no auth)
['public-newcomer-profile']
```

Mutations invalidate `['profiles']` on create/update/delete.

### 5.4 `ProfileFormRenderer` component

```typescript
interface ProfileFormRendererProps {
  schema: ProfileRenderResponse | PublicProfileSchema;
  prefill?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
  submitLabel?: string;
  isPublic?: boolean;  // if true: renders the honeypot field; hides admin-only hints
}
```

**Rendering logic**:
1. Group `fields[]` by `section` (using `field.section` or `"General"` if null), sort each group by `weight`.
2. For each field, render the appropriate `<FormField>` variant (from S03's `components/ui/FormField.tsx`):
   - `core:first_name`, `core:last_name`, `core:phone`, `core:street_address`, `core:facebook_name` → `<input type="text">`
   - `core:gender` → `<select>` with `Male/Female/Other`
   - `core:birth_date`, `custom:date` → `<input type="date">`
   - `custom:textarea` → `<textarea>`
   - `custom:select` → `<select>` from `field.options`
   - `custom:multiselect` → checkbox group or multi-select
   - `custom:contact_reference` (invited_by, consolidated_by) → free-text `<input>` (resolution happens server-side; do NOT render a contact picker here — the public user types a name)
   - `custom:checkbox` → `<input type="checkbox">`
   - `custom:number` → `<input type="number">`
3. **Honeypot field** (public only): render `<input name="website" type="text" style="display:none" tabIndex=-1 autoComplete="off" />` — `aria-hidden` + visually hidden via CSS. The field is included in the submission payload; server rejects non-empty values.
4. **Validation**: required fields show inline error on blur + on submit attempt. Uses controlled `useState` per field.
5. **Submit state**: button disabled + spinner while `isSubmitting`.
6. **Error display**: `toast.error(err.response?.data?.detail || "Submission failed")` on API error; on `status="duplicate"` show a non-error informational toast.

### 5.5 `ProfileFormPage` — admin builder

Two-panel layout (desktop) / stacked (mobile):
- **Left panel**: preview pane showing `<ProfileFormRenderer>` live-updating as the schema is edited.
- **Right panel**: field list with drag-to-reorder (`ProfileFieldEditor`); "Add field" dropdown listing available core fields + active `custom_field_def` rows (fetched from `GET /custom-fields/schema?entity=contact` — S02 endpoint); per-field: label override, required toggle, section assignment.

The **builder form** itself uses `<FormField>` for the profile `name`, `entity`, `is_public` toggle, and `settings.*` properties.

On save, calls `POST /profiles` or `PUT /profiles/:id`. On success: `toast.success("Profile saved")` + navigate to `/profiles`.

### 5.6 `ProfilesPage` — list

Uses `DataTable` (S03 primitive) with columns: Name, Entity, Public (badge), Actions (Edit | Delete). Delete uses `<ConfirmDialog>`. Toggle `is_public` inline with a switch (calls `PUT /profiles/:id` with `{is_public: !current}`). Shows a warning chip if no profile is `is_public` (newcomer form is unreachable).

### 5.7 `WelcomePage` — public

Route: `/welcome`. Layout: centered card (max-w-lg), full-height background using `bg-background`. No `BottomNav`, no header links. Shows the church name/logo (from `admin_settings.church_name` / `admin_settings.church_logo_url`).

Flow:
1. Fetch `GET /public/newcomer/profile` → `useQuery(['public-newcomer-profile'], ...)`. Show `<LoadingState>` and `<ErrorState>` (from `components/ui/StateViews.tsx`).
2. Render `<ProfileFormRenderer schema={data} isPublic={true} onSubmit={handleSubmit} submitLabel={data.settings.submit_label} />`.
3. `handleSubmit`: `api.post('/public/newcomer', payload)` — uses the same `api` axios instance but without the Bearer token interceptor (which won't add a token since no user is logged in). On success: show `data.settings.success_message` in a full-page success card; if `settings.redirect_after_submit` is set, redirect to that URL after 3 seconds.
4. **Error**: `toast.error(err.response?.data?.detail || "Submission failed")`.
5. **Rate-limit 429**: display "Too many submissions. Please try again in a minute." (do not expose rate limit details).

**Mobile-first**: single-column form; `FormField` stacks label above input. Touch targets minimum 44px height.

### 5.8 "New from template" launcher on `ContactsPage`

Add a split button or dropdown to the `ContactsPage` header (S03):
```
[ + New Contact ▾ ]  → dropdown: [Blank] [New Friend] [Community Member] [Volunteer]
```
On selecting a template:
- Fetch `POST /profiles/:id/render` → get resolved schema.
- Open a slide-over drawer (or navigate to `/contacts/new?profile=:id`) rendering `<ProfileFormRenderer>` driven by the rendered schema.
- On submit: call `POST /members` (S03 endpoint) with `{ ...core_fields, custom_data: {...} }` derived from the form values.

> Slide-over vs. navigate is a UX choice; the spec recommends a **navigation approach** (`/contacts/new?profile=:id`) for simplicity and deep-link support. The `ContactFormPage` (S03) can accept a `profile` query param and pre-fetch the rendered schema.

### 5.9 Role gating

- `/profiles`, `/profiles/new`, `/profiles/:id/edit` → `AdminRoute`.
- `/welcome` → no guard (public).
- "New from template" launcher → visible to `volunteer` and `admin`; the dropdown is always available to anyone who can create contacts.

### 5.10 `frontend/src/App.tsx` changes

```typescript
// Add imports
import ProfilesPage from './pages/ProfilesPage'
import ProfileFormPage from './pages/ProfileFormPage'
import WelcomePage from './pages/WelcomePage'

// Inside <Routes>:
<Route path="/welcome" element={<WelcomePage />} />  {/* public — no guard */}
<Route path="/profiles" element={<AdminRoute><ProfilesPage /></AdminRoute>} />
<Route path="/profiles/new" element={<AdminRoute><ProfileFormPage /></AdminRoute>} />
<Route path="/profiles/:id/edit" element={<AdminRoute><ProfileFormPage /></AdminRoute>} />
```

### 5.11 Navigation

Add "Profiles" to the admin "More" sheet in `BottomNav.tsx` (under Settings/Users/etc.). Link to `/profiles`.

### 5.12 Tokens / styling

- No hex colors. Use `bg-card`, `text-foreground`, `border-border`, `bg-primary`/`text-primary-foreground`.
- Public `WelcomePage` uses the same token system; dark-mode is `class`-based and pre-applied from `main.tsx` (`localStorage.getItem('theme') === 'dark'`) — so visitors who have visited any page before will inherit their preference, but the welcome page works fine in both modes.
- Required field asterisk: `text-destructive`.
- Success state: `bg-card` with a checkmark icon and muted text.

---

## 6. Migration / data

No existing production data migration is required by this sprint. The seed presets are inserted idempotently by the Alembic migration; if re-run on a DB that already has the seeds, the migration skips them.

**Custom field prerequisite**: the seed profiles reference `custom_field_name` values that must exist in `custom_field_def`. These are created by S02's seed migration. S13's migration should **not** fail if those custom fields don't exist yet (defer the seed rows if any referenced `custom_field_name` is absent). The safest approach: insert seed profiles with `fields = []` unconditionally; the owner configures the field lists via the admin UI after both S02 and S13 are deployed. Document this in rollout §9.

---

## 7. Acceptance criteria

1. Admin can create, edit, and delete a profile via the `/profiles` admin UI; saving persists to the `profiles` table.
2. Exactly one profile can be `is_public=true` at any time; attempting to set a second one returns 409.
3. The three seed profiles (New Friend, Community Member, Volunteer) exist in `profiles` after migration.
4. `GET /public/newcomer/profile` returns the resolved schema of the `is_public=true` profile without authentication.
5. `POST /public/newcomer` with valid data creates a `Contact` row with `contact_subtype="New Friend"` and the submitted fields in `custom_data`.
6. `POST /public/newcomer` with a non-empty `website` (honeypot) field returns `201` but creates **no** contact row.
7. `POST /public/newcomer` called more than 5 times in a minute from the same IP returns `429`.
8. `POST /public/newcomer` with a `invited_by` name that matches a known contact sets `contacts.custom_data["invited_by"] = matched_contact_id`; an unknown name creates a `name_match_review_queue` row with `source="newcomer"`.
9. `POST /public/newcomer` with a valid prayer request enqueues a `gmail.prayer_request` outbox row; with an invalid/empty prayer request it does not.
10. A `google_chat.new_friend` outbox row is created for every successful newcomer submission (regardless of prayer request validity).
11. The `_drain_outbox_stub` attempts to POST to the Google Chat webhook URL (from `admin_settings.google_chat_webhook_url`) and marks the outbox row `sent` on success or `failed` with `last_error` on failure.
12. `WelcomePage` at `/welcome` renders with no authentication and is accessible without a logged-in session.
13. `WelcomePage` renders the form from the `GET /public/newcomer/profile` schema dynamically — adding a field to the profile via admin and refreshing the public page shows the new field.
14. Submitting the public form with only `first_name` and `last_name` (all optional fields absent) creates a valid `Contact` with minimal `custom_data`.
15. `<ProfileFormRenderer>` shows inline validation errors when required fields are empty on submit attempt.
16. The "New from template" dropdown on `ContactsPage` shows all active profiles; selecting "New Friend" opens a form driven by the New Friend profile schema.
17. An `audit_log` row with `action="contact.create"` and `entity_id=<new contact id>` is written for every successful newcomer submission.
18. Re-submitting the same `first_name`, `last_name`, and `phone` within 5 minutes returns `status="duplicate"` and does not create a second contact.

---

## 8. Test plan

### 8.1 Backend pytest (`backend/tests/test_profiles.py`)

```python
# test_profile_crud
async def test_create_profile_admin(client, admin_token):
    """POST /profiles with admin token creates profile."""

async def test_create_profile_volunteer_forbidden(client, volunteer_token):
    """POST /profiles with volunteer token returns 403."""

async def test_unique_profile_name(client, admin_token):
    """Creating two profiles with same name returns 422."""

async def test_only_one_public_profile(client, admin_token):
    """Setting is_public on a second profile when one is already public returns 409."""

async def test_render_profile_resolves_custom_fields(client, volunteer_token, db):
    """POST /profiles/:id/render returns resolved fields including custom_field_def label and options."""

async def test_delete_profile(client, admin_token):
    """DELETE /profiles/:id returns 204 and profile is gone."""

async def test_get_public_schema_no_auth(client):
    """GET /public/newcomer/profile returns 200 with fields when is_public profile exists."""

async def test_get_public_schema_no_public_profile(client):
    """GET /public/newcomer/profile returns 404 when no is_public profile exists."""
```

### 8.2 Backend pytest (`backend/tests/test_public_newcomer.py`)

```python
async def test_newcomer_happy_path(client, db):
    """POST /public/newcomer creates contact with correct subtype and custom_data."""
    resp = await client.post("/public/newcomer", json={
        "first_name": "Maria", "last_name": "Santos",
        "phone": "09171234567", "gender": "Female",
        "service_time": "Second Service (10AM)",
        "invited_by": "", "prayer_request": "",
    })
    assert resp.status_code == 201
    assert resp.json()["status"] == "created"
    # Verify DB row
    contact = await db.get(Contact, resp.json()["contact_id"])
    assert contact.contact_subtype == "New Friend"
    assert contact.phone == "09171234567"

async def test_honeypot_returns_201_no_row(client, db):
    """POST /public/newcomer with website field creates no contact."""
    before = await db.scalar(select(func.count(Contact.id)))
    resp = await client.post("/public/newcomer", json={
        "first_name": "Bot", "last_name": "Bot", "website": "http://spam.com"
    })
    assert resp.status_code == 201
    after = await db.scalar(select(func.count(Contact.id)))
    assert after == before

async def test_rate_limit_5_per_minute(client):
    """Sixth submission in a minute returns 429."""
    payload = {"first_name": "A", "last_name": "B"}
    for i in range(5):
        await client.post("/public/newcomer", json={**payload, "first_name": f"A{i}"})
    resp = await client.post("/public/newcomer", json=payload)
    assert resp.status_code == 429

async def test_invited_by_matched(client, db, seed_contact):
    """POST /public/newcomer with a known invited_by name resolves contact_id in custom_data."""
    # seed_contact has first_name="Jose", last_name="Reyes"
    resp = await client.post("/public/newcomer", json={
        "first_name": "Maria", "last_name": "Santos",
        "invited_by": "Jose Reyes",
    })
    assert resp.status_code == 201
    contact = await db.get(Contact, resp.json()["contact_id"])
    assert contact.custom_data.get("invited_by") == seed_contact.id
    assert resp.json()["invited_by_resolved"] is True

async def test_invited_by_unmatched_creates_review_queue(client, db):
    """Unmatched invited_by creates a name_match_review_queue row."""
    resp = await client.post("/public/newcomer", json={
        "first_name": "Maria", "last_name": "Santos",
        "invited_by": "Xzythqwerty Unknown",
    })
    assert resp.status_code == 201
    assert resp.json()["invited_by_resolved"] is False
    row = await db.scalar(
        select(NameMatchReviewQueue).where(NameMatchReviewQueue.raw_name == "Xzythqwerty Unknown")
    )
    assert row is not None
    assert row.source == "newcomer"

async def test_duplicate_suppression(client, db):
    """Same name+phone within 5 minutes returns status=duplicate."""
    payload = {"first_name": "Maria", "last_name": "Santos", "phone": "09171234567"}
    r1 = await client.post("/public/newcomer", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/public/newcomer", json=payload)
    assert r2.status_code == 201
    assert r2.json()["status"] == "duplicate"
    # Only one contact row created
    count = await db.scalar(
        select(func.count(Contact.id)).where(
            Contact.first_name == "Maria", Contact.last_name == "Santos"
        )
    )
    assert count == 1

async def test_outbox_rows_created(client, db):
    """Successful submission creates google_chat and gmail outbox rows."""
    await client.post("/public/newcomer", json={"first_name": "Ana", "last_name": "Cruz"})
    rows = (await db.execute(select(Outbox).where(Outbox.status.in_(["pending","sent","failed"])))).scalars().all()
    types = {r.event_type for r in rows}
    assert "google_chat.new_friend" in types
    assert "gmail.new_friend_report" in types

async def test_prayer_request_valid_enqueues_prayer_email(client, db, mock_anthropic_valid):
    """Valid prayer request enqueues gmail.prayer_request outbox row."""
    await client.post("/public/newcomer", json={
        "first_name": "Ben", "last_name": "Lim",
        "prayer_request": "Please pray for my mother's health",
    })
    rows = (await db.execute(
        select(Outbox).where(Outbox.event_type == "gmail.prayer_request")
    )).scalars().all()
    assert len(rows) == 1

async def test_prayer_request_invalid_no_prayer_email(client, db, mock_anthropic_invalid):
    """Invalid prayer request (N/A) does not enqueue prayer email."""
    await client.post("/public/newcomer", json={
        "first_name": "Ben", "last_name": "Lim", "prayer_request": "N/A"
    })
    rows = (await db.execute(
        select(Outbox).where(Outbox.event_type == "gmail.prayer_request")
    )).scalars().all()
    assert len(rows) == 0

async def test_audit_log_created(client, db):
    """Newcomer submission creates an audit_log row."""
    resp = await client.post("/public/newcomer", json={"first_name": "Luz", "last_name": "Gomez"})
    log = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "contact.create",
            AuditLog.entity_id == str(resp.json()["contact_id"])
        )
    )
    assert log is not None
```

> **Mocking strategy**: `mock_anthropic_valid` / `mock_anthropic_invalid` are `pytest.fixture`s that monkeypatch `anthropic.AsyncAnthropic.messages.create` to return a synthetic response with `"valid"` / `"invalid"`. S22's `name_match` service should have its own mock fixture; tests here mock it at the service boundary (`monkeypatch.setattr("app.services.profile_service.name_match.match_name", ...)`).

### 8.3 Frontend vitest

```typescript
// tests/ProfileFormRenderer.test.tsx
test('renders required fields with asterisk')
test('shows inline error for empty required field on submit')
test('honeypot field is visually hidden')
test('calls onSubmit with correct values')
test('renders contact_reference field as free-text input')
test('groups fields by section correctly')

// tests/WelcomePage.test.tsx
test('renders without auth token')
test('shows loading state while fetching schema')
test('shows success message after submit')
test('shows error toast on API failure')
test('429 rate limit shows user-friendly message')
```

### 8.4 Build validation

```bash
# From frontend/
npm run build      # must exit 0 (TypeScript + Vite)
npm run lint       # ESLint must exit 0
npm run test:run   # vitest

# From backend/
DATABASE_URL=sqlite+aiosqlite:///./ci_test.db \
  REDIS_URL=memory:// \
  ENVIRONMENT=test \
  pytest tests/test_profiles.py tests/test_public_newcomer.py -q
ruff check app
```

---

## 9. Rollout / rollback / risks

### Rollout order

1. Deploy backend with the S13 Alembic migration (`alembic upgrade head`) — creates `profiles` table and seeds 3 presets; conditionally creates `outbox` if absent.
2. Deploy frontend — adds `/welcome`, `/profiles/*` routes and `<ProfileFormRenderer>`.
3. Admin configures `admin_settings`:
   - `google_chat_webhook_url` — Google Chat incoming webhook URL.
   - `newcomer_notify_email` — defaults to `attendance.monitoring@lightnc.org`.
   - `prayer_notify_email` — prayer team email.
   - `anthropic_api_key` — for prayer-request classification.
4. Admin opens `/profiles` → edits the seeded "New Friend" profile to add the correct `custom_field_name` references (once S02 custom fields are created).
5. Set `is_public=true` on the New Friend profile.
6. Share `/welcome` URL with the church team; test end-to-end with a dummy submission.
7. Decommission the n8n `jdVHzcMWXdANwG8R` workflow once submissions are confirmed flowing through the new system.

### Rollback

- **Schema**: `alembic downgrade -1` drops `profiles` (and `outbox` if S13 created it). Contact rows created by the public form are not reverted automatically — acceptable since they are valid CRM data.
- **Frontend**: revert the App.tsx route additions; the `/welcome` URL returns 404.
- **n8n**: re-enable the `jdVHzcMWXdANwG8R` workflow in n8n.

### Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| S22 `name_match.match_name()` not yet deployed | S13 public form cannot resolve Invited By/Consolidated By | Add a feature flag: if S22 service is unavailable (import error), skip resolution and queue all people-link names with `status="pending"` in `name_match_review_queue` |
| Claude API key not set or rate-limited | Prayer request classification fails | `_classify_prayer_request` catches `anthropic.APIError` and returns `"invalid"` (skip prayer email); this is safe and non-blocking |
| Google Chat webhook key not configured | Notification silently skipped | `_drain_outbox_stub` logs a warning and marks the outbox row `failed`; S17's worker will retry. The contact IS created regardless |
| Gmail not yet configured (S18 not yet deployed) | Gmail outbox rows accumulate | Mark as `failed` after stub attempt; S18 replaces the stub with proper delivery |
| Spam / abuse of public endpoint | Flood of fake contacts | Honeypot + rate limit (5/min, 20/hr); add `newcomer_allowed_origins` allowlist for extra protection |
| Profile schema mismatch with S02 custom field names | Renderer shows blank fields or errors | Validation at `POST /profiles` catches unknown `custom_field_name` values; owner sees 422 on save |
| Only one public profile at a time | Admin confusion when editing profiles | Admin UI shows a badge and a tooltip explaining the constraint |

---

## 10. Open questions & pending owner artifacts

**Q1 — Custom field names for New Friend seed** (BLOCKING for seed): The seed profile references `custom_field_name` values (`facebook_name`, `new_friend_add_date`, `service_time`, `invited_by`, `consolidated_by`, `prayer_request`, `season`). These must exactly match the `custom_field_def.name` values seeded by S02. **Master must confirm S02's canonical custom field names for the New Friend form and add them to the seed.**

> n8n ground truth (from `jdVHzcMWXdANwG8R.json` `Edit Fields` node): the fields used are `First Name`, `Last Name`, `Contact Number`, `Birthday`, `Gender`, `Home Address`, `Date Invited`, `Service Time`, `Invited by`, `Facebook Name`, `Consolidated By`, `Prayer Request`, `What best describes your season?`. CiviCRM custom IDs: `custom_25` = `new_friend_add_date`, `custom_26` = `invited_by`, `custom_29` = `facebook_name`, `custom_63` = `consolidated_by`, `custom_67`/`custom_68` = (unknown, set to `2`).

**Q2 — Notification email addresses**: `attendance.monitoring@lightnc.org` and `marichupiller01@gmail.com` (prayer team) are hard-coded in the n8n workflow. Must these be configurable at runtime, or are they permanent? Spec has made them configurable via `admin_settings`. Owner should confirm the correct final addresses.

**Q3 — Google Chat webhook URL**: S18 is the sprint that provisions the Google Chat webhook and configures the settings panel for it. Until S18 deploys, S13's stub drain cannot send Google Chat notifications. Is a simplified console-log-only mode acceptable for testing S13 before S18? **Recommended**: yes, the stub checks for the key and skips silently; document in runbook.

**Q4 — Public form origin / CORS**: If `/welcome` is served from the same domain as the app (Cloudflare Tunnel → nginx → same container), CORS is not an issue. If the public form is ever embedded in an iframe on a different domain (church website), CORS origins must be expanded. Owner should confirm the intended deployment topology.

**Q5 — Idempotency window (5 minutes)**: The n8n workflow had no dedup logic; the "New Friend" Google Form could receive the same person twice (e.g., submitted by two volunteers). A 5-minute window by (first_name, last_name, phone) is the proposed guard. Owner should confirm if a longer window or email-based dedup is preferred.

**Q6 — `contact_subtype` value**: The n8n workflow creates contacts with `contact_type=Individual` but no explicit CiviCRM sub-type in the API call (sub-types were a tag/group in CiviCRM). In the native CRM, `contact_subtype = "New Friend"` is the canonical value. Owner must confirm this is the correct string.

**Q7 — S18 dependency vs. stub**: S13 ships a `_drain_outbox_stub` that handles Google Chat notifications inline (best-effort). If S18 deploys before S13, the master must reconcile which sprint owns the `Outbox` model and initial migration (currently spec assigns conditional ownership to S13; S17/S18 may own it). The master spec must designate one sprint as `outbox` table owner.

**Q8 — `anthropic_api_key` in admin_settings vs. environment variable**: Other API keys in this project flow through `admin_settings` (DB-backed). The `ANTHROPIC_API_KEY` could alternatively come from an environment variable (standard for the Anthropic SDK). Recommend: read from environment variable `ANTHROPIC_API_KEY` first (SDK default), fall back to `admin_settings` key `anthropic_api_key`. Master should standardize this pattern.

---

### Cross-sprint dependencies / shared-model touchpoints / inconsistencies for master reconciliation

1. **S22 `name_match.match_name()` is a hard runtime dependency** of `process_newcomer_submission`. S13 must not ship to production before S22 is deployed, or the feature-flag fallback (Q1 in S22's open questions) must be confirmed. The dependency on S22 is explicit in `Depends on:` but the feature-flag path for partial deployment needs to be decided.

2. **`outbox` table ownership**: S13, S17, and S18 all touch the `outbox` table. Currently S17 owns the full table; S13 adds a conditional creation guard. The master must designate exactly one sprint as the Alembic owner of `outbox` and mark S13 as a consumer. If S17 comes first in deployment, S13's conditional creation is a no-op (correct). If S13 deploys first, it creates the minimal schema that S17 then extends — this requires S17's migration to use `op.add_column` (additive), not `op.create_table`.

3. **`audit_log` table ownership**: S13 writes to `audit_log`. The table must exist before S13 deploys. Based on the master spec, `audit_log` is introduced in S02 or S03. Master must confirm which sprint creates it.

4. **`Outbox` ORM model location**: if S17 defines `class Outbox(Base)` in `models.py`, S13 must import it from there, not redefine it. If S17 has not shipped yet, S13 defines it conditionally. The master must prevent two conflicting class definitions.

5. **S02 custom field seed names** (Q1 above): the seed profile's `fields[]` array references `custom_field_def.name` strings. If S02's migration uses different canonical names, the seed is broken. Master reconciliation is required here before S13 can be spec-finalized.

6. **`create_contact(...)` service signature (S03)**: S13 calls `await create_contact(db=db, first_name=..., ...)`. S03 must export this as an async function from `app.services.contact_service` (or wherever S03 places it). The master must confirm the exact module path and signature.

7. **`viewer` role** (S15): once S15 deploys, `/public/*` endpoints must remain unauthenticated (no role check added). All other S13 endpoints (`/profiles`) already use `require_admin`/`require_volunteer` which exclude viewers. No S13 change needed for S15 compatibility, but the master should audit `/profiles` GET endpoints — viewers should probably be able to see profile schemas for the internal form launcher even though they cannot create contacts.
