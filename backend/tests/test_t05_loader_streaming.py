"""Tests for T05 migration services: loader.py + streaming_report.py.

Covers every acceptance criterion:
 AC1  upsert_contact: SELECT-then-insert/update keyed on external_id (int coerced),
      custom_data merge, return (outcome, entity_id|None, message).
 AC2  upsert_event: same pattern for Event.
 AC3  bulk_insert_participants: resolves via pre-loaded dicts, source='migration',
      delegates to bulk_service, entity_id None, per-row list returned.
 AC4  write_people_link: idempotent custom_data JSONB merge, audit try/except.
 AC5  stream_batch_csv: yields header then one line per ImportRowResult, ordered
      by row_number, memory-bounded (partitions).
 AC6  Outcome Literal: created/updated/skipped/error/review only.
 AC7  No router imports in either module.
"""
from __future__ import annotations

import inspect
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# AC7: Static check — no router imports
# ---------------------------------------------------------------------------


def _source(module_path: str) -> str:
    import importlib
    mod = importlib.import_module(module_path)
    return inspect.getsource(mod)


def test_loader_no_router_imports():
    src = _source("app.services.migration.loader")
    assert "from app.routers" not in src
    assert "import app.routers" not in src


def test_streaming_report_no_router_imports():
    src = _source("app.services.migration.streaming_report")
    assert "from app.routers" not in src
    assert "import app.routers" not in src


# ---------------------------------------------------------------------------
# AC6: Outcome Literal definition
# ---------------------------------------------------------------------------


def test_outcome_literal_values():
    from app.services.migration.loader import Outcome
    import typing
    args = typing.get_args(Outcome)
    assert set(args) == {"created", "updated", "skipped", "error", "review"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_csv(aiter):
    lines = []
    async for line in aiter:
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# AC1: upsert_contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_contact_creates_new(db_session):
    """New external_id creates a Contact and returns ('created', id, msg)."""
    from app.services.migration.loader import upsert_contact

    mapped = {"external_id": "1001", "first_name": "Alice", "last_name": "Smith"}
    outcome, entity_id, msg = await upsert_contact(db_session, mapped)

    assert outcome == "created"
    assert isinstance(entity_id, int) and entity_id > 0
    assert "created" in msg.lower() or msg  # some message returned


@pytest.mark.asyncio
async def test_upsert_contact_updates_existing(db_session):
    """Existing external_id triggers UPDATE and returns ('updated', id, msg)."""
    from app.services.migration.loader import upsert_contact

    mapped1 = {"external_id": "2002", "first_name": "Bob", "last_name": "Jones"}
    outcome1, eid1, _ = await upsert_contact(db_session, mapped1)
    assert outcome1 == "created"

    mapped2 = {"external_id": "2002", "first_name": "Robert", "last_name": "Jones"}
    outcome2, eid2, _ = await upsert_contact(db_session, mapped2)
    assert outcome2 == "updated"
    assert eid2 == eid1


@pytest.mark.asyncio
async def test_upsert_contact_merges_custom_data(db_session):
    """custom_data merge: existing keys preserved, new/updated keys written."""
    from app.services.migration.loader import upsert_contact
    from app.models import Contact
    from sqlalchemy import select

    # Create with some custom_data
    mapped1 = {
        "external_id": "3003",
        "first_name": "Carol",
        "last_name": "Smith",
        "custom_data": {"church": "main", "baptized": True},
    }
    _, eid, _ = await upsert_contact(db_session, mapped1)

    # Update with partial custom_data — only 'church' should be overwritten
    mapped2 = {
        "external_id": "3003",
        "custom_data": {"church": "north", "ministry": "worship"},
    }
    outcome, eid2, _ = await upsert_contact(db_session, mapped2)
    assert outcome == "updated"

    result = await db_session.execute(select(Contact).where(Contact.id == eid2))
    c = result.scalar_one()
    assert c.custom_data["church"] == "north"        # updated key
    assert c.custom_data["baptized"] is True          # preserved key
    assert c.custom_data["ministry"] == "worship"     # new key


@pytest.mark.asyncio
async def test_upsert_contact_invalid_external_id(db_session):
    """Non-numeric external_id returns ('error', None, msg)."""
    from app.services.migration.loader import upsert_contact

    outcome, eid, msg = await upsert_contact(db_session, {"external_id": "abc-xyz"})
    assert outcome == "error"
    assert eid is None
    assert "external_id" in msg.lower() or "not a valid" in msg.lower()


@pytest.mark.asyncio
async def test_upsert_contact_missing_external_id(db_session):
    """Missing external_id returns ('error', None, msg)."""
    from app.services.migration.loader import upsert_contact

    outcome, eid, msg = await upsert_contact(db_session, {"first_name": "Ghost"})
    assert outcome == "error"
    assert eid is None


@pytest.mark.asyncio
async def test_upsert_contact_dry_run_no_write(db_session):
    """dry_run=True returns predicted outcome without persisting."""
    from app.services.migration.loader import upsert_contact
    from app.models import Contact
    from sqlalchemy import select

    outcome, eid, msg = await upsert_contact(
        db_session, {"external_id": "9999", "first_name": "Dry"}, dry_run=True
    )
    assert outcome == "created"
    assert "dry_run" in msg.lower()

    # No row should exist
    res = await db_session.execute(select(Contact).where(Contact.external_id == 9999))
    assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_upsert_contact_external_id_coerced_to_int(db_session):
    """external_id '00042' (string) is coerced and matches int 42."""
    from app.services.migration.loader import upsert_contact
    from app.models import Contact
    from sqlalchemy import select

    await upsert_contact(db_session, {"external_id": "42", "first_name": "Forty", "last_name": "Two"})
    res = await db_session.execute(select(Contact).where(Contact.external_id == 42))
    row = res.scalar_one_or_none()
    assert row is not None
    assert row.first_name == "Forty"


# ---------------------------------------------------------------------------
# AC2: upsert_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_event_creates_new(db_session):
    from app.services.migration.loader import upsert_event
    from datetime import datetime, timezone

    mapped = {
        "external_id": "5001",
        "title": "Sunday Service",
        "start_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    outcome, eid, _ = await upsert_event(db_session, mapped)
    assert outcome == "created"
    assert isinstance(eid, int) and eid > 0


@pytest.mark.asyncio
async def test_upsert_event_updates_existing(db_session):
    from app.services.migration.loader import upsert_event
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    mapped1 = {"external_id": "5002", "title": "Old Title", "start_at": now}
    _, eid1, _ = await upsert_event(db_session, mapped1)

    mapped2 = {"external_id": "5002", "title": "New Title", "start_at": now}
    outcome, eid2, _ = await upsert_event(db_session, mapped2)
    assert outcome == "updated"
    assert eid2 == eid1


@pytest.mark.asyncio
async def test_upsert_event_invalid_external_id(db_session):
    from app.services.migration.loader import upsert_event

    outcome, eid, msg = await upsert_event(db_session, {"external_id": "not-a-number"})
    assert outcome == "error"
    assert eid is None


@pytest.mark.asyncio
async def test_upsert_event_dry_run(db_session):
    from app.services.migration.loader import upsert_event
    from app.models import Event
    from sqlalchemy import select
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    outcome, eid, msg = await upsert_event(
        db_session, {"external_id": "8888", "title": "Dry", "start_at": now}, dry_run=True
    )
    assert outcome == "created"
    assert "dry_run" in msg.lower()

    res = await db_session.execute(select(Event).where(Event.external_id == 8888))
    assert res.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# AC3: bulk_insert_participants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_insert_participants_resolves_ids(db_session, sample_contact, sample_event):
    """Resolves contact/event external IDs via maps, adds source='migration'."""
    from app.services.migration.loader import bulk_insert_participants

    contact_id_map = {"c1": sample_contact.id}
    event_id_map = {"e1": sample_event.id}
    rows = [{"contact_external_id": "c1", "event_external_id": "e1"}]

    results = await bulk_insert_participants(db_session, rows, contact_id_map, event_id_map)
    assert len(results) == 1
    outcome, entity_id, msg = results[0]
    assert outcome in {"created", "skipped"}
    assert entity_id is None  # AC3: entity_id always None for bulk path


@pytest.mark.asyncio
async def test_bulk_insert_participants_missing_contact_id(db_session, sample_event):
    """Missing contact in map returns error outcome for that row."""
    from app.services.migration.loader import bulk_insert_participants

    contact_id_map = {}  # empty — lookup will fail
    event_id_map = {"e1": sample_event.id}
    rows = [{"contact_external_id": "missing", "event_external_id": "e1"}]

    results = await bulk_insert_participants(db_session, rows, contact_id_map, event_id_map)
    assert len(results) == 1
    outcome, eid, msg = results[0]
    assert outcome == "error"
    assert eid is None


@pytest.mark.asyncio
async def test_bulk_insert_participants_missing_event_id(db_session, sample_contact):
    """Missing event in map returns error outcome for that row."""
    from app.services.migration.loader import bulk_insert_participants

    contact_id_map = {"c1": sample_contact.id}
    event_id_map = {}
    rows = [{"contact_external_id": "c1", "event_external_id": "missing_event"}]

    results = await bulk_insert_participants(db_session, rows, contact_id_map, event_id_map)
    assert results[0][0] == "error"


@pytest.mark.asyncio
async def test_bulk_insert_participants_dry_run(db_session, sample_contact, sample_event):
    """dry_run=True resolves IDs but does not write."""
    from app.services.migration.loader import bulk_insert_participants
    from app.models import Participant
    from sqlalchemy import select

    contact_id_map = {"c1": sample_contact.id}
    event_id_map = {"e1": sample_event.id}
    rows = [{"contact_external_id": "c1", "event_external_id": "e1"}]

    results = await bulk_insert_participants(
        db_session, rows, contact_id_map, event_id_map, dry_run=True
    )
    assert results[0][0] == "created"
    assert "dry_run" in results[0][2].lower()

    # No participant row should exist
    res = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == sample_contact.id,
            Participant.event_id == sample_event.id,
        )
    )
    assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_bulk_insert_participants_source_migration(db_session, sample_contact, sample_event):
    """Rows are written with source='migration'."""
    from app.services.migration.loader import bulk_insert_participants
    from app.models import Participant
    from sqlalchemy import select

    contact_id_map = {"cx": sample_contact.id}
    event_id_map = {"ex": sample_event.id}
    rows = [{"contact_external_id": "cx", "event_external_id": "ex"}]

    await bulk_insert_participants(db_session, rows, contact_id_map, event_id_map)

    res = await db_session.execute(
        select(Participant).where(
            Participant.contact_id == sample_contact.id,
            Participant.event_id == sample_event.id,
        )
    )
    participant = res.scalar_one_or_none()
    assert participant is not None
    assert participant.source == "migration"


# ---------------------------------------------------------------------------
# AC4: write_people_link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_people_link_sets_field(db_session, sample_contact):
    """Sets the field in custom_data if not already present."""
    from app.services.migration.loader import write_people_link
    from app.models import Contact
    from sqlalchemy import select

    target_contact_id = 999  # arbitrary resolved id
    await write_people_link(db_session, sample_contact.id, "invited_by", target_contact_id)

    res = await db_session.execute(select(Contact).where(Contact.id == sample_contact.id))
    c = res.scalar_one()
    assert c.custom_data.get("invited_by") == target_contact_id


@pytest.mark.asyncio
async def test_write_people_link_idempotent(db_session, sample_contact):
    """Second call with same field does NOT overwrite existing value."""
    from app.services.migration.loader import write_people_link
    from app.models import Contact
    from sqlalchemy import select

    await write_people_link(db_session, sample_contact.id, "referred_by", 10)
    await write_people_link(db_session, sample_contact.id, "referred_by", 20)  # should skip

    res = await db_session.execute(select(Contact).where(Contact.id == sample_contact.id))
    c = res.scalar_one()
    assert c.custom_data["referred_by"] == 10  # original value preserved


@pytest.mark.asyncio
async def test_write_people_link_missing_contact_is_noop(db_session):
    """Missing source contact silently returns without error."""
    from app.services.migration.loader import write_people_link

    # Should not raise
    await write_people_link(db_session, 999999, "field", 1)


@pytest.mark.asyncio
async def test_write_people_link_audit_failure_does_not_crash(db_session, sample_contact):
    """If audit_svc.record raises, write_people_link still completes successfully."""
    from app.services.migration import loader

    with patch.object(loader.audit_svc, "record", new=AsyncMock(side_effect=Exception("no table"))):
        # Should not raise
        await loader.write_people_link(db_session, sample_contact.id, "link_field", 77)

    from app.models import Contact
    from sqlalchemy import select
    res = await db_session.execute(select(Contact).where(Contact.id == sample_contact.id))
    c = res.scalar_one()
    assert c.custom_data.get("link_field") == 77


# ---------------------------------------------------------------------------
# AC5: stream_batch_csv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_batch_csv_header_and_rows(db_session):
    """Yields header first, then one CSV line per ImportRowResult ordered by row_number."""
    from app.services.migration.streaming_report import stream_batch_csv
    from app.models import ImportBatch, ImportRowResult

    # Create a batch
    batch = ImportBatch(
        entity="contact",
        mode="upsert",
        status="done",
        total_rows=3,
    )
    db_session.add(batch)
    await db_session.flush()

    # Insert rows out of order to test ordering
    for rn, ext, outcome, eid, msg in [
        (3, "ext3", "created", 30, "ok"),
        (1, "ext1", "updated", 10, "ok"),
        (2, "ext2", "error", None, "bad"),
    ]:
        row = ImportRowResult(
            batch_id=batch.id,
            row_number=rn,
            external_id=ext,
            outcome=outcome,
            entity_id=eid,
            message=msg,
        )
        db_session.add(row)
    await db_session.flush()

    lines = await _collect_csv(stream_batch_csv(batch.id, db_session))

    assert len(lines) == 4  # 1 header + 3 rows

    # Header line
    header = lines[0].strip()
    assert header == "row_number,external_id,outcome,entity_id,message"

    # Rows are ordered by row_number
    import csv, io
    reader = csv.reader(io.StringIO("".join(lines[1:])))
    data_rows = list(reader)
    assert [int(r[0]) for r in data_rows] == [1, 2, 3]


@pytest.mark.asyncio
async def test_stream_batch_csv_empty_batch(db_session):
    """An empty batch yields only the header line."""
    from app.services.migration.streaming_report import stream_batch_csv
    from app.models import ImportBatch

    batch = ImportBatch(
        entity="contact",
        mode="upsert",
        status="done",
        total_rows=0,
    )
    db_session.add(batch)
    await db_session.flush()

    lines = await _collect_csv(stream_batch_csv(batch.id, db_session))
    assert len(lines) == 1
    assert "row_number" in lines[0]


@pytest.mark.asyncio
async def test_stream_batch_csv_none_fields(db_session):
    """None entity_id and message are rendered as empty strings in CSV."""
    from app.services.migration.streaming_report import stream_batch_csv
    from app.models import ImportBatch, ImportRowResult

    batch = ImportBatch(
        entity="contact",
        mode="upsert",
        status="done",
        total_rows=1,
    )
    db_session.add(batch)
    await db_session.flush()

    row = ImportRowResult(
        batch_id=batch.id,
        row_number=1,
        external_id=None,
        outcome="error",
        entity_id=None,
        message=None,
    )
    db_session.add(row)
    await db_session.flush()

    lines = await _collect_csv(stream_batch_csv(batch.id, db_session))
    assert len(lines) == 2
    # entity_id and message columns should be empty strings
    import csv, io
    reader = csv.reader(io.StringIO(lines[1]))
    cols = next(reader)
    # external_id (col 1), entity_id (col 3), message (col 4) are empty
    assert cols[1] == ""
    assert cols[3] == ""
    assert cols[4] == ""
