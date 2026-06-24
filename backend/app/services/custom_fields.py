"""Custom field engine — validation, coercion, and schema assembly.

This is the single chokepoint for all custom_data writes.  Called by:
  - S03 contact create/update
  - S06 migration ETL
  - POST /custom-fields/validate (admin live-preview / dry-run)

No circular imports: this module imports ONLY from app.models, app.database,
SQLAlchemy, FastAPI, and stdlib (CN-25).  Do NOT import from any router or
from app.dependencies.

The service never commits — callers are responsible for db.commit().
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine as _app_engine
from app.models import Contact, CustomFieldDef, CustomFieldGroup


# ---------------------------------------------------------------------------
# Internal signal class — defined first so all functions below can raise it
# ---------------------------------------------------------------------------


class _CoercionError(Exception):
    """Internal signal carrying the error code string.

    Never escapes this module — callers convert to HTTPException(422).
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_option_value(s: str) -> str:
    """lowercase + strip.

    Available as a seam for S06's DEPARO/Deparo normalization.
    Not auto-applied on write (admin option values are authoritative);
    called by S06 to match CiviCRM values to seeded options.
    """
    return s.strip().lower()


def _dialect_name(db: AsyncSession) -> str:  # noqa: ARG001  (db unused but kept for API compat)
    """Return dialect name (e.g. 'sqlite', 'postgresql').

    Reads from the module-level async engine (app.database.engine) to avoid
    the deprecated AsyncSession.bind attribute and any run_sync() calls.
    run_sync() was found to corrupt aiosqlite connection state during tests,
    causing teardown failures.
    """
    try:
        return _app_engine.dialect.name
    except Exception:
        return "sqlite"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_active_schema(
    db: AsyncSession, entity: str
) -> list[dict[str, Any]]:
    """Return active groups (weight asc, id asc) for *entity*.

    Each entry is a plain dict::

        {
            "group": CustomFieldGroup,
            "defs":  list[CustomFieldDef],   # active defs, weight/id ordered
        }

    Uses exactly **2 queries** regardless of group count (no N+1):
      1. SELECT * FROM custom_field_group WHERE entity=? AND is_active=TRUE
      2. SELECT * FROM custom_field_def   WHERE group_id IN (...) AND is_active=TRUE

    Returns an empty list when no active groups exist for the entity.
    """
    # Query 1: active groups for entity, weight/id ordered
    groups_result = await db.execute(
        select(CustomFieldGroup)
        .where(
            CustomFieldGroup.entity == entity,
            CustomFieldGroup.is_active.is_(True),
        )
        .order_by(CustomFieldGroup.weight, CustomFieldGroup.id)
    )
    groups: list[CustomFieldGroup] = list(groups_result.scalars().all())

    if not groups:
        return []

    group_ids = [g.id for g in groups]

    # Query 2: all active defs for those groups, weight/id ordered
    defs_result = await db.execute(
        select(CustomFieldDef)
        .where(
            CustomFieldDef.group_id.in_(group_ids),
            CustomFieldDef.is_active.is_(True),
        )
        .order_by(CustomFieldDef.weight, CustomFieldDef.id)
    )
    all_defs: list[CustomFieldDef] = list(defs_result.scalars().all())

    # Index defs by group_id
    defs_by_group: dict[int, list[CustomFieldDef]] = {g.id: [] for g in groups}
    for d in all_defs:
        defs_by_group[d.group_id].append(d)

    return [{"group": g, "defs": defs_by_group[g.id]} for g in groups]


async def validate_and_coerce(
    db: AsyncSession, entity: str, raw: dict[str, Any]
) -> dict[str, Any]:
    """Validate and coerce *raw* custom_data for *entity*.

    The single chokepoint for custom_data writes.  Returns a normalized dict
    with None / empty-string / empty-list values dropped (lean JSONB).
    ``False`` for a checkbox IS stored (absence means False for optional fields,
    but an explicit False round-trips correctly — AC7 idempotence).

    Raises HTTPException(422) with structured detail on any field error::

        [{"field": "<name>", "error": "<code>"}]

    Algorithm (spec §4.3):
      1. Get active schema → flatten to ``{name: def}`` dict.
      2. Reject unknown keys.
      3. For each active def, read raw value and coerce by data_type.
      4. Drop None / "" / [] from output.
      5. Return normalized dict.  Idempotent.
    """
    # Step 1: build {name: def} map from active schema
    schema_entries = await get_active_schema(db, entity)
    def_map: dict[str, CustomFieldDef] = {}
    for entry in schema_entries:
        for d in entry["defs"]:
            def_map[d.name] = d

    # Step 2: reject unknown keys
    unknown = [k for k in raw if k not in def_map]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"field": k, "error": "unknown_field"} for k in unknown],
        )

    # Step 3: coerce each active def
    output: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    for name, field_def in def_map.items():
        value = raw.get(name)

        # Required check
        if field_def.is_required:
            if value is None or value == "" or value == []:
                errors.append({"field": name, "error": "required"})
                continue

        # Skip absent optional fields
        if value is None:
            continue

        # Coerce by data_type
        try:
            coerced = _coerce_value(field_def, value)
        except _CoercionError as exc:
            errors.append({"field": name, "error": exc.code})
            continue

        # contact_reference: verify contacts exist and are not soft-deleted
        if field_def.data_type == "contact_reference":
            try:
                coerced = await _validate_contact_reference(
                    db, name, coerced, field_def.is_multi
                )
            except HTTPException:
                # Re-raise directly — already structured as [{"field", "error"}]
                raise
            except _CoercionError as exc:
                errors.append({"field": name, "error": exc.code})
                continue

        # Step 4: drop None / "" / [] (lean JSONB)
        # Do NOT drop False — a stored False checkbox must round-trip (AC7).
        if coerced is None or coerced == "" or coerced == []:
            continue

        output[name] = coerced

    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=errors,
        )

    return output


def _coerce_value(field_def: CustomFieldDef, value: Any) -> Any:
    """Coerce *value* to the canonical type for *field_def*.

    Raises _CoercionError(code) on failure (caller converts to HTTP 422).
    Does NOT handle contact_reference DB lookups — that is done by the caller.
    """
    dt = field_def.data_type

    # ---- text ---------------------------------------------------------------
    if dt == "text":
        v = str(value).strip()
        if len(v) > 1000:
            raise _CoercionError("too_long")
        return v if v else None

    # ---- textarea -----------------------------------------------------------
    if dt == "textarea":
        v = str(value).strip()
        if len(v) > 5000:
            raise _CoercionError("too_long")
        return v if v else None

    # ---- number -------------------------------------------------------------
    if dt == "number":
        try:
            fv = float(value)
        except (TypeError, ValueError):
            raise _CoercionError("not_a_number")
        # Store as int when lossless (e.g. 3.0 → 3), but guard against
        # overflow on very large floats (inf, nan) which cannot be int-narrowed.
        try:
            iv = int(fv)
        except (OverflowError, ValueError):
            return fv
        return iv if fv == iv else fv

    # ---- date ---------------------------------------------------------------
    if dt == "date":
        if not isinstance(value, str):
            raise _CoercionError("bad_date")
        # Only accept YYYY-MM-DD; fromisoformat also accepts datetimes
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise _CoercionError("bad_date")
        try:
            datetime.fromisoformat(value)
        except (ValueError, TypeError):
            raise _CoercionError("bad_date")
        return value

    # ---- checkbox -----------------------------------------------------------
    if dt == "checkbox":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("true", "1"):
                return True
            if value.lower() in ("false", "0", ""):
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        # Any other truthy/falsy value
        return bool(value)

    # ---- select / multiselect -----------------------------------------------
    if dt in ("select", "multiselect"):
        # options may be dicts (from DB JSON) or OptionItem objects (Pydantic)
        valid_options: set[str] = set()
        for opt in field_def.options:
            if isinstance(opt, dict):
                valid_options.add(opt["value"])
            else:
                valid_options.add(opt.value)

        is_multi = field_def.is_multi or dt == "multiselect"

        if not is_multi:
            v = str(value)
            if v not in valid_options:
                raise _CoercionError("invalid_option")
            return v

        # Multi: accept list or coerce comma-separated string (migration convenience)
        if isinstance(value, str):
            items = [s.strip() for s in value.split(",") if s.strip()]
        elif isinstance(value, list):
            items = [str(i) for i in value]
        else:
            items = [str(value)]

        for item in items:
            if item not in valid_options:
                raise _CoercionError("invalid_option")

        # Deduplicate preserving order (AC4)
        seen: set[str] = set()
        deduped: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped if deduped else None

    # ---- contact_reference --------------------------------------------------
    if dt == "contact_reference":
        # Coerce to int(s); actual DB lookup is done by the caller
        if not field_def.is_multi:
            try:
                return int(value)
            except (TypeError, ValueError):
                raise _CoercionError("unknown_contact")
        else:
            if isinstance(value, (int, float)):
                raw_list: list[Any] = [value]
            elif isinstance(value, str):
                raw_list = [value]
            elif isinstance(value, list):
                raw_list = value
            else:
                raise _CoercionError("unknown_contact")
            try:
                return [int(v) for v in raw_list]
            except (TypeError, ValueError):
                raise _CoercionError("unknown_contact")

    # Unknown data_type — should not happen if admin UI validates on create
    return value


async def _validate_contact_reference(
    db: AsyncSession,
    field_name: str,
    coerced: Any,
    is_multi: bool,
) -> Any:
    """Verify contact_reference IDs exist and are not soft-deleted.

    Raises HTTPException(422, detail=[{field, error:'unknown_contact'}]).
    Returns the verified value: int for single, list[int] for multi (C13).
    """
    if not is_multi:
        # Single: coerced is an int
        contact_id: int = coerced
        result = await db.execute(
            select(Contact.id).where(
                Contact.id == contact_id,
                Contact.is_deleted.is_(False),
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[{"field": field_name, "error": "unknown_contact"}],
            )
        return contact_id

    # Multi: coerced is list[int]
    ids: list[int] = coerced
    if not ids:
        return []

    # Single ORM query works on both Postgres and SQLite.
    # Postgres uses ANY() under the hood for .in_(); SQLite uses IN().
    # SQLAlchemy handles the dialect difference automatically.
    result = await db.execute(
        select(Contact.id).where(
            Contact.id.in_(ids),
            Contact.is_deleted.is_(False),
        )
    )
    found_ids: set[int] = {row[0] for row in result.all()}
    missing = [i for i in ids if i not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[{"field": field_name, "error": "unknown_contact"}],
        )

    # Preserve input order; deduplicate
    seen_ids: set[int] = set()
    deduped: list[int] = []
    for i in ids:
        if i not in seen_ids:
            seen_ids.add(i)
            deduped.append(i)
    return deduped


async def assert_entity_field_name_unique(
    db: AsyncSession,
    entity: str,
    name: str,
    exclude_def_id: Optional[int] = None,
) -> None:
    """Enforce that no two active defs across all groups of the same entity
    share the same ``name``.

    custom_data is keyed by bare field name — a collision across groups
    silently overwrites on write and produces ambiguous reads.

    Raises HTTPException(409) on collision.
    """
    stmt = (
        select(CustomFieldDef.id)
        .join(CustomFieldGroup, CustomFieldDef.group_id == CustomFieldGroup.id)
        .where(
            CustomFieldGroup.entity == entity,
            CustomFieldGroup.is_active.is_(True),
            CustomFieldDef.name == name,
            CustomFieldDef.is_active.is_(True),
        )
    )
    if exclude_def_id is not None:
        stmt = stmt.where(CustomFieldDef.id != exclude_def_id)

    result = await db.execute(stmt)
    existing_id = result.scalar_one_or_none()
    if existing_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A field named '{name}' already exists on entity '{entity}'. "
                "Field names must be unique within an entity (they are JSONB keys)."
            ),
        )


async def count_contacts_with_field_data(
    db: AsyncSession, field_name: str
) -> int:
    """Count contacts where custom_data has a non-null, non-empty value
    for *field_name*.

    Handles both dialects:
      - Postgres: ``custom_data ->> :field`` (jsonb text extraction).
      - SQLite (tests): ``JSON_EXTRACT(custom_data, '$.field')``.

    Soft-deleted contacts are intentionally included — the caller (PATCH /defs
    and DELETE /defs) needs the true usage count across all contacts before
    allowing a hard delete or flagging affected_contacts.
    """
    dialect = _dialect_name(db)

    if dialect == "postgresql":
        sql = text(
            "SELECT COUNT(*) FROM contacts "
            "WHERE custom_data ->> :field IS NOT NULL "
            "AND custom_data ->> :field <> ''"
        )
        params: dict[str, Any] = {"field": field_name}
    else:
        # SQLite: JSON_EXTRACT returns NULL for missing keys.
        # Also exclude the literal string 'null' which SQLite JSON returns for
        # JSON null values.
        sql = text(
            "SELECT COUNT(*) FROM contacts "
            "WHERE JSON_EXTRACT(custom_data, :path) IS NOT NULL "
            "AND JSON_EXTRACT(custom_data, :path) <> '' "
            "AND JSON_EXTRACT(custom_data, :path) <> 'null'"
        )
        params = {"path": f"$.{field_name}"}

    result = await db.execute(sql, params)
    row = result.one()
    return int(row[0])
