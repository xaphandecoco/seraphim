"""QA test file for s02-custom-fields-service acceptance criteria.

Covers gaps in AC1, AC6, AC9 that are not fully exercised in the existing
test_custom_fields_validation.py:
  - AC1: text strip+max1000, textarea max5000, number int-narrowing edge cases,
         date ISO-only (no datetime), checkbox falsy string coercion
  - AC6: count_contacts_with_field_data SQLite correctness
  - AC9: get_active_schema uses exactly 2 queries regardless of group count

All tests use async fixtures from conftest.py.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, CustomFieldDef, CustomFieldGroup
from app.services.custom_fields import (
    count_contacts_with_field_data,
    get_active_schema,
    validate_and_coerce,
)


# ---------------------------------------------------------------------------
# AC1: text — strip whitespace, enforce max 1000 chars
# ---------------------------------------------------------------------------


async def test_text_strips_leading_trailing_whitespace(db_session: AsyncSession, sample_custom_group):
    """text field: leading/trailing whitespace is stripped."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="text_strip_test",
        label="Text Strip",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"text_strip_test": "  hello  "})
    assert result["text_strip_test"] == "hello"


async def test_text_max_1000_chars_accepted(db_session: AsyncSession, sample_custom_group):
    """text field: exactly 1000 chars is accepted."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="text_max_ok",
        label="Text Max OK",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"text_max_ok": "a" * 1000})
    assert len(result["text_max_ok"]) == 1000


async def test_text_exceeds_max_1000_rejected(db_session: AsyncSession, sample_custom_group):
    """text field: 1001 chars -> HTTPException 422 too_long."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="text_toolong",
        label="Text Too Long",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"text_toolong": "a" * 1001})
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "too_long" for e in errors)


# ---------------------------------------------------------------------------
# AC1: textarea — strip whitespace, enforce max 5000 chars
# ---------------------------------------------------------------------------


async def test_textarea_strips_whitespace(db_session: AsyncSession, sample_custom_group):
    """textarea field: leading/trailing whitespace is stripped."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="textarea_strip",
        label="Textarea Strip",
        data_type="textarea",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"textarea_strip": "  notes  "})
    assert result["textarea_strip"] == "notes"


async def test_textarea_max_5000_accepted(db_session: AsyncSession, sample_custom_group):
    """textarea field: exactly 5000 chars is accepted."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="textarea_max_ok",
        label="Textarea Max OK",
        data_type="textarea",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"textarea_max_ok": "a" * 5000})
    assert len(result["textarea_max_ok"]) == 5000


async def test_textarea_exceeds_max_5000_rejected(db_session: AsyncSession, sample_custom_group):
    """textarea field: 5001 chars -> HTTPException 422 too_long."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="textarea_toolong",
        label="Textarea Too Long",
        data_type="textarea",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"textarea_toolong": "a" * 5001})
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "too_long" for e in errors)


# ---------------------------------------------------------------------------
# AC1: number — int-narrowing: 3.0 -> 3 (int), 3.5 stays float
# ---------------------------------------------------------------------------


async def test_number_int_narrowing_lossless(db_session: AsyncSession, sample_custom_group):
    """number field: 3.0 (float with no fractional part) -> stored as int 3."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="num_narrow",
        label="Num Narrow",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"num_narrow": 3.0})
    assert result["num_narrow"] == 3
    assert isinstance(result["num_narrow"], int)


async def test_number_float_stays_float(db_session: AsyncSession, sample_custom_group):
    """number field: 3.5 (fractional) stays as float."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="num_float",
        label="Num Float",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"num_float": 3.5})
    assert result["num_float"] == 3.5
    assert isinstance(result["num_float"], float)


# ---------------------------------------------------------------------------
# AC1: date — must be YYYY-MM-DD only, not datetime
# ---------------------------------------------------------------------------


async def test_date_with_time_component_rejected(db_session: AsyncSession, sample_custom_group):
    """date field: '2026-01-31T10:00:00' (datetime) -> HTTPException 422 bad_date.

    fromisoformat() accepts datetimes; the service guards against this with
    a len==10 check.
    """
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="date_datetime",
        label="Date Datetime",
        data_type="date",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"date_datetime": "2026-01-31T10:00:00"})
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "bad_date" for e in errors)


async def test_date_invalid_month_rejected(db_session: AsyncSession, sample_custom_group):
    """date field: '2026-13-01' (invalid month 13) -> HTTPException 422 bad_date."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="date_bad_month",
        label="Date Bad Month",
        data_type="date",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"date_bad_month": "2026-13-01"})
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "bad_date" for e in errors)


# ---------------------------------------------------------------------------
# AC1: checkbox — non-bool/str/int types coerce via bool()
# ---------------------------------------------------------------------------


async def test_checkbox_arbitrary_truthy_object(db_session: AsyncSession, sample_checkbox_field):
    """checkbox field: non-zero int coerces to True."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_checkbox_field.name: 42}
    )
    assert result[sample_checkbox_field.name] is True


async def test_checkbox_explicit_false_stored_not_dropped(db_session: AsyncSession, sample_checkbox_field):
    """checkbox field: False stored explicitly — not dropped from output (AC7 idempotence)."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_checkbox_field.name: False}
    )
    # False must appear in output, not be dropped
    assert sample_checkbox_field.name in result
    assert result[sample_checkbox_field.name] is False


# ---------------------------------------------------------------------------
# AC3: Unknown keys raise HTTPException 422 with structured detail
# ---------------------------------------------------------------------------


async def test_unknown_key_error_detail_structure(db_session: AsyncSession, sample_custom_group):
    """AC3: detail list contains {field: 'xyz', error: 'unknown_field'}."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"not_a_field": "x"})
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert isinstance(detail, list)
    assert len(detail) >= 1
    matching = [e for e in detail if e.get("field") == "not_a_field"]
    assert matching, f"Expected field='not_a_field' in errors, got: {detail}"
    assert matching[0]["error"] == "unknown_field"


async def test_multiple_unknown_keys_all_reported(db_session: AsyncSession, sample_custom_group):
    """AC3: multiple unknown keys -> all reported in the error list."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(
            db_session, "contact", {"bad_key_1": "x", "bad_key_2": "y"}
        )
    await db_session.rollback()
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    field_names = {e["field"] for e in detail}
    assert "bad_key_1" in field_names
    assert "bad_key_2" in field_names


# ---------------------------------------------------------------------------
# AC4: multiselect dedup preserves order
# ---------------------------------------------------------------------------


async def test_multiselect_dedup_order_preserved(db_session: AsyncSession, sample_multiselect_field):
    """AC4: ['b','a','b'] -> ['b','a'] (dedup preserving FIRST occurrence order)."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_multiselect_field.name: ["b", "a", "b"]}
    )
    assert result[sample_multiselect_field.name] == ["b", "a"]


# ---------------------------------------------------------------------------
# AC5: comma-separated string coerced to list for multiselect
# ---------------------------------------------------------------------------


async def test_multiselect_csv_with_spaces(db_session: AsyncSession, sample_multiselect_field):
    """AC5: 'a, b' (with space) -> ['a', 'b'] (spaces stripped from items)."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_multiselect_field.name: "a, b"}
    )
    assert result[sample_multiselect_field.name] == ["a", "b"]


async def test_select_with_is_multi_true_accepts_csv(db_session: AsyncSession, sample_custom_group):
    """AC5: select field with is_multi=True also accepts CSV string -> list."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="multi_select_field",
        label="Multi Select",
        data_type="select",
        options=[{"value": "x", "label": "X"}, {"value": "y", "label": "Y"}],
        is_required=False,
        is_multi=True,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"multi_select_field": "x,y"})
    assert result["multi_select_field"] == ["x", "y"]


# ---------------------------------------------------------------------------
# AC6: count_contacts_with_field_data on SQLite
# ---------------------------------------------------------------------------


async def test_count_contacts_with_field_data_sqlite_empty(db_session: AsyncSession):
    """AC6: count=0 when no contacts have field data."""
    count = await count_contacts_with_field_data(db_session, "nonexistent_field")
    assert count == 0


async def test_count_contacts_with_field_data_sqlite_with_data(db_session: AsyncSession):
    """AC6: count=1 when exactly one contact has non-null, non-empty value."""
    c1 = Contact(
        first_name="Alice",
        last_name="Smith",
        email="alice@test.org",
        contact_type="Individual",
        custom_data={"my_field": "hello"},
    )
    c2 = Contact(
        first_name="Bob",
        last_name="Jones",
        email="bob@test.org",
        contact_type="Individual",
        custom_data={},
    )
    db_session.add_all([c1, c2])
    await db_session.commit()

    count = await count_contacts_with_field_data(db_session, "my_field")
    assert count == 1


async def test_count_contacts_with_field_data_sqlite_multiple(db_session: AsyncSession):
    """AC6: count matches all contacts with non-null, non-empty value."""
    for i in range(3):
        c = Contact(
            first_name=f"Person{i}",
            last_name="Test",
            email=f"person{i}@test.org",
            contact_type="Individual",
            custom_data={"scored_field": f"value{i}"},
        )
        db_session.add(c)
    # One contact with empty string (should not be counted)
    c_empty = Contact(
        first_name="Empty",
        last_name="Test",
        email="empty@test.org",
        contact_type="Individual",
        custom_data={"scored_field": ""},
    )
    db_session.add(c_empty)
    await db_session.commit()

    count = await count_contacts_with_field_data(db_session, "scored_field")
    assert count == 3, f"Expected 3 (empty string excluded), got {count}"


async def test_count_contacts_with_field_data_sqlite_null_excluded(db_session: AsyncSession):
    """AC6: contacts with JSON null value for the field are not counted."""
    import json

    c = Contact(
        first_name="NullData",
        last_name="Test",
        email="nulldata@test.org",
        contact_type="Individual",
        # SQLite stores custom_data as text; JSON null value
        custom_data={"some_field": None},
    )
    db_session.add(c)
    await db_session.commit()

    count = await count_contacts_with_field_data(db_session, "some_field")
    assert count == 0, f"Expected 0 (null value excluded), got {count}"


async def test_count_contacts_includes_soft_deleted(db_session: AsyncSession):
    """AC6: soft-deleted contacts ARE counted (service counts all contacts)."""
    c = Contact(
        first_name="Deleted",
        last_name="Contact",
        email="deleted@test.org",
        contact_type="Individual",
        custom_data={"archived_field": "exists"},
        is_deleted=True,
    )
    db_session.add(c)
    await db_session.commit()

    count = await count_contacts_with_field_data(db_session, "archived_field")
    assert count == 1, (
        f"Expected 1 (soft-deleted contacts included in count), got {count}"
    )


# ---------------------------------------------------------------------------
# AC9: get_active_schema uses exactly 2 queries
# ---------------------------------------------------------------------------


async def test_get_active_schema_uses_exactly_2_queries(db_session: AsyncSession):
    """AC9: get_active_schema issues exactly 2 SQL queries regardless of group count."""
    # Create 3 active groups for 'contact', each with 2 defs
    for i in range(3):
        grp = CustomFieldGroup(
            name=f"grp_{i}",
            label=f"Group {i}",
            entity="contact",
            weight=i,
            is_active=True,
        )
        db_session.add(grp)
        await db_session.flush()
        for j in range(2):
            d = CustomFieldDef(
                group_id=grp.id,
                name=f"field_{i}_{j}",
                label=f"Field {i}.{j}",
                data_type="text",
                options=[],
                is_required=False,
                is_multi=False,
                weight=j,
                is_active=True,
            )
            db_session.add(d)
    await db_session.commit()

    # Count queries by listening to engine events
    query_count = 0

    from app.database import engine as _engine

    @sa_event.listens_for(_engine.sync_engine, "before_cursor_execute")
    def count_queries(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        # Only count SELECT statements (ignore CREATE TABLE, INSERT, etc.)
        if statement.strip().upper().startswith("SELECT"):
            query_count += 1

    try:
        result = await get_active_schema(db_session, "contact")
    finally:
        sa_event.remove(_engine.sync_engine, "before_cursor_execute", count_queries)

    assert len(result) == 3, f"Expected 3 groups, got {len(result)}"
    assert query_count == 2, (
        f"AC9: get_active_schema must use exactly 2 queries; used {query_count}. "
        "This indicates an N+1 query bug — each group must NOT have a separate "
        "query for its defs. Fix: use a single IN() query for all defs."
    )


async def test_get_active_schema_no_groups_returns_empty(db_session: AsyncSession):
    """AC9 edge case: no active groups for entity returns [] in 1 query (not 2)."""
    result = await get_active_schema(db_session, "activity")
    assert result == []


async def test_get_active_schema_inactive_groups_excluded(db_session: AsyncSession):
    """get_active_schema excludes inactive groups from both query stages."""
    active_grp = CustomFieldGroup(
        name="active_grp",
        label="Active",
        entity="event",
        weight=0,
        is_active=True,
    )
    inactive_grp = CustomFieldGroup(
        name="inactive_grp",
        label="Inactive",
        entity="event",
        weight=1,
        is_active=False,
    )
    db_session.add_all([active_grp, inactive_grp])
    await db_session.flush()

    d1 = CustomFieldDef(
        group_id=active_grp.id, name="ev_field_1", label="EV1",
        data_type="text", options=[], is_required=False, is_multi=False,
        weight=0, is_active=True,
    )
    d2 = CustomFieldDef(
        group_id=inactive_grp.id, name="ev_field_2", label="EV2",
        data_type="text", options=[], is_required=False, is_multi=False,
        weight=0, is_active=True,
    )
    db_session.add_all([d1, d2])
    await db_session.commit()

    result = await get_active_schema(db_session, "event")
    assert len(result) == 1
    assert result[0]["group"].name == "active_grp"
    def_names = [d.name for d in result[0]["defs"]]
    assert "ev_field_2" not in def_names


# ---------------------------------------------------------------------------
# AC7: idempotent — re-validating normalized output returns identical result
# ---------------------------------------------------------------------------


async def test_idempotent_text_field(db_session: AsyncSession, sample_custom_group):
    """AC7: text field normalization is idempotent (already-trimmed value unchanged)."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="idempotent_text",
        label="Idempotent Text",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    first = await validate_and_coerce(db_session, "contact", {"idempotent_text": "hello"})
    second = await validate_and_coerce(db_session, "contact", first)
    assert first == second


async def test_idempotent_number_field(db_session: AsyncSession, sample_custom_group):
    """AC7: number field normalization is idempotent (int 3 -> int 3)."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="idempotent_num",
        label="Idempotent Number",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    first = await validate_and_coerce(db_session, "contact", {"idempotent_num": 3})
    second = await validate_and_coerce(db_session, "contact", first)
    assert first == second


# ---------------------------------------------------------------------------
# AC2: contact_reference storage shapes (C13)
# ---------------------------------------------------------------------------


async def test_contact_ref_single_string_coerced_to_int(
    db_session: AsyncSession,
    sample_contact_ref_field,
    sample_contact,
):
    """AC2: contact_reference single: passing ID as string -> stored as int."""
    result = await validate_and_coerce(
        db_session,
        "contact",
        {sample_contact_ref_field.name: str(sample_contact.id)},
    )
    stored = result[sample_contact_ref_field.name]
    assert isinstance(stored, int), f"Expected int, got {type(stored).__name__}: {stored!r}"
    assert stored == sample_contact.id
