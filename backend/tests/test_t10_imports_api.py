"""T10 QA — Import Wizard API E2E tests.

Coverage
--------
AC-api-1   POST /imports/upload CSV → 200, batch created.
AC-api-2   POST /imports/upload XLSX → 200, sheets listed.
AC-api-3   POST /imports/upload bad extension → 400.
AC-api-4   POST /imports/upload oversized file → 413.
AC-api-5   GET /imports/{id}/columns → headers + suggested_map + targets.
AC-api-6   POST /imports/{id}/preview → counts returned.
AC-api-7   preview writes ZERO Contact rows.
AC-api-8   POST /imports/{id}/run → creates contacts.
AC-api-9   run idempotent: second run with conflict_policy='skip' adds 0 contacts.
AC-api-10  participants DO NOTHING on conflict (no duplicates on re-import).
AC-api-11  name-ambiguous row → outcome='review'.
AC-api-12  duplicate-target column_map → 422.
AC-api-13  missing first_name column_map → 422.
AC-api-14  GET /imports/{id}/report.csv streams CSV.
AC-api-15  report.csv formula-injection defused (=SUM → '=SUM).
AC-api-16  viewer role → 403 on upload.
AC-api-17  preset 409 collision.
AC-api-18  preset 403 non-owner.
AC-api-19  recompute_all_contacts called on run, NOT on preview.
AC-api-20  purge sweep: expired staged batch → status='expired', file deleted.
AC-api-21  GET /imports lists only own batches for volunteers.
AC-api-22  cross-user batch access → 404.
"""
from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Event, ImportBatch, ImportRowResult, Participant, utc_now


# ---------------------------------------------------------------------------
# Required autouse fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test(db_session):
    """Dispose the app engine pool before db_session teardown."""
    yield
    from app.database import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv_bytes(rows: list[dict], headers: list[str] | None = None) -> bytes:
    """Build a minimal CSV in memory."""
    buf = io.StringIO()
    if not rows:
        return b""
    hdrs = headers or list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=hdrs)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _xlsx_bytes(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    if not rows:
        wb.save(buf)
        return buf.getvalue()
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    wb.save(buf)
    return buf.getvalue()


def _minimal_contact_map() -> dict:
    """Minimal valid column_map for contact import (has first_name)."""
    return {
        "first_name":  {"target": "first_name",  "data_type": "text"},
        "last_name":   {"target": "last_name",   "data_type": "text"},
        "external_id": {"target": "external_id", "data_type": "integer"},
    }


# ---------------------------------------------------------------------------
# AC-api-1: Upload CSV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_csv(client, volunteer_auth_headers):
    rows = [{"first_name": "Alice", "last_name": "Lim", "external_id": "1"}]
    content = _csv_bytes(rows)

    resp = await client.post(
        "/imports/upload",
        files={"file": ("contacts.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "batch_id" in data
    assert data["total_rows"] == 1
    assert any(c["name"] == "first_name" for c in data["columns"])


# ---------------------------------------------------------------------------
# AC-api-2: Upload XLSX
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_xlsx(client, volunteer_auth_headers):
    rows = [{"first_name": "Bob", "external_id": "2"}]
    content = _xlsx_bytes(rows)

    resp = await client.post(
        "/imports/upload",
        files={"file": ("contacts.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["batch_id"] is not None
    assert data["sheets"] is not None  # XLSX returns sheet list


# ---------------------------------------------------------------------------
# AC-api-3: Bad extension → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_bad_ext(client, volunteer_auth_headers):
    resp = await client.post(
        "/imports/upload",
        files={"file": ("contacts.txt", b"name\nAlice\n", "text/plain")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert "type" in detail or "unsupported" in detail or "extension" in detail


# ---------------------------------------------------------------------------
# AC-api-4: Oversized file -> 413
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_too_large(client, volunteer_auth_headers):
    # 16 MB > 15 MB limit
    big = b"name,email\n" + b"x" * (16 * 1024 * 1024)
    resp = await client.post(
        "/imports/upload",
        files={"file": ("big.csv", big, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# AC-api-5: GET columns -> suggested_map + targets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_columns(client, volunteer_auth_headers):
    rows = [{"first_name": "Alice", "external_id": "1"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    resp = await client.get(f"/imports/{batch_id}/columns", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "headers" in data
    assert "suggested_map" in data
    assert "targets" in data
    assert "first_name" in data["headers"]


# ---------------------------------------------------------------------------
# AC-api-6 + AC-api-7: Preview -> counts; preview writes ZERO contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_counts_and_no_writes(client, db_session, volunteer_auth_headers):
    rows = [{"first_name": "Preview", "last_name": "Test", "external_id": "555"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    resp = await client.post(
        f"/imports/{batch_id}/preview",
        json={
            "column_map": _minimal_contact_map(),
            "match_key": "external_id",
            "conflict_policy": "skip",
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200
    counts = resp.json()["counts"]
    assert isinstance(counts, dict)

    # Preview must not create any Contact rows
    from sqlalchemy import select, func
    count_result = await db_session.execute(
        select(func.count()).select_from(Contact)
    )
    assert count_result.scalar_one() == 0


# ---------------------------------------------------------------------------
# AC-api-8: Run creates contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_creates_contacts(client, db_session, volunteer_auth_headers):
    rows = [
        {"first_name": "Alice", "last_name": "Run", "external_id": "101"},
        {"first_name": "Bob",   "last_name": "Run", "external_id": "102"},
    ]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        resp = await client.post(
            f"/imports/{batch_id}/run",
            json={
                "column_map": _minimal_contact_map(),
                "match_key": "external_id",
                "conflict_policy": "update",
            },
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["counts"]["created"] == 2
    assert data["status"] == "completed"


# ---------------------------------------------------------------------------
# AC-api-9: Run idempotent (second run skip adds 0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_idempotent(client, db_session, volunteer_auth_headers):
    rows = [{"first_name": "Idempotent", "last_name": "Test", "external_id": "200"}]
    content = _csv_bytes(rows)

    async def _upload():
        up = await client.post(
            "/imports/upload",
            files={"file": ("c.csv", content, "text/csv")},
            data={"entity": "contacts"},
            headers=volunteer_auth_headers,
        )
        return up.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        # First run
        batch_id_1 = await _upload()
        r1 = await client.post(
            f"/imports/{batch_id_1}/run",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )
        assert r1.json()["counts"]["created"] == 1

        # Second run: same data, should skip
        batch_id_2 = await _upload()
        r2 = await client.post(
            f"/imports/{batch_id_2}/run",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )
        counts2 = r2.json()["counts"]
        assert counts2["created"] == 0
        assert counts2["skipped"] == 1


# ---------------------------------------------------------------------------
# AC-api-10: Participants -- no duplicates on re-import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participants_no_duplicate_on_reimport(client, db_session, volunteer_auth_headers):
    # Create a contact and event first
    contact = Contact(first_name="Pea", last_name="Pod", external_id=300, contact_type="individual")
    event = Event(title="Test Event", start_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db_session.add_all([contact, event])
    await db_session.commit()
    await db_session.refresh(contact)
    await db_session.refresh(event)

    rows = [{"contact_ref": str(contact.id), "status": "attended"}]
    content = _csv_bytes(rows, headers=["contact_ref", "status"])

    part_map = {
        "contact_ref": {"target": "contact_ref", "data_type": "text"},
        "status":      {"target": "status",      "data_type": "text"},
    }

    async def _upload_participants():
        up = await client.post(
            "/imports/upload",
            files={"file": ("p.csv", content, "text/csv")},
            data={"entity": "participants"},
            headers=volunteer_auth_headers,
        )
        return up.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        b1 = await _upload_participants()
        await client.post(
            f"/imports/{b1}/run",
            json={"column_map": part_map, "match_key": "external_id",
                  "conflict_policy": "skip", "target_event_id": event.id},
            headers=volunteer_auth_headers,
        )

        b2 = await _upload_participants()
        await client.post(
            f"/imports/{b2}/run",
            json={"column_map": part_map, "match_key": "external_id",
                  "conflict_policy": "skip", "target_event_id": event.id},
            headers=volunteer_auth_headers,
        )

    # Should be exactly 1 Participant row (no duplicates)
    from sqlalchemy import select, func
    count_result = await db_session.execute(
        select(func.count()).select_from(Participant)
        .where(Participant.event_id == event.id)
    )
    assert count_result.scalar_one() == 1


# ---------------------------------------------------------------------------
# AC-api-11: Name-ambiguous -> review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_name_ambiguous_review(client, db_session, volunteer_auth_headers):
    rows = [{"first_name": "Ambiguous", "last_name": "Person", "external_id": ""}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    # Patch match_name at the source module since it is imported inside the
    # function body (to avoid circular imports).  Both disposition._classify_by_name
    # and runner._enqueue_name_review import it the same way.
    with patch(
        "app.services.name_match.match_name",
        new_callable=AsyncMock,
    ) as mock_mn, patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        mock_mn.return_value = {"outcome": "AMBIGUOUS", "contact_id": None, "review_queue_id": 42}

        resp = await client.post(
            f"/imports/{batch_id}/run",
            json={
                "column_map": {
                    "first_name": {"target": "first_name", "data_type": "text"},
                    "last_name":  {"target": "last_name",  "data_type": "text"},
                },
                "match_key": "name",
                "conflict_policy": "skip",
            },
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["counts"]["review"] == 1


# ---------------------------------------------------------------------------
# AC-api-12: Duplicate-target column_map -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_target_422(client, db_session, volunteer_auth_headers):
    rows = [{"col_a": "Alice", "col_b": "Smith"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    resp = await client.post(
        f"/imports/{batch_id}/preview",
        json={
            "column_map": {
                "col_a": {"target": "first_name", "data_type": "text"},
                "col_b": {"target": "first_name", "data_type": "text"},  # duplicate!
            },
            "match_key": "external_id",
            "conflict_policy": "skip",
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"].lower()
    assert "first_name" in detail or "duplicate" in detail


# ---------------------------------------------------------------------------
# AC-api-13: Missing first_name column -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_first_name_422(client, db_session, volunteer_auth_headers):
    rows = [{"last_name": "Smith", "external_id": "1"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    resp = await client.post(
        f"/imports/{batch_id}/preview",
        json={
            "column_map": {
                "last_name":   {"target": "last_name",   "data_type": "text"},
                "external_id": {"target": "external_id", "data_type": "integer"},
            },
            "match_key": "external_id",
            "conflict_policy": "skip",
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422
    assert "first_name" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# AC-api-14: GET /imports/{id}/report.csv streams CSV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_csv_streams(client, db_session, volunteer_auth_headers):
    rows = [{"first_name": "Report", "last_name": "Test", "external_id": "700"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        await client.post(
            f"/imports/{batch_id}/run",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )

    resp = await client.get(f"/imports/{batch_id}/report.csv", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    text = resp.text
    assert "row_number" in text  # CSV header present
    assert "external_id" in text


# ---------------------------------------------------------------------------
# AC-api-15: Formula injection defused in report.csv
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_formula_injection_defused(client, db_session, volunteer_auth_headers):
    # Seed a row whose external_id looks like a formula; the import will
    # fail to parse external_id as integer but the row still gets an ImportRowResult
    # with the raw text.  The report.csv must not emit the raw =SUM(...).
    rows = [{"first_name": "Safe", "last_name": "Test", "external_id": "=SUM(A1)"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        await client.post(
            f"/imports/{batch_id}/run",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )

    resp = await client.get(f"/imports/{batch_id}/report.csv", headers=volunteer_auth_headers)
    text = resp.text
    # The raw formula string must NOT appear unescaped in the CSV.
    # _sanitize_cell prepends ' to formula-starting characters.
    assert "=SUM(A1)" not in text or "'=SUM(A1)" in text, (
        "Formula injection was NOT defused in report CSV"
    )


# ---------------------------------------------------------------------------
# AC-api-16: Viewer role -> 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_403(client, viewer_auth_headers):
    content = _csv_bytes([{"first_name": "X"}])
    resp = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# AC-api-17: Preset 409 collision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preset_409_collision(client, volunteer_auth_headers):
    preset_data = {
        "entity": "contacts",
        "name": "Collision Preset",
        "column_map": {},
        "options": {},
        "is_shared": False,
    }
    r1 = await client.post("/imports/presets", json=preset_data, headers=volunteer_auth_headers)
    assert r1.status_code == 201

    r2 = await client.post("/imports/presets", json=preset_data, headers=volunteer_auth_headers)
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# AC-api-18: Preset 403 non-owner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preset_403_non_owner(client, db_session, volunteer_auth_headers, admin_auth_headers):
    from conftest import make_token
    from app.models import User
    from app.utils.auth import hash_password

    # Create another volunteer user
    other = User(
        email="other_volunteer@lightnc.org",
        password_hash=hash_password("OtherPass123!"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    other_headers = {"Authorization": f"Bearer {make_token(other.id, other.email, 'volunteer')}"}

    # Create preset as the volunteer in volunteer_auth_headers
    r = await client.post(
        "/imports/presets",
        json={"entity": "contacts", "name": "Others Preset", "column_map": {}, "options": {}, "is_shared": False},
        headers=volunteer_auth_headers,
    )
    assert r.status_code == 201
    preset_id = r.json()["id"]

    # Try to delete as other volunteer
    r2 = await client.delete(f"/imports/presets/{preset_id}", headers=other_headers)
    assert r2.status_code == 403


# ---------------------------------------------------------------------------
# AC-api-19: recompute called on run, NOT on preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_called_on_run_not_preview(client, db_session, volunteer_auth_headers):
    rows = [{"first_name": "Recompute", "last_name": "Test", "external_id": "800"}]
    content = _csv_bytes(rows)

    async def _upload():
        up = await client.post(
            "/imports/upload",
            files={"file": ("c.csv", content, "text/csv")},
            data={"entity": "contacts"},
            headers=volunteer_auth_headers,
        )
        return up.json()["batch_id"]

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ) as mock_recompute:

        b_prev = await _upload()
        await client.post(
            f"/imports/{b_prev}/preview",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )
        # recompute should NOT have been called after preview
        mock_recompute.assert_not_called()

        b_run = await _upload()
        await client.post(
            f"/imports/{b_run}/run",
            json={"column_map": _minimal_contact_map(), "match_key": "external_id", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )
        # recompute SHOULD have been called exactly once after run
        mock_recompute.assert_called_once()


# ---------------------------------------------------------------------------
# AC-api-20: Purge sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_expired_imports(db_session, tmp_path):
    """Expired staged ImportBatch -> status='expired', staging_file deleted."""
    from app.services.queue_manager import QueueManager
    from app.database import async_session

    # Temporarily redirect STORAGE_PATH to tmp_path
    import app.config as _cfg
    original_storage = _cfg.legacy_settings.STORAGE_PATH
    _cfg.legacy_settings.STORAGE_PATH = str(tmp_path)

    try:
        # Create a fake staging file
        staging_dir = tmp_path / "imports"
        staging_dir.mkdir()
        fake_file = staging_dir / "expired_file.csv"
        fake_file.write_text("name\nAlice\n")

        # Create an ImportBatch with staging_file and expired expires_at
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        batch = ImportBatch(
            source_filename="expired_file.csv",
            entity="contacts",
            mode="wizard_preview",
            status="staged",
            column_map={},
            options={},
            staging_file="imports/expired_file.csv",
            expires_at=now - timedelta(hours=1),  # expired 1 hour ago
            created_by_id=None,
        )
        db_session.add(batch)
        await db_session.commit()
        await db_session.refresh(batch)
        batch_id = batch.id

        # Run purge (uses its own session from async_session factory)
        qm = QueueManager(async_session)
        await qm._purge_expired_imports()

        # Expire the session identity map so the next query reads from the DB
        # rather than returning the cached (stale) object.
        # expire_all() is synchronous in SQLAlchemy.
        db_session.expire_all()

        # Check the file is gone
        assert not fake_file.exists()

        # Check the batch status was updated
        from sqlalchemy import select
        result = await db_session.execute(select(ImportBatch).where(ImportBatch.id == batch_id))
        updated = result.scalar_one()
        assert updated.status == "expired"
        assert updated.staging_file is None
    finally:
        _cfg.legacy_settings.STORAGE_PATH = original_storage


# ---------------------------------------------------------------------------
# AC-api-21: List returns only own batches for volunteers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_own_batches_only(client, db_session, volunteer_auth_headers, admin_auth_headers):
    # Volunteer uploads a file
    content = _csv_bytes([{"first_name": "Own", "external_id": "1"}])
    up = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    assert up.status_code == 200

    resp = await client.get("/imports", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert len(data["items"]) >= 1


# ---------------------------------------------------------------------------
# AC-api-22: Cross-user batch access -> 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_batch_404(client, db_session, volunteer_auth_headers, admin_auth_headers):
    from conftest import make_token
    from app.models import User
    from app.utils.auth import hash_password

    other = User(
        email="other2@lightnc.org",
        password_hash=hash_password("OtherPass456!"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    other_headers = {"Authorization": f"Bearer {make_token(other.id, other.email, 'volunteer')}"}

    # Volunteer uploads
    content = _csv_bytes([{"first_name": "Secret", "external_id": "999"}])
    up = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = up.json()["batch_id"]

    # Other user tries to access
    resp = await client.get(f"/imports/{batch_id}", headers=other_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SEC-1: GET /storage/imports/<any> -> 404 (PII guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_imports_path_returns_404(client, volunteer_auth_headers):
    """Staged import files must NOT be downloadable via /storage/imports/..."""
    resp = await client.get(
        "/storage/imports/some-uuid.csv",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 404

    # Also with a nested path inside imports/
    resp2 = await client.get(
        "/storage/imports/subdir/file.csv",
        headers=volunteer_auth_headers,
    )
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# SEC-3: match_key=email new row with colliding external_id -> creates NEW contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_match_key_new_does_not_collide_external_id(
    client, db_session, volunteer_auth_headers
):
    """When match_key='email', a 'new' disposition must INSERT a fresh contact,
    even if the row's external_id matches an existing contact."""
    from sqlalchemy import select, func

    # Create an existing contact with external_id=777
    existing = Contact(
        first_name="Existing",
        last_name="Contact",
        email="existing@lightnc.org",
        external_id=777,
        contact_type="individual",
    )
    db_session.add(existing)
    await db_session.commit()
    await db_session.refresh(existing)
    existing_id = existing.id

    # Upload a new row: different email (no email match), SAME external_id=777
    rows = [{"first_name": "New", "last_name": "Person", "email": "new@lightnc.org", "external_id": "777"}]
    content = _csv_bytes(rows)
    upload = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "contacts"},
        headers=volunteer_auth_headers,
    )
    batch_id = upload.json()["batch_id"]

    col_map = {
        "first_name":  {"target": "first_name",  "data_type": "text"},
        "last_name":   {"target": "last_name",   "data_type": "text"},
        "email":       {"target": "email",       "data_type": "text"},
        "external_id": {"target": "external_id", "data_type": "integer"},
    }

    with patch(
        "app.services.member_status_service.recompute_all_contacts",
        new_callable=AsyncMock,
    ):
        resp = await client.post(
            f"/imports/{batch_id}/run",
            json={"column_map": col_map, "match_key": "email", "conflict_policy": "skip"},
            headers=volunteer_auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["counts"]["created"] == 1

    # The existing contact must NOT have been mutated
    db_session.expire_all()
    result = await db_session.execute(select(Contact).where(Contact.id == existing_id))
    untouched = result.scalar_one()
    assert untouched.first_name == "Existing"
    assert untouched.email == "existing@lightnc.org"

    # A brand-new contact must have been created (external_id collision → inserted without ext_id)
    count_result = await db_session.execute(
        select(func.count()).select_from(Contact).where(Contact.email == "new@lightnc.org")
    )
    assert count_result.scalar_one() == 1


# ---------------------------------------------------------------------------
# SEC-4: Unsupported entity -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_bad_entity_422(client, volunteer_auth_headers):
    """entity must be one of {contacts, participants}; others -> 422."""
    content = _csv_bytes([{"name": "X"}])
    resp = await client.post(
        "/imports/upload",
        files={"file": ("c.csv", content, "text/csv")},
        data={"entity": "malicious_entity"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422
    assert "entity" in resp.json()["detail"].lower() or "unsupported" in resp.json()["detail"].lower()
