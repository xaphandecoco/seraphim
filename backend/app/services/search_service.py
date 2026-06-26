"""Advanced search engine — S09 injection gate and CRUD helpers.

CN-25: imports ONLY from app.models, app.database, app.services, SQLAlchemy,
FastAPI, and stdlib.  Never imports from any router or app.dependencies.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine as _app_engine
from app.models import Contact, Group, GroupMember, SavedSearch, utc_now
from app.services import audit as audit_svc
from app.services.search_fields import FieldSpec, build_registry


# ---------------------------------------------------------------------------
# Public signal class
# ---------------------------------------------------------------------------


class InvalidCriteria(Exception):
    """Raised when user-supplied search criteria are invalid.

    The message is safe to surface to the client — it contains no raw SQL or
    internal identifiers beyond the field key or operator name explicitly
    provided by the caller.
    """


# ---------------------------------------------------------------------------
# Dialect helper
# ---------------------------------------------------------------------------


def _dialect_name() -> str:
    """Return SQLAlchemy dialect name ('postgresql' or 'sqlite')."""
    try:
        return _app_engine.dialect.name
    except Exception:
        return "sqlite"


# ---------------------------------------------------------------------------
# LIKE escape
# ---------------------------------------------------------------------------


def _escape_like(value: str) -> str:
    """Escape backslash, percent, and underscore for ilike(escape='\\\\')."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Step 1: Bounds guard
# ---------------------------------------------------------------------------


def _check_bounds(
    node: dict[str, Any],
    depth: int = 0,
    counter: Optional[list[int]] = None,
) -> None:
    """Recursively enforce depth ≤ 10, total nodes ≤ 100, list len ≤ 200.

    Discrimination uses the SAME signal as _compile_node ("logic" in node),
    so a leaf that carries a spurious "conditions" key cannot skip the
    list-length check.  The list-length check also runs UNCONDITIONALLY
    whenever "value" is present to guard against hybrid nodes.
    """
    if counter is None:
        counter = [0]

    counter[0] += 1
    if counter[0] > 100:
        raise InvalidCriteria("criteria exceeds maximum of 100 nodes")
    if depth > 10:
        raise InvalidCriteria("criteria exceeds maximum depth of 10")

    # Check list-length UNCONDITIONALLY whenever "value" is present —
    # catches leaf nodes that also carry spurious "conditions" keys.
    if "value" in node:
        v = node["value"]
        if isinstance(v, list) and len(v) > 200:
            raise InvalidCriteria("list value exceeds maximum of 200 elements")

    # Use same discriminator as _compile_node: "logic" in node ↔ group node.
    if "logic" in node:
        for child in node.get("conditions", []):
            _check_bounds(child, depth + 1, counter)


# ---------------------------------------------------------------------------
# Step 4: Type coercion
# ---------------------------------------------------------------------------


def _coerce_single(value: Any, value_type: str, spec: FieldSpec) -> Any:
    """Coerce a single value to the Python type required by *value_type*."""
    if value_type == "string":
        return str(value)

    if value_type == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            raise InvalidCriteria(f"expected integer value, got {value!r}")

    if value_type == "number":
        try:
            return float(value)
        except (ValueError, TypeError):
            raise InvalidCriteria(f"expected numeric value, got {value!r}")

    if value_type == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        if isinstance(value, str):
            lv = value.lower()
            if lv in ("true", "1", "yes"):
                return True
            if lv in ("false", "0", "no"):
                return False
        raise InvalidCriteria(f"expected boolean value, got {value!r}")

    if value_type in ("date", "datetime"):
        if isinstance(value, datetime):
            return value
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime(value.year, value.month, value.day)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
            try:
                d = date.fromisoformat(value)
                return datetime(d.year, d.month, d.day)
            except ValueError:
                raise InvalidCriteria(f"expected ISO date/datetime, got {value!r}")
        raise InvalidCriteria(f"expected date/datetime value, got {value!r}")

    if value_type == "enum":
        v = str(value)
        if spec.options is not None:
            valid = {o["value"] for o in spec.options}
            if v not in valid:
                raise InvalidCriteria(f"invalid enum value {v!r}")
        return v

    if value_type == "multiselect":
        # Individual element — always a plain string
        return str(value)

    if value_type == "contact_reference":
        try:
            return int(value)
        except (ValueError, TypeError):
            raise InvalidCriteria(f"expected integer contact id, got {value!r}")

    # Fallthrough — return as-is
    return value


def _coerce_value(value: Any, op: str, spec: FieldSpec) -> Any:
    """Coerce the full value for an operator, handling list ops."""
    if op in ("is_set", "is_empty"):
        return None

    vt = spec.value_type

    if op in ("in", "contains_any"):
        if not isinstance(value, list) or len(value) == 0:
            raise InvalidCriteria(f"operator '{op}' requires a non-empty list value")
        return [_coerce_single(v, vt, spec) for v in value]

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise InvalidCriteria(
                "operator 'between' requires a 2-element list [low, high]"
            )
        return [_coerce_single(v, vt, spec) for v in value]

    return _coerce_single(value, vt, spec)


# ---------------------------------------------------------------------------
# Step 5: Expression builder
# ---------------------------------------------------------------------------


def _build_expr(spec: FieldSpec, op: str, coerced: Any, dialect: str) -> Any:
    """Build a parameterized SQLAlchemy WHERE expression for one leaf node.

    User input NEVER flows into a text() template — all values go through
    SQLAlchemy's bind-parameter mechanism.
    """
    col = spec.resolve(dialect)

    if op == "is_set":
        return col.is_not(None)
    if op == "is_empty":
        return col.is_(None)

    if op == "eq":
        return col == coerced
    if op == "ne":
        return col != coerced
    if op == "gt":
        return col > coerced
    if op == "gte":
        return col >= coerced
    if op == "lt":
        return col < coerced
    if op == "lte":
        return col <= coerced
    if op == "before":
        return col < coerced
    if op == "after":
        return col > coerced
    if op == "on_or_before":
        return col <= coerced
    if op == "on_or_after":
        return col >= coerced

    if op == "between":
        return col.between(coerced[0], coerced[1])

    if op == "in":
        return col.in_(coerced)

    if op in ("contains", "not_contains", "starts_with", "ends_with"):
        escaped = _escape_like(str(coerced))
        if op == "contains":
            pattern = f"%{escaped}%"
        elif op == "not_contains":
            pattern = f"%{escaped}%"
        elif op == "starts_with":
            pattern = f"{escaped}%"
        else:  # ends_with
            pattern = f"%{escaped}"
        expr = col.ilike(pattern, escape="\\")
        if op == "not_contains":
            return ~expr
        return expr

    if op == "contains_any":
        # Multiselect: stored JSON array; check if array text contains any value.
        # col is json_extract result (SQLite) or astext JSONB extraction (PG).
        # For both dialects the result is the JSON array as a text string, e.g. '["a","b"]'.
        # Use LIKE '%"val"%' with escape="\\" so that % and _ in values that were
        # escaped by _escape_like() are treated literally (not as wildcards).
        conditions = []
        for v in coerced:
            escaped = _escape_like(str(v))
            conditions.append(col.like(f'%"{escaped}"%', escape="\\"))
        if not conditions:
            return false()
        return or_(*conditions)

    raise InvalidCriteria(f"unknown operator '{op}'")


# ---------------------------------------------------------------------------
# Recursive node compiler
# ---------------------------------------------------------------------------


def _compile_node(
    node: dict[str, Any],
    registry: dict[str, FieldSpec],
    dialect: str,
) -> Any:
    """Recursively compile one criteria node into a SQLAlchemy expression."""
    if "logic" in node:
        logic = str(node.get("logic", "and")).lower()
        children = node.get("conditions", [])
        if not children:
            return true()
        exprs = [_compile_node(c, registry, dialect) for c in children]
        if logic == "or":
            return or_(*exprs)
        return and_(*exprs)

    # Leaf node — apply the full validation pipeline
    field_key = node.get("field")
    op = str(node.get("op", ""))
    value = node.get("value")

    # Step 2: WHITELIST — the raw field identifier MUST be a registry key;
    # it is NEVER interpolated into SQL as a literal identifier.
    if field_key not in registry:
        raise InvalidCriteria(f"unknown field '{field_key}'")

    spec = registry[field_key]

    # Step 3: operator whitelist
    if op not in spec.allowed_ops:
        raise InvalidCriteria(
            f"operator '{op}' is not allowed for field '{field_key}'"
        )

    # Step 4: type-coerce (LIKE ops: value escaped inside _build_expr)
    coerced = _coerce_value(value, op, spec)

    # Step 5: build parameterized expression
    return _build_expr(spec, op, coerced, dialect)


# ---------------------------------------------------------------------------
# Public compile_criteria — the injection gate
# ---------------------------------------------------------------------------


def compile_criteria(
    criteria: Optional[dict[str, Any]],
    registry: dict[str, FieldSpec],
    dialect: str,
    *,
    include_deleted: bool = False,
) -> Any:
    """7-step criteria compiler.  Returns a SQLAlchemy WHERE clause.

    Step 1 — structure + bounds guard (depth ≤ 10, nodes ≤ 100, list ≤ 200).
    Step 2 — field whitelist (raw identifier never reaches SQL).
    Step 3 — operator whitelist per spec.allowed_ops.
    Step 4 — type-coerce value per value_type; LIKE ops escape % _ \\.
    Step 5 — build PARAMETERIZED expr via spec.resolve(dialect).
    Step 6 — append Contact.is_deleted==False OUTSIDE user tree (unless include_deleted).
    Step 7 — return whereclause.

    Empty/absent criteria → match-all (deleted filter still applied).
    """
    if not criteria:
        user_clause = true()
    else:
        # Step 1
        _check_bounds(criteria)
        # Steps 2-5
        user_clause = _compile_node(criteria, registry, dialect)

    # Step 6: deleted filter appended OUTSIDE user tree so it cannot be bypassed
    if include_deleted:
        return user_clause
    return and_(user_clause, Contact.is_deleted == False)  # noqa: E712


# ---------------------------------------------------------------------------
# run_search
# ---------------------------------------------------------------------------


async def run_search(
    db: AsyncSession,
    criteria: Optional[dict[str, Any]],
    page: int,
    page_size: int,
    include_deleted: bool = False,
) -> tuple[int, list[Contact]]:
    """Build registry, compile, run paginated count + select.

    page_size is clamped to max 100.
    Returns (total, rows).
    """
    page_size = min(max(page_size, 1), 100)
    page = max(page, 1)
    offset = (page - 1) * page_size

    registry = await build_registry(db)
    dialect = _dialect_name()
    where = compile_criteria(criteria, registry, dialect, include_deleted=include_deleted)

    count_stmt = select(func.count(Contact.id)).where(where)
    total: int = (await db.execute(count_stmt)).scalar_one()

    data_stmt = (
        select(Contact)
        .where(where)
        .order_by(Contact.last_name, Contact.first_name, Contact.id)
        .offset(offset)
        .limit(page_size)
    )
    rows = list((await db.execute(data_stmt)).scalars().all())

    return total, rows


# ---------------------------------------------------------------------------
# Saved Search CRUD (owner-scoped)
# ---------------------------------------------------------------------------


async def list_saved_searches(
    db: AsyncSession,
    owner_id: int,
) -> list[SavedSearch]:
    """List all saved searches owned by *owner_id*."""
    result = await db.execute(
        select(SavedSearch)
        .where(SavedSearch.owner_id == owner_id)
        .order_by(SavedSearch.name)
    )
    return list(result.scalars().all())


async def create_saved_search(
    db: AsyncSession,
    owner_id: int,
    name: str,
    entity: str,
    criteria: dict[str, Any],
    actor_id: int,
) -> SavedSearch:
    """Create a saved search, validating criteria first.

    Raises InvalidCriteria if criteria are structurally invalid.
    Raises HTTPException(409) on duplicate name for this owner.
    """
    registry = await build_registry(db)
    dialect = _dialect_name()
    compile_criteria(criteria, registry, dialect)  # raises InvalidCriteria on failure

    saved = SavedSearch(
        owner_id=owner_id,
        name=name,
        entity=entity,
        criteria=criteria,
    )
    db.add(saved)
    try:
        await db.flush()
    except Exception as exc:
        err = str(exc).upper()
        if "UQ_SAVED_SEARCHES_OWNER_NAME" in err or "UNIQUE" in err:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A saved search named '{name}' already exists",
            ) from exc
        raise

    await audit_svc.record(
        db, actor_id, "saved_search.create", "saved_search", saved.id,
        None, {"name": name, "entity": entity},
    )
    await db.commit()
    await db.refresh(saved)
    return saved


async def get_saved_search(
    db: AsyncSession,
    search_id: int,
    owner_id: int,
) -> Optional[SavedSearch]:
    """Return saved search *search_id* only if owned by *owner_id*, else None."""
    result = await db.execute(
        select(SavedSearch).where(
            SavedSearch.id == search_id,
            SavedSearch.owner_id == owner_id,
        )
    )
    return result.scalar_one_or_none()


async def update_saved_search(
    db: AsyncSession,
    saved: SavedSearch,
    updates: dict[str, Any],
    actor_id: int,
) -> SavedSearch:
    """Apply a partial update.  Re-validates criteria if included in updates."""
    if "criteria" in updates and updates["criteria"] is not None:
        registry = await build_registry(db)
        dialect = _dialect_name()
        compile_criteria(updates["criteria"], registry, dialect)

    before = {"name": saved.name, "criteria": saved.criteria}
    for k, v in updates.items():
        if v is not None:
            setattr(saved, k, v)
    saved.updated_at = utc_now()

    await db.flush()
    await audit_svc.record(
        db, actor_id, "saved_search.update", "saved_search", saved.id,
        before, {"name": saved.name, "criteria": saved.criteria},
    )
    await db.commit()
    await db.refresh(saved)
    return saved


async def delete_saved_search(
    db: AsyncSession,
    saved: SavedSearch,
    actor_id: int,
) -> None:
    """Permanently delete a saved search."""
    before = {"name": saved.name}
    search_id = saved.id
    await db.delete(saved)
    await audit_svc.record(
        db, actor_id, "saved_search.delete", "saved_search", search_id,
        before, None,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Group functions
# ---------------------------------------------------------------------------


async def list_groups(
    db: AsyncSession,
    group_type: Optional[str] = None,
    entity: Optional[str] = None,
    with_counts: bool = True,
) -> list[dict[str, Any]]:
    """List groups, optionally filtered.

    Returns list of dicts: {'group': Group, 'member_count': int|None}.
    member_count is None when with_counts=False.
    """
    stmt = select(Group).order_by(Group.name)
    if group_type:
        stmt = stmt.where(Group.group_type == group_type)
    if entity:
        stmt = stmt.where(Group.entity == entity)

    groups = list((await db.execute(stmt)).scalars().all())

    if not with_counts:
        return [{"group": g, "member_count": None} for g in groups]

    out: list[dict[str, Any]] = []
    for g in groups:
        if g.group_type == "static":
            count_row = await db.execute(
                select(func.count(GroupMember.id)).where(GroupMember.group_id == g.id)
            )
            count = count_row.scalar_one()
        else:
            # Smart group: live criteria evaluation
            try:
                registry = await build_registry(db)
                dialect = _dialect_name()
                where = compile_criteria(g.criteria, registry, dialect, include_deleted=False)
                count_row = await db.execute(
                    select(func.count(Contact.id)).where(where)
                )
                count = count_row.scalar_one()
            except (InvalidCriteria, Exception):
                count = 0
        out.append({"group": g, "member_count": count})
    return out


async def create_group(
    db: AsyncSession,
    name: str,
    entity: str,
    group_type: str,
    criteria: Optional[dict[str, Any]],
    owner_id: int,
    actor_id: int,
) -> Group:
    """Create a group.

    Smart groups require valid criteria.
    Raises HTTPException(409) on duplicate name+entity.
    """
    if group_type == "smart":
        if not criteria:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="criteria is required for smart groups",
            )
        registry = await build_registry(db)
        dialect = _dialect_name()
        try:
            compile_criteria(criteria, registry, dialect)
        except InvalidCriteria as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    else:
        criteria = None  # static groups store no criteria

    group = Group(
        name=name,
        entity=entity,
        group_type=group_type,
        criteria=criteria,
        owner_id=owner_id,
    )
    db.add(group)
    try:
        await db.flush()
    except Exception as exc:
        err = str(exc).upper()
        if "UQ_GROUPS_NAME_ENTITY" in err or "UNIQUE" in err:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group '{name}' already exists for entity '{entity}'",
            ) from exc
        raise

    await audit_svc.record(
        db, actor_id, "group.create", "group", group.id,
        None, {"name": name, "entity": entity, "group_type": group_type},
    )
    await db.commit()
    await db.refresh(group)
    return group


async def get_group(db: AsyncSession, group_id: int) -> Optional[Group]:
    """Return Group by id or None."""
    result = await db.execute(select(Group).where(Group.id == group_id))
    return result.scalar_one_or_none()


async def update_group(
    db: AsyncSession,
    group: Group,
    updates: dict[str, Any],
    actor_id: int,
    is_admin: bool = False,
) -> Group:
    """Partial update.  group_type is immutable.  Smart criteria edit admin-only."""
    before = {"name": group.name, "criteria": group.criteria}

    if "name" in updates and updates["name"] is not None:
        group.name = updates["name"]

    if "criteria" in updates and updates["criteria"] is not None:
        if group.group_type != "smart":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="criteria can only be updated on smart groups",
            )
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only admins can edit smart group criteria",
            )
        registry = await build_registry(db)
        dialect = _dialect_name()
        try:
            compile_criteria(updates["criteria"], registry, dialect)
        except InvalidCriteria as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        group.criteria = updates["criteria"]

    group.updated_at = utc_now()
    await db.flush()
    await audit_svc.record(
        db, actor_id, "group.update", "group", group.id,
        before, {"name": group.name, "criteria": group.criteria},
    )
    await db.commit()
    await db.refresh(group)
    return group


async def delete_group(
    db: AsyncSession,
    group: Group,
    actor_id: int,
) -> None:
    """Delete a group (cascades to group_members)."""
    before = {"name": group.name, "group_type": group.group_type}
    group_id = group.id
    await db.delete(group)
    await audit_svc.record(
        db, actor_id, "group.delete", "group", group_id,
        before, None,
    )
    await db.commit()


async def resolve_group_contacts(
    db: AsyncSession,
    group: Group,
    page: int,
    page_size: int,
) -> tuple[int, list[Contact]]:
    """Resolve contacts in *group* with pagination.

    Static: frozen snapshot via group_members JOIN contacts.
    Smart: live criteria compilation (Contact.is_deleted==False enforced).
    """
    page_size = min(max(page_size, 1), 100)
    page = max(page, 1)
    offset = (page - 1) * page_size

    if group.group_type == "static":
        count_stmt = (
            select(func.count(Contact.id))
            .join(GroupMember, GroupMember.contact_id == Contact.id)
            .where(GroupMember.group_id == group.id)
        )
        total = (await db.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(Contact)
            .join(GroupMember, GroupMember.contact_id == Contact.id)
            .where(GroupMember.group_id == group.id)
            .order_by(Contact.last_name, Contact.first_name, Contact.id)
            .offset(offset)
            .limit(page_size)
        )
        rows = list((await db.execute(data_stmt)).scalars().all())
    else:
        # Smart: live
        registry = await build_registry(db)
        dialect = _dialect_name()
        where = compile_criteria(group.criteria, registry, dialect, include_deleted=False)

        total = (await db.execute(
            select(func.count(Contact.id)).where(where)
        )).scalar_one()

        rows = list((await db.execute(
            select(Contact)
            .where(where)
            .order_by(Contact.last_name, Contact.first_name, Contact.id)
            .offset(offset)
            .limit(page_size)
        )).scalars().all())

    return total, rows


async def add_members_to_group(
    db: AsyncSession,
    group: Group,
    contact_ids: list[int],
    actor_id: int,
) -> dict[str, int]:
    """Add contacts to a static group with on_conflict_do_nothing.

    Returns {added, skipped}.
    Raises HTTPException(400) for smart groups.
    """
    if group.group_type == "smart":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot manually add members to a smart group",
        )

    if not contact_ids:
        return {"added": 0, "skipped": 0}

    dialect = _dialect_name()
    rows = [
        {
            "group_id": group.id,
            "contact_id": cid,
            "added_by_id": actor_id,
            "added_at": utc_now(),
        }
        for cid in contact_ids
    ]

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        stmt = pg_insert(GroupMember).values(rows).on_conflict_do_nothing(
            index_elements=["group_id", "contact_id"]
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: PLC0415

        stmt = sqlite_insert(GroupMember).values(rows).on_conflict_do_nothing(
            index_elements=["group_id", "contact_id"]
        )

    result = await db.execute(stmt)
    added = result.rowcount if result.rowcount >= 0 else len(contact_ids)
    skipped = len(contact_ids) - added

    await audit_svc.record(
        db, actor_id, "group.add_members", "group", group.id,
        None, {"contact_count": len(contact_ids), "added": added, "skipped": skipped},
    )
    await db.commit()
    return {"added": added, "skipped": skipped}


async def remove_member_from_group(
    db: AsyncSession,
    group: Group,
    contact_id: int,
    actor_id: int,
) -> bool:
    """Remove one contact from a static group.

    Returns True if removed, False if membership not found.
    Raises HTTPException(400) for smart groups.
    """
    if group.group_type == "smart":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove members from a smart group",
        )

    result = await db.execute(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.contact_id == contact_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return False

    await db.delete(member)
    await audit_svc.record(
        db, actor_id, "group.remove_member", "group", group.id,
        {"contact_id": contact_id}, None,
    )
    await db.commit()
    return True


async def populate_static_group(
    db: AsyncSession,
    group: Group,
    criteria: Optional[dict[str, Any]],
    saved_search_id: Optional[int],
    mode: str,
    actor_id: int,
    owner_id: Optional[int] = None,
) -> dict[str, int]:
    """Populate a static group from criteria or saved search.

    mode='replace': clear existing members first.
    mode='append': add without removing existing.

    When saved_search_id is provided, owner_id is required and the saved search
    is scoped to that owner (cross-owner → 404) to prevent IDOR.

    Raises HTTPException(400) for smart groups.
    """
    if group.group_type == "smart":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot populate a smart group — membership is computed dynamically",
        )

    # Resolve effective criteria
    effective_criteria: Optional[dict[str, Any]] = criteria
    if saved_search_id is not None and effective_criteria is None:
        # IDOR fix: scope SavedSearch lookup to the requesting owner.
        ss_filter = [SavedSearch.id == saved_search_id]
        if owner_id is not None:
            ss_filter.append(SavedSearch.owner_id == owner_id)
        ss_result = await db.execute(select(SavedSearch).where(*ss_filter))
        ss = ss_result.scalar_one_or_none()
        if ss is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Saved search {saved_search_id} not found",
            )
        effective_criteria = ss.criteria

    registry = await build_registry(db)
    dialect = _dialect_name()
    try:
        where = compile_criteria(
            effective_criteria, registry, dialect, include_deleted=False
        )
    except InvalidCriteria as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    id_rows = await db.execute(select(Contact.id).where(where))
    contact_ids = [r[0] for r in id_rows.all()]

    if mode == "replace":
        existing = list((await db.execute(
            select(GroupMember).where(GroupMember.group_id == group.id)
        )).scalars().all())
        for m in existing:
            await db.delete(m)
        await db.flush()

    if not contact_ids:
        await audit_svc.record(
            db, actor_id, "group.populate", "group", group.id,
            None, {"mode": mode, "added": 0, "skipped": 0},
        )
        await db.commit()
        return {"added": 0, "skipped": 0}

    rows = [
        {
            "group_id": group.id,
            "contact_id": cid,
            "added_by_id": actor_id,
            "added_at": utc_now(),
        }
        for cid in contact_ids
    ]

    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        stmt = pg_insert(GroupMember).values(rows).on_conflict_do_nothing(
            index_elements=["group_id", "contact_id"]
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert  # noqa: PLC0415

        stmt = sqlite_insert(GroupMember).values(rows).on_conflict_do_nothing(
            index_elements=["group_id", "contact_id"]
        )

    result = await db.execute(stmt)
    added = result.rowcount if result.rowcount >= 0 else len(contact_ids)
    skipped = len(contact_ids) - added

    await audit_svc.record(
        db, actor_id, "group.populate", "group", group.id,
        None, {"mode": mode, "added": added, "skipped": skipped},
    )
    await db.commit()
    return {"added": added, "skipped": skipped}


async def promote_saved_search_to_group(
    db: AsyncSession,
    saved_search: SavedSearch,
    group_name: str,
    entity: str,
    actor_id: int,
) -> Group:
    """Create a smart group from a saved search.

    Raises HTTPException(409) if group name already exists for entity.
    """
    existing = (await db.execute(
        select(Group).where(Group.name == group_name, Group.entity == entity)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{group_name}' already exists for entity '{entity}'",
        )

    group = Group(
        name=group_name,
        entity=entity,
        group_type="smart",
        criteria=saved_search.criteria,
        owner_id=actor_id,
    )
    db.add(group)
    await db.flush()
    await audit_svc.record(
        db, actor_id, "group.create", "group", group.id,
        None, {
            "name": group_name,
            "entity": entity,
            "source": "promote_saved_search",
            "saved_search_id": saved_search.id,
        },
    )
    await db.commit()
    await db.refresh(group)
    return group
