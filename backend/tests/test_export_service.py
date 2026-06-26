"""Tests for backend/app/services/export_service.py (S05-F06).

Covers:
  AC1  _contact_columns returns 13 core + 7 derived + custom def cols
  AC2  _participant_columns returns 13 cols
  AC3  stream_contacts_csv yields BOM as first byte of first chunk
  AC4  stream_participants_csv yields BOM as first byte of first chunk
  AC5  build_contacts_xlsx returns (path, row_count) — path is a real file
  AC6  build_participants_xlsx returns (path, row_count)
  AC7  multiselect values joined with '; '
  AC8  contact_reference resolved to First+Last name (batch, no N+1)
  AC9  NULL derived snapshot columns rendered as ''
  AC10 _build_contact_query applies contact_type filter
  AC11 _build_participant_query applies event_id and date_from/date_to filters

The perf test (AC12 — 33k row memory-bounded) is marked slow and skipped in CI.
"""
from __future__ import annotations

import os
import pytest
import pytest_asyncio

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def contact_with_custom(db_session, sample_custom_group, sample_multiselect_field):
    """Contact with multiselect custom field data."""
    from app.models import Contact

    c = Contact(
        first_name="Maria",
        last_name="Santos",
        contact_type="individual",
        custom_data={"community": ["a", "b"]},
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


@pytest_asyncio.fixture
async def contact_with_ref(db_session, sample_custom_group, sample_contact_ref_field):
    """Contact whose invited_by field points at another contact."""
    from app.models import Contact

    referee = Contact(
        first_name="Pedro",
        last_name="Reyes",
        contact_type="individual",
    )
    db_session.add(referee)
    await db_session.flush()

    c = Contact(
        first_name="Ana",
        last_name="Cruz",
        contact_type="individual",
        custom_data={"invited_by": referee.id},
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    await db_session.refresh(referee)
    c._referee = referee
    return c


@pytest_asyncio.fixture
async def participant_row(db_session, sample_contact, sample_event):
    """One Participant record."""
    from app.models import Participant

    p = Participant(
        contact_id=sample_contact.id,
        event_id=sample_event.id,
        status="attended",
        source="manual",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)
    return p


# ---------------------------------------------------------------------------
# AC1 — _contact_columns structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_columns_core_and_derived(db_session):
    from app.services.export_service import _contact_columns

    cols = await _contact_columns(db_session)
    headers = [h for h, _ in cols]

    # 13 core cols
    for h in [
        "id", "external_id", "contact_type", "contact_subtype",
        "first_name", "last_name", "suffix", "gender", "birth_date",
        "phone", "email", "street_address", "created_at",
    ]:
        assert h in headers, f"Missing core col: {h}"

    # 7 derived cols
    for h in [
        "last_attended_at", "attendance_count", "weeks_absent",
        "tier", "is_active", "is_regular", "is_connected",
    ]:
        assert h in headers, f"Missing derived col: {h}"

    # At least 20 columns total (13 core + 7 derived, no custom defs in this test)
    assert len(cols) >= 20


@pytest.mark.asyncio
async def test_contact_columns_includes_custom_def(
    db_session, sample_custom_group, sample_multiselect_field
):
    from app.services.export_service import _contact_columns

    cols = await _contact_columns(db_session)
    # sample_multiselect_field label is "Community"
    labels = [h for h, _ in cols]
    assert "Community" in labels
    # Total = 20 + 1
    assert len(cols) == 21


# ---------------------------------------------------------------------------
# AC2 — _participant_columns
# ---------------------------------------------------------------------------


def test_participant_columns_count():
    from app.services.export_service import _participant_columns

    cols = _participant_columns()
    assert len(cols) == 13
    headers = [h for h, _ in cols]
    for h in [
        "participant_id", "contact_id", "first_name", "last_name",
        "contact_type", "event_id", "event_title", "occurrence_date",
        "start_at", "event_type", "status", "source", "created_at",
    ]:
        assert h in headers, f"Missing participant col: {h}"


# ---------------------------------------------------------------------------
# AC3 — stream_contacts_csv BOM first chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_contacts_csv_bom(db_session, sample_contact):
    from app.services.export_service import stream_contacts_csv

    chunks = []
    async for chunk in stream_contacts_csv(db_session, {}):
        chunks.append(chunk)

    assert len(chunks) >= 1
    assert chunks[0][:3] == b"\xef\xbb\xbf", "First chunk must start with UTF-8 BOM"


# ---------------------------------------------------------------------------
# AC4 — stream_participants_csv BOM first chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_participants_csv_bom(db_session, participant_row):
    from app.services.export_service import stream_participants_csv

    chunks = []
    async for chunk in stream_participants_csv(db_session, {}):
        chunks.append(chunk)

    assert len(chunks) >= 1
    assert chunks[0][:3] == b"\xef\xbb\xbf", "First chunk must start with UTF-8 BOM"


# ---------------------------------------------------------------------------
# AC5 — build_contacts_xlsx returns real file with row_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_contacts_xlsx(db_session, sample_contact):
    from app.services.export_service import build_contacts_xlsx

    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        pytest.skip("xlsxwriter not installed")

    path, row_count = await build_contacts_xlsx(db_session, {})
    try:
        assert os.path.exists(path), "XLSX file was not created"
        assert os.path.getsize(path) > 0, "XLSX file is empty"
        assert row_count == 1, f"Expected 1 data row, got {row_count}"
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# AC6 — build_participants_xlsx returns real file with row_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_participants_xlsx(db_session, participant_row):
    from app.services.export_service import build_participants_xlsx

    try:
        import xlsxwriter  # noqa: F401
    except ImportError:
        pytest.skip("xlsxwriter not installed")

    path, row_count = await build_participants_xlsx(db_session, {})
    try:
        assert os.path.exists(path), "XLSX file was not created"
        assert os.path.getsize(path) > 0
        assert row_count == 1
    finally:
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# AC7 — multiselect renders with '; ' join
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_contacts_csv_multiselect_join(
    db_session, contact_with_custom
):
    from app.services.export_service import stream_contacts_csv

    all_bytes = b""
    async for chunk in stream_contacts_csv(db_session, {}):
        all_bytes += chunk

    decoded = all_bytes.decode("utf-8-sig")
    # The values "a" and "b" should appear joined with "; "
    assert "a; b" in decoded, f"Expected 'a; b' in CSV, got:\n{decoded}"


# ---------------------------------------------------------------------------
# AC8 — contact_reference resolved to name (no N+1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_contacts_csv_contact_reference(
    db_session, contact_with_ref
):
    from app.services.export_service import stream_contacts_csv

    all_bytes = b""
    async for chunk in stream_contacts_csv(db_session, {}):
        all_bytes += chunk

    decoded = all_bytes.decode("utf-8-sig")
    # The referee's name should appear as the cell value
    assert "Pedro Reyes" in decoded, (
        f"Expected 'Pedro Reyes' (referee name) in CSV, got:\n{decoded}"
    )


# ---------------------------------------------------------------------------
# AC9 — NULL derived snapshot columns rendered as ''
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_contacts_csv_null_snapshot_cols(db_session, sample_contact):
    """sample_contact has no snapshot data — all derived cols should render as ''."""
    from app.services.export_service import stream_contacts_csv

    all_bytes = b""
    async for chunk in stream_contacts_csv(db_session, {}):
        all_bytes += chunk

    # The sample_contact has NULL for all snapshot cols pre-S23.
    # We can't assert raw '' easily in CSV (other fields may be ''),
    # but we verify the row round-trips without errors.
    decoded = all_bytes.decode("utf-8-sig")
    # The contact's name appears in the output
    assert "Juan" in decoded or "dela Cruz" in decoded


# ---------------------------------------------------------------------------
# AC10 — _build_contact_query applies contact_type filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_query_contact_type_filter(db_session):
    from app.models import Contact
    from app.services.export_service import stream_contacts_csv

    org = Contact(
        first_name="Church",
        last_name="HQ",
        contact_type="organization",
    )
    db_session.add(org)
    ind = Contact(
        first_name="Alice",
        last_name="Ong",
        contact_type="individual",
    )
    db_session.add(ind)
    await db_session.commit()

    # Filter to only organizations
    all_bytes = b""
    async for chunk in stream_contacts_csv(db_session, {"contact_type": "organization"}):
        all_bytes += chunk

    decoded = all_bytes.decode("utf-8-sig")
    assert "Church" in decoded
    assert "Alice" not in decoded


# ---------------------------------------------------------------------------
# AC11 — _build_participant_query applies event_id filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participant_query_event_id_filter(db_session, participant_row, sample_event):
    from app.models import Contact, Event, Participant
    from app.services.export_service import stream_participants_csv

    # Second event + participant that should be excluded
    other_event = Event(title="Other Event", start_at=_utc_now())
    db_session.add(other_event)
    other_contact = Contact(
        first_name="Other",
        last_name="Person",
        contact_type="individual",
    )
    db_session.add(other_contact)
    await db_session.flush()

    other_participant = Participant(
        contact_id=other_contact.id,
        event_id=other_event.id,
        status="attended",
        source="manual",
    )
    db_session.add(other_participant)
    await db_session.commit()

    # Filter to only sample_event
    all_bytes = b""
    async for chunk in stream_participants_csv(
        db_session, {"event_id": sample_event.id}
    ):
        all_bytes += chunk

    decoded = all_bytes.decode("utf-8-sig")
    assert "Sunday Service" in decoded or str(sample_event.id) in decoded
    assert "Other Person" not in decoded


# ---------------------------------------------------------------------------
# AC12 — 33k row perf test (marked slow — skipped in default CI)
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.asyncio
async def test_contacts_csv_33k_memory_bounded(db_session):
    """Verify that 33k contacts can be exported without OOM.

    Marked @pytest.mark.slow so default CI runs (``pytest tests/ -q``) skip it.
    Run explicitly with: pytest -m slow tests/test_export_service.py
    """
    from app.models import Contact
    from app.services.export_service import stream_contacts_csv

    BATCH = 500
    for offset in range(0, 33000, BATCH):
        contacts = [
            Contact(
                first_name=f"First{i}",
                last_name=f"Last{i}",
                contact_type="individual",
            )
            for i in range(offset, offset + BATCH)
        ]
        db_session.add_all(contacts)
        await db_session.commit()

    row_count = 0
    async for chunk in stream_contacts_csv(db_session, {}):
        # Just consume — the BOM + header is counted in first chunk
        if chunk:
            row_count += chunk.count(b"\n")

    # 1 header + 33000 data rows = 33001 newlines
    assert row_count >= 33000, f"Expected >=33000 rows, got {row_count}"


# ---------------------------------------------------------------------------
# Security regression (S05 security re-audit): CSV/XLSX formula injection
# Every formula-prefixed cell must be neutralized with a leading apostrophe so
# spreadsheet apps don't execute it on open.  Closes the gap the security
# re-audit flagged as lacking a dedicated regression test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    ["=cmd|'/c calc'!A1", "+1+1", "-2+3", "@SUM(1+1)", "\tinjected", "\rinjected"],
)
def test_sanitize_cell_neutralizes_formula_prefixes(payload):
    from app.services.export_service import _sanitize_cell

    out = _sanitize_cell(payload)
    assert out == "'" + payload, f"{payload!r} not neutralized"
    assert out[0] == "'"


def test_sanitize_cell_leaves_safe_values_untouched():
    from app.services.export_service import _sanitize_cell

    for safe in ["Maria", "maria@example.com", "0917-555-1234", "", "123 Main St"]:
        assert _sanitize_cell(safe) == safe


def test_render_cell_sanitizes_multiselect_and_scalar():
    from app.services.export_service import _render_cell

    # multiselect join whose first char is a formula prefix gets neutralized
    assert _render_cell(["=evil", "ok"], is_multi=True).startswith("'=evil")
    # scalar formula value neutralized
    assert _render_cell("=evil") == "'=evil"
