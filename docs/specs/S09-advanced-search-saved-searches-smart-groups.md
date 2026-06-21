# S09 — Advanced Search, Saved Searches & Smart Groups
**Phase:** D — Power features · **Depends on:** S01 (schema inversion: `contacts`, `Contact` model, `audit_log`), S02 (custom-field engine: `custom_field_def`/`custom_field_group`, `validate_and_coerce`, `build_field_registry`), S03 (contact CRUD: `ContactResponse`, `DataTable`/`Pagination`/`StatusBadge` primitives, upgraded `GET /members` paginated shape) · **Effort:** L · **Status:** Not started

> **S23 dependency (DERIVED FIELDS):** The charter specifies filtering "over core + custom + DERIVED fields (tier/active/regular/connected)." The derived snapshot columns (`tier`, `is_active`, `is_regular`, `is_connected`, `weeks_absent`, `last_attended_at`, `attendance_count`) live on `contacts` and are written nightly by S23's job. S09's field registry includes them as filterable columns from the outset. The values are `NULL` until S23's first job run — the compiler treats `NULL` correctly (IS NULL / IS NOT NULL). If S23 has not yet landed at implementation time, these columns are still present in the schema (created by S01 per ruling C2) but will yield empty or null results until the job runs. No feature flag needed; the compiler simply operates on the columns.

> **RBAC resolution (ruling C20 / §10 discussion):** The locked decision (decisions.md Round 4) is that **viewers see reports/dashboard only — no individual contact records.** Search returns individual contact rows; therefore all `/search/*` and `/groups/*` endpoints that return contact rows are gated at `volunteer+` minimum. Read-only aggregates (field registry, group metadata without member rows) may later be relaxed to `viewer+` if S15 decides viewers need to self-serve filters for dashboard widgets — but S09 ships `require_volunteer` on all endpoints and defers the relaxation to S15.

---

## 1. Goal & rationale

Replace CiviCRM's **Advanced Search** + **Search Builder** + **Smart Groups** with a native, injection-safe equivalent. Today the only contact discovery mechanism is a single free-text box: `GET /members` is `ilike` over name/email only, `limit`-capped with no offset (`backend/app/routers/members.py:18-45`), and the frontend `AttendeesPage`/`MemberSearchModal` both provide single inputs with no multi-criteria UI, no saved searches, and no group concept (`docs/crm-research/frontend.md:69`).

This sprint delivers four tightly coupled capabilities:

1. **Whitelisted structured-query engine** — clients POST a declarative filter tree `{field, operator, value}` (AND/OR groups); the backend compiles it to parameterized SQLAlchemy `WHERE` clauses against `contacts`. A strict **column whitelist** (`CORE_CONTACT_FIELDS` + dynamically loaded `custom_field_def` rows + the seven derived snapshot columns from S23) is the sole gate between a field-name string and a database column. No raw SQL, no `text()` interpolation of identifiers.

2. **Saved Searches** — persist a named criteria tree per user in `saved_searches`; re-run live at any time.

3. **Groups** — `static` (frozen `group_members` snapshot) and `smart` (stored criteria tree resolved live, never materialized). A one-click **promote** copies a saved search into a smart group.

4. **Multi-criteria filter builder UI** — reusable `FilterBuilder` component consumed by the Contacts list, the Saved-Search editor, and the Group editor.

**Why S09 is foundational:** every downstream sprint that operates on a *set* of contacts relies on S09's canonical "resolve a contact set from criteria" service: S10 (import targeting), S12 (bulk-assign activities), S14 (reporting segments, tier counts), S17 (automation `contact_in_group` condition), S18 (audience selection). S09 builds that service once; subsequent sprints import `resolve_group_contacts` / `build_contact_query` from `app/services/search_service.py`.

---

## 2. Scope

### In scope
- A reusable **query-compiler service** (`search_service.py`) that turns a validated, depth-guarded criteria tree into a SQLAlchemy `Select` over `contacts` (always `is_deleted=False` by default).
- A **field registry** (`search_fields.py`) enumerating all filterable core columns, the seven S23 derived snapshot columns, and dynamic custom fields from `custom_field_def` (entity=`contact`). The registry IS the column whitelist.
- `POST /search/contacts` — execute an ad-hoc criteria tree, paginated.
- `GET /search/fields?entity=contact` — return the filterable field registry (core + derived + custom) for the builder UI.
- **`saved_searches` CRUD** (owner-scoped; admin can see/edit all via `?all=true`).
- **`groups` + `group_members` CRUD**: create/list/update/delete; add/remove members for static groups; live resolution for smart groups; paginated members listing for both.
- **Promote** a saved search → smart group (one click; copies `criteria`; 409 on duplicate group name).
- **Populate static group from criteria** (`POST /groups/{id}/populate`, `mode: replace|append`): resolve criteria once and snapshot matched contact ids into `group_members`.
- Multi-criteria filter builder UI: field/operator/value rows, AND/OR group toggle, one level of nesting, "+ Add condition" / "+ Add group" / per-row remove.
- **Derived-field filters**: `tier`, `is_active`, `is_regular`, `is_connected`, `weeks_absent`, `last_attended_at`, `attendance_count` — treated as regular core columns in the compiler.
- Role gating: `volunteer+` for all reads AND writes (viewer may not access individual contact rows — see header ruling); admin additionally can edit/delete others' searches and edit smart-group criteria.
- Backend pytest + frontend vitest coverage; `npm run build` + `npm run lint` clean.

### Out of scope (explicit)
- **Search over events / activities / participants** — entity discriminator `saved_searches.entity` and `groups.entity` are forward-compatible, but only `contact` is wired this sprint.
- **Bulk actions on result sets** beyond "populate static group": bulk-export → S05; bulk-import → S10; bulk-assign activities → S12.
- **Full-text / fuzzy / phonetic / trigram search** — exact + `ilike` (`contains`/`starts_with`/`ends_with`) only. Trigram GIN is deferred (dataset is 1,440 contacts; sequential scan is sub-millisecond).
- **Saved-search sharing** beyond owner + admin-override.
- **Materialized smart groups** / background refresh — smart groups resolve live every read.
- **Recursive nesting deeper than one level in the UI** (compiler supports arbitrary depth; UI caps at depth 2 and the depth guard at 10).
- **`contact_reference` traversal joins** (e.g. "contacts whose *Invited By* attended event X") — contact_reference fields are filterable by stored id value only (`eq`/`in`/`is_set`).
- **GIN expression indexes** on specific custom-data keys — acceptable perf at 1,440 contacts; deferred.
- **Viewer access to contact rows** — per locked RBAC decision; deferred to S15 for any relaxation.

---

## 3. Data model changes

All JSON columns use `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` (the alias already at `backend/app/models.py:22`). All timestamps use `utc_now()` (`models.py:27-28`). FK `ondelete` is declared explicitly on every FK.

### 3.1 New table: `saved_searches`

Owner: **S09**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | identity | app-minted |
| `name` | String(200) | no | — | user-facing display name |
| `owner_id` | Integer FK→`users.id` ON DELETE CASCADE | no | — | creator/owner; CASCADE so searches disappear with the user |
| `entity` | String(20) | no | `'contact'` | discriminator; only `contact` wired this sprint |
| `criteria` | JSONB | no | `{}` | the criteria tree (§4.2 schema) |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |
| `updated_at` | DateTime (naive UTC) | no | `utc_now()`, `onupdate=utc_now()` | |

Indexes: `Index("ix_saved_searches_owner_id", "owner_id")`; `Index("ix_saved_searches_entity", "entity")`.

### 3.2 New table: `groups`

Owner: **S09**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | identity | |
| `name` | String(200) | no | — | UNIQUE per (entity) — see constraint |
| `type` | String(10) | no | `'static'` | `static` \| `smart` |
| `entity` | String(20) | no | `'contact'` | only `contact` wired |
| `criteria` | JSONB | yes | NULL | required when `type='smart'`; NULL/empty for static (enforced in service layer) |
| `description` | String(500) | yes | NULL | |
| `created_by_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |
| `updated_at` | DateTime (naive UTC) | no | `utc_now()`, `onupdate=utc_now()` | |

Constraints/indexes:
- `UniqueConstraint("name", "entity", name="uq_groups_name_entity")` — declared in both migration and `__table_args__` (so `create_all` under SQLite builds the same constraint as Postgres migration — per AGENTS.md dual-constraint pattern: `Attendance.__table_args__` at `models.py:212-214`).
- `Index("ix_groups_type", "type")`.

### 3.3 New table: `group_members`

Owner: **S09**. Static-group membership only — smart groups are **never** stored here.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `group_id` | Integer FK→`groups.id` ON DELETE CASCADE | no | — | PK part 1 |
| `contact_id` | Integer FK→`contacts.id` ON DELETE CASCADE | no | — | PK part 2 |
| `added_by_id` | Integer FK→`users.id` ON DELETE SET NULL | yes | NULL | |
| `added_at` | DateTime (naive UTC) | no | `utc_now()` | |

Constraints/indexes:
- Composite PK `(group_id, contact_id)` — provides uniqueness + the hot lookup index.
- `Index("ix_group_members_contact_id", "contact_id")` — needed for "which groups is this contact in?" (profile panel, S11 FK-reassignment scan).
- `Index("ix_group_members_group_id", "group_id")` — explicit for the ORM query even though covered by the composite PK; ensures the SQLite test-suite `create_all` path builds it.

> **S11 FK manifest (ruling C4):** `group_members.contact_id` is a FK to `contacts`. S11's loser→survivor reassignment MUST include this table; de-duplicate against the composite PK (if the survivor already has a `group_members` row for the same `group_id`, skip/drop the loser's row). The `test_manifest_covers_all_contact_fks` reflection test in S11 must catch this.

### 3.4 Alembic migration plan

One migration file: `backend/alembic/versions/<rev>_s09_search_groups.py`.
- `down_revision` = the real alembic head after S03 and S02 migrations have been applied. **Never invent revision IDs** — run `alembic heads` at implementation time (AGENTS.md:100; MASTER §5).
- `depends_on` = [S02 head, S03 head] (both must precede).

Forward ops (in this order, respecting FK creation order):
```python
# 1. saved_searches
op.create_table(
    "saved_searches",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("entity", sa.String(20), nullable=False, server_default=sa.text("'contact'")),
    sa.Column("criteria", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False, server_default="{}"),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
)
op.create_index("ix_saved_searches_owner_id", "saved_searches", ["owner_id"])
op.create_index("ix_saved_searches_entity", "saved_searches", ["entity"])

# 2. groups
op.create_table(
    "groups",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("type", sa.String(10), nullable=False, server_default=sa.text("'static'")),
    sa.Column("entity", sa.String(20), nullable=False, server_default=sa.text("'contact'")),
    sa.Column("criteria", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
    sa.Column("description", sa.String(500), nullable=True),
    sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("name", "entity", name="uq_groups_name_entity"),
)
op.create_index("ix_groups_type", "groups", ["type"])

# 3. group_members
op.create_table(
    "group_members",
    sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
    sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
    sa.Column("added_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    sa.Column("added_at", sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint("group_id", "contact_id"),
)
op.create_index("ix_group_members_contact_id", "group_members", ["contact_id"])
op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
```

Downgrade (reverse FK order): drop indexes, then `group_members`, `groups`, `saved_searches`.

Data backfill: **none** — all three tables are new and empty on deploy.

Note in migration docstring: JSONB columns created via `.with_variant`; SQLite tests see plain JSON (JSON1 functions used for path extraction in the compiler). Migration is non-destructive (additive only); rollback safe.

### 3.5 Models to add (`backend/app/models.py`)

Add three classes following the existing style (`Mapped[...]`, `mapped_column`, `JSONB` alias from line 22, `utc_now` from line 27). Declare `__table_args__` on `Group` and `GroupMember` so `create_all` under SQLite and the Alembic migration produce identical schemas (mirroring `Attendance.__table_args__` at `models.py:212-214`).

```python
class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), default="contact")
    criteria: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(10), default="static")
    entity: Mapped[str] = mapped_column(String(20), default="contact")
    criteria: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint("name", "entity", name="uq_groups_name_entity"),
    )


class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    added_by_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    __table_args__ = (
        Index("ix_group_members_contact_id", "contact_id"),
        Index("ix_group_members_group_id", "group_id"),
    )
```

---

## 4. Backend

### 4.1 Endpoints

New router: `backend/app/routers/search.py`, `APIRouter(prefix="/search", tags=["search"])`. Registered in `backend/app/main.py` with `dependencies=[Depends(check_setup_complete)]`, inserted after `members.router` at line 112 (approximate; match existing style). All routes are prefix-free (nginx strips `/api`).

Auth guards (from `backend/app/dependencies.py`): `require_volunteer` (admin + volunteer) for all endpoints this sprint. S15 may relax field-registry and group-metadata-only endpoints to `require_viewer` — **do not pre-implement S15's guard here**; use `require_volunteer`.

#### Search endpoints

| METHOD | Path | Role | Request | Response | Notes |
|---|---|---|---|---|---|
| GET | `/search/fields` | volunteer+ | `?entity=contact` | `FieldRegistryResponse` | core + derived + active custom fields; each entry has `name, label, data_type, ops[], options?` |
| POST | `/search/contacts` | volunteer+ | `ContactSearchRequest` | `PaginatedContactResponse` | execute ad-hoc criteria; default `is_deleted=False`; clamp `page_size ≤ 100` |

#### Saved-search endpoints

| METHOD | Path | Role | Request | Response | Notes |
|---|---|---|---|---|---|
| GET | `/search/saved` | volunteer+ | `?all=true` (admin only) | `list[SavedSearchResponse]` | own searches; admin `?all=true` sees all |
| POST | `/search/saved` | volunteer+ | `SavedSearchCreate` | `SavedSearchResponse` | validates criteria tree before save; 422 on invalid |
| GET | `/search/saved/{id}` | owner or admin | — | `SavedSearchResponse` | 404 if not owner and not admin |
| PUT | `/search/saved/{id}` | owner or admin | `SavedSearchUpdate` | `SavedSearchResponse` | re-validates criteria |
| DELETE | `/search/saved/{id}` | owner or admin | — | 204 | |
| POST | `/search/saved/{id}/run` | owner or admin | `RunOptions` (`page, page_size, order_by, order_dir`) | `PaginatedContactResponse` | runs stored criteria live |
| POST | `/search/saved/{id}/promote` | owner or admin | `PromoteRequest` (`group_name, description?`) | `GroupResponse` | copies `criteria` → new `smart` group; 409 on duplicate `(group_name, entity)` |

#### Group endpoints

| METHOD | Path | Role | Request | Response | Notes |
|---|---|---|---|---|---|
| GET | `/groups` | volunteer+ | `?type=&entity=` | `list[GroupResponse]` (with `member_count`) | static: `COUNT(group_members)`; smart: live count (subquery) |
| POST | `/groups` | volunteer+ | `GroupCreate` | `GroupResponse` | static → `criteria` must be null/absent; smart → `criteria` required and validated |
| GET | `/groups/{id}` | volunteer+ | — | `GroupResponse` | 404 if missing |
| PUT | `/groups/{id}` | creator or admin (smart-group criteria edit = admin only) | `GroupUpdate` | `GroupResponse` | **cannot change `type` after creation** → 400; smart-group criteria edits restricted to admin |
| DELETE | `/groups/{id}` | creator or admin | — | 204 | cascades to `group_members` |
| GET | `/groups/{id}/members` | volunteer+ | `?page=&page_size=` | `PaginatedContactResponse` | static: join `group_members`; smart: resolve criteria live |
| POST | `/groups/{id}/members` | volunteer+ | `AddMembersRequest` (`contact_ids: list[int]`, max 500) | `{added: int, skipped: int}` | **static only** — 400 for smart; set-based insert `ON CONFLICT DO NOTHING`; validate each id exists in `contacts` |
| DELETE | `/groups/{id}/members/{contact_id}` | volunteer+ | — | 204 | static only — 400 for smart |
| POST | `/groups/{id}/populate` | volunteer+ | `PopulateRequest` (`criteria?` or `saved_search_id?`, `mode: replace\|append`) | `{added: int, total: int}` | **static only** — 400 for smart; resolve criteria once, snapshot into `group_members` |

### 4.2 Criteria tree wire shape (the contract)

A criteria document is a recursive structure. Every node is either a **group node** or a **condition node**:

```jsonc
// Group node — AND/OR combinator
{
  "op": "AND",           // "AND" | "OR" — uppercase; reject lowercase variants
  "rules": [ <node>, ... ] // 1..N child nodes; 0 nodes → emit SQL true() (empty criteria = all contacts)
}

// Condition node — a single predicate
{
  "field": "last_name",          // must be in the field registry whitelist; error if absent
  "operator": "contains",        // must be in the field's allowed ops list; error if not
  "value": "cruz"                // type-coerced per data_type; null only valid for is_set/is_empty
}
```

Top-level criteria MUST be a group node (validated by the Pydantic model before it reaches the compiler).

Custom fields are referenced as `field: "custom.<def_name>"` where `<def_name>` is the `custom_field_def.name` (snake_case). The `custom.` prefix routes the compiler to a JSONB path expression on `contacts.custom_data` keyed by `<def_name>`.

Derived snapshot columns are referenced by their bare column name: `field: "tier"`, `field: "is_active"`, etc. They are core-contact columns (physically on `contacts`) not JSONB path lookups.

### 4.3 Field registry (`backend/app/services/search_fields.py`)

```python
@dataclass
class FieldSpec:
    name: str                        # "last_name" | "custom.ministry" | "tier" etc.
    label: str                       # human label for UI
    data_type: str                   # text | select | multiselect | date | number | checkbox |
                                     # contact_reference | tier_enum | boolean
    ops: list[str]                   # allowed operators for this data_type
    options: list[dict] | None       # [{value, label}] for select/multiselect fields; None otherwise
    column: Any                      # SQLAlchemy column reference or None (None = JSONB path)
    is_custom: bool = False          # True for custom.* fields
    is_derived: bool = False         # True for S23 derived snapshot columns
```

**Operator sets by data_type** (these are the ONLY operators the compiler will accept):

| data_type | allowed operators |
|---|---|
| text / textarea | `eq, neq, contains, starts_with, ends_with, is_set, is_empty, in` |
| select | `eq, neq, in, is_set, is_empty` |
| multiselect | `contains_any, contains_all, is_set, is_empty` |
| number / integer | `eq, neq, gt, gte, lt, lte, between, is_set, is_empty` |
| date | `eq, before, after, between, is_set, is_empty` |
| checkbox / boolean | `eq, is_set` |
| contact_reference | `eq, in, is_set, is_empty` |
| tier_enum | `eq, neq, in, is_set, is_empty` |

**Core contact fields** (`CORE_CONTACT_FIELDS: dict[str, FieldSpec]`):

```python
CORE_CONTACT_FIELDS = {
    "first_name":       FieldSpec("first_name",       "First Name",        "text",     TEXT_OPS,    None, Contact.first_name),
    "last_name":        FieldSpec("last_name",        "Last Name",         "text",     TEXT_OPS,    None, Contact.last_name),
    "nickname":         FieldSpec("nickname",         "Nickname",          "text",     TEXT_OPS,    None, Contact.nickname),
    "suffix":           FieldSpec("suffix",           "Suffix",            "text",     TEXT_OPS,    None, Contact.suffix),
    "email":            FieldSpec("email",            "Email",             "text",     TEXT_OPS,    None, Contact.email),
    "phone":            FieldSpec("phone",            "Phone",             "text",     TEXT_OPS,    None, Contact.phone),
    "street_address":   FieldSpec("street_address",   "Street Address",    "text",     TEXT_OPS,    None, Contact.street_address),
    "gender":           FieldSpec("gender",           "Gender",            "select",   SELECT_OPS,  GENDER_OPTIONS, Contact.gender),
    "contact_type":     FieldSpec("contact_type",     "Contact Type",      "select",   SELECT_OPS,  CONTACT_TYPE_OPTIONS, Contact.contact_type),
    "contact_subtype":  FieldSpec("contact_subtype",  "Contact Subtype",   "select",   SELECT_OPS,  SUBTYPE_OPTIONS, Contact.contact_subtype),
    "birth_date":       FieldSpec("birth_date",       "Birth Date",        "date",     DATE_OPS,    None, Contact.birth_date),
    "external_id":      FieldSpec("external_id",      "CiviCRM ID",        "number",   ["eq","in","is_set","is_empty"], None, Contact.external_id),
    "created_at":       FieldSpec("created_at",       "Created At",        "date",     DATE_OPS,    None, Contact.created_at),
    "updated_at":       FieldSpec("updated_at",       "Updated At",        "date",     DATE_OPS,    None, Contact.updated_at),
    "is_deleted":       FieldSpec("is_deleted",       "Is Deleted",        "boolean",  BOOL_OPS,    None, Contact.is_deleted),
}
```

**Derived snapshot fields** (separate dict `DERIVED_FIELDS`, merged at registry build time):

```python
DERIVED_FIELDS = {
    "tier":             FieldSpec("tier",             "Engagement Tier",   "tier_enum", TIER_OPS,   TIER_OPTIONS,   Contact.tier,             is_derived=True),
    "is_active":        FieldSpec("is_active",        "Active",            "boolean",  BOOL_OPS,    None,           Contact.is_active,         is_derived=True),
    "is_regular":       FieldSpec("is_regular",       "Regular Attendee",  "boolean",  BOOL_OPS,    None,           Contact.is_regular,        is_derived=True),
    "is_connected":     FieldSpec("is_connected",     "Connected",         "boolean",  BOOL_OPS,    None,           Contact.is_connected,      is_derived=True),
    "weeks_absent":     FieldSpec("weeks_absent",     "Weeks Absent",      "number",   NUM_OPS,     None,           Contact.weeks_absent,      is_derived=True),
    "last_attended_at": FieldSpec("last_attended_at", "Last Attended",     "date",     DATE_OPS,    None,           Contact.last_attended_at,  is_derived=True),
    "attendance_count": FieldSpec("attendance_count", "Attendance Count",  "number",   NUM_OPS,     None,           Contact.attendance_count,  is_derived=True),
}
```

Where `TIER_OPTIONS = [{"value": v, "label": l} for v, l in [("tier0","Tier 0 — This Week"), ("tier1","Tier 1 — 1–4 Weeks"), ("tier2","Tier 2 — 5–8 Weeks"), ("tier3","Tier 3 — 9–12 Weeks"), ("inactive","Inactive — 12+ Weeks")]]`.

**`build_field_registry(db, entity="contact") -> dict[str, FieldSpec]`** (async):
1. Start with `{**CORE_CONTACT_FIELDS, **DERIVED_FIELDS}`.
2. Query `custom_field_def JOIN custom_field_group WHERE custom_field_group.entity = entity AND custom_field_def.is_active = True AND custom_field_group.is_active = True ORDER BY weight, id`.
3. For each row: key = `f"custom.{row.name}"`, data_type = `row.data_type`, ops from the operator table above (ruling C8: entity is **lowercase** `contact`), options = `row.options` (for select/multiselect/contact_reference).
4. Return the merged dict. The registry is the **only** source of valid field names — nothing else touches column identifiers.

Cache the registry in-request (no Redis caching needed at this scale; a module-level dict reset on settings reload if needed later).

### 4.4 Query compiler (`backend/app/services/search_service.py`)

#### Core API

```python
class InvalidCriteria(ValueError):
    """Raised for schema violations, unknown fields, disallowed operators."""
    pass

def compile_criteria(node: dict, registry: dict[str, FieldSpec], dialect: str) -> ColumnElement:
    """Sync, pure (no I/O). Recursively compile a criteria node to a SQLAlchemy expression."""

async def build_contact_query(
    criteria: dict,
    registry: dict[str, FieldSpec],
    db: AsyncSession,
    *,
    include_deleted: bool = False,
    order_by: str | None = None,
    order_dir: str = "asc",
) -> Select:
    """Compile criteria and wrap in a SELECT over Contact with pagination-ready columns."""

async def resolve_group_contacts(
    db: AsyncSession,
    group: Group,
    registry: dict[str, FieldSpec],
    *,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[Contact]]:
    """Canonical contact-set resolver. Returns (total, rows). Consumed by S12/S14/S17/S18."""

async def populate_static_group(
    db: AsyncSession,
    group: Group,
    criteria: dict,
    registry: dict[str, FieldSpec],
    *,
    mode: str = "append",
    actor_id: int | None = None,
) -> dict[str, int]:
    """Resolve criteria once, snapshot ids into group_members. Returns {added, total}."""
```

#### `compile_criteria` algorithm

1. **Guard — depth and size**: traverse the tree first. If total condition nodes > 100 or nesting depth > 10 → raise `InvalidCriteria("criteria too large")`. (Prevents DoS via deeply recursive compiled queries.)

2. **Recursive dispatch:**
   - If node has `"op"` key → group node:
     - `op` must be `"AND"` or `"OR"` (uppercase); else `InvalidCriteria`.
     - `rules` must be a list. Empty list → return `true()` (empty criteria = no filter).
     - Recurse `compile_criteria` on each child; combine with `and_(*clauses)` or `or_(*clauses)`.
   - Else → condition node:
     - `field` MUST be present and a string; else `InvalidCriteria`.
     - Look up `field` in `registry`. **If absent → raise `InvalidCriteria(f"Unknown field: {field!r}")`.** This is the injection gate — an attacker-controlled string that is not in the registry dict never reaches a column expression.
     - `operator` MUST be in `spec.ops`; else `InvalidCriteria(f"Operator {operator!r} not allowed for {field}")`.
     - **Coerce `value`** to the correct Python type (see §4.4.1). Coercion failure → `InvalidCriteria` with a descriptive message. Never pass raw client value into a `text()` fragment.
     - Build and return the SQLAlchemy expression (see §4.4.2).

3. **Ordering** in `build_contact_query`: `order_by` must be in the registry whitelist (checked the same way as condition fields); invalid → fall back to `last_name, first_name` (default). `order_dir ∈ {"asc","desc"}` else default `"asc"`.

4. **Soft-delete**: unless `include_deleted=True`, always append `AND Contact.is_deleted == False`. This is appended **outside** the user criteria (cannot be bypassed by a criteria node targeting `is_deleted` — the outer `AND` still applies).

#### 4.4.1 Value coercion rules

| data_type | Expected Python type | Coercion |
|---|---|---|
| text / textarea / select / multiselect / tier_enum | `str` or `list[str]` (for `in`/`contains_any`/`contains_all`) | `str(value)` or `[str(v) for v in value]` |
| number / integer | `int` or `float` | `int(value)` or `float(value)`; `between` expects 2-element list |
| date | `datetime.date` | parse ISO-8601 `YYYY-MM-DD`; `between` expects 2-element list |
| boolean | `bool` | `bool(value)` (truthy cast) |
| contact_reference | `int` or `list[int]` | `int(value)` or `[int(v) for v in value]` |

`is_set` and `is_empty` operators: `value` MUST be absent or null; if a non-null value is provided → silently ignore it (do not error; consistent with "is_set takes no value").

`in`, `contains_any`, `contains_all`: `value` must be a non-empty list (≤ 200 elements); else `InvalidCriteria`.

`between`: `value` must be a 2-element list `[lo, hi]`; `lo > hi` is not enforced (empty result is fine; do not error).

**LIKE escaping** for `contains` / `starts_with` / `ends_with`: escape literal `%` and `_` in the value before building the ilike pattern. Use `value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")` and pass `escape="\\"` to SQLAlchemy's `.ilike()`.

#### 4.4.2 SQLAlchemy expression construction

**Core column operators** (operate on `spec.column` directly — no string interpolation):

| operator | SQLAlchemy expression |
|---|---|
| `eq` | `col == value` |
| `neq` | `col != value` |
| `contains` | `col.ilike(f"%{esc_val}%", escape="\\")` |
| `starts_with` | `col.ilike(f"{esc_val}%", escape="\\")` |
| `ends_with` | `col.ilike(f"%{esc_val}", escape="\\")` |
| `in` | `col.in_(values)` |
| `gt` | `col > value` |
| `gte` | `col >= value` |
| `lt` | `col < value` |
| `lte` | `col <= value` |
| `between` | `col.between(lo, hi)` |
| `before` (date) | `col < value` |
| `after` (date) | `col > value` |
| `is_set` | `col.isnot(None)` (for text types also `and_(col.isnot(None), col != "")`) |
| `is_empty` | `or_(col.is_(None), col == "")` for text; `col.is_(None)` for non-text |

**Custom JSONB operators** (for `custom.<name>` fields):

The key `<name>` is known to be a `custom_field_def.name` value obtained from the registry — it is NOT raw client input. Construct path expressions:

```python
def _jsonb_get_scalar(custom_data_col, key: str, dialect: str):
    if dialect == "postgresql":
        return custom_data_col[key].astext            # ->>'key'
    else:
        return func.json_extract(custom_data_col, f"$.{key}")  # SQLite JSON1
```

- `text`/`textarea`/`select` → use `_jsonb_get_scalar(Contact.custom_data, key, dialect)` then core-like operators (ilike for contains, == for eq, etc.).
- `number`/`date` → cast the result: `cast(_jsonb_get_scalar(...), Numeric)` / `cast(..., Date)`.
- `multiselect` (stored as JSON array) on Postgres:
  - `contains_any` → `Contact.custom_data[key].astext` `LIKE ANY (ARRAY[...])` is fragile; use a subquery or: `or_(*[Contact.custom_data[key].astext.contains(f'"{v}"') for v in values])` (simple; works for JSON arrays stored as `["a","b"]`). For correctness at scale, use `func.jsonb_path_exists(Contact.custom_data, f'$.{key}[*] ? (@ == $v)', bindparam('v', v))` per element — but the simple containment approach is acceptable at 1,440 rows. **Document the trade-off.**
  - On SQLite: `func.json_each(Contact.custom_data, f"$.{key}")` in a lateral join or the simple `LIKE '%"v"%'` approach for the test-path.
- `contact_reference` (stored as int or list[int] per ruling C13):
  - Single value: `cast(_jsonb_get_scalar(...), Integer) == int_value`.
  - `in` operator: `cast(_jsonb_get_scalar(...), Integer).in_(int_values)`.
  - `is_set` / `is_empty`: `Contact.custom_data[key].isnot(None)` / `Contact.custom_data[key].is_(None)`.
- `checkbox` (stored as JSON boolean): `func.json_extract(Contact.custom_data, f"$.{key}") == (1 if value else 0)` (SQLite JSON1 maps booleans to 1/0); Postgres: `cast(_jsonb_get_scalar(...), Boolean) == value`.

**Provide a `_make_jsonb_helper(dialect: str)` factory** that returns the correct accessor callable so every place that needs `custom_data` path access calls the same function (avoids drift between tests and production).

#### 4.4.3 `resolve_group_contacts`

```python
async def resolve_group_contacts(db, group, registry, *, page=1, page_size=50):
    if group.type == "static":
        q = (
            select(Contact)
            .join(GroupMember, and_(GroupMember.group_id == group.id, GroupMember.contact_id == Contact.id))
            .where(Contact.is_deleted == False)
        )
    else:  # smart
        registry_cache = await build_field_registry(db, entity=group.entity)
        q = await build_contact_query(group.criteria, registry_cache, db)
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar_one()
    rows = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return total, rows
```

#### 4.4.4 `populate_static_group`

```python
async def populate_static_group(db, group, criteria, registry, *, mode="append", actor_id=None):
    assert group.type == "static"
    id_q = await build_contact_query(criteria, registry, db)
    id_q = id_q.with_only_columns(Contact.id)
    ids = list((await db.execute(id_q)).scalars().all())
    if mode == "replace":
        await db.execute(delete(GroupMember).where(GroupMember.group_id == group.id))
    # Dialect-aware bulk insert with ON CONFLICT DO NOTHING
    dialect = db.bind.dialect.name
    rows = [{"group_id": group.id, "contact_id": cid, "added_by_id": actor_id, "added_at": utc_now()} for cid in ids]
    if rows:
        if dialect == "postgresql":
            stmt = insert(GroupMember).values(rows).on_conflict_do_nothing()
        else:  # sqlite
            stmt = insert(GroupMember).prefix_with("OR IGNORE").values(rows)
        await db.execute(stmt)
    await db.commit()
    # Count added rows
    count_after = (await db.execute(select(func.count()).where(GroupMember.group_id == group.id))).scalar_one()
    return {"added": len(ids), "total": count_after}
```

Note: the `ON CONFLICT DO NOTHING` / `OR IGNORE` dialect pattern mirrors S05's bulk participants implementation. If S05 hoists a shared helper to `app/utils/db_helpers.py`, use it; otherwise maintain the branch here and flag for consolidation (§10 item 8).

### 4.5 Pydantic schemas (`backend/app/schemas.py`)

Add the following, following existing `model_config = ConfigDict(from_attributes=True)` style:

```python
# Criteria tree (recursive via model_validator)
class CriteriaGroupNode(BaseModel):
    op: Literal["AND", "OR"]
    rules: list["CriteriaNode"]  # forward ref

class CriteriaConditionNode(BaseModel):
    field: str
    operator: str
    value: Any = None

CriteriaNode = Union[CriteriaGroupNode, CriteriaConditionNode]

# The compiler validates field/operator at runtime; Pydantic only validates structure.

class ContactSearchRequest(BaseModel):
    criteria: CriteriaGroupNode
    page: int = 1
    page_size: int = 20
    order_by: Optional[str] = None
    order_dir: Literal["asc", "desc"] = "asc"
    include_deleted: bool = False

class RunOptions(BaseModel):
    page: int = 1
    page_size: int = 20
    order_by: Optional[str] = None
    order_dir: Literal["asc", "desc"] = "asc"

class FieldSpecResponse(BaseModel):
    name: str
    label: str
    data_type: str
    ops: list[str]
    options: Optional[list[dict]] = None
    is_custom: bool = False
    is_derived: bool = False

class FieldRegistryResponse(BaseModel):
    entity: str
    fields: list[FieldSpecResponse]

class SavedSearchCreate(BaseModel):
    name: str
    entity: str = "contact"
    criteria: CriteriaGroupNode

class SavedSearchUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[CriteriaGroupNode] = None

class SavedSearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    owner_id: int
    entity: str
    criteria: dict
    created_at: datetime
    updated_at: datetime

class GroupCreate(BaseModel):
    name: str
    type: Literal["static", "smart"] = "static"
    entity: str = "contact"
    criteria: Optional[CriteriaGroupNode] = None
    description: Optional[str] = None

class GroupUpdate(BaseModel):
    name: Optional[str] = None
    criteria: Optional[CriteriaGroupNode] = None  # smart groups only; admin-only mutation
    description: Optional[str] = None

class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    type: str
    entity: str
    criteria: Optional[dict]
    description: Optional[str]
    created_by_id: Optional[int]
    member_count: Optional[int] = None  # None when ?with_counts=false
    created_at: datetime
    updated_at: datetime

class AddMembersRequest(BaseModel):
    contact_ids: list[int]
    @field_validator("contact_ids")
    @classmethod
    def validate_size(cls, v):
        if not v:
            raise ValueError("contact_ids must not be empty")
        if len(v) > 500:
            raise ValueError("contact_ids may not exceed 500 per request")
        return v

class PopulateRequest(BaseModel):
    criteria: Optional[CriteriaGroupNode] = None
    saved_search_id: Optional[int] = None
    mode: Literal["replace", "append"] = "append"
    @model_validator(mode="after")
    def one_source(self):
        if (self.criteria is None) == (self.saved_search_id is None):
            raise ValueError("Provide exactly one of criteria or saved_search_id")
        return self

class PromoteRequest(BaseModel):
    group_name: str
    description: Optional[str] = None
```

Reuse the `ContactResponse` (or `ContactListItem`) defined in S03 for the items in `PaginatedContactResponse`. If S03 named it `ContactListItem`, import that. S09 defines `PaginatedContactResponse(BaseModel)` with `total: int, page: int, page_size: int, items: list[ContactListItem]` — mirroring the `PaginatedTaskResponse` shape at `schemas.py:145-149`.

### 4.6 Router implementation details (`backend/app/routers/search.py`)

Key handler logic (summary; full implementation follows from §4.1/§4.3/§4.4):

- **GET `/search/fields`**: call `build_field_registry(db, entity=query_entity)`, convert to `list[FieldSpecResponse]`, return.
- **POST `/search/contacts`**: call `build_field_registry`, then `build_contact_query(req.criteria, registry, db, include_deleted=req.include_deleted, ...)`, paginate (`count subquery + offset/limit`), return `PaginatedContactResponse`.
- **Saved-search ownership**: helper `_get_saved_search_or_403(db, id, user)` — fetches, returns if `owner_id == user.sub` or `user.role == "admin"`, else 404 (not 403 — do not leak existence). Admin's `?all=true` on list: `if query_all and user.role != "admin": raise 403`.
- **Smart-group criteria edit**: in `PUT /groups/{id}`, if `body.criteria is not None and group.type == "smart" and user.role != "admin"` → 403.
- **Type immutability**: in `PUT /groups/{id}`, if `body.type` (not present in `GroupUpdate`) is somehow provided (ignore it); document that `type` is immutable post-creation. If implementation needs explicit guard: check `group.type != body.type` → 400.
- **member_count for list**: default `with_counts=True` query param. For static groups: `COUNT(group_members)` via a subquery. For smart groups: a `SELECT count(*) FROM (build_contact_query(group.criteria)) AS sq`. If `with_counts=false` → set `member_count=None` in response (for large group lists where count is expensive).
- **Audit logging**: write `audit_log` rows (via `app/services/audit.py::record(...)`, the shared helper from S02) for: `saved_search.create`, `saved_search.update`, `saved_search.delete`, `group.create`, `group.update`, `group.delete`, `group.populate` (with `before`/`after` counts). Use `actor_id = int(user["sub"])`.

### 4.7 File-by-file change list

| Action | File | What changes |
|---|---|---|
| **CREATE** | `backend/app/routers/search.py` | All endpoints from §4.1 |
| **CREATE** | `backend/app/services/search_service.py` | `compile_criteria`, `build_contact_query`, `resolve_group_contacts`, `populate_static_group`, `InvalidCriteria`, depth/size guards, dialect helpers |
| **CREATE** | `backend/app/services/search_fields.py` | `FieldSpec`, `CORE_CONTACT_FIELDS`, `DERIVED_FIELDS`, operator tables, `build_field_registry` |
| **MODIFY** | `backend/app/models.py` | Add `SavedSearch`, `Group`, `GroupMember` classes (§3.5) |
| **MODIFY** | `backend/app/schemas.py` | Add all schemas from §4.5 |
| **MODIFY** | `backend/app/main.py` | `from app.routers import search`; `app.include_router(search.router, dependencies=[Depends(check_setup_complete)])` after `members.router` |
| **CREATE** | `backend/alembic/versions/<rev>_s09_search_groups.py` | Migration from §3.4 |

No CiviCRM symbols are touched. The compiler operates on the `Contact` model (post-S01 rename from `CiviCRMMember`). If S01 has not landed, `Contact` does not exist — this is caught at import time in CI.

---

## 5. Frontend

### 5.1 Pages, routes, components

All new routes gated by `ProtectedRoute` (any authenticated volunteer+). No `AdminRoute` — role distinctions are implemented inside each component based on `useAuth().user.role`.

Add to `frontend/src/App.tsx` `<Routes>` (following the existing flat-route pattern at `App.tsx:50-65`):

```tsx
<Route path="/contacts/search" element={<ProtectedRoute><SearchPage /></ProtectedRoute>} />
<Route path="/searches" element={<ProtectedRoute><SavedSearchesPage /></ProtectedRoute>} />
<Route path="/groups" element={<ProtectedRoute><GroupsPage /></ProtectedRoute>} />
<Route path="/groups/:id" element={<ProtectedRoute><GroupDetailPage /></ProtectedRoute>} />
```

Navigation: add a "Search & Groups" link to the `BottomNav` admin "More" sheet (`frontend/src/components/layout/BottomNav.tsx`) for now; link to `/contacts/search` also appears as an "Advanced search" button on the S03 `ContactsPage`. Full nav placement is a S20 concern.

#### New files

**`frontend/src/services/search.ts`** — typed API wrapper using the shared `api` axios client (`frontend/src/services/api.ts`):

```ts
export const searchApi = {
  getFields: (entity = "contact") =>
    api.get<FieldRegistryResponse>("/search/fields", { params: { entity } }),
  searchContacts: (req: ContactSearchRequest) =>
    api.post<PaginatedContactResponse>("/search/contacts", req),
  listSavedSearches: (all = false) =>
    api.get<SavedSearch[]>("/search/saved", { params: { all } }),
  createSavedSearch: (body: SavedSearchCreate) =>
    api.post<SavedSearch>("/search/saved", body),
  getSavedSearch: (id: number) =>
    api.get<SavedSearch>(`/search/saved/${id}`),
  updateSavedSearch: (id: number, body: SavedSearchUpdate) =>
    api.put<SavedSearch>(`/search/saved/${id}`, body),
  deleteSavedSearch: (id: number) =>
    api.delete(`/search/saved/${id}`),
  runSavedSearch: (id: number, opts: RunOptions) =>
    api.post<PaginatedContactResponse>(`/search/saved/${id}/run`, opts),
  promoteSavedSearch: (id: number, body: PromoteRequest) =>
    api.post<GroupResponse>(`/search/saved/${id}/promote`, body),
  listGroups: (params?: { type?: string; entity?: string; with_counts?: boolean }) =>
    api.get<GroupResponse[]>("/groups", { params }),
  createGroup: (body: GroupCreate) =>
    api.post<GroupResponse>("/groups", body),
  getGroup: (id: number) =>
    api.get<GroupResponse>(`/groups/${id}`),
  updateGroup: (id: number, body: GroupUpdate) =>
    api.put<GroupResponse>(`/groups/${id}`, body),
  deleteGroup: (id: number) =>
    api.delete(`/groups/${id}`),
  getGroupMembers: (id: number, page: number, pageSize: number) =>
    api.get<PaginatedContactResponse>(`/groups/${id}/members`, { params: { page, page_size: pageSize } }),
  addGroupMembers: (id: number, contactIds: number[]) =>
    api.post<{added: number; skipped: number}>(`/groups/${id}/members`, { contact_ids: contactIds }),
  removeGroupMember: (id: number, contactId: number) =>
    api.delete(`/groups/${id}/members/${contactId}`),
  populateGroup: (id: number, body: PopulateRequest) =>
    api.post<{added: number; total: number}>(`/groups/${id}/populate`, body),
};
```

**`frontend/src/types/search.ts`** — TS types mirroring the Pydantic schemas:

```ts
export type CriteriaGroupNode = { op: "AND" | "OR"; rules: CriteriaNode[] };
export type CriteriaConditionNode = { field: string; operator: string; value?: unknown };
export type CriteriaNode = CriteriaGroupNode | CriteriaConditionNode;

export interface FieldSpec {
  name: string;
  label: string;
  data_type: string;
  ops: string[];
  options?: { value: string; label: string }[];
  is_custom: boolean;
  is_derived: boolean;
}

export interface FieldRegistryResponse {
  entity: string;
  fields: FieldSpec[];
}

export interface SavedSearch {
  id: number;
  name: string;
  owner_id: number;
  entity: string;
  criteria: CriteriaGroupNode;
  created_at: string;
  updated_at: string;
}

export interface GroupResponse {
  id: number;
  name: string;
  type: "static" | "smart";
  entity: string;
  criteria: CriteriaGroupNode | null;
  description: string | null;
  created_by_id: number | null;
  member_count: number | null;
  created_at: string;
  updated_at: string;
}
```

**`frontend/src/components/search/FilterBuilder.tsx`** — the core multi-criteria UI:

- Props: `criteria: CriteriaGroupNode`, `onChange: (c: CriteriaGroupNode) => void`, `registry: FieldSpec[]`, `readonly?: boolean`.
- Renders a list of condition rows. Each row: `[FieldPicker] [OperatorSelect] [ValueInput]` + `[Remove button]` (hidden when `readonly`).
- `+ Add condition` button appends a new blank condition row to the current group's `rules`.
- `+ Add group` button appends a nested `CriteriaGroupNode` (one level deep; "Add group" is hidden when already at depth 1 to keep the UI clean).
- AND/OR toggle (`<ToggleGroup>` or `<select>`) for the top-level and each nested group.
- `ValueInput` is type-aware:
  - `text`/`textarea` → `<input type="text" />`.
  - `number`/`integer` → `<input type="number" />`.
  - `date` → `<input type="date" />` (produces ISO string).
  - `boolean` → `<select>` with `true`/`false`.
  - `select` / `multiselect` / `tier_enum` → `<select>` (single or `multiple`) sourced from `spec.options`.
  - `contact_reference` → opens the existing `MemberSearchModal` (from S03/S02 contact picker) returning a contact id.
  - `is_set` / `is_empty` operators → no value input rendered.
  - `between` → two date/number inputs.
- Mobile-first: condition row stacks vertically (field → op → value on separate lines) at `< sm`; inline at `sm:`.
- Tailwind tokens only: `bg-card`, `border-border`, `text-foreground`, `bg-primary`/`text-primary-foreground` for the Add buttons.

**`frontend/src/components/search/FieldPicker.tsx`** — searchable dropdown of registry fields:

- Groups options into "Core Fields", "Derived Fields" (tier/active/etc.), "Custom Fields" sections.
- Shows field label; `data_type` badge in muted text.
- On selection, emits the field name string.

**`frontend/src/components/search/OperatorSelect.tsx`** — a `<select>` rendered from `spec.ops`, with friendly labels (e.g. `eq` → "is", `contains` → "contains", `starts_with` → "starts with", `is_set` → "is set", `before` → "before", etc.).

**`frontend/src/pages/SearchPage.tsx`** — the Advanced Search view:

- Layout: top `<FilterBuilder />` (manages `criteria` in local `useState`), "Search" button → triggers `POST /search/contacts`, results `<DataTable>` (reuse S03 `DataTable` + `Pagination`).
- Right-side action bar (hidden from read-only view): "Save search" (opens a name input popover → `POST /search/saved`); "Save as smart group" (name input → `POST /search/saved` then `POST /search/saved/{id}/promote`, or skip the intermediate step and call `POST /groups` directly with `type="smart"` and the current criteria).
- TanStack Query key: `["search","contacts", criteriaHash, page, pageSize]` where `criteriaHash = JSON.stringify(criteria)` (stable if criteria object is stable — use `useMemo`).
- Loading: `LoadingState` from `frontend/src/components/ui/StateViews.tsx`.
- Empty result: `EmptyState` ("No contacts match these filters." + "Clear filters" button that resets criteria to `{op:"AND",rules:[]}`).
- Error: `ErrorState` + `toast.error(err.response?.data?.detail || "Search failed")`.

**`frontend/src/pages/SavedSearchesPage.tsx`** — list / manage saved searches:

- `useQuery(["saved-searches", {all}])` → `GET /search/saved`.
- Cards: name, entity, last-updated, "Run" → navigates to `/contacts/search?saved=<id>` (or opens inline), "Edit criteria" → opens `FilterBuilder` in a modal with the saved criteria pre-loaded, "Promote to group" (opens name dialog → `POST /search/saved/{id}/promote`), "Delete" → `ConfirmDialog` → `DELETE`.
- Admin toggle "Show all users' searches" (shows when `isAdmin`).
- Only owner or admin sees Edit/Delete; volunteer sees their own.

**`frontend/src/pages/GroupsPage.tsx`** — list groups with type badge:

- `useQuery(["groups", {type, entity}])` → `GET /groups`.
- Table (S03 `DataTable`): name, type badge (`static` = `bg-blue-100/text-blue-700`-equivalent in token terms = `bg-accent text-accent-foreground` dark-compatible; `smart` = `bg-primary/10 text-primary`), member_count, created_by.
- Row click → `/groups/:id`.
- "New group" button (volunteer+) → inline drawer or modal with type toggle + name + description + (for smart: `FilterBuilder`).
- Admin sees all groups; volunteer sees all groups (groups are shared) but can only edit/delete groups they created.

**`frontend/src/pages/GroupDetailPage.tsx`** — group profile + members:

- Header: group name, type badge, description, member_count, "Edit" / "Delete" (creator or admin).
- For **static** groups: paginated `DataTable` of members (`GET /groups/{id}/members`), "Add members" (contact search modal → `POST /groups/{id}/members`), "Remove" per row, "Populate from criteria" (opens `FilterBuilder` → `POST /groups/{id}/populate`).
- For **smart** groups: paginated live-resolved member list (`GET /groups/{id}/members`), read-only criteria display (rendered as a `FilterBuilder` with `readonly=true`), "Edit criteria" (admin only → opens `FilterBuilder` editor → `PUT /groups/{id}`).
- Loading/empty/error states as per §5.4.

### 5.2 TanStack Query keys

```ts
// Registry (rarely changes; 5 min stale)
["search", "fields", entity]                    // staleTime: 5 * 60 * 1000

// Ad-hoc search results
["search", "contacts", criteriaHash, page, pageSize, orderBy, orderDir]

// Saved searches
["saved-searches"]                              // list (own or all)
["saved-search", id]                            // single

// Groups
["groups", { type, entity }]                    // list
["group", id]                                   // single
["group", id, "members", page, pageSize]        // members (paginated)
```

Mutations invalidate:
- Save / update / delete saved search → `["saved-searches"]` + `["saved-search", id]`.
- Promote → `["groups", { type: "smart", entity: "contact" }]` + `["groups", {}]`.
- Create / update / delete group → `["groups"]` + `["group", id]`.
- Add / remove member → `["group", id, "members"]` + `["group", id]` (to refresh `member_count`).
- Populate group → same as above.

No new Zustand stores. Criteria builder state is local `useState<CriteriaGroupNode>` inside `SearchPage` / editor modals. Auth remains in the existing `authStore` (`frontend/src/store/authStore.ts`).

### 5.3 Role gating (frontend)

Derive from `useAuthStore((s) => s.user?.role)`:

| Control | Visibility rule |
|---|---|
| "Save search" button | `role === "volunteer" \|\| role === "admin"` |
| "Edit" / "Delete" on saved search | `search.owner_id === currentUser.id \|\| role === "admin"` |
| "Create group" button | `role === "volunteer" \|\| role === "admin"` |
| "Edit" / "Delete" group | `group.created_by_id === currentUser.id \|\| role === "admin"` |
| "Edit criteria" on smart group | `role === "admin"` only |
| "Add members" / "Remove member" / "Populate" | `role === "volunteer" \|\| role === "admin"` |

The backend enforces these same rules — the frontend gating is UX only (never rely solely on frontend for security).

### 5.4 UX states

- **Loading** → `<LoadingState />` from `frontend/src/components/ui/StateViews.tsx`.
- **Empty search result** → `<EmptyState />` with "No contacts match these filters." + "Clear filters" button.
- **Empty groups list** → `<EmptyState />` with "No groups yet. Create one to organize your contacts."
- **Empty static group members** → `<EmptyState />` with "No members. Add some or populate from a search."
- **Error** → `toast.error(err.response?.data?.detail || "Something went wrong")` via sonner (CLAUDE.md / AGENTS.md pattern). Field-level validation errors from the backend (422 `InvalidCriteria`) surface as inline banners next to the offending condition row plus a toast.
- **Destructive actions** (delete saved search, delete group, remove member from group, replace-mode populate) → `<ConfirmDialog />` (`frontend/src/components/ui/ConfirmDialog.tsx`) before executing. Never `confirm()` (AGENTS.md).
- **Tokens** (per AGENTS.md): `bg-card`, `text-foreground`, `bg-background`, `border-border`, `bg-primary`, `text-primary-foreground`, `bg-muted`, `text-muted-foreground`. No hardcoded hex or HSL. Dark mode works automatically via token classes.
- **Mobile-first** (375px baseline per AGENTS.md): condition rows stack vertically; results table becomes cards (`DataTable` responsive behavior from S03). Desktop: condition rows inline, results dense table.

---

## 6. Migration / data

None. All three tables (`saved_searches`, `groups`, `group_members`) are new and empty on deploy. No backfill.

CiviCRM smart groups are **not** migrated: the owner re-creates the handful they use through the new builder; the underlying contact data they filter on is migrated by S06. This is a locked simplification (smart-group definitions are trivially rebuilable; migrating their stale CiviCRM SQL criteria is not worthwhile).

---

## 7. Acceptance criteria

1. `GET /search/fields?entity=contact` returns at minimum 14 whitelisted core fields + 7 derived snapshot fields + every active `custom_field_def` (entity=`contact`) as `custom.<name>`, each with a non-empty `ops` array; select/multiselect/tier_enum fields include `options`.

2. `POST /search/contacts` with `{"op":"AND","rules":[{"field":"last_name","operator":"contains","value":"Cruz"},{"field":"contact_type","operator":"eq","value":"individual"}]}` returns only matching non-deleted contacts, paginated, with `total` accurately reflecting the full match count (not just the page).

3. A criteria node with `field: "password_hash"` (or any non-whitelisted / SQL-injection string) returns HTTP 400/422 `InvalidCriteria` and **no** query involving that identifier is emitted (verified by asserting the field name does not appear anywhere in emitted SQL strings in unit tests).

4. An OR group nested inside an AND group resolves with correct boolean precedence — verified against a fixture with known contact membership where the nested OR must succeed AND the outer AND condition must also hold.

5. `field: "tier"` with `operator: "in"`, `value: ["tier1","tier2"]` returns only contacts whose `contacts.tier` is in that set (or no results if S23 has not yet run — `NULL` values correctly excluded). Verified on SQLite test path.

6. `field: "is_active"` with `operator: "eq"`, `value: true` returns only contacts whose `contacts.is_active == True`. Derived field columns are treated as ordinary columns in the compiler.

7. `field: "custom.<select_field>"` with `operator: "eq"` and `field: "custom.<multiselect_field>"` with `operator: "contains_any"` each return the correct contacts on both SQLite (JSON1) and Postgres JSONB paths (asserted via dialect-branching unit test and Postgres CI).

8. Saved-search CRUD is owner-scoped: a volunteer cannot GET/PUT/DELETE another user's saved search (receives 404, not 403, to avoid leaking existence); admin with `?all=true` sees all saved searches.

9. `POST /search/saved/{id}/run` returns the same result set (same `total`, same contact ids) as running its stored `criteria` via `POST /search/contacts` directly.

10. `POST /search/saved/{id}/promote` creates a `smart` group with `criteria` equal to the saved search's `criteria` and `type="smart"`; calling again with the same `group_name` returns 409.

11. Static group — `POST /groups/{id}/members` with a list inserts only unique memberships (re-POSTing an existing contact_id returns `skipped: 1, added: 0`); `DELETE /groups/{id}/members/{contact_id}` removes exactly that member; `POST /groups/{id}/members` on a smart group returns 400.

12. `POST /groups/{id}/populate` with `mode: replace` exactly snapshots the criteria-matched contact ids (replacing any prior members); `mode: append` adds new ids without removing existing ones.

13. `GET /groups/{id}/members` returns the frozen snapshot for static groups and the **live-resolved** set for smart groups — changing a contact's field so it newly matches the smart group's criteria makes it appear in subsequent member GET without any membership write.

14. Soft-deleted contacts (`is_deleted=True`) never appear in any search or group-member result unless `include_deleted=True` is explicitly sent on the ad-hoc search endpoint.

15. A criteria tree exceeding 100 total condition nodes or nesting depth > 10 returns HTTP 400 with detail "criteria too large".

16. Frontend: `npm run build` + `npm run lint` pass; `FilterBuilder` correctly emits a `CriteriaGroupNode` that round-trips through `POST /search/contacts` without validation errors.

17. Frontend: a volunteer sees the "Save search" and "Create group" buttons; the "Edit criteria" button on a smart group is hidden for volunteers and visible only to admin.

18. `resolve_group_contacts` (the canonical service function) is importable from `app.services.search_service` and returns `(total, rows)` for both static and smart groups — verified by the backend test suite without going through HTTP.

---

## 8. Test plan

### Backend pytest (`backend/tests/`)

Fixtures (add to `conftest.py` or a new `fixtures/search.py`):
- `contact_fixture`: create 10 contacts with varied names, tiers, custom field values, one soft-deleted.
- `custom_field_fixture`: one `select` field (`ministry`) and one `multiselect` field (`community`), entity=`contact`, via the S02 admin endpoint or direct DB insert.
- `saved_search_fixture`: one saved search owned by a volunteer user.
- `group_fixture`: one static group with 3 members, one smart group with a `last_name contains "cruz"` criteria.

All tests run against `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test` (CLAUDE.md pattern).

**`backend/tests/test_search_fields.py`**
- `test_fields_registry_contains_core_fields` — asserts all 14 core + 7 derived fields present, each with correct `ops`.
- `test_fields_registry_contains_custom_field` — with custom_field_fixture seeded, asserts `custom.ministry` appears with `select` data_type and the correct options.
- `test_fields_entity_default_contact` — `?entity=contact` returns `entity="contact"` in response.

**`backend/tests/test_search_compiler.py`** (unit tests — no HTTP, call `compile_criteria` directly)
- `test_compiler_simple_and` — `{op:AND,rules:[{field:last_name,operator:eq,value:Cruz}]}` produces a WHERE containing a parameterized bind, not the literal string `Cruz` in the SQL string.
- `test_compiler_nested_or_in_and` — nested OR inside AND produces correct boolean precedence (verified by executing against fixture data).
- `test_compiler_rejects_unknown_field` — `{field:"password_hash",...}` → raises `InvalidCriteria`.
- `test_compiler_rejects_bad_operator` — `{field:"last_name",operator:"gt",...}` → raises `InvalidCriteria` (gt not in text ops).
- `test_compiler_like_escaping` — value `"50%_off"` with `contains` produces a LIKE pattern `%50\%\_off%` with escape `\\`.
- `test_compiler_custom_select_eq` — `{field:"custom.ministry",operator:eq,value:"IT"}` on SQLite produces a JSON_EXTRACT expression (not JSONB); asserts correct results against fixture.
- `test_compiler_custom_multiselect_contains_any` — `{field:"custom.community",operator:contains_any,value:["YA","YP"]}` returns contacts whose custom_data `community` array includes either value.
- `test_compiler_derived_tier_in` — `{field:"tier",operator:in,value:["tier1","tier2"]}` correctly filters on the `contacts.tier` column.
- `test_compiler_derived_is_active` — `{field:"is_active",operator:eq,value:true}` matches contacts where `is_active=True`.
- `test_compiler_depth_guard` — tree with 11 nesting levels → `InvalidCriteria`.
- `test_compiler_size_guard` — tree with 101 condition nodes → `InvalidCriteria`.
- `test_compiler_in_requires_nonempty` — `{field:last_name,operator:in,value:[]}` → `InvalidCriteria`.
- `test_compiler_between_date` — `{field:birth_date,operator:between,value:["1990-01-01","2000-12-31"]}` produces correct `BETWEEN` expression.

**`backend/tests/test_search_endpoint.py`** (HTTP via test client)
- `test_search_requires_auth` — no token → 401.
- `test_search_simple_results_paginated` — runs a simple criteria, checks `total` and `items` length match.
- `test_search_excludes_soft_deleted` — soft-deleted contact in fixture does not appear.
- `test_search_include_deleted_flag` — `include_deleted=true` brings the soft-deleted contact back.
- `test_search_pagination_total_accurate` — page 1 of 5-per-page search returns `total` = all matches (not just page count).

**`backend/tests/test_saved_searches.py`**
- `test_saved_search_crud_owner_scoped` — volunteer A creates, volunteer B cannot GET/PUT/DELETE it (404); admin can.
- `test_saved_search_run_matches_adhoc` — run-endpoint result equals ad-hoc search with same criteria.
- `test_promote_creates_smart_group` — promote returns a `GroupResponse` with `type="smart"` and same `criteria`.
- `test_promote_duplicate_name_409` — second promote with same group_name → 409.
- `test_saved_search_validates_criteria_on_save` — invalid criteria (unknown field) → 422, search not saved.

**`backend/tests/test_groups.py`**
- `test_static_group_add_members_dedup` — add {1,2,3}, then add {2,4}: response `added=2, skipped=1`; member count = 4.
- `test_static_group_remove_member` — remove contact 2; member count = 3.
- `test_smart_group_rejects_member_write` — POST to `/groups/{smart_id}/members` → 400.
- `test_smart_group_rejects_member_delete` → 400.
- `test_group_populate_replace` — populate `mode=replace` with criteria matching 2 contacts; then change criteria to match 0 of the original 2; re-populate with replace: member count = 0 of old, correct count of new.
- `test_group_populate_append` — append adds without removing existing members.
- `test_smart_group_live_resolution` — smart group has `last_name contains "Rivera"`; GET members; add a new contact "Rivera"; GET members again → new contact appears.
- `test_group_type_immutable` — PUT with attempted type change → 400.
- `test_smart_group_criteria_edit_admin_only` — volunteer editing smart group criteria → 403; admin can.
- `test_group_delete_cascades_members` — delete static group → `group_members` rows gone.
- `test_resolve_group_contacts_importable` — `from app.services.search_service import resolve_group_contacts`; call it directly with both static and smart groups, verify `(total, rows)` tuple.

### Frontend vitest (`frontend/src/`)

**`frontend/src/components/search/__tests__/FilterBuilder.test.tsx`**
- Renders with empty criteria → one blank condition row.
- Changing the field picker updates the operator dropdown options to match the new field's `ops`.
- Changing the operator to `is_set` hides the value input.
- Selecting a `select`-type field with options renders a `<select>` in the value column.
- `+ Add condition` button appends a new condition row; `CriteriaGroupNode.rules.length` increases.
- `+ Add group` button appends a nested `CriteriaGroupNode`.
- AND/OR toggle updates `op` in the emitted criteria.
- `readonly=true` hides all mutating controls.

**`frontend/src/components/search/__tests__/FieldPicker.test.tsx`**
- Groups fields into "Core Fields", "Derived Fields", "Custom Fields" sections from a mocked registry.
- Emits the correct field `name` on selection.

**`frontend/src/services/__tests__/search.service.test.ts`**
- `searchApi.searchContacts` POSTs to `/search/contacts` with the criteria body and returns typed `PaginatedContactResponse` (mock axios).
- `searchApi.getFields` GETs `/search/fields` with `entity=contact`.

**`frontend/src/pages/__tests__/GroupsPage.test.tsx`**
- Renders group list from mocked API; static and smart badge displayed.
- "Create group" button hidden when not rendered (viewer restriction is backend-enforced; frontend hides based on role check from `useAuth` mock).
- "Edit criteria" button absent for non-admin on a smart group row.

**Empty-state snapshot test** — `SearchPage` with an empty result renders `EmptyState` with the "Clear filters" action visible.

---

## 9. Rollout / rollback / risks

### Rollout
This sprint is **additive only**: new router, three new tables, new services. Safe to deploy independently.

1. Deploy backend with the S09 Alembic migration (creates `saved_searches`, `groups`, `group_members`; no existing tables altered).
2. Deploy frontend (new routes/components; existing routes unchanged).
3. No feature flag required; the new routes are opt-in (users must navigate to them).
4. If S02 custom fields are not yet deployed, the registry returns only core + derived fields; custom-field rows in the builder are simply absent. No flag needed.

### Rollback
1. Revert frontend deploy (route/component removal).
2. Drop the router include in `main.py`.
3. Run Alembic downgrade: drops `group_members`, `groups`, `saved_searches` (all empty at early deployment; no data loss concern).

### Risks

**1. SQL injection via `field` parameter** — mitigated by the field registry being the **sole** identifier source. The only string that ever reaches a column expression is a key in `CORE_CONTACT_FIELDS` or `DERIVED_FIELDS` (hardcoded) or a `custom_field_def.name` from the database (which was admin-validated when created). An attacker-supplied `field` not in the registry raises `InvalidCriteria` before any query is constructed. **This is the #1 review priority.** Acceptance criterion #3 and `test_compiler_rejects_unknown_field` enforce it.

**2. JSONB dialect drift** — Postgres `->>`/`@>` vs SQLite JSON1 `json_extract`/`json_each`. Mitigated by the `_jsonb_get_scalar` helper that branches on `dialect`, a unit test asserting correct expressions on SQLite, and Postgres CI assertions. Every new custom-field operator case must add a corresponding SQLite branch — this must be audited in code review.

**3. Smart-group live-resolution cost at scale** — at 1,440 contacts a live `SELECT count(*) FROM (compiled query)` per group in the list view is acceptable (~milliseconds). If the group list grows significantly or criteria become complex, add a `?with_counts=false` escape hatch (already in the spec) and document for S16/S17 to consider caching via S16's APScheduler.

**4. RBAC collision with S15** — S15 adds the `viewer` role and `require_viewer` guard. S09 ships `require_volunteer` on all endpoints. When S15 lands, S15's author must review S09's endpoints and decide whether viewers can access field-registry or group-metadata endpoints (per locked decision: viewers cannot see individual contact rows, so `/search/contacts` and `/groups/{id}/members` stay volunteer+; `/search/fields` and `/groups` metadata-only could be relaxed). The master doc must record this as a S15 TODO.

**5. `Contact` model rename dependency** — S09 compiles against the `Contact` model (post-S01 rename from `CiviCRMMember`). If S01 has not landed, the import fails at startup and is caught by CI immediately. This is an upstream Phase-A sprint and will be far ahead of the Phase-D S09.

**6. `group_members.contact_id` FK in S11 reassignment** — if S11 ships before S09, it will not have this FK in its manifest. The `test_manifest_covers_all_contact_fks` reflection test in S11 will fail when S09 migrations run. Resolution: S09 must be mentioned in S11's test manifest; the master should note this sequencing dependency.

---

## 10. Open questions & pending owner artifacts

**Q1 (Master must resolve — RBAC):** Confirm: viewers should have **zero** access to `/search/*` and `/groups/*` contact-row endpoints. The spec assumes so (volunteer+ on all endpoints). If dashboard/reporting widgets (S14) need to issue a group-member count call, those should go through a separate aggregate endpoint in S14, not through S09's member-row endpoints. **Ruling needed in 00-MASTER.md.**

**Q2 (Master must resolve — Promote semantics):** Is a promoted saved search a **one-time copy** (changes to the saved search do not affect the group) or a **linked reference** (the group's criteria tracks the search)? This spec assumes one-time copy (simpler; matches CiviCRM behavior). Confirm with owner.

**Q3 (Owner):** Maximum members per static group and maximum criteria complexity (condition node count) that should be exposed in the UI cap. Spec implements: 200-element `in` lists, depth ≤ 2 in UI, depth ≤ 10 in compiler. Confirm acceptable.

**Q4 (Implementation — S23 timing):** At S09 implementation time, confirm whether S23's nightly job has run at least once on the dev/test environment so that derived-field filter tests can use non-null values. If not, seed `contacts.tier` values directly in test fixtures for the derived-field tests.

---

### Cross-sprint dependencies / shared-model touchpoints / inconsistencies for the master to reconcile

1. **S09 depends on S02 (custom fields) as a co-requisite** — the charter lists only S03. 00-MASTER.md §3 build-order graph should add the `S02 → S09` edge explicitly, or accept the core-field-only fallback at the field-registry level.

2. **S09 depends on S01's `Contact` rename** — `contacts` table / `Contact` model must exist. Phase A, so it lands first; listed here for the dependency graph completeness.

3. **S09 depends on S23's derived snapshot columns being present on `contacts`** — ruling C2 assigns these to S01 (schema) + S23 (writer). S09 reads them. Columns are nullable on day one; S09 must not break if they are all NULL.

4. **`group_members.contact_id` is a new FK to `contacts`** — S11 (Find & Merge Duplicates) MUST add it to the loser→survivor FK-reassignment manifest (ruling C4). De-duplicate against the composite PK. Flag in S11's `test_manifest_covers_all_contact_fks`.

5. **`resolve_group_contacts` / `build_contact_query` are the canonical contact-set resolver** — S12 (bulk-assign activities to a group), S14 (reporting segments by group/criteria), S17 (automation `contact_in_group` condition + smart-group audience), S18 (notification audience) must import from `app.services.search_service` rather than re-implementing filtering. 00-MASTER.md §5 "Reuse contracts" should record this.

6. **RBAC ownership conflict with S15** — `require_viewer` guard and `users.role` enum value `viewer` are defined by S15. S09 ships `require_volunteer`. When S15 lands, S15 must review S09's endpoint table and tighten/relax as appropriate. The locked decision (decisions.md Round 4: "viewer = reports/dashboard only, no individual contact records") implies `/search/contacts` and group-member endpoints stay at volunteer+. Record this as a S15 obligation in 00-MASTER.md.

7. **Shared `ON CONFLICT DO NOTHING` / `OR IGNORE` dialect helper** — S09's `populate_static_group` and S05's bulk-participant insert both implement the same Postgres/SQLite dialect branch. If S05 hoisted a shared helper to `app/utils/db_helpers.py`, S09 should use it. If not, S09 creates the pattern here and the master should flag S05 + S09 for consolidation in a tech-debt sprint.

8. **`saved_searches.entity` / `groups.entity` are forward-compatible discriminators** — only `contact` is wired this sprint. When event/activity search is added, `build_field_registry` simply receives a different `entity` argument. Flag so future sprints extend the registry rather than adding a parallel mechanism.

9. **`PaginatedContactResponse` reuses S03's `ContactListItem`** — if S03 named its response schema differently, S09's import must be adjusted. The master should pin the exact schema name once S03 is implemented (recommend `ContactListItem` for list/search contexts, `ContactDetailResponse` for the full profile — S03 spec §2 uses these names).
