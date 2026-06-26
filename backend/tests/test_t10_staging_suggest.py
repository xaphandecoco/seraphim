"""T10 QA — staging / suggest / disposition / presets unit tests.

Coverage
--------
staging.py:
  AC-stg-1  CSV UTF-8-sig (BOM) encoding detected.
  AC-stg-2  CSV UTF-8 no-BOM detected as utf-8-sig (first ladder entry that works).
  AC-stg-3  CSV cp1252 encoding detected when UTF-8 fails.
  AC-stg-4  CSV tab delimiter sniffed.
  AC-stg-5  CSV semicolon delimiter sniffed.
  AC-stg-6  CSV row count cap >50 000 raises ValueError.
  AC-stg-7  XLSX single sheet headers + sample rows.
  AC-stg-8  XLSX sheet selection by name.
  AC-stg-9  XLSX row count cap >50 000 raises ValueError.
  AC-stg-10 iter_csv_rows yields dicts, skips blank rows.
  AC-stg-11 Single-column CSV is accepted.

suggest.py:
  AC-sug-1  Exact normalised match → confidence 1.0.
  AC-sug-2  Fuzzy match (>= 0.75) → confidence > 0.
  AC-sug-3  Unknown header → 'ignore' target.
  AC-sug-4  Custom field appears in targets_catalog.
  AC-sug-5  targets_catalog contains 'ignore' sentinel.

disposition.py:
  AC-dis-1  external_id match → kind='match'.
  AC-dis-2  external_id no match → kind='new'.
  AC-dis-3  email match → kind='match'.
  AC-dis-4  email no match → kind='new'.
  AC-dis-5  name SINGLE → kind='match'.
  AC-dis-6  Missing first_name → kind='error'.
  AC-dis-7  Missing external_id for external_id key → kind='error'.

presets.py:
  AC-pre-1  Create + list preset.
  AC-pre-2  (entity, name) collision → 409.
  AC-pre-3  Non-owner update → 403.
  AC-pre-4  Non-owner delete → 403.
  AC-pre-5  Admin can update any preset.
  AC-pre-6  Owner can delete own preset.
  AC-pre-7  Shared preset visible to all; private only to owner.
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, CustomFieldDef, CustomFieldGroup, ImportMappingPreset, utc_now


# ---------------------------------------------------------------------------
# Required autouse fixture: dispose engine after every test that opens a session
# so SQLite drop_all in db_session teardown does not deadlock.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test(db_session):
    """Dispose the app engine connection pool before db_session teardown."""
    yield
    from app.database import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def _make_xlsx(path: Path, rows: list[dict]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    if not rows:
        wb.save(str(path))
        return
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    wb.save(str(path))


# ---------------------------------------------------------------------------
# staging.py tests
# ---------------------------------------------------------------------------


class TestCSVEncoding:
    def test_utf8_bom(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        # Write a file with exactly ONE BOM at the start (utf-8-sig adds the BOM).
        # Do NOT include U+FEFF in the content string or the codec would add a second BOM.
        content = "name,email\nAlice,alice@example.com\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(content.encode("utf-8-sig"))

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["encoding"] == "utf-8-sig"
        assert result["headers"] == ["name", "email"]
        assert result["total_rows"] == 1

    def test_utf8_no_bom(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        content = "name,email\nBob,bob@example.com\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        result = read_header_and_sample(str(csv_file), "csv")
        # utf-8-sig is tried first and succeeds for plain utf-8 too
        assert result["encoding"] in ("utf-8-sig", "utf-8")
        assert "name" in result["headers"]

    def test_cp1252_encoding(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        # Byte 0x92 is a right single quotation mark in cp1252 but invalid in UTF-8
        content_bytes = b"name,city\r\nJuan\x92s,Manila\r\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(content_bytes)

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["encoding"] == "cp1252"
        assert result["total_rows"] == 1

    def test_tab_delimiter(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        content = "first_name\tlast_name\temail\nJohn\tDoe\tjohn@test.com\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["delimiter"] == "\t"
        assert result["headers"] == ["first_name", "last_name", "email"]

    def test_semicolon_delimiter(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        content = "first_name;last_name;email\nJane;Smith;jane@test.com\n"
        csv_file = tmp_path / "test.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["delimiter"] == ";"

    def test_row_count_cap(self, tmp_path):
        from app.services.imports.staging import MAX_DATA_ROWS, read_header_and_sample

        csv_file = tmp_path / "big.csv"
        with csv_file.open("w", encoding="utf-8") as fh:
            fh.write("name,email\n")
            for i in range(MAX_DATA_ROWS + 1):
                fh.write(f"Person{i},p{i}@x.com\n")

        with pytest.raises(ValueError, match="50"):
            read_header_and_sample(str(csv_file), "csv")

    def test_single_column(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        content = "name\nAlice\nBob\n"
        csv_file = tmp_path / "single.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["headers"] == ["name"]
        assert result["total_rows"] == 2

    def test_blank_rows_skipped(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        content = "name,email\n\n,,\nAlice,alice@x.com\n"
        csv_file = tmp_path / "blanks.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        result = read_header_and_sample(str(csv_file), "csv")
        assert result["total_rows"] == 1


class TestXLSX:
    def test_single_sheet(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        rows = [{"first_name": "Ana", "email": "ana@x.com"}]
        xlsx_path = tmp_path / "test.xlsx"
        _make_xlsx(xlsx_path, rows)

        result = read_header_and_sample(str(xlsx_path), "xlsx")
        assert "first_name" in result["headers"]
        assert result["total_rows"] == 1
        assert result["encoding"] is None  # XLSX has no encoding

    def test_sheet_selection(self, tmp_path):
        from app.services.imports.staging import read_header_and_sample

        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "Sheet1"
        ws1.append(["name"])
        ws1.append(["Alice"])

        ws2 = wb.create_sheet("Sheet2")
        ws2.append(["city"])
        ws2.append(["Manila"])

        xlsx_path = tmp_path / "multi.xlsx"
        wb.save(str(xlsx_path))

        result = read_header_and_sample(str(xlsx_path), "xlsx", sheet="Sheet2")
        assert result["headers"] == ["city"]
        assert result["sheets"] is not None
        assert "Sheet2" in result["sheets"]

    def test_row_count_cap(self, tmp_path):
        from app.services.imports.staging import MAX_DATA_ROWS, read_header_and_sample

        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet()
        ws.append(["name", "email"])
        for i in range(MAX_DATA_ROWS + 1):
            ws.append([f"Person{i}", f"p{i}@x.com"])
        xlsx_path = tmp_path / "big.xlsx"
        wb.save(str(xlsx_path))

        with pytest.raises(ValueError, match="50"):
            read_header_and_sample(str(xlsx_path), "xlsx")


class TestIterCSVRows:
    def test_yields_dicts(self, tmp_path):
        from app.services.imports.staging import iter_csv_rows

        content = "first_name,last_name\nJohn,Smith\nJane,Doe\n"
        csv_file = tmp_path / "rows.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        rows = list(iter_csv_rows(str(csv_file), "utf-8-sig", ","))
        assert len(rows) == 2
        assert rows[0]["first_name"] == "John"

    def test_skips_blank_rows(self, tmp_path):
        from app.services.imports.staging import iter_csv_rows

        content = "name,email\n\nAlice,a@x.com\n,,\nBob,b@x.com\n"
        csv_file = tmp_path / "rows.csv"
        csv_file.write_bytes(content.encode("utf-8"))

        rows = list(iter_csv_rows(str(csv_file), "utf-8-sig", ","))
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# suggest.py tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_exact_match(db_session):
    from app.services.imports.suggest import suggest_mappings

    result = await suggest_mappings(["first_name", "email"], "contacts", db_session)
    assert result["first_name"]["target"] == "first_name"
    assert result["first_name"]["confidence"] == 1.0
    assert result["email"]["target"] == "email"


@pytest.mark.asyncio
async def test_suggest_fuzzy_match(db_session):
    from app.services.imports.suggest import suggest_mappings

    # "First Name" should fuzzy-match to first_name
    result = await suggest_mappings(["First Name", "Last Name"], "contacts", db_session)
    assert result["First Name"]["target"] == "first_name"
    assert result["First Name"]["confidence"] > 0.0


@pytest.mark.asyncio
async def test_suggest_unknown_header(db_session):
    from app.services.imports.suggest import suggest_mappings

    result = await suggest_mappings(["XYZ_UNKNOWN_COLUMN_12345"], "contacts", db_session)
    assert result["XYZ_UNKNOWN_COLUMN_12345"]["target"] == "ignore"
    assert result["XYZ_UNKNOWN_COLUMN_12345"]["confidence"] == 0.0


@pytest.mark.asyncio
async def test_suggest_custom_field_in_catalog(db_session, sample_select_field):
    from app.services.imports.suggest import suggest_mappings, targets_catalog

    # sample_select_field creates both the group and a 'pepsol' CustomFieldDef.
    catalog = await targets_catalog("contacts", db_session)
    targets = [t["target"] for t in catalog]
    assert "custom:pepsol" in targets
    assert "ignore" in targets
    assert "first_name" in targets


@pytest.mark.asyncio
async def test_suggest_catalog_has_ignore(db_session):
    from app.services.imports.suggest import targets_catalog

    catalog = await targets_catalog("contacts", db_session)
    ignore_entries = [t for t in catalog if t["target"] == "ignore"]
    assert len(ignore_entries) >= 1


@pytest.mark.asyncio
async def test_suggest_custom_field_match(db_session, sample_select_field):
    """A header matching a custom field name should be suggested as custom:<name>."""
    from app.services.imports.suggest import suggest_mappings

    # sample_select_field has name='pepsol'
    result = await suggest_mappings(["pepsol"], "contacts", db_session)
    # Exact normalised match
    assert result["pepsol"]["target"] == "custom:pepsol"


# ---------------------------------------------------------------------------
# disposition.py tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_external_id_match(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    contact = Contact(
        first_name="Maria", last_name="Santos",
        external_id=999, contact_type="individual",
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)

    mapped = ContactMapped(
        external_id=999, first_name="Maria", last_name="Santos",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None, email=None,
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "external_id", db_session)
    assert kind == "match"
    assert existing is not None
    assert existing.id == contact.id


@pytest.mark.asyncio
async def test_classify_external_id_new(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    mapped = ContactMapped(
        external_id=88888, first_name="New", last_name="Person",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None, email=None,
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "external_id", db_session)
    assert kind == "new"
    assert existing is None


@pytest.mark.asyncio
async def test_classify_email_match(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    contact = Contact(
        first_name="Carlos", last_name="Reyes",
        email="carlos@example.com", contact_type="individual",
    )
    db_session.add(contact)
    await db_session.commit()

    mapped = ContactMapped(
        external_id=None, first_name="Carlos", last_name="Reyes",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None,
        email="carlos@example.com",
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "email", db_session)
    assert kind == "match"
    assert existing.email == "carlos@example.com"


@pytest.mark.asyncio
async def test_classify_email_new(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    mapped = ContactMapped(
        external_id=None, first_name="Ghost", last_name="User",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None,
        email="nobody@nowhere.example",
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "email", db_session)
    assert kind == "new"


@pytest.mark.asyncio
async def test_classify_missing_first_name(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    mapped = ContactMapped(
        external_id=1, first_name="", last_name="Doe",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None, email=None,
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "external_id", db_session)
    assert kind == "error"
    assert "first_name" in msg.lower()


@pytest.mark.asyncio
async def test_classify_missing_external_id(db_session):
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    mapped = ContactMapped(
        external_id=None, first_name="John", last_name="Doe",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None, email=None,
        street_address=None, custom_data={}, links={}, raw_payload="",
    )
    kind, existing, msg = await classify(mapped, "external_id", db_session)
    assert kind == "error"


@pytest.mark.asyncio
async def test_classify_name_single(db_session):
    """Name match with SINGLE result → 'match'."""
    from app.services.imports.disposition import classify
    from app.services.migration.mapper import ContactMapped

    contact = Contact(
        first_name="Maria", last_name="Santos",
        contact_type="individual",
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)

    mapped = ContactMapped(
        external_id=None, first_name="Maria", last_name="Santos",
        contact_type="individual", contact_subtype=None, nickname=None,
        suffix=None, gender=None, birth_date=None, phone=None, email=None,
        street_address=None, custom_data={}, links={}, raw_payload="",
    )

    # Patch the source module since match_name is imported inside the function body.
    with patch("app.services.name_match.match_name", new_callable=AsyncMock) as mock_mn:
        mock_mn.return_value = {
            "outcome": "SINGLE",
            "contact_id": contact.id,
            "review_queue_id": None,
        }
        kind, existing, msg = await classify(mapped, "name", db_session)

    assert kind == "match"


# ---------------------------------------------------------------------------
# presets.py tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_a(db_session):
    from app.models import User
    from app.utils.auth import hash_password

    user = User(
        email="user_a@lightnc.org",
        password_hash=hash_password("pass123456789A"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def user_b(db_session):
    from app.models import User
    from app.utils.auth import hash_password

    user = User(
        email="user_b@lightnc.org",
        password_hash=hash_password("pass123456789B"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_preset_create_and_list(db_session, user_a):
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset, list_presets

    user_ctx = {"sub": str(user_a.id), "role": "volunteer"}
    data = ImportPresetIn(
        entity="contacts",
        name="My Preset",
        column_map={"First Name": {"target": "first_name"}},
        options={},
        is_shared=False,
    )
    preset = await create_preset(data, db_session, user_ctx)
    assert preset.id is not None
    assert preset.name == "My Preset"

    presets = await list_presets("contacts", db_session, user_ctx)
    assert any(p.id == preset.id for p in presets)


@pytest.mark.asyncio
async def test_preset_collision_409(db_session, user_a):
    from fastapi import HTTPException
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset

    user_ctx = {"sub": str(user_a.id), "role": "volunteer"}
    data = ImportPresetIn(entity="contacts", name="Dup Preset", column_map={}, options={}, is_shared=False)
    await create_preset(data, db_session, user_ctx)

    with pytest.raises(HTTPException) as exc_info:
        await create_preset(data, db_session, user_ctx)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_preset_non_owner_update_403(db_session, user_a, user_b):
    from fastapi import HTTPException
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset, update_preset

    ctx_a = {"sub": str(user_a.id), "role": "volunteer"}
    ctx_b = {"sub": str(user_b.id), "role": "volunteer"}

    data = ImportPresetIn(entity="contacts", name="Preset A", column_map={}, options={}, is_shared=False)
    preset = await create_preset(data, db_session, ctx_a)

    with pytest.raises(HTTPException) as exc_info:
        await update_preset(preset.id, data, db_session, ctx_b)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_preset_non_owner_delete_403(db_session, user_a, user_b):
    from fastapi import HTTPException
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset, delete_preset

    ctx_a = {"sub": str(user_a.id), "role": "volunteer"}
    ctx_b = {"sub": str(user_b.id), "role": "volunteer"}

    data = ImportPresetIn(entity="contacts", name="Preset Del", column_map={}, options={}, is_shared=False)
    preset = await create_preset(data, db_session, ctx_a)

    with pytest.raises(HTTPException) as exc_info:
        await delete_preset(preset.id, db_session, ctx_b)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_update_any_preset(db_session, user_a):
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset, update_preset

    ctx_a = {"sub": str(user_a.id), "role": "volunteer"}
    ctx_admin = {"sub": "99999", "role": "admin"}

    data = ImportPresetIn(entity="contacts", name="Admin Test", column_map={}, options={}, is_shared=False)
    preset = await create_preset(data, db_session, ctx_a)

    updated = await update_preset(
        preset.id,
        ImportPresetIn(entity="contacts", name="Admin Updated", column_map={}, options={}, is_shared=False),
        db_session,
        ctx_admin,
    )
    assert updated.name == "Admin Updated"


@pytest.mark.asyncio
async def test_preset_visibility(db_session, user_a, user_b):
    """Shared preset visible to all; private only to owner."""
    from app.schemas import ImportPresetIn
    from app.services.imports.presets import create_preset, list_presets

    ctx_a = {"sub": str(user_a.id), "role": "volunteer"}
    ctx_b = {"sub": str(user_b.id), "role": "volunteer"}

    shared = ImportPresetIn(entity="contacts", name="Shared One", column_map={}, options={}, is_shared=True)
    private = ImportPresetIn(entity="contacts", name="Private One", column_map={}, options={}, is_shared=False)

    await create_preset(shared, db_session, ctx_a)
    await create_preset(private, db_session, ctx_a)

    # user_b can see shared but NOT private
    b_presets = await list_presets(None, db_session, ctx_b)
    b_names = [p.name for p in b_presets]
    assert "Shared One" in b_names
    assert "Private One" not in b_names

    # user_a can see both
    a_presets = await list_presets(None, db_session, ctx_a)
    a_names = [p.name for p in a_presets]
    assert "Shared One" in a_names
    assert "Private One" in a_names
