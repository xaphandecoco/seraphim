# S02 — Dynamic Custom-Field Engine

**Phase:** A — Foundation · **Depends on:** S01 · **Effort:** L · **Status:** Not started

---

## 1. Goal & rationale

CiviCRM's irreplaceable value to Light North Caloocan is not its attendance counting (Seraphim already does that better with face-recognition) — it is the **admin-defined custom fields** that describe each contact: Barangay, PEPSOL pathway, Ministry, Community, people-link fields (Invited By, Consolidated By, Community/Ministry/Network/Lifegroup Leader), Followup Listing, Church Info, and more. To retire CiviCRM, Seraphim must own a runtime-configurable field engine so an admin can define field groups, fields, types, and option lists **without code changes or new migrations**, and so every downstream sprint (Contact CRUD S03, Migration ETL S06, Advanced Search S09, Reporting S14, Automation S17) reads field metadata from one authoritative source.

This sprint delivers that engine: two metadata tables (`custom_field_group`, `custom_field_def`), the JSONB `custom_data` storage column already owned and created by S01 on `contacts` (see MASTER ruling C7 — this sprint's inspector guard is a no-op), a validation/coercion service covering all eight data types (including `multiselect` and `contact_reference`), admin CRUD endpoints, a schema-metadata endpoint for form consumers, an admin UI at `/settings/custom-fields`, reusable React renderer components (`<CustomFieldRenderer>`, `<CustomFieldsSection>`), and idempotent seed data for the six church field groups.

Per the locked decision (decisions.md Round 3, civicrm.md §1), we do **not** replicate CiviCRM's EAV per-group physical tables (`civicrm_value_*`) or `option_group`/`option_value` indirection. At 1,440 contacts, a single JSONB column keyed by field `name` is faster, simpler, and re-runnable for migration. **No repeating multi-record sets** (locked decision, decisions.md Round 3).

This sprint defines the metadata layer and storage contract. It does **not** build the contact create/edit form (S03) — but ships the metadata endpoint and renderer building blocks S03 mounts directly.

MASTER consistency rulings that constrain this sprint (00-MASTER.md §8):
- **C3**: `audit_log` table is **created by S01**; `app/services/audit.py::record(...)` helper is **created by S02** and reused by all subsequent sprints.
- **C7**: `contacts.custom_data` is **created by S01**; S02's inspector guard is a no-op; S02 downgrade must **not** drop it.
- **C8**: `entity` values are **lowercase** — `contact|event|activity` everywhere (not `Contact|Event|Activity`).
- **C13**: `contact_reference` stored shape — single = JSON **integer**, multi = JSON **list[int]**. Must be numeric, not string.
- **C15**: The contact search endpoint is `GET /members?search=` (not `/contacts`), per router prefix ruling. `MemberSearchModal` calls `/members`.
- **C16**: Post-S03, `GET /members` returns a paginated shape `{items, total, page, page_size}`; S02's picker reads `.items`. Until S03 lands, the current bare-list shape from `members.py:18` is in effect — the picker is written to be forward-compatible (see §5.3).
- **C26**: Alembic `down_revision` values are **illustrative only**; at implementation time chain off the real `alembic heads` output.

---

## 2. Scope

### In scope

- Two new metadata tables: `custom_field_group` and `custom_field_def` with exact canonical columns and constraints.
- Inspector-guarded `add_column` for `contacts.custom_data` JSONB — is a **no-op** because S01 owns the column (C7); guard kept for safety so `alembic upgrade head` is idempotent regardless of S01 merge order.
- Inspector-guarded conditional `create_table` for `audit_log` — **no-op** because S01 owns the table (C3); guard kept for safety.
- **`app/services/audit.py`** — creates the `record(...)` helper (sole writer of `audit_log`; this is S02's primary ownership contribution for C3).
- All eight data types: `text`, `textarea`, `select`, `multiselect`, `date`, `number`, `checkbox`, `contact_reference`.
- `is_multi` semantics for `select` (multi-select mode) and `contact_reference` (multi-link mode). `multiselect` is a distinct `data_type` that forces `is_multi=True` automatically on save.
- Pure reusable validation/coercion service `app/services/custom_fields.py` — the single chokepoint called by S03 (contact write), S06 (migration ETL), and any future entity write. `contact_reference` values stored as JSON **numbers** per C13.
- Generic JSONB helper pattern (entity-parameterized) so `events.custom_data` / `activities.custom_data` can adopt it later without code duplication; storage column added on `contacts` only (per C23 — no `events.custom_data` in v1).
- Admin CRUD endpoints (`/custom-fields/groups`, `/custom-fields/defs`) and an authenticated `GET /custom-fields/schema` endpoint (returns form metadata; consumed by S03 and the admin live preview).
- `POST /custom-fields/validate` — stateless validate+coerce passthrough for S06 dry-run and admin preview.
- Admin UI page `/settings/custom-fields` (`AdminRoute`): group/field CRUD with live preview pane.
- Reusable `<CustomFieldRenderer>` and `<CustomFieldsSection>` React components (driven by schema; mounted by S03 in the contact form). These are the canonical shared primitives per MASTER §5.
- `contact_reference` picker reuses existing `MemberSearchModal` (`frontend/src/components/tasks/MemberSearchModal.tsx`), calling `GET /members?search=` (C15). Picker reads `.items` for forward-compatibility with S03's paginated response (C16).
- Seed data (idempotent, insert-if-missing) for the six church field groups with representative fields and placeholder option lists.
- Audit logging of admin metadata changes via `audit_log` rows (written through `app/services/audit.py::record`).

### Out of scope

- Contact create/edit form and contact detail page — **S03** (renderer components ship here but are mounted there).
- Custom fields on `events` and `activities` end-to-end UI — `events.custom_data` deferred per C23; only the service is made entity-generic now.
- GIN indexes on `custom_data` for search — **S09**.
- Migration of real CiviCRM custom-field values, option normalization (DEPARO/Deparo), people-link name resolution — **S06** (S02 provides the `validate_and_coerce` API that S06 calls).
- Repeating multi-record sets — explicitly excluded (locked decision).
- Field-level RBAC / per-field visibility by role — deferred.
- Conditional/dependent fields — deferred.
- `viewer` role — S15 adds it; until then the schema endpoint is behind `require_volunteer` (admin + volunteer). Note for S15 author: viewers must be able to call `GET /custom-fields/schema` to render the contact detail page read-only (per C20: viewers cannot call contact-row endpoints, but reading field metadata is metadata, not a contact row — confirm with S15).

---

## 3. Data model changes

### 3.1 New table — `custom_field_group`

A named set of fields attached to one entity type. Owner: S02.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | seq | app-minted |
| `name` | String(100) | no | — | machine name, snake_case; UNIQUE per `(entity, name)` |
| `label` | String(255) | no | — | display title shown to users |
| `entity` | String(20) | no | `'contact'` | **lowercase** `contact \| event \| activity` (C8) |
| `weight` | Integer | no | `0` | sort order among groups of the same entity |
| `is_active` | Boolean | no | `true` | soft-disable; hides from schema, does not delete |
| `created_at` | DateTime (naive UTC) | no | `utc_now()` | |
| `updated_at` | DateTime (naive UTC) | no | `utc_now()`, `onupdate=utc_now()` | |

Constraints and indexes:
- `UniqueConstraint("entity", "name", name="uq_custom_field_group_entity_name")`.
- `Index("ix_cfg_entity_active_weight", "entity", "is_active", "weight")` — schema-assembly hot path.

SQLAlchemy ORM (add to `backend/app/models.py`):

```python
class CustomFieldGroup(Base):
    __tablename__ = "custom_field_group"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="contact")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (
        UniqueConstraint("entity", "name", name="uq_custom_field_group_entity_name"),
        Index("ix_cfg_entity_active_weight", "entity", "is_active", "weight"),
    )
```

### 3.2 New table — `custom_field_def`

One field within a group. Owner: S02.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK (autoincrement) | no | seq | app-minted |
| `group_id` | Integer FK→`custom_field_group.id` `ON DELETE CASCADE` | no | — | |
| `name` | String(100) | no | — | machine name snake_case; UNIQUE within group; entity-scoped uniqueness enforced in service |
| `label` | String(255) | no | — | display label |
| `data_type` | String(20) | no | — | `text \| textarea \| select \| multiselect \| date \| number \| checkbox \| contact_reference` |
| `options` | `JSON().with_variant(JSONB,'postgresql')` | no | `[]` | list of `{value: str, label: str}` for select/multiselect; `[]` otherwise |
| `is_required` | Boolean | no | `false` | |
| `is_multi` | Boolean | no | `false` | true → stored value is a JSON array; auto-forced true for `multiselect` |
| `weight` | Integer | no | `0` | sort order within group |
| `is_active` | Boolean | no | `true` | soft-disable |
| `help_text` | String(500) | yes | `null` | optional inline hint rendered under the field |
| `created_at` | DateTime | no | `utc_now()` | |
| `updated_at` | DateTime | no | `utc_now()`, `onupdate=utc_now()` | |

Constraints and indexes:
- `UniqueConstraint("group_id", "name", name="uq_custom_field_def_group_name")`.
- `Index("ix_cfd_group_active_weight", "group_id", "is_active", "weight")`.
- FK `ON DELETE CASCADE` (both the Alembic migration and the ORM `ForeignKey(..., ondelete="CASCADE")`).

`data_type` vs `is_multi` semantics: `multiselect` is a distinct `data_type` value AND the service forces `is_multi=True` when saving it. `select` with `is_multi=True` is also valid (equivalent). `contact_reference` respects the admin's explicit `is_multi` toggle (single vs multi people-link).

SQLAlchemy ORM (add to `backend/app/models.py`):

```python
class CustomFieldDef(Base):
    __tablename__ = "custom_field_def"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("custom_field_group.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), nullable=False)
    options: Mapped[list] = mapped_column(JSONB, default=list)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_multi: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    help_text: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_custom_field_def_group_name"),
        Index("ix_cfd_group_active_weight", "group_id", "is_active", "weight"),
    )
```

Use the module-level `JSONB = JSON().with_variant(_PG_JSONB, "postgresql")` alias already present in `backend/app/models.py` (models.py:22).

### 3.3 Changed table — `contacts.custom_data` (inspector guard, no-op)

S01 owns this column (MASTER C7). S01's migration already adds `custom_data JSONB NOT NULL DEFAULT '{}'` to `contacts`. This migration adds it **only if absent** via an inspector guard, making the upgrade idempotent regardless of merge order:

```python
from sqlalchemy import inspect as sa_inspect, text as sa_text
bind = op.get_bind()
cols = [c["name"] for c in sa_inspect(bind).get_columns("contacts")]
if "custom_data" not in cols:
    op.add_column(
        "contacts",
        sa.Column(
            "custom_data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa_text("'{}'::jsonb"),
        ),
    )
```

The `Contact` ORM model in `models.py` must carry `custom_data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))` so `create_all` (test suite path) also populates it. Coordinate with S01 — if S01 adds the column to the ORM model, S02 must not duplicate it.

### 3.4 `audit_log` table (inspector guard, no-op)

S01 owns this table (MASTER C3). This migration creates it **only if the table does not already exist** (inspector guard on `get_table_names()`), for merge-order safety.

Canonical columns (defined by S01, consumed by S02+):

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `id` | Integer PK | no | seq | |
| `actor_id` | Integer FK→`users.id` `ON DELETE SET NULL` | yes | null | null = system action |
| `action` | String(50) | no | — | e.g. `custom_group.create` |
| `entity` | String(50) | no | — | e.g. `custom_field_group` |
| `entity_id` | Integer | yes | null | PK of the affected row |
| `before` | JSONB | yes | null | snapshot before change |
| `after` | JSONB | yes | null | snapshot after change |
| `at` | DateTime (naive UTC) | no | `utc_now()` | |

Indexes: `ix_audit_log_entity_entity_id` on `(entity, entity_id)`; `ix_audit_log_at` on `(at)`.

The `AuditLog` ORM class is **owned by S01** and lives in `models.py`. If S01 has not added it, S02 adds a guard-wrapped class definition; coordinate to avoid duplicate declaration.

### 3.5 Alembic migration plan

**One new migration file:** `backend/alembic/versions/<real_rev_id>_add_custom_field_engine.py`

`down_revision` = the S01 migration revision id. At implementation time, run `alembic heads` after S01 is committed to get the real ID. Do not hard-code a placeholder (C26). The last committed migration before S01 is `f3a4b5c6d7e8` (`f3a4b5c6d7e8_add_task_action_approval_unique.py`); S01 adds one revision; S02 chains off that.

**JSONB dialect handling in migration:** `sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")` for column definitions; `sa.text("'[]'::jsonb")` / `sa.text("'{}'::jsonb")` for server defaults. SQLite test path uses the JSON variant automatically.

**Forward ops (in order):**

```
1. op.create_table("custom_field_group", ...)
   - all columns, uq_custom_field_group_entity_name, ix_cfg_entity_active_weight
   - server defaults: is_active=sa.true(), entity='contact', weight=0
2. op.create_table("custom_field_def", ...)
   - FK group_id ondelete="CASCADE"
   - JSONB options server_default=sa.text("'[]'::jsonb")
   - uq_custom_field_def_group_name, ix_cfd_group_active_weight
3. Inspector guard: add contacts.custom_data if absent (§3.3)
4. Inspector guard: create audit_log if absent (§3.4)
5. seed_church_custom_fields(op.get_bind())  — idempotent
```

**Downgrade ops:**

```
1. op.drop_table("custom_field_def")   -- CASCADE removes rows
2. op.drop_table("custom_field_group")
-- DO NOT drop contacts.custom_data or audit_log (owned by S01)
```

Document asymmetric downgrade in the migration docstring.

---

## 4. Backend

### 4.1 Endpoints

New router: `backend/app/routers/custom_fields.py`, prefix `"/custom-fields"`, tag `"custom-fields"`.

Included in `backend/app/main.py` with `dependencies=[Depends(check_setup_complete)]` (same pattern as all other domain routers at `main.py:106–118`). Role deps use existing `require_admin` and `require_volunteer` from `backend/app/dependencies.py` (`dependencies.py:45–64`).

| METHOD | Path | Role | Request schema | Response schema | Notes |
|---|---|---|---|---|---|
| GET | `/custom-fields/schema` | volunteer | query: `entity: str = "contact"` | `CustomFieldSchemaResponse` | Active groups (weight-ordered) each with active defs (weight-ordered). Contract S03's form + renderer consume. Not admin-only — volunteers render contact forms. Returns 200 with empty `groups: []` if no active groups. |
| GET | `/custom-fields/groups` | admin | query: `entity?: str`, `include_inactive: bool = False` | `list[CustomFieldGroupResponse]` | Admin list; includes inactive when requested. Groups include nested `fields` list of defs. |
| POST | `/custom-fields/groups` | admin | `CustomFieldGroupCreate` | `CustomFieldGroupResponse` 201 | Creates group. Writes `audit_log` row (`action="custom_group.create"`). 409 on entity+name collision. |
| PATCH | `/custom-fields/groups/{group_id}` | admin | `CustomFieldGroupUpdate` | `CustomFieldGroupResponse` | Partial update of `label`, `weight`, `is_active`. `name` and `entity` are **immutable** after create (bare `name` is the JSONB key — changing it would orphan stored data). Audited. 404 if not found. |
| DELETE | `/custom-fields/groups/{group_id}` | admin | query: `hard: bool = False` | 204 | Default: soft-delete (`is_active=False` on group + all child defs). `?hard=True`: 409 with `affected_contacts` count if any contact has data under any child field name; else hard-deletes group (cascade removes defs). Audited. |
| GET | `/custom-fields/defs` | admin | query: `group_id?: int`, `entity?: str`, `include_inactive: bool = False` | `list[CustomFieldDefResponse]` | |
| POST | `/custom-fields/defs` | admin | `CustomFieldDefCreate` | `CustomFieldDefResponse` 201 | Validates `data_type`, option shape, name uniqueness scoped to entity (not just group). Normalizes: `multiselect` forces `is_multi=True`. Audited. |
| PATCH | `/custom-fields/defs/{def_id}` | admin | `CustomFieldDefUpdate` | `CustomFieldDefResponse` | `name` and `data_type` **immutable** after create. `label`, `options`, `is_required`, `is_multi`, `weight`, `is_active`, `help_text` are editable. Removing an option that contacts currently use: allowed (soft); PATCH response includes `affected_contacts: int` (count of contacts with a now-invalid stored value, computed by `count_contacts_with_field_data`). Audited. |
| DELETE | `/custom-fields/defs/{def_id}` | admin | query: `hard: bool = False` | 204 | Soft-delete by default. `?hard=True`: 409 with `affected_contacts` count if any contact holds a non-null/non-empty value for this field's `name`. Audited. |
| POST | `/custom-fields/validate` | volunteer | `CustomDataValidateRequest` | `CustomDataValidateResponse` | Stateless validate+coerce passthrough over `validate_and_coerce`. Used by S06 dry-run and by the admin live-preview. Returns 422 with structured field errors on failure. |

### 4.2 Pydantic schemas

Add a `# ============ Custom Fields ============` section to `backend/app/schemas.py`. All existing sections preserved. Pydantic v2 models.

```python
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, field_validator
import re

VALID_DATA_TYPES = Literal[
    "text", "textarea", "select", "multiselect",
    "date", "number", "checkbox", "contact_reference"
]

def _snake_case_name(v: str) -> str:
    if not re.match(r'^[a-z][a-z0-9_]*$', v):
        raise ValueError(
            "name must be snake_case: start with a-z, then a-z/0-9/_ only"
        )
    if len(v) > 100:
        raise ValueError("name must be ≤ 100 characters")
    return v


class OptionItem(BaseModel):
    value: str
    label: str


class CustomFieldDefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    name: str
    label: str
    data_type: str
    options: list[OptionItem]
    is_required: bool
    is_multi: bool
    weight: int
    is_active: bool
    help_text: Optional[str]


class CustomFieldGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    label: str
    entity: str
    weight: int
    is_active: bool
    fields: list[CustomFieldDefResponse] = []


class CustomFieldSchemaResponse(BaseModel):
    entity: str
    groups: list[CustomFieldGroupResponse]


class CustomFieldGroupCreate(BaseModel):
    name: str
    label: str
    entity: Literal["contact", "event", "activity"] = "contact"
    weight: int = 0

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _snake_case_name(v)


class CustomFieldGroupUpdate(BaseModel):
    label: Optional[str] = None
    weight: Optional[int] = None
    is_active: Optional[bool] = None


class CustomFieldDefCreate(BaseModel):
    group_id: int
    name: str
    label: str
    data_type: VALID_DATA_TYPES
    options: list[OptionItem] = []
    is_required: bool = False
    is_multi: bool = False
    weight: int = 0
    help_text: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _snake_case_name(v)

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: list[OptionItem], info) -> list[OptionItem]:
        data_type = info.data.get("data_type")
        if data_type in ("select", "multiselect") and not v:
            raise ValueError("options must be non-empty for select/multiselect fields")
        values = [opt.value for opt in v]
        if len(values) != len(set(values)):
            raise ValueError("option values must be unique within the field")
        return v


class CustomFieldDefUpdate(BaseModel):
    label: Optional[str] = None
    options: Optional[list[OptionItem]] = None
    is_required: Optional[bool] = None
    is_multi: Optional[bool] = None
    weight: Optional[int] = None
    is_active: Optional[bool] = None
    help_text: Optional[str] = None


class CustomDataValidateRequest(BaseModel):
    entity: str = "contact"
    custom_data: dict[str, Any]


class CustomDataValidateResponse(BaseModel):
    custom_data: dict[str, Any]
    normalized: bool = True
```

### 4.3 Service — `backend/app/services/custom_fields.py`

The engine core. Pure async functions; no circular imports. Type-hint all public functions.

```python
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
from app.models import CustomFieldGroup, CustomFieldDef, Contact

async def get_active_schema(
    db: AsyncSession, entity: str
) -> list[CustomFieldGroup]:
    """
    Returns active groups (weight asc, id asc) for entity,
    each with their active defs (weight asc, id asc).
    Uses two queries: one for groups, one for all defs of those group_ids.
    Avoids N+1. Filters is_active on both.
    """

async def validate_and_coerce(
    db: AsyncSession, entity: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """
    The single chokepoint for custom_data writes.
    Called by S03 (contact save), S06 (migration), POST /custom-fields/validate.
    Returns normalized dict (empty/None values dropped).
    Raises HTTPException(422) with structured detail on any field error.
    contact_reference values stored as JSON integers per MASTER C13.
    """

async def assert_entity_field_name_unique(
    db: AsyncSession,
    entity: str,
    name: str,
    exclude_def_id: Optional[int] = None,
) -> None:
    """
    Enforces that no two active defs across all groups of the same entity
    share the same `name` (custom_data is keyed by bare name — collision
    silently overwrites). Raises HTTPException(409) on collision.
    """

async def count_contacts_with_field_data(
    db: AsyncSession, field_name: str
) -> int:
    """
    Counts contacts where custom_data->>field_name IS NOT NULL AND != ''.
    Postgres: uses jsonb ->> operator.
    SQLite (tests): uses JSON_EXTRACT.
    Handles both dialects via db.bind.dialect.name check.
    """

def _normalize_option_value(s: str) -> str:
    """
    lowercase + strip. Available as a seam for S06's DEPARO/Deparo
    normalization. Not auto-applied on write (admin option values are
    authoritative); called by S06 to match CiviCRM values to seeded options.
    """
```

**`validate_and_coerce` algorithm (complete):**

1. Call `get_active_schema(db, entity)` → flatten to `{name: def}` dict.
2. **Reject unknown keys**: any key in `raw` not in the active def map → raise `HTTPException(422, detail=[{"field": key, "error": "unknown_field"}])`.
3. For each active def, read `raw.get(def.name)` as `value`:
   - **Required check:** if `is_required` and value is `None` or `""` or `[]` → raise 422 `[{"field": name, "error": "required"}]`.
   - **Coerce by `data_type`:**
     - `text`: `str(value).strip()`; max 1000 chars; raise 422 `"too_long"` if exceeded.
     - `textarea`: `str(value).strip()`; max 5000 chars.
     - `number`: `float(value)` (coerce str→float); if `float(value) == int(float(value))` store as int; non-numeric → 422 `"not_a_number"`.
     - `date`: accept `YYYY-MM-DD` string; validate with `datetime.fromisoformat(value)`; store as ISO string; malformed → 422 `"bad_date"`.
     - `checkbox`: truthy (`True`, `"true"`, `1`, any non-empty non-zero) → `True`; falsy (`False`, `"false"`, `0`, `None`, `""`) → `False`.
     - `select` (single, `is_multi=False`): must be in `{opt.value for opt in def.options}`; raise 422 `"invalid_option"` otherwise.
     - `select` (`is_multi=True`) / `multiselect`: accept list or coerce comma-separated string to list (migration convenience); each element must be a valid option value; deduplicate preserving order; raise 422 `"invalid_option"` for any invalid element.
     - `contact_reference` (single, `is_multi=False`): coerce to `int(value)`; verify `SELECT id FROM contacts WHERE id=:id AND is_deleted=FALSE` (one query); store as **integer** (C13); raise 422 `"unknown_contact"` if missing or soft-deleted.
     - `contact_reference` (`is_multi=True`): accept list; coerce each to `int`; batched `SELECT id FROM contacts WHERE id=ANY(:ids) AND is_deleted=FALSE`; store as **list[int]** (C13); raise 422 `"unknown_contact"` listing missing IDs if any are absent or deleted.
4. **Drop None/empty/empty-list** from output dict (lean JSONB — non-required absent fields are omitted rather than set to null; "fill" semantics in S06 mean missing = absent).
5. Return normalized dict. **Idempotent**: validating an already-normalized dict produces identical output.

**Edge-case rules:**

- `data_type` is immutable after create — changing it would orphan/invalidate stored JSONB values. Admin workaround: deactivate the field, create a new one with the new type.
- Removing an option a contact already uses: the PATCH endpoint allows it (soft). `validate_and_coerce` raises 422 for that value on the *next* save, prompting the admin to update the contact. PATCH response includes `affected_contacts` count.
- `contact_reference` self-reference: allowed (a contact can be their own "Invited By"). No cycle detection needed (flat links).
- Two fields with the same `name` in different groups of the same entity: blocked by `assert_entity_field_name_unique` (409). Same `name` across different entities (`contact` and `event`) is allowed.
- Performance: schema assembly is O(groups + defs) (~6 groups, ~40 fields). `contact_reference` batches ALL IDs in one `ANY` query. Safe per contact save. S06 bulk path loads schema once, then validates per-row.
- SQLite test path: `contacts.id` int equality check for `contact_reference` works on SQLite (no `ANY()`). The `count_contacts_with_field_data` function branches on `db.bind.dialect.name`.

### 4.4 Audit helper — `backend/app/services/audit.py`

**Create this file** (the `audit_log` table itself is created by S01; S02 only adds this `record(...)` helper module — per CN-03 / CN-25). It is the sole writer of `audit_log` and the shared helper for all subsequent sprints (C3). If S01 has already created this file, S02 extends rather than duplicates.

**Circular-import direction (CN-25):** `app/services/audit.py` imports ONLY from `app.models` (e.g. `AuditLog`, `utc_now`) and SQLAlchemy — it must NEVER import from any router module. All routers import `audit.py`; `audit.py` never imports a router. The S02 implementer enforces this one-way direction.

```python
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AuditLog, utc_now

async def record(
    db: AsyncSession,
    actor_id: Optional[int],
    action: str,
    entity: str,
    entity_id: Optional[int],
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
) -> None:
    """
    Writes one audit_log row. Called after the primary mutation is flushed
    but before commit — both are committed together.
    actor_id=None signals a system/background action.
    """
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        at=utc_now(),
    )
    db.add(log)
    # Caller commits the session — record() does not commit.
```

Action name conventions for S02:
`custom_group.create`, `custom_group.update`, `custom_group.soft_delete`, `custom_group.hard_delete`, `custom_field.create`, `custom_field.update`, `custom_field.soft_delete`, `custom_field.hard_delete`.

### 4.5 Seed — `backend/app/seeds/custom_fields_seed.py`

Idempotent function `seed_church_custom_fields(conn)` — takes a sync SQLAlchemy Connection from the Alembic `op.get_bind()` context. Uses raw SQL so it runs in migration context without an ORM session. Insert-if-missing semantics:

```sql
INSERT INTO custom_field_group (name, label, entity, weight, is_active, created_at, updated_at)
VALUES (:name, :label, 'contact', :weight, TRUE, NOW(), NOW())
ON CONFLICT (entity, name) DO NOTHING
```

Per-group field inserts use `ON CONFLICT (group_id, name) DO NOTHING`. To resolve `group_id`, the function reads back the group row after group insert.

Church field groups (entity=`contact`; from charter + civicrm.md §1 + decisions.md Rounds 3+4):

| Group name | Label | Weight | Fields (name, type, notes) |
|---|---|---|---|
| `constituent_info` | Constituent Info | 10 | `barangay` (text), `pepsol` (select, placeholder options: see below), `facebook_name` (text) |
| `new_friend_info` | New Friend Info | 20 | `invited_by` (contact_reference, `is_multi=False`), `consolidated_by` (contact_reference, `is_multi=False`), `first_visit_date` (date), `how_heard` (select, placeholder options) |
| `community_info` | Community Info | 30 | `community` (multiselect, placeholder options), `community_leader` (contact_reference, `is_multi=False`), `community_add_date` (date) |
| `leader_info` | Leader Info | 40 | `ministry` (multiselect, placeholder options), `ministry_leader` (contact_reference, `is_multi=False`), `network_leader` (contact_reference, `is_multi=False`), `lifegroup_leader` (contact_reference, `is_multi=False`) |
| `followup_info` | Followup Info | 50 | `followup_listing` (textarea), `followup_status` (select, placeholder options) |
| `church_info` | Church Info | 60 | `membership_class` (select, placeholder options), `water_baptized` (checkbox), `spirit_baptized` (checkbox), `date_joined` (date) |

All placeholder `select`/`multiselect` option lists carry 2–3 stub values with `help_text` = `"Pending owner confirmation — update option list in admin UI"`. The real PEPSOL stages, ministry names, community names, followup statuses, and membership classes are pending owner input (§10 Q3). These stub values are the source-of-truth starting point; S06 migration normalization (`_normalize_option_value`) maps CiviCRM values case-insensitively to whichever seeded option value matches.

Note: `community` is seeded as `multiselect` (a contact may belong to multiple communities). Owner should confirm before finalizing (§10 Q8).

### 4.6 File-by-file backend changes

- **CREATE** `backend/app/services/custom_fields.py` — engine (`get_active_schema`, `validate_and_coerce`, `assert_entity_field_name_unique`, `count_contacts_with_field_data`, `_normalize_option_value`).
- **CREATE** `backend/app/services/audit.py` — `record(...)` helper. Create only if S01 did not; if S01 created it, verify signature matches and extend if needed.
- **CREATE** `backend/app/routers/custom_fields.py` — all §4.1 endpoints with role deps from `app/dependencies.py` (`dependencies.py:45–64`). Imports `require_admin`, `require_volunteer`, `get_db` from existing modules.
- **CREATE** `backend/app/seeds/__init__.py` (empty, marks `seeds` as a package).
- **CREATE** `backend/app/seeds/custom_fields_seed.py` — `seed_church_custom_fields(conn)`.
- **CREATE** `backend/alembic/versions/<real_rev>_add_custom_field_engine.py` — §3.5 migration. `down_revision` = actual S01 revision id.
- **MODIFY** `backend/app/models.py` — add `CustomFieldGroup`, `CustomFieldDef` ORM classes. Add `AuditLog` only if S01 did not add it (coordinate). Add `custom_data` to `Contact` model only if S01 did not add it (coordinate per C7). Reuse module-level `JSONB` alias at `models.py:22`.
- **MODIFY** `backend/app/schemas.py` — append "Custom Fields" section (§4.2). Preserve all existing sections.
- **MODIFY** `backend/app/main.py` — import `custom_fields` from `app.routers`; add `app.include_router(custom_fields.router, dependencies=[Depends(check_setup_complete)])` after the existing domain routers (main.py:106–118 pattern).
- **MODIFY** `backend/tests/conftest.py` — add fixtures: `sample_contact` (post-S01 `Contact` row with `custom_data={}`; if S01 already provides it, extend rather than duplicate), `sample_custom_group`, `sample_select_field`, `sample_multiselect_field`, `sample_contact_ref_field`, `sample_checkbox_field`, `admin_token`, `volunteer_token` (if S01 does not provide these).

---

## 5. Frontend

### 5.1 New page — `/settings/custom-fields`

**File:** `frontend/src/pages/CustomFieldsPage.tsx`

**Route guard:** `AdminRoute` (same as `/settings/users` — `App.tsx`). Non-admins redirected to `/` before render.

**Layout (mobile-first 375px, desktop 2-column at `lg:` breakpoint):**

Mobile (single column, full-width):
- Entity tab selector: Contact / Event / Activity (three `<button>` tabs, active state with `bg-primary text-primary-foreground`). Switching tab invalidates and refetches group list.
- List of active `CustomFieldGroup` cards for the selected entity, weight-ordered, rendered as collapsible sections with `<details>` / animated expand.
- Each group card:
  - Header: `group.label` + machine `name` in a muted badge.
  - Inline Edit button (opens inline form to edit label/weight), Deactivate/Reactivate toggle, Delete (hard) button — on mobile these collapse to a 3-dot overflow menu.
  - Deactivate shows `<ConfirmDialog>` with `affected_contacts` warning if a pre-flight hard-delete probe returns 409.
  - Within each group: list of active field rows (`label`, `data_type` badge, required badge, weight, Edit/Deactivate buttons).
  - "+ Add Field" button below the field list — opens an inline drawer (`<dialog>`) with `CustomFieldDefCreate` inputs and `<OptionEditor>` for select/multiselect types.
- "+ Add Group" button below the group list.

Desktop (`lg:` breakpoint, 2-column via `grid grid-cols-[55%_45%]`):
- Left col: group/field CRUD (as above but with drag-handle weight reorder as optional enhancement).
- Right col: **Live Preview** pane, titled "Preview — Contact". Renders the full group via `<CustomFieldsSection>` with a sample `custom_data={}` value object. A "Test Validate" button calls `POST /custom-fields/validate` with the current preview values and shows field-level errors inline under each field.

**UX states:**
- Loading: `<LoadingState />` from `frontend/src/components/ui/StateViews.tsx`.
- Empty: `<EmptyState>` with "No field groups yet — create your first group".
- Error: `<ErrorState>` with retry callback.
- Mutations: `toast.success(...)` / `toast.error(err.response?.data?.detail || 'Could not save field')` (sonner, per AGENTS.md).
- Destructive actions (deactivate group, deactivate field, hard delete): `<ConfirmDialog>` (`frontend/src/components/ui/ConfirmDialog.tsx`). Hard-delete dialog shows the `affected_contacts` count from the 409 probe body.

### 5.2 Reusable components (canonical shared primitives per MASTER §5)

These are the canonical shared components. S03 and all later sprints import and reuse them; they must not be re-created elsewhere.

#### `frontend/src/components/customFields/CustomFieldRenderer.tsx`

Props:
```typescript
interface CustomFieldRendererProps {
  field: CustomFieldDef;
  value: CustomDataValue;
  onChange: (v: CustomDataValue) => void;
  disabled?: boolean;
  error?: string;
}
```

Renders by `data_type`:
- `text` → `<input type="text" className="..." />`.
- `textarea` → `<textarea className="..." />`.
- `number` → `<input type="number" />` (onChange converts to number before calling `onChange`).
- `date` → `<input type="date" />` (returns ISO `YYYY-MM-DD` string).
- `checkbox` → `<input type="checkbox" />` (boolean value; `checked={!!value}`).
- `select` (single, `is_multi=false`) → `<select>` with `<option>` per `field.options`.
- `select` (`is_multi=true`) / `multiselect` → chip-list: selected values rendered as removable chips (`bg-primary/10 text-primary` pill); a dropdown/`<select>` to add more; value is `string[]`.
- `contact_reference` (single) → "Select contact…" button; on click opens `MemberSearchModal` (existing `frontend/src/components/tasks/MemberSearchModal.tsx`); selected contact shown as a chip with name and a remove `×` button; value is `number` (contact id, C13). The picker calls `GET /members?search=` and reads `.items` from the response (forward-compatible with S03 paginated shape per C16).
- `contact_reference` (`is_multi=true`) → same but allows multiple chips; value is `number[]` (C13).

Styles: Tailwind tokens only — `bg-card`, `text-foreground`, `border-border`, `focus:ring-primary/30`, `rounded-xl` for inputs. Works under `.dark` (all via token classes). Required asterisk `*` in `text-destructive` if `field.is_required`. `help_text` as `<p className="mt-1 text-sm text-foreground/50">`. Inline `error` in `text-destructive text-sm`.

#### `frontend/src/components/customFields/CustomFieldsSection.tsx`

Props:
```typescript
interface CustomFieldsSectionProps {
  group: CustomFieldGroup;
  value: Record<string, CustomDataValue>;
  onChange: (name: string, v: CustomDataValue) => void;
  disabled?: boolean;
  errors?: Record<string, string>;
}
```

Renders all active fields of the group via `<CustomFieldRenderer>`, weight-ordered. Section header `<h3>` with `group.label`. Passes `errors[field.name]` to each renderer. This is the canonical building block S03 mounts in the contact create/edit form.

#### `frontend/src/components/customFields/OptionEditor.tsx`

Used within the admin page's add/edit field forms. Props: `options: OptionItem[]`, `onChange: (opts: OptionItem[]) => void`. Renders a list of `{value, label}` rows with add/remove controls. Validates that `value` strings are unique within the list; shows inline error on duplicate.

### 5.3 Hooks and services

**`frontend/src/hooks/useCustomFieldSchema.ts`**
```typescript
import { useQuery } from "@tanstack/react-query";
import { customFieldsApi } from "@/services/customFields";

export function useCustomFieldSchema(entity: string = "contact") {
  return useQuery({
    queryKey: ["custom-fields", "schema", entity],
    queryFn: () => customFieldsApi.getSchema(entity),
    staleTime: 5 * 60 * 1000, // 5 min; matches QueryClient default
  });
}
```

**`frontend/src/services/customFields.ts`**

Typed API functions wrapping `api` (the axios instance in `frontend/src/services/api.ts`):

```typescript
import api from "./api";
import type {
  CustomFieldSchema, CustomFieldGroupResponse, CustomFieldDefResponse,
  CustomFieldGroupCreate, CustomFieldGroupUpdate,
  CustomFieldDefCreate, CustomFieldDefUpdate,
} from "@/types/customFields";

export const customFieldsApi = {
  getSchema: (entity = "contact") =>
    api.get<CustomFieldSchema>(`/custom-fields/schema?entity=${entity}`).then(r => r.data),
  listGroups: (entity?: string, includeInactive = false) =>
    api.get<CustomFieldGroupResponse[]>(
      `/custom-fields/groups?${entity ? `entity=${entity}&` : ""}include_inactive=${includeInactive}`
    ).then(r => r.data),
  createGroup: (body: CustomFieldGroupCreate) =>
    api.post<CustomFieldGroupResponse>("/custom-fields/groups", body).then(r => r.data),
  updateGroup: (id: number, body: CustomFieldGroupUpdate) =>
    api.patch<CustomFieldGroupResponse>(`/custom-fields/groups/${id}`, body).then(r => r.data),
  deleteGroup: (id: number, hard = false) =>
    api.delete(`/custom-fields/groups/${id}?hard=${hard}`),
  listDefs: (groupId?: number, entity?: string, includeInactive = false) =>
    api.get<CustomFieldDefResponse[]>(
      `/custom-fields/defs?${groupId ? `group_id=${groupId}&` : ""}${entity ? `entity=${entity}&` : ""}include_inactive=${includeInactive}`
    ).then(r => r.data),
  createDef: (body: CustomFieldDefCreate) =>
    api.post<CustomFieldDefResponse>("/custom-fields/defs", body).then(r => r.data),
  updateDef: (id: number, body: CustomFieldDefUpdate) =>
    api.patch<CustomFieldDefResponse>(`/custom-fields/defs/${id}`, body).then(r => r.data),
  deleteDef: (id: number, hard = false) =>
    api.delete(`/custom-fields/defs/${id}?hard=${hard}`),
  validate: (entity: string, customData: Record<string, unknown>) =>
    api.post("/custom-fields/validate", { entity, custom_data: customData }).then(r => r.data),
};
```

**`frontend/src/types/customFields.ts`** (new file):

```typescript
export type DataType =
  | "text" | "textarea" | "select" | "multiselect"
  | "date" | "number" | "checkbox" | "contact_reference";

export interface OptionItem { value: string; label: string; }

export interface CustomFieldDef {
  id: number;
  group_id: number;
  name: string;
  label: string;
  data_type: DataType;
  options: OptionItem[];
  is_required: boolean;
  is_multi: boolean;
  weight: number;
  is_active: boolean;
  help_text: string | null;
}

export interface CustomFieldGroup {
  id: number;
  name: string;
  label: string;
  entity: string;  // lowercase: "contact" | "event" | "activity"
  weight: number;
  is_active: boolean;
  fields: CustomFieldDef[];
}

export interface CustomFieldSchema {
  entity: string;
  groups: CustomFieldGroup[];
}

// contact_reference: single=number, multi=number[] (MASTER C13)
export type CustomDataValue =
  | string
  | number
  | boolean
  | string[]
  | number[]
  | null;
```

### 5.4 TanStack Query keys and mutations

| Key | Usage |
|---|---|
| `["custom-fields", "schema", entity]` | Schema endpoint; shared with S03 contact form |
| `["custom-fields", "groups", entity, includeInactive]` | Admin group list |
| `["custom-fields", "defs", groupId]` | Admin field list per group |

Mutations — establish `useMutation` as the project standard (this is the first use; subsequent sprints replicate this pattern):

```typescript
const createGroupMutation = useMutation({
  mutationFn: (body: CustomFieldGroupCreate) => customFieldsApi.createGroup(body),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ["custom-fields"] });
    toast.success("Group created");
  },
  onError: (err: AxiosError<{ detail?: string }>) =>
    toast.error(err.response?.data?.detail ?? "Could not create group"),
});
```

All mutations invalidate the whole `["custom-fields"]` namespace so schema + admin lists refetch. There are eight mutations total: createGroup, updateGroup, deleteGroup (×1 soft, ×1 hard), createDef, updateDef, deleteDef (×1 soft, ×1 hard).

### 5.5 Role gating

- `/settings/custom-fields` → `AdminRoute` — admin-only page; volunteers never see it.
- `GET /custom-fields/schema` (and `useCustomFieldSchema` hook) → `require_volunteer` — any authenticated user. Volunteers need schema to render the contact form in S03.
- `POST /custom-fields/validate` → `require_volunteer` — any authenticated user; used by admin preview and S06 dry-run.
- All CRUD mutations → `require_admin` on the backend (belt-and-suspenders; the admin page never renders to non-admins).
- Zustand: no new store state. `useAuthStore((s) => s.isAdmin)` (existing `authStore.ts`) for any inline admin-only render decisions.

### 5.6 Design tokens, mobile-first, dark mode

Tokens only — no hardcoded hex (AGENTS.md, frontend.md):
- Containers/cards: `bg-card`, `border border-border`, `rounded-2xl`.
- Text: `text-foreground`, `text-foreground/50` (muted/help text).
- Primary action buttons: `bg-primary text-primary-foreground`.
- Secondary/ghost: `bg-transparent border border-border text-foreground`.
- Destructive: `bg-destructive text-destructive-foreground`.
- Focus ring: `focus:ring-2 focus:ring-primary/30`.
- Input border radius: `rounded-xl`.

Dark mode: all token classes adapt under `.dark`. Every new component verified under `.dark`. `dark:` prefix used only when a token class is insufficient.

Mobile-first (375px base): single-column stacked group cards, full-width fields, option editor as stacked rows, reorder via up/down buttons. Desktop (`lg:`): two-column layout; drag-handle reorder is an optional enhancement, not required for AC.

### 5.7 File-by-file frontend changes

- **CREATE** `frontend/src/pages/CustomFieldsPage.tsx`
- **CREATE** `frontend/src/components/customFields/CustomFieldRenderer.tsx`
- **CREATE** `frontend/src/components/customFields/CustomFieldsSection.tsx`
- **CREATE** `frontend/src/components/customFields/OptionEditor.tsx`
- **CREATE** `frontend/src/hooks/useCustomFieldSchema.ts`
- **CREATE** `frontend/src/services/customFields.ts`
- **CREATE** `frontend/src/types/customFields.ts`
- **MODIFY** `frontend/src/App.tsx` — add `<Route path="/settings/custom-fields" element={<AdminRoute><CustomFieldsPage /></AdminRoute>} />` alongside the existing `/settings/users` route (App.tsx:59).
- **MODIFY** `frontend/src/components/layout/BottomNav.tsx` — add "Custom Fields" entry under the admin "More" sheet alongside "Users" and "Settings".
- **MODIFY** `frontend/src/pages/SettingsPage.tsx` — add a navigation tile/card linking to `/settings/custom-fields` in the settings hub list (alongside "Manage Users").

---

## 6. Migration / data

This sprint ships **seed metadata only**, not contact-value migration (that is S06). The migration's `upgrade()` runs `seed_church_custom_fields` idempotently. No existing contact rows are modified — they get `custom_data='{}'` from the S01-owned server default on the column.

S06 (Data Migration ETL) will:
1. Read the seeded `custom_field_def` rows to know legal field names and data types.
2. Map CiviCRM CSV columns to field names via the column-mapping importer.
3. Call `validate_and_coerce` per-contact.
4. Multi-value fields (community, ministry): split comma-separated CiviCRM values; pass as `list[str]` to `validate_and_coerce`.
5. People-link fields (invited_by, consolidated_by, community_leader, ministry_leader, network_leader, lifegroup_leader): resolve CiviCRM contact names → integer contact_ids via the name-matching service (S22); unmatched rows go to `name_match_review_queue`; S06 does **not** call `validate_and_coerce` for `contact_reference` until a resolved `int` id is available.
6. Option normalization (DEPARO/Deparo → `deparo`): call `_normalize_option_value(raw_value)` and find the seeded option whose value matches case-insensitively. Non-matching values go to the import row result with `outcome="review"`.

---

## 7. Acceptance criteria

1. `alembic upgrade head` on a clean Postgres DB creates `custom_field_group` and `custom_field_def` with all documented columns, the two unique constraints (`uq_custom_field_group_entity_name`, `uq_custom_field_def_group_name`), and the two indexes; running it twice produces no error and no duplicate rows (CI's "upgrade head idempotent" assertion passes).

2. After upgrade, exactly six church groups exist with `entity='contact'` (lowercase), in weight order (10/20/30/40/50/60); re-running `seed_church_custom_fields` a second time inserts nothing new.

3. `contacts.custom_data` is a non-null JSONB column defaulting to `{}` for existing rows; the S01 column is present; the S02 inspector guard is a confirmed no-op; `alembic downgrade -1` does **not** drop `contacts.custom_data`.

4. Admin token: `POST /custom-fields/groups` → 201 with full response body + one `audit_log` row with `action='custom_group.create'` and correct `actor_id`. Volunteer token → 403. Unauthenticated → 401.

5. Admin can create all eight `data_type`s via `POST /custom-fields/defs`. `multiselect` automatically has `is_multi=True` in the response regardless of request value. Creating `select` or `multiselect` with `options=[]` → 422. Creating `text` with non-empty `options` is accepted (stored).

6. Two fields with the same `name` in different groups of the same entity `contact` → 409 on the second `POST /custom-fields/defs`. Same `name` for `entity='event'` → 201 (allowed).

7. `GET /custom-fields/schema?entity=contact` returns only active groups/fields, weight-ordered, each field with resolved `options`. Inactive groups and fields are absent. A volunteer token returns 200. Response matches `CustomFieldSchemaResponse` shape exactly.

8. `POST /custom-fields/validate` with valid `custom_data` returns 200 with normalized dict. Invalid cases return 422 with per-field error detail: unknown key → `"unknown_field"`; out-of-list select value → `"invalid_option"`; bad date → `"bad_date"`; non-numeric for number field → `"not_a_number"`; missing required → `"required"`; nonexistent contact_id → `"unknown_contact"`.

9. `multiselect` field: input `["a", "a", "b"]` → output `["a", "b"]` (dedup preserving order). Non-list input (e.g. plain string) → 422.

10. `contact_reference` (single, `is_multi=False`): valid non-deleted contact id → stored as integer (not string) in `contacts.custom_data`. Nonexistent id → 422 `"unknown_contact"`. Soft-deleted contact → 422 `"unknown_contact"`. `is_multi=True` version → accepts and validates a list of ints, stores as `[int, ...]` (C13).

11. `PATCH /custom-fields/defs/{id}` with body `{"name": "new_name"}` → validation error (422 or 400 — name is immutable). `PATCH` with `{"label": "New Label"}` → 200 + one `audit_log` row with `action='custom_field.update'`. Removing an option used by a contact → PATCH succeeds and response includes `affected_contacts=1`.

12. `DELETE /custom-fields/defs/{id}` (default soft) → field `is_active=False`; absent from subsequent `GET /custom-fields/schema`. `DELETE /custom-fields/defs/{id}?hard=True` when a contact holds a non-null value for this field → 409 with `affected_contacts > 0`. Hard delete when no data → 204.

13. `DELETE /custom-fields/groups/{group_id}?hard=True` when contacts hold values in child fields → 409. After clearing data, hard delete → 204 and all child defs deleted (CASCADE). Soft delete → group and all child defs `is_active=False`.

14. Validating an already-normalized dict returns the identical dict (idempotency check).

15. Frontend: `/settings/custom-fields` renders for admin tokens; non-admin user is redirected to `/` by `AdminRoute`. The page lists the six seeded groups. Adding a new field via the form calls `POST /custom-fields/defs`, invalidates `["custom-fields"]` queryKey, and the live preview re-renders with the new field.

16. `<CustomFieldRenderer>` renders all eight data types in both light and `.dark` themes (visual check). The `contact_reference` type opens `MemberSearchModal` on click and shows the selected contact as a chip with remove. `multiselect` shows chip-list. All rendered inputs use Tailwind token classes only; no inline hex colors.

17. `npm run build`, `npm run lint`, and `npm run test:run` all pass without errors or type errors. Backend `pytest tests/ -q` and `ruff check app` pass.

---

## 8. Test plan

All backend tests use `DATABASE_URL=sqlite+aiosqlite:///./ci_test.db REDIS_URL=memory:// ENVIRONMENT=test`. Schema created by `create_all` from ORM models (includes `CustomFieldGroup`, `CustomFieldDef`, `AuditLog`, `Contact` with `custom_data`). Fixtures from `conftest.py`.

### `backend/tests/test_custom_fields.py` (integration — HTTP client)

```python
test_create_group_admin_ok
  # admin POST /custom-fields/groups -> 201; row in DB; audit_log row action='custom_group.create' with actor_id

test_create_group_forbidden_for_volunteer
  # volunteer token -> 403

test_create_group_forbidden_for_unauthenticated
  # no token -> 401

test_create_group_name_must_be_snake_case
  # name="Bad Name" -> 422

test_group_name_immutable_on_patch
  # create group; PATCH {"name": "new_name"} -> 422 or 400 (field immutable)

test_group_entity_immutable_on_patch
  # PATCH {"entity": "event"} -> 422 or 400

test_create_def_all_eight_types
  # parametrize over all eight data_types; select/multiselect: include options=[...]
  # assert data_type and is_multi stored correctly
  # multiselect: assert is_multi=True in response regardless of request value

test_create_select_without_options_422
  # POST data_type="select", options=[] -> 422

test_create_multiselect_without_options_422
  # POST data_type="multiselect", options=[] -> 422

test_create_text_with_options_accepted
  # POST data_type="text", options=[{"value":"a","label":"A"}] -> 201

test_field_name_unique_per_entity_cross_group
  # create two groups for entity='contact'; field name='barangay' in first -> 201
  # same name in second -> 409

test_field_name_same_in_different_entity_allowed
  # create contact field 'ministry' -> 201; event field 'ministry' -> 201

test_schema_returns_active_only_for_volunteer
  # create group+field; volunteer token GET /custom-fields/schema -> 200, field present
  # deactivate field; schema -> field absent; deactivate group -> group absent

test_schema_weight_ordering
  # create groups weight=20, 10, 30; assert schema.groups ordered [10, 20, 30]

test_def_data_type_immutable
  # create text field; PATCH {"data_type": "number"} -> error (4xx)

test_soft_delete_group_cascades_to_defs
  # create group+field; DELETE /groups/{id} -> 204; group is_active=False; field is_active=False

test_hard_delete_group_blocked_when_data_exists
  # create group+field; set contact.custom_data={"barangay":"Deparo"}
  # DELETE /groups/{id}?hard=true -> 409 with affected_contacts > 0

test_hard_delete_field_blocked_when_data_exists
  # set contact.custom_data with field value; DELETE /defs/{id}?hard=true -> 409

test_hard_delete_field_ok_when_no_data
  # DELETE /defs/{id}?hard=true -> 204

test_option_removal_warning_in_patch
  # contact has custom_data={"pepsol": "stub_val"}
  # PATCH /defs removes "stub_val" from options -> 200; response includes affected_contacts=1

test_validate_endpoint_unknown_field
  # POST /custom-fields/validate {"entity":"contact","custom_data":{"no_such_field":"x"}} -> 422 unknown_field

test_validate_endpoint_valid
  # POST with valid data matching a seeded field -> 200 normalized dict

test_audit_log_written_on_create
  # create group; query audit_log -> 1 row action='custom_group.create', entity='custom_field_group'

test_audit_log_written_on_update
  # update group label; query audit_log -> row action='custom_group.update'

test_audit_log_written_on_delete
  # soft delete group; query audit_log -> row action='custom_group.soft_delete'
```

### `backend/tests/test_custom_fields_validation.py` (unit-level — service direct)

Tests call `validate_and_coerce` directly with a seeded db_session.

```python
test_number_coerce_string_int      # "12" -> 12
test_number_coerce_string_float    # "12.5" -> 12.5
test_number_reject_non_numeric     # "abc" -> HTTPException 422 not_a_number
test_date_iso_ok                   # "2026-01-31" -> "2026-01-31" stored
test_date_bad_format               # "31/01/2026" -> HTTPException 422 bad_date
test_select_valid_option           # value in list -> accepted
test_select_invalid_option         # value not in list -> HTTPException 422 invalid_option
test_multiselect_dedup             # ["a","a","b"] -> ["a","b"]
test_multiselect_invalid_item      # ["a","INVALID"] -> HTTPException 422 invalid_option
test_multiselect_coerce_csv        # "a,b" -> ["a","b"] (migration convenience)
test_checkbox_truthy_values        # True/1/"true" -> True; False/0/""/None -> False
test_required_field_missing        # missing required field -> HTTPException 422 required
test_required_field_empty_str      # "" for required -> HTTPException 422 required
test_optional_field_dropped        # None/empty for non-required -> absent from output
test_unknown_key_rejected          # {"unknown": "x"} -> HTTPException 422 unknown_field
test_contact_ref_single_stores_int # valid contact id -> stored as int, not string (C13)
test_contact_ref_missing_id        # nonexistent id -> HTTPException 422 unknown_contact
test_contact_ref_soft_deleted      # soft-deleted contact -> HTTPException 422 unknown_contact
test_contact_ref_multi_stores_list # is_multi=True; [id1, id2] -> [int, int] (C13)
test_validate_idempotent           # validate already-normalized dict -> same output
```

### `backend/tests/test_custom_fields_seed.py`

```python
test_seed_creates_six_contact_groups
  # run seed_church_custom_fields; assert 6 groups entity='contact'
  # names: constituent_info, new_friend_info, community_info, leader_info,
  #        followup_info, church_info

test_seed_weight_ordering
  # assert groups at weights 10,20,30,40,50,60

test_seed_idempotent
  # run seed twice; group count == 6, field count unchanged

test_seed_preserves_admin_edits
  # edit seeded group label; run seed again; label unchanged (insert-if-missing)

test_seed_includes_contact_reference_fields
  # assert invited_by, consolidated_by, community_leader data_type='contact_reference'
  # assert is_multi=False for single-link fields

test_seed_includes_multiselect_fields
  # assert community.data_type='multiselect' and is_multi=True
  # assert ministry.data_type='multiselect' and is_multi=True
```

### `frontend/src/components/customFields/CustomFieldRenderer.test.tsx`

Use vitest + React Testing Library. Mock `MemberSearchModal` for `contact_reference` tests.

```typescript
it("renders text field with input[type=text]")
it("renders textarea as <textarea> element")
it("renders number field with input[type=number]")
it("renders date field with input[type=date] and returns ISO string onChange")
it("renders checkbox as input[type=checkbox]")
it("renders select field with option elements from field.options")
it("renders multiselect as chip list; selecting two values calls onChange with string[]")
it("multiselect: removing a chip updates onChange value")
it("renders contact_reference single: clicking button opens MemberSearchModal")
it("contact_reference single: selecting contact from modal calls onChange with number (not string)")
it("contact_reference single: clicking remove on chip calls onChange(null)")
it("renders contact_reference multi: allows multiple chips, onChange called with number[]")
it("shows required asterisk (*) when field.is_required=true")
it("shows help_text below the input")
it("shows error message in text-destructive when error prop provided")
it("uses only design token classes; no hardcoded hex in className strings")
```

### `frontend/src/pages/CustomFieldsPage.test.tsx`

```typescript
it("lists seeded groups from mocked GET /custom-fields/groups")
it("shows EmptyState when groups array is empty")
it("shows LoadingState while groups query is in-flight")
it("add-group form: submitting calls POST /custom-fields/groups with correct body")
it("create group mutation invalidates ['custom-fields'] queryKey")
it("add-field form: submitting calls POST /custom-fields/defs")
it("deactivate group: opens ConfirmDialog before sending DELETE /groups/{id}")
it("live preview pane renders CustomFieldsSection with mock schema")
it("test-validate button calls POST /custom-fields/validate and shows inline errors")
it("non-admin user is redirected (AdminRoute redirects to /)")
it("entity tab switch refetches groups with new entity param")
```

---

## 9. Rollout / rollback / risks

**Rollout order (single Unraid instance; no blue-green):**

1. `alembic upgrade head` — creates `custom_field_group`, `custom_field_def`; runs seed; inspector guards no-op for S01-owned columns/tables.
2. Deploy backend — new `/custom-fields/*` router registered; all existing routes unchanged.
3. Deploy frontend — new `/settings/custom-fields` route, BottomNav entry, SettingsPage link.

No feature flag needed — foundation sprint, additive only. Schema endpoint returns seeded groups immediately. No contact data is touched.

**Rollback:**

- Frontend: revert FE deploy removes admin page, no data impact.
- Backend: revert BE deploy removes router, existing routes unaffected.
- DB: `alembic downgrade -1` drops `custom_field_def` then `custom_field_group` (seed rows vanish with them). **Does not drop `contacts.custom_data` or `audit_log`** (S01-owned — documented in migration docstring). If rollback occurs after contacts have had `custom_data` written (post-S03), the JSONB column remains and is inert until tables are re-created. Downgrade is safe at this phase because no contact writes are possible until S03.

**Risks and mitigations:**

| Risk | Likelihood | Mitigation |
|---|---|---|
| Ownership overlap with S01 for `contacts.custom_data` and `audit_log` | Medium (spec overlap) | Inspector-guarded conditional add/create; asymmetric downgrade documented; coordinate via §10 Q2 and Q4 |
| Field `name` collision across groups (bare-name JSONB key) | Low (admin-controlled) | Entity-scoped uniqueness enforced in `assert_entity_field_name_unique` (409); covered by `test_field_name_unique_per_entity_cross_group` |
| `data_type` change corrupting stored values | Low (admin action) | `data_type` immutable after create; documented admin workaround (deactivate + new field); PATCH rejects `data_type` change |
| Seed option lists incomplete (pending owner input) | High (known) | Placeholder options with `help_text` noting they are stubs; admin edits them in the UI; not a blocker for development |
| `validate_and_coerce` called from S06 on raw CiviCRM names for `contact_reference` | Medium (integration risk) | S06 resolves names→ids via S22 before calling `validate_and_coerce`; the service never receives raw string names as `contact_reference` values |
| SQLite test path: `::jsonb` casts | Low (solved pattern) | Migration uses `.with_variant(JSONB,'postgresql')`; `create_all` uses Python `default=dict`; confirmed by existing `JSONB` alias at `models.py:22` |
| Unbounded `custom_data` JSONB size | Low | Text max 1000 / textarea max 5000 enforced by `validate_and_coerce`; GIN index deferred to S09 |
| Performance at S06 bulk migration (1,440 contacts) | Low | Schema loaded once; validated per-row; `contact_reference` uses batched `ANY` per call; trivially fast at this scale |
| `contact_reference` picker breaks if S03 changes `/members` response shape | Medium (C16) | Picker reads `.items` defensively (`response.data?.items ?? response.data`) for forward-compatibility with S03 paginated shape |

---

## 10. Open questions & pending owner artifacts

**Q1 — S01 dependency: contact search endpoint.** `contact_reference` picker uses `MemberSearchModal`, which currently calls `GET /members` (bare list). S01 may rename to `/contacts` or keep `/members` (MASTER C15 ruling: keep `/members`). S02 author must confirm the post-S01 path and update `MemberSearchModal`'s URL if needed. Current ruling: `GET /members?search=&limit=`. **Action: S01 author confirms endpoint path; S02 picker hardcodes `/members`.**

**Q2 — Ownership of `contacts.custom_data`.** MASTER C7 ruling: S01 owns it. Confirm S01's migration adds `custom_data JSONB NOT NULL DEFAULT '{}'` and the `Contact` ORM model carries the column. S02's inspector guard then becomes a confirmed no-op. **Action: S01 author adds `custom_data` to the `Contact` model and migration; S02 confirms guard is no-op at review.**

**Q3 — Seed option lists (authoritative values).** The seeded `select`/`multiselect` option lists for PEPSOL stages, Ministry names, Community/zone names, Followup statuses, and Membership classes are placeholders. Owner should provide a CiviCRM `option_value` export or manually curated list. Not a blocker for S02 itself, but S06 migration quality depends on these matching the actual CiviCRM export values. **Action: owner provides option lists before S06 dry-run.**

**Q4 — Ownership of `audit_log` and `audit.py`.** MASTER C3 ruling: `audit_log` is created by S01; `audit.py::record(...)` is created by S02. Confirm S01 creates the `AuditLog` ORM model and the migration. S02 creates `services/audit.py`. No other sprint should fork `audit_service.py`. **Action: S01 author adds `AuditLog` model + migration table; S02 author creates `services/audit.py`.**

**Q5 — `viewer` role gating on schema endpoint.** `GET /custom-fields/schema` uses `require_volunteer` now (admin+volunteer). When S15 ships the `viewer` role, viewers must be able to call this endpoint to render the contact detail page in read-only mode. This is metadata, not a contact row, so viewers should be allowed (distinct from C20's restriction on contact-row endpoints). **Action: S15 author confirms and adjusts scope — S02 needs no change.**

**Q6 — `is_multi` on `select` field (UI behavior).** The backend allows `data_type='select'` + `is_multi=True` as equivalent to `multiselect`. The admin UI should present a "Allow multiple values?" checkbox on `select` type fields. Confirm whether to expose this toggle or force the admin to choose `multiselect` as the distinct type. Both are valid on the backend. **Action: confirm before admin UI implementation.**

**Q7 — Repeating multi-record followup entries.** The locked decision excludes repeating multi-record sets. Confirm no church use case requires a log of multiple followup records per contact over time. If that use case exists, it maps to `activities` (S12), not to this custom-field engine. **Action: owner confirms before S02 implementation.**

**Q8 — `community` multiselect vs single.** The CiviCRM "Community" field may store a single community per contact in the existing data. `multiselect` is correct if a contact can belong to multiple communities; `select` (single) is cleaner if always one. **Action: owner confirms whether contacts can belong to multiple communities; seed updated accordingly before S06 migration.**

**Q9 — `down_revision` at implementation time.** The S02 migration's `down_revision` must be set to the real revision id produced by S01's migration at commit time. Run `alembic heads` after S01 is committed. The value `f3a4b5c6d7e8` used as a base in planning is the current last committed migration (`f3a4b5c6d7e8_add_task_action_approval_unique.py`); S01 adds one revision between that and S02. **Action: S02 implementer chains off S01's real revision id (C26).**
