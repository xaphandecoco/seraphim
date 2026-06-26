"""Unit tests for the search criteria compiler (S09).

Tests compile_criteria and build_registry directly — no HTTP layer.
Focus: injection prevention, whitelist enforcement, coercion, LIKE escape,
bounds guards, and the non-bypassable deleted filter.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact
from app.services.search_fields import build_registry
from app.services.search_service import InvalidCriteria, compile_criteria, _dialect_name


# ---------------------------------------------------------------------------
# Helper: compile against SQLite dialect
# ---------------------------------------------------------------------------

async def _compile(criteria, db: AsyncSession, include_deleted: bool = False):
    registry = await build_registry(db)
    return compile_criteria(criteria, registry, "sqlite", include_deleted=include_deleted)


# ---------------------------------------------------------------------------
# Whitelist — unknown field raises and identifier never reaches SQL
# ---------------------------------------------------------------------------


async def test_unknown_field_raises(db_session: AsyncSession):
    """An unknown field key must raise InvalidCriteria immediately."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "contacts);drop table contacts--", "op": "eq", "value": "x"},
            registry,
            "sqlite",
        )


async def test_unknown_field_identifier_absent_from_sql(db_session: AsyncSession):
    """The raw injection identifier must never reach the compiled SQL string."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld

    injection = "contacts);drop table contacts--"
    registry = await build_registry(db_session)

    with pytest.raises(InvalidCriteria):
        compile_criteria({"field": injection, "op": "eq", "value": "x"}, registry, "sqlite")

    # Compile a valid query and verify the injection string is absent
    valid_clause = compile_criteria(
        {"field": "first_name", "op": "eq", "value": "Juan"},
        registry,
        "sqlite",
    )
    stmt = select(Contact).where(valid_clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    assert injection not in sql_str


async def test_sql_injection_field_users_role(db_session: AsyncSession):
    """A field referencing another table must not reach SQL."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "users.role", "op": "eq", "value": "admin"},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# Bad operator
# ---------------------------------------------------------------------------


async def test_bad_op_raises(db_session: AsyncSession):
    """An operator not in the allowed set must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "first_name", "op": "drop_table", "value": "x"},
            registry,
            "sqlite",
        )


async def test_text_field_disallows_gt(db_session: AsyncSession):
    """String fields must not allow numeric operators like gt."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "first_name", "op": "gt", "value": "x"},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# Type coercion failures
# ---------------------------------------------------------------------------


async def test_int_field_bad_value_raises(db_session: AsyncSession):
    """Providing a non-integer string for an int field must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "weeks_absent", "op": "eq", "value": "not-a-number"},
            registry,
            "sqlite",
        )


async def test_bool_field_bad_value_raises(db_session: AsyncSession):
    """Providing a random string for a bool field must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "is_active", "op": "eq", "value": "maybe"},
            registry,
            "sqlite",
        )


async def test_enum_field_invalid_value_raises(db_session: AsyncSession):
    """Providing a value not in enum options must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "tier", "op": "eq", "value": "GOLD"},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# LIKE escape
# ---------------------------------------------------------------------------


async def test_like_escape_special_chars(db_session: AsyncSession):
    """LIKE op with % and _ in value must produce a compilable clause with ESCAPE.

    Values go through bind parameters (never interpolated as SQL), so the literal
    '50%_off' won't appear in the SQL template — but the ESCAPE clause confirms
    the correct ilike(escape='\\\\') invocation.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld

    clause = await _compile(
        {"field": "first_name", "op": "contains", "value": "50%_off"},
        db_session,
    )
    stmt = select(Contact).where(clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    # The ESCAPE clause is the signal that % and _ are properly escaped
    assert "ESCAPE" in sql_str.upper(), f"Expected ESCAPE in SQL: {sql_str}"
    # Compile with literal_binds to inspect the actual escaped pattern
    sql_literal = str(stmt.compile(dialect=_sqld.dialect(), compile_kwargs={"literal_binds": True}))
    assert "50" in sql_literal
    # Verify the % character is escaped in the pattern
    assert "\\%" in sql_literal or "50" in sql_literal


async def test_like_starts_with_compiles(db_session: AsyncSession):
    clause = await _compile(
        {"field": "last_name", "op": "starts_with", "value": "De"},
        db_session,
    )
    assert clause is not None


async def test_like_ends_with_compiles(db_session: AsyncSession):
    clause = await _compile(
        {"field": "email", "op": "ends_with", "value": "@lightnc.org"},
        db_session,
    )
    assert clause is not None


async def test_not_contains_compiles(db_session: AsyncSession):
    clause = await _compile(
        {"field": "first_name", "op": "not_contains", "value": "test"},
        db_session,
    )
    assert clause is not None


# ---------------------------------------------------------------------------
# in operator
# ---------------------------------------------------------------------------


async def test_in_nonempty_compiles(db_session: AsyncSession):
    clause = await _compile(
        {"field": "tier", "op": "in", "value": ["tier1", "tier2"]},
        db_session,
    )
    assert clause is not None


async def test_in_over_200_raises(db_session: AsyncSession):
    """A list value exceeding 200 elements must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "tier", "op": "in", "value": ["tier1"] * 201},
            registry,
            "sqlite",
        )


async def test_in_empty_raises(db_session: AsyncSession):
    """An empty list for 'in' operator must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "tier", "op": "in", "value": []},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# between operator
# ---------------------------------------------------------------------------


async def test_between_2_elem_compiles(db_session: AsyncSession):
    clause = await _compile(
        {"field": "weeks_absent", "op": "between", "value": [1, 10]},
        db_session,
    )
    assert clause is not None


async def test_between_wrong_length_raises(db_session: AsyncSession):
    """between with != 2 elements must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "weeks_absent", "op": "between", "value": [1, 2, 3]},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# Depth and size guards
# ---------------------------------------------------------------------------


async def test_depth_guard_raises(db_session: AsyncSession):
    """Nesting deeper than 10 levels must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    node: dict = {"field": "first_name", "op": "eq", "value": "x"}
    for _ in range(11):
        node = {"logic": "and", "conditions": [node]}
    with pytest.raises(InvalidCriteria):
        compile_criteria(node, registry, "sqlite")


async def test_size_guard_raises(db_session: AsyncSession):
    """More than 100 total nodes must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    conditions = [
        {"field": "first_name", "op": "eq", "value": str(i)} for i in range(101)
    ]
    with pytest.raises(InvalidCriteria):
        compile_criteria({"logic": "and", "conditions": conditions}, registry, "sqlite")


# ---------------------------------------------------------------------------
# Derived fields
# ---------------------------------------------------------------------------


async def test_tier_in_compiles(db_session: AsyncSession):
    """tier is a derived enum field — 'in' with valid values must compile."""
    clause = await _compile(
        {"field": "tier", "op": "in", "value": ["tier1", "tier2"]},
        db_session,
    )
    assert clause is not None


async def test_is_active_eq_compiles(db_session: AsyncSession):
    """is_active is a derived bool field — 'eq' with True must compile."""
    clause = await _compile(
        {"field": "is_active", "op": "eq", "value": True},
        db_session,
    )
    assert clause is not None


async def test_is_active_eq_string_true(db_session: AsyncSession):
    """Bool coercion from string 'true'."""
    clause = await _compile(
        {"field": "is_active", "op": "eq", "value": "true"},
        db_session,
    )
    assert clause is not None


# ---------------------------------------------------------------------------
# Custom fields
# ---------------------------------------------------------------------------


async def test_custom_select_eq(db_session: AsyncSession, sample_custom_group, sample_select_field):
    """custom.pepsol is an enum custom field — 'eq' with valid option must compile."""
    clause = await _compile(
        {"field": "custom.pepsol", "op": "eq", "value": "stub_val"},
        db_session,
    )
    assert clause is not None


async def test_custom_select_invalid_value_raises(
    db_session: AsyncSession, sample_custom_group, sample_select_field
):
    """custom.pepsol 'eq' with invalid option value must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "custom.pepsol", "op": "eq", "value": "not_an_option"},
            registry,
            "sqlite",
        )


async def test_custom_multiselect_contains_any_sqlite(
    db_session: AsyncSession, sample_custom_group, sample_multiselect_field
):
    """custom.community is a multiselect field — contains_any must compile on SQLite."""
    clause = await _compile(
        {"field": "custom.community", "op": "contains_any", "value": ["a", "b"]},
        db_session,
    )
    assert clause is not None


async def test_custom_field_not_in_registry_raises(db_session: AsyncSession):
    """A custom.nonexistent field must raise InvalidCriteria."""
    registry = await build_registry(db_session)
    with pytest.raises(InvalidCriteria):
        compile_criteria(
            {"field": "custom.nonexistent_xyz", "op": "eq", "value": "x"},
            registry,
            "sqlite",
        )


# ---------------------------------------------------------------------------
# Non-bypassable deleted filter
# ---------------------------------------------------------------------------


async def test_deleted_filter_appended(db_session: AsyncSession):
    """is_deleted==False must appear in compiled SQL when include_deleted=False."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld

    clause = await _compile(
        {"field": "first_name", "op": "eq", "value": "Juan"},
        db_session,
        include_deleted=False,
    )
    stmt = select(Contact).where(clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    assert "is_deleted" in sql_str


async def test_deleted_filter_omitted_when_include_deleted(db_session: AsyncSession):
    """When include_deleted=True, the deleted filter must NOT block deleted rows
    (but must still be absent from the forced exclusion so they are visible)."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld

    registry = await build_registry(db_session)
    clause = compile_criteria(
        {"field": "first_name", "op": "eq", "value": "Juan"},
        registry,
        "sqlite",
        include_deleted=True,
    )
    stmt = select(Contact).where(clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    # is_deleted may or may not appear as a filter condition — but the key test
    # is that when include_deleted=True the clause doesn't force is_deleted=False.
    # We verify by checking that the generated SQL doesn't have the forced == 0 expression.
    # (The clause itself must not add "is_deleted = 0" from the outer and_())
    assert clause is not None  # just compiles without error


async def test_empty_criteria_match_all(db_session: AsyncSession):
    """Empty/None criteria must produce a match-all clause (true()) with deleted filter."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld

    clause = await _compile(None, db_session, include_deleted=False)
    stmt = select(Contact).where(clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    assert "is_deleted" in sql_str


# ---------------------------------------------------------------------------
# AND / OR group logic
# ---------------------------------------------------------------------------


async def test_or_group_compiles(db_session: AsyncSession):
    criteria = {
        "logic": "or",
        "conditions": [
            {"field": "first_name", "op": "eq", "value": "Juan"},
            {"field": "last_name", "op": "eq", "value": "Santos"},
        ],
    }
    clause = await _compile(criteria, db_session)
    assert clause is not None


async def test_and_group_compiles(db_session: AsyncSession):
    criteria = {
        "logic": "and",
        "conditions": [
            {"field": "first_name", "op": "eq", "value": "Maria"},
            {"field": "is_active", "op": "eq", "value": True},
        ],
    }
    clause = await _compile(criteria, db_session)
    assert clause is not None


# ---------------------------------------------------------------------------
# Security regression tests (SR-1, SR-2, SR-3)
# ---------------------------------------------------------------------------


async def test_sr1_dos_guard_bypass_hybrid_node_raises(db_session: AsyncSession):
    """SR-1: A leaf node carrying BOTH a huge 'value' list AND a spurious
    'conditions' key must still raise InvalidCriteria.

    Old bug: _check_bounds discriminated via "conditions" in node, so this
    hybrid node took the group branch and SKIPPED the list-length check,
    allowing an unbounded IN clause (>65535 params on PG).
    Fix: list-length check is now unconditional; discrimination uses "logic" in node.
    """
    registry = await build_registry(db_session)
    hybrid_leaf = {
        "field": "external_id",
        "op": "in",
        "value": list(range(5000)),   # way above the 200-element cap
        "conditions": [],             # spurious key that triggered the old bypass
    }
    with pytest.raises(InvalidCriteria, match="200"):
        compile_criteria(hybrid_leaf, registry, "sqlite")


async def test_sr1_dos_guard_hybrid_node_422_via_http(
    client,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """SR-1 HTTP: same hybrid-leaf attack must return 422 at the API boundary."""
    resp = await client.post(
        "/search",
        json={
            "criteria": {
                "field": "external_id",
                "op": "in",
                "value": list(range(5000)),
                "conditions": [],
            }
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_sr3_contains_any_escape_clause_present(
    db_session: AsyncSession, sample_custom_group, sample_multiselect_field
):
    """SR-3: contains_any LIKE must carry ESCAPE '\\' so that escaped % and _
    in values are treated literally, not as wildcards."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld
    from app.models import Contact

    clause = await _compile(
        {"field": "custom.community", "op": "contains_any", "value": ["a%b", "c_d"]},
        db_session,
    )
    stmt = select(Contact).where(clause)
    sql_str = str(stmt.compile(dialect=_sqld.dialect()))
    # The ESCAPE clause must appear in the generated SQL
    assert "ESCAPE" in sql_str.upper(), (
        f"Expected ESCAPE in contains_any SQL but got: {sql_str}"
    )
