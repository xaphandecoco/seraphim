"""Unit-level service tests for validate_and_coerce (S02 §8 block 2).

These tests call app.services.custom_fields.validate_and_coerce directly
with a seeded db_session — no HTTP layer.
"""
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, CustomFieldDef, CustomFieldGroup
from app.services.custom_fields import validate_and_coerce


# ---------------------------------------------------------------------------
# Number coercion
# ---------------------------------------------------------------------------


async def test_number_coerce_string_int(db_session: AsyncSession, sample_custom_group):
    """'12' (string) coerces to int 12."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="qty",
        label="Qty",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"qty": "12"})
    assert result["qty"] == 12
    assert isinstance(result["qty"], int)


async def test_number_coerce_string_float(db_session: AsyncSession, sample_custom_group):
    """'12.5' (string) coerces to float 12.5."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="score",
        label="Score",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"score": "12.5"})
    assert result["score"] == 12.5
    assert isinstance(result["score"], float)


async def test_number_reject_non_numeric(db_session: AsyncSession, sample_custom_group):
    """'abc' -> HTTPException 422 with error='not_a_number'."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="age",
        label="Age",
        data_type="number",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"age": "abc"})
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "not_a_number" for e in errors)


# ---------------------------------------------------------------------------
# Date coercion
# ---------------------------------------------------------------------------


async def test_date_iso_ok(db_session: AsyncSession, sample_custom_group):
    """'2026-01-31' -> stored as-is."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="join_date",
        label="Join Date",
        data_type="date",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"join_date": "2026-01-31"})
    assert result["join_date"] == "2026-01-31"


async def test_date_bad_format(db_session: AsyncSession, sample_custom_group):
    """'31/01/2026' -> HTTPException 422 bad_date."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="visit_date",
        label="Visit Date",
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
        await validate_and_coerce(db_session, "contact", {"visit_date": "31/01/2026"})
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "bad_date" for e in errors)


# ---------------------------------------------------------------------------
# Select coercion
# ---------------------------------------------------------------------------


async def test_select_valid_option(db_session: AsyncSession, sample_select_field):
    """Value in options list -> accepted and returned."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_select_field.name: "stub_val"}
    )
    assert result[sample_select_field.name] == "stub_val"


async def test_select_invalid_option(db_session: AsyncSession, sample_select_field):
    """Value not in options -> HTTPException 422 invalid_option."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(
            db_session, "contact", {sample_select_field.name: "nonexistent_value"}
        )
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "invalid_option" for e in errors)


# ---------------------------------------------------------------------------
# Multiselect coercion
# ---------------------------------------------------------------------------


async def test_multiselect_dedup(db_session: AsyncSession, sample_multiselect_field):
    """['a','a','b'] -> ['a','b'] (dedup preserving order)."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_multiselect_field.name: ["a", "a", "b"]}
    )
    assert result[sample_multiselect_field.name] == ["a", "b"]


async def test_multiselect_invalid_item(db_session: AsyncSession, sample_multiselect_field):
    """['a','INVALID'] -> HTTPException 422 invalid_option."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(
            db_session, "contact", {sample_multiselect_field.name: ["a", "INVALID"]}
        )
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "invalid_option" for e in errors)


async def test_multiselect_coerce_csv(db_session: AsyncSession, sample_multiselect_field):
    """'a,b' (string) -> ['a','b'] (migration convenience)."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_multiselect_field.name: "a,b"}
    )
    assert result[sample_multiselect_field.name] == ["a", "b"]


# ---------------------------------------------------------------------------
# Checkbox coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expect_true,expect_absent",
    [
        (True, True, False),
        (1, True, False),
        ("true", True, False),
        (False, False, False),   # False stored explicitly (not dropped)
        (0, False, False),       # 0 -> False stored
        ("", False, False),      # "" -> False stored
        (None, False, True),     # None -> absent from output (not in dict)
    ],
)
async def test_checkbox_truthy_values(
    db_session: AsyncSession,
    sample_checkbox_field,
    raw,
    expect_true,
    expect_absent,
):
    """Truthy inputs -> True stored; falsy inputs -> False stored; None -> absent."""
    result = await validate_and_coerce(
        db_session, "contact", {sample_checkbox_field.name: raw}
    )
    if expect_absent:
        # None input: field absent from output (None is treated as missing)
        assert sample_checkbox_field.name not in result
    elif expect_true:
        assert result[sample_checkbox_field.name] is True
    else:
        assert result[sample_checkbox_field.name] is False


# ---------------------------------------------------------------------------
# Required field checks
# ---------------------------------------------------------------------------


async def test_required_field_missing(db_session: AsyncSession, sample_custom_group):
    """Missing required field -> HTTPException 422 required."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="barangay_req",
        label="Barangay (required)",
        data_type="text",
        options=[],
        is_required=True,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {})
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "required" for e in errors)


async def test_required_field_empty_str(db_session: AsyncSession, sample_custom_group):
    """Empty string for required field -> HTTPException 422 required."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="firstname_req",
        label="First Name (required)",
        data_type="text",
        options=[],
        is_required=True,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"firstname_req": ""})
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "required" for e in errors)


async def test_optional_field_dropped(db_session: AsyncSession, sample_custom_group):
    """None or empty for non-required field -> absent from output dict."""
    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="nickname_opt",
        label="Nickname",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()

    result = await validate_and_coerce(db_session, "contact", {"nickname_opt": None})
    assert "nickname_opt" not in result

    result2 = await validate_and_coerce(db_session, "contact", {"nickname_opt": ""})
    assert "nickname_opt" not in result2


# ---------------------------------------------------------------------------
# Unknown key rejection
# ---------------------------------------------------------------------------


async def test_unknown_key_rejected(db_session: AsyncSession, sample_custom_group):
    """{'unknown': 'x'} -> HTTPException 422 unknown_field."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(db_session, "contact", {"unknown_key_xyz": "x"})
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "unknown_field" for e in errors)


# ---------------------------------------------------------------------------
# contact_reference coercion (C13)
# ---------------------------------------------------------------------------


async def test_contact_ref_single_stores_int(
    db_session: AsyncSession,
    sample_contact_ref_field,
    sample_contact,
):
    """Valid contact id stored as int, not string (C13)."""
    result = await validate_and_coerce(
        db_session,
        "contact",
        {sample_contact_ref_field.name: sample_contact.id},
    )
    stored = result[sample_contact_ref_field.name]
    assert isinstance(stored, int)
    assert stored == sample_contact.id


async def test_contact_ref_missing_id(
    db_session: AsyncSession,
    sample_contact_ref_field,
):
    """Nonexistent contact id -> HTTPException 422 unknown_contact."""
    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(
            db_session,
            "contact",
            {sample_contact_ref_field.name: 999999},
        )
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "unknown_contact" for e in errors)


async def test_contact_ref_soft_deleted(
    db_session: AsyncSession,
    sample_contact_ref_field,
    sample_contact,
):
    """Soft-deleted contact -> HTTPException 422 unknown_contact."""
    sample_contact.is_deleted = True
    db_session.add(sample_contact)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        await validate_and_coerce(
            db_session,
            "contact",
            {sample_contact_ref_field.name: sample_contact.id},
        )
    await db_session.rollback()  # Reset session state after exception
    assert exc_info.value.status_code == 422
    errors = exc_info.value.detail
    assert any(e["error"] == "unknown_contact" for e in errors)


async def test_contact_ref_multi_stores_list(
    db_session: AsyncSession,
    sample_custom_group,
    sample_contact,
):
    """is_multi=True contact_reference: [id1, id2] -> [int, int] (C13)."""
    # Create a second contact to test multi
    contact2 = Contact(
        first_name="Maria",
        last_name="Santos",
        email="maria@lightnc.org",
        contact_type="Individual",
    )
    db_session.add(contact2)
    await db_session.commit()
    await db_session.refresh(contact2)

    # Create multi contact_reference field
    multi_ref_field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="invited_by_multi",
        label="Invited By (multi)",
        data_type="contact_reference",
        options=[],
        is_required=False,
        is_multi=True,
        weight=50,
        is_active=True,
    )
    db_session.add(multi_ref_field)
    await db_session.commit()

    result = await validate_and_coerce(
        db_session,
        "contact",
        {"invited_by_multi": [sample_contact.id, contact2.id]},
    )
    stored = result["invited_by_multi"]
    assert isinstance(stored, list)
    assert all(isinstance(i, int) for i in stored)
    assert set(stored) == {sample_contact.id, contact2.id}


async def test_validate_idempotent(
    db_session: AsyncSession,
    sample_custom_group,
    sample_select_field,
):
    """Validating an already-normalized dict produces identical output."""
    raw = {sample_select_field.name: "stub_val"}
    first = await validate_and_coerce(db_session, "contact", raw)
    second = await validate_and_coerce(db_session, "contact", first)
    assert first == second
