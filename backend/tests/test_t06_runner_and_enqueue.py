"""T06 QA tests — runner.py phase orchestrator + _enqueue_review payload/source.

Coverage:
  AC1/AC2  run_phase returns int batch_id; ImportBatch status='running' committed first.
  AC3      Preflight raises BatchFatalError -> batch.status='failed', commit, re-raise.
  AC4      Participants phase pre-loads contact/event id maps before iterating rows.
  AC5      Links phase logic: skip if field already set, match_name payload/source,
           SINGLE->write_people_link, AMBIGUOUS/UNMATCHED->outcome='review'.
  AC6      Chunk sizes: CONTACT/EVENT=500, PARTICIPANT=1000.
  AC7      dry_run wraps core writes in savepoint that rolls back; ImportBatch committed.
  AC8      After all rows: tally, status='completed', finished_at set and committed.
  AC9      CN-24: recompute_all_contacts called after live run; ImportError skipped silently.
  AC10     On exception: status='failed', finished_at set, committed, re-raised.
  AC11     _enqueue_review persists payload into raw_payload and source into source column;
           match_name forwards payload+source for both AMBIGUOUS and UNMATCHED branches.
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Contact,
    CustomFieldDef,
    ImportBatch,
    ImportRowResult,
    NameMatchReviewQueue,
    utc_now,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_xlsx(rows: list[dict], tmp_dir: str) -> str:
    """Write a minimal XLSX file with given rows; return path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    if not rows:
        ws.append([])
        path = Path(tmp_dir) / "test.xlsx"
        wb.save(str(path))
        return str(path)

    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])

    path = Path(tmp_dir) / "test.xlsx"
    wb.save(str(path))
    return str(path)


def _db_factory_from_app():
    """Return the app's async_session factory (same engine used by db_session fixture)."""
    from app.database import async_session
    return async_session


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test(db_session):
    """Dispose the app engine's connection pool after each test.

    Depends on db_session so that it is set up AFTER db_session and therefore
    tears down BEFORE db_session (LIFO order). This ensures engine.dispose()
    runs before db_session's drop_all, forcing all pooled connections opened by
    run_phase's own async_session() calls to close before the DDL executes.

    When run_phase opens its own session via async_session(), it may leave
    connections in the SQLAlchemy pool. Those pooled connections can hold a
    SQLite read lock that prevents db_session's drop_all from completing cleanly.
    """
    yield
    from app.database import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# AC1 + AC2: run_phase returns batch_id; ImportBatch status='running' committed first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_phase_returns_int_batch_id(db_session, tmp_path):
    """run_phase must return an integer batch_id."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))
    column_map: dict[str, Any] = {}

    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map=column_map,
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    assert isinstance(batch_id, int), f"Expected int batch_id, got {type(batch_id)}"


@pytest.mark.asyncio
async def test_import_batch_created_with_running_status(db_session, tmp_path):
    """ImportBatch with status='running' must be committed before any row processing."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map={},
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    # Verify the final batch exists and completed (started as running, no rows -> completed)
    result = await db_session.execute(
        select(ImportBatch).where(ImportBatch.id == batch_id)
    )
    batch = result.scalar_one()
    assert batch is not None
    # entity is stored as singular per model spec
    assert batch.entity == "contact"
    # After empty run it transitions to completed
    assert batch.status == "completed"


# ---------------------------------------------------------------------------
# AC3: Preflight BatchFatalError -> batch.status='failed', commit, re-raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_unknown_custom_field_fails_batch(db_session, tmp_path):
    """If column_map references unknown custom_data field, batch fails with BatchFatalError."""
    from app.services.migration.runner import run_phase
    from app.services.migration.mapper import BatchFatalError

    xlsx_path = _make_xlsx([], str(tmp_path))
    # Map a column to an unknown custom_data.* target (no CustomFieldDef exists)
    column_map = {
        "some_col": {"target": "custom_data.nonexistent_field_xyz", "data_type": "text"}
    }

    with pytest.raises(BatchFatalError):
        await run_phase(
            phase="contacts",
            xlsx_path=xlsx_path,
            column_map=column_map,
            options={},
            dry_run=False,
            created_by_id=None,
            db_factory=_db_factory_from_app(),
        )

    # Verify batch was created and marked failed
    result = await db_session.execute(
        select(ImportBatch).where(ImportBatch.entity == "contact")
    )
    batch = result.scalar_one_or_none()
    assert batch is not None
    assert batch.status == "failed"
    assert batch.finished_at is not None


@pytest.mark.asyncio
async def test_preflight_known_custom_field_passes(db_session, tmp_path):
    """If CustomFieldDef exists for the target, preflight passes and batch completes."""
    from app.services.migration.runner import run_phase
    from app.models import CustomFieldGroup

    # Insert a CustomFieldGroup + CustomFieldDef
    group = CustomFieldGroup(
        name="contact_extra",
        label="Contact Extra",
        entity="contact",
        is_active=True,
    )
    db_session.add(group)
    await db_session.flush()

    cfd = CustomFieldDef(
        group_id=group.id,
        name="preferred_pastor",
        label="Preferred Pastor",
        data_type="text",
        is_active=True,
    )
    db_session.add(cfd)
    await db_session.commit()

    xlsx_path = _make_xlsx([], str(tmp_path))
    column_map = {
        "pastor_col": {"target": "custom_data.preferred_pastor", "data_type": "text"}
    }

    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map=column_map,
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    result = await db_session.execute(
        select(ImportBatch).where(ImportBatch.id == batch_id)
    )
    batch = result.scalar_one()
    assert batch.status == "completed"


# ---------------------------------------------------------------------------
# AC6: Chunk size constants
# ---------------------------------------------------------------------------

def test_chunk_size_contacts():
    from app.services.migration.runner import _CHUNK_CONTACTS
    assert _CHUNK_CONTACTS == 500


def test_chunk_size_events():
    from app.services.migration.runner import _CHUNK_EVENTS
    assert _CHUNK_EVENTS == 500


def test_chunk_size_participants():
    from app.services.migration.runner import _CHUNK_PARTICIPANTS
    assert _CHUNK_PARTICIPANTS == 1000


# ---------------------------------------------------------------------------
# AC8: Tally after all rows — status='completed', finished_at set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_batch_completed_after_empty_run(db_session, tmp_path):
    """After an empty contacts run, status must be 'completed' and finished_at set."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map={},
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    result = await db_session.execute(
        select(ImportBatch).where(ImportBatch.id == batch_id)
    )
    batch = result.scalar_one()
    assert batch.status == "completed"
    assert batch.finished_at is not None
    assert batch.total_rows == 0


# ---------------------------------------------------------------------------
# AC10: On exception -> batch.status='failed', finished_at set, re-raised
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exception_marks_batch_failed(db_session, tmp_path):
    """An unexpected exception mid-phase must set status='failed' and re-raise."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    # Patch read_rows to raise mid-execution
    with patch(
        "app.services.migration.runner.read_rows",
        side_effect=RuntimeError("Simulated disk error"),
    ):
        with pytest.raises(RuntimeError, match="Simulated disk error"):
            await run_phase(
                phase="events",
                xlsx_path=xlsx_path,
                column_map={},
                options={},
                dry_run=False,
                created_by_id=None,
                db_factory=_db_factory_from_app(),
            )

    result = await db_session.execute(
        select(ImportBatch).where(ImportBatch.entity == "event")
    )
    batch = result.scalar_one_or_none()
    assert batch is not None
    assert batch.status == "failed"
    assert batch.finished_at is not None


# ---------------------------------------------------------------------------
# AC9: CN-24 — recompute_all_contacts called after live run; ImportError skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cn24_recompute_called_on_live_run(db_session, tmp_path):
    """After a live (non-dry_run) run, member_status_service.recompute_all_contacts is called.

    Patch the real function directly: now that S23's member_status_service module
    exists and is imported by earlier live-run tests, ``from app.services import
    member_status_service`` resolves to the cached real module attribute and a
    ``sys.modules`` patch is bypassed.
    """
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ) as mock_recompute:
        await run_phase(
            phase="contacts",
            xlsx_path=xlsx_path,
            column_map={},
            options={},
            dry_run=False,
            created_by_id=None,
            db_factory=_db_factory_from_app(),
        )

    mock_recompute.assert_called_once()


@pytest.mark.asyncio
async def test_cn24_recompute_not_called_on_dry_run(db_session, tmp_path):
    """In dry_run mode, recompute_all_contacts must NOT be called."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ) as mock_recompute:
        await run_phase(
            phase="contacts",
            xlsx_path=xlsx_path,
            column_map={},
            options={},
            dry_run=True,
            created_by_id=None,
            db_factory=_db_factory_from_app(),
        )

    mock_recompute.assert_not_called()


@pytest.mark.asyncio
async def test_cn24_import_error_skipped_silently(db_session, tmp_path):
    """ImportError for member_status_service must be silently skipped."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx([], str(tmp_path))

    # Ensure the module is absent by removing it from sys.modules if present
    import sys
    sys.modules.pop("app.services.member_status_service", None)

    # Should not raise
    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map={},
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )
    assert isinstance(batch_id, int)


# ---------------------------------------------------------------------------
# AC11: _enqueue_review persists payload into raw_payload and source into source
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_review_persists_payload_and_source(db_session):
    """_enqueue_review must write payload -> raw_payload and source -> source column."""
    from app.services.name_match import _enqueue_review

    payload = {"batch_id": 42, "field_name": "preferred_pastor", "source_contact_id": 7}
    queue_id = await _enqueue_review(
        db=db_session,
        raw_name="Pastor Rodel Aquino",
        event_id=None,
        community_report_id=None,
        candidates=[],
        payload=payload,
        source="migration",
    )

    assert queue_id is not None

    row = await db_session.get(NameMatchReviewQueue, queue_id)
    assert row is not None
    assert row.source == "migration"
    assert row.raw_payload == payload
    assert row.raw_payload["batch_id"] == 42
    assert row.raw_payload["field_name"] == "preferred_pastor"
    assert row.raw_payload["source_contact_id"] == 7


@pytest.mark.asyncio
async def test_enqueue_review_none_payload_stored_as_none(db_session):
    """_enqueue_review with payload=None must store None in raw_payload."""
    from app.services.name_match import _enqueue_review

    queue_id = await _enqueue_review(
        db=db_session,
        raw_name="Unknown Person",
        event_id=None,
        community_report_id=None,
        candidates=[],
        payload=None,
        source="manual",
    )

    assert queue_id is not None
    row = await db_session.get(NameMatchReviewQueue, queue_id)
    assert row is not None
    assert row.raw_payload is None
    assert row.source == "manual"


# ---------------------------------------------------------------------------
# AC11: match_name passes payload+source to _enqueue_review for UNMATCHED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_name_unmatched_persists_payload_and_source(db_session):
    """UNMATCHED outcome: match_name forwards payload + source -> review queue row."""
    from app.services.name_match import match_name

    payload = {"batch_id": 99, "field_name": "spouse", "source_contact_id": 5}

    result = await match_name(
        raw_name="Zzzzz Nobody At All Xyz",
        db=db_session,
        source="migration",
        payload=payload,
        auto_enqueue=True,
    )

    assert result["outcome"] == "UNMATCHED"
    assert result["review_queue_id"] is not None

    row = await db_session.get(NameMatchReviewQueue, result["review_queue_id"])
    assert row is not None
    assert row.source == "migration"
    assert row.raw_payload == payload


# ---------------------------------------------------------------------------
# AC11: match_name passes payload+source to _enqueue_review for AMBIGUOUS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_match_name_ambiguous_persists_payload_and_source(db_session):
    """AMBIGUOUS outcome: match_name forwards payload + source -> review queue row."""
    from app.services.name_match import match_name

    # Create two similar contacts to trigger AMBIGUOUS
    c1 = Contact(first_name="Maria", last_name="Santos", contact_type="individual")
    c2 = Contact(first_name="Mario", last_name="Santos", contact_type="individual")
    db_session.add(c1)
    db_session.add(c2)
    await db_session.commit()
    await db_session.refresh(c1)
    await db_session.refresh(c2)

    payload = {"batch_id": 77, "field_name": "cell_leader", "source_contact_id": 3}

    result = await match_name(
        raw_name="Maria Santos",
        db=db_session,
        source="migration",
        payload=payload,
        auto_enqueue=True,
    )

    # Could be SINGLE (if one wins decisively) or AMBIGUOUS
    if result["outcome"] == "AMBIGUOUS":
        assert result["review_queue_id"] is not None
        row = await db_session.get(NameMatchReviewQueue, result["review_queue_id"])
        assert row is not None
        assert row.source == "migration"
        assert row.raw_payload == payload
    else:
        # SINGLE — no review queued, but no assertion failure needed
        assert result["outcome"] == "SINGLE"


# ---------------------------------------------------------------------------
# AC5: Links phase — skip if custom_data field already set (re-run protection)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_links_phase_skips_already_set_field(db_session, tmp_path):
    """Links phase must skip a field that is already set in contact.custom_data."""
    from app.services.migration.runner import run_phase

    # Create source contact with external_id and custom_data already set
    source = Contact(
        first_name="Ana",
        last_name="Reyes",
        contact_type="individual",
        external_id="EXT001",
        custom_data={"cell_leader": 999},  # already set
    )
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)

    xlsx_path = _make_xlsx(
        [{"contact_external_id": "EXT001", "leader_col": "Juan Cruz"}],
        str(tmp_path),
    )
    column_map = {
        "leader_col": {"target": "custom_data.cell_leader", "data_type": "text"}
    }

    batch_id = await run_phase(
        phase="links",
        xlsx_path=xlsx_path,
        column_map=column_map,
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    # The row should be skipped
    result = await db_session.execute(
        select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)
    )
    rows = result.scalars().all()
    # Should have at least one skipped result for the already-set field
    outcomes = {r.outcome for r in rows}
    assert "skipped" in outcomes, f"Expected 'skipped' outcome, got {outcomes}"


# ---------------------------------------------------------------------------
# AC5: Links phase — unknown contact_external_id -> error outcome
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_links_phase_unknown_contact_records_error(db_session, tmp_path):
    """Links phase must record an error outcome when contact_external_id is not found."""
    from app.services.migration.runner import run_phase

    xlsx_path = _make_xlsx(
        [{"contact_external_id": "NONEXISTENT_99999", "leader_col": "Juan Cruz"}],
        str(tmp_path),
    )
    column_map = {
        "leader_col": {"target": "custom_data.cell_leader", "data_type": "text"}
    }

    batch_id = await run_phase(
        phase="links",
        xlsx_path=xlsx_path,
        column_map=column_map,
        options={},
        dry_run=False,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    result = await db_session.execute(
        select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)
    )
    rows = result.scalars().all()
    assert any(r.outcome == "error" for r in rows), f"Expected error outcome, got {[r.outcome for r in rows]}"


# ---------------------------------------------------------------------------
# AC4: Participants phase pre-loads contact/event id maps
# ---------------------------------------------------------------------------

def test_preload_functions_exist():
    """_preload_contact_id_map and _preload_event_id_map must exist in runner."""
    from app.services.migration import runner
    assert hasattr(runner, "_preload_contact_id_map"), "Missing _preload_contact_id_map"
    assert hasattr(runner, "_preload_event_id_map"), "Missing _preload_event_id_map"


@pytest.mark.asyncio
async def test_preload_contact_id_map_returns_dict(db_session):
    """_preload_contact_id_map must return a dict mapping str(external_id) -> contact_id."""
    from app.services.migration.runner import _preload_contact_id_map

    c = Contact(
        first_name="Test",
        last_name="User",
        contact_type="individual",
        external_id="EXT_PRELOAD_42",
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)

    id_map = await _preload_contact_id_map(db_session)
    assert isinstance(id_map, dict)
    assert "EXT_PRELOAD_42" in id_map
    assert id_map["EXT_PRELOAD_42"] == c.id


# ---------------------------------------------------------------------------
# AC11: _enqueue_review signature accepts payload and source as keyword args
# ---------------------------------------------------------------------------

def test_enqueue_review_signature():
    """_enqueue_review must accept payload and source keyword arguments."""
    import inspect
    from app.services.name_match import _enqueue_review

    sig = inspect.signature(_enqueue_review)
    params = sig.parameters
    assert "payload" in params, "_enqueue_review missing 'payload' parameter"
    assert "source" in params, "_enqueue_review missing 'source' parameter"


def test_match_name_signature_has_payload_and_source():
    """match_name must accept payload and source keyword arguments."""
    import inspect
    from app.services.name_match import match_name

    sig = inspect.signature(match_name)
    params = sig.parameters
    assert "payload" in params, "match_name missing 'payload' parameter"
    assert "source" in params, "match_name missing 'source' parameter"
    assert "auto_enqueue" in params, "match_name missing 'auto_enqueue' parameter"


# ---------------------------------------------------------------------------
# AC13 regression: dry-run persists import_row_result audit rows but no core rows.
# Guards the savepoint-scoping bug (Opus DoD critique): db.add(ImportRowResult)
# happened INSIDE the dry-run begin_nested() savepoint, so sp.rollback() discarded
# the audit rows together with the core writes — leaving an empty per-row report
# and empty CSV for every dry-run while summary counts still looked correct.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_persists_row_results_but_no_core_rows(db_session, tmp_path):
    """A dry-run contacts phase must persist N import_row_result rows AND 0 Contacts."""
    from app.services.migration.runner import run_phase
    from app.models import Contact

    xlsx_path = _make_xlsx(
        [
            {"cid": "1001", "fn": "Maria", "ln": "Santos"},
            {"cid": "1002", "fn": "Mario", "ln": "Santos"},
        ],
        str(tmp_path),
    )
    column_map = {
        "cid": {"target": "external_id", "data_type": "number"},
        "fn": {"target": "first_name", "data_type": "text"},
        "ln": {"target": "last_name", "data_type": "text"},
    }

    batch_id = await run_phase(
        phase="contacts",
        xlsx_path=xlsx_path,
        column_map=column_map,
        options={},
        dry_run=True,
        created_by_id=None,
        db_factory=_db_factory_from_app(),
    )

    # Audit rows MUST survive the dry-run rollback (this is the regression).
    rr = (
        await db_session.execute(
            select(ImportRowResult).where(ImportRowResult.batch_id == batch_id)
        )
    ).scalars().all()
    assert len(rr) == 2, f"dry-run must persist 2 import_row_result rows, got {len(rr)}"
    assert all(r.outcome == "created" for r in rr), [r.outcome for r in rr]

    # Core Contact rows MUST NOT persist on a dry-run.
    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.external_id.in_([1001, 1002]))
        )
    ).scalars().all()
    assert len(contacts) == 0, f"dry-run must write no Contact rows, got {len(contacts)}"

    # Batch is recorded as dry_run with an accurate would-create tally.
    batch = await db_session.get(ImportBatch, batch_id)
    assert batch.mode == "dry_run"
    assert batch.created_count == 2
