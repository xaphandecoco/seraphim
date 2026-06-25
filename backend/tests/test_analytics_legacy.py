"""Legacy analytics endpoint tests — S05 cleanup verification.

Asserts:
  1. GET /analytics/export/attendance — the old attendance CSV export endpoint
     has been removed (returns 404 or 405).
  2. GET /analytics/export/logs — the logs CSV export endpoint still works cleanly
     (200, content-type text/csv, no push_status column in header).
  3. The logs export does not include push_status in its CSV header or data.
  4. Unauthenticated requests to /analytics/export/logs return 401.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Fixtures — minimal log row so the CSV has data
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sample_log(db_session: AsyncSession, sample_event):
    """Create a minimal Log row for the logs export test."""
    from app.models import Log

    log = Log(
        action="auto",
        matched_name="Test Person",
        confidence=0.95,
        tier="91-99",
        event_id=sample_event.id,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


# ---------------------------------------------------------------------------
# Deleted: attendance export endpoint (GET /analytics/export/attendance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attendance_export_endpoint_removed(
    client: AsyncClient,
    admin_auth_headers,
):
    """The attendance CSV export endpoint must no longer be available.

    It was removed in S05 (replaced by async export jobs + streaming endpoints).
    The route should return 404 (not found) or 405 (method not allowed).
    """
    resp = await client.get(
        "/analytics/export/attendance",
        headers=admin_auth_headers,
    )
    assert resp.status_code in (404, 405), (
        f"Expected 404 or 405 for removed endpoint /analytics/export/attendance, "
        f"got {resp.status_code}. The endpoint must be deleted, not just deprecated."
    )


@pytest.mark.asyncio
async def test_attendance_export_post_also_gone(
    client: AsyncClient,
    admin_auth_headers,
):
    """POST to the removed attendance export endpoint also returns 404/405."""
    resp = await client.post(
        "/analytics/export/attendance",
        headers=admin_auth_headers,
    )
    assert resp.status_code in (404, 405)


# ---------------------------------------------------------------------------
# Clean: logs export endpoint (GET /analytics/export/logs) — still present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logs_export_returns_200(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """The logs CSV export endpoint must return 200."""
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200, (
        f"Expected 200 from /analytics/export/logs, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_logs_export_content_type_csv(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """The logs export must return text/csv content type."""
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert "text/csv" in content_type, (
        f"Expected content-type text/csv, got: {content_type}"
    )


@pytest.mark.asyncio
async def test_logs_export_has_header_row(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """The logs CSV must include a header row with the expected columns."""
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert len(lines) >= 1, "Expected at least a header row"
    header = lines[0]
    # Expected columns per analytics.py export_logs_csv
    for col in ("id", "timestamp", "action"):
        assert col in header, f"Missing column '{col}' in logs CSV header: {header}"


@pytest.mark.asyncio
async def test_logs_export_no_push_status_column(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """The logs CSV must NOT contain a push_status column.

    push_status was removed in S01 migration. The export must remain clean.
    """
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert len(lines) >= 1
    header = lines[0]
    assert "push_status" not in header, (
        f"'push_status' must not appear in logs CSV header. Got: {header}"
    )


@pytest.mark.asyncio
async def test_logs_export_data_row_present(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """With a log row in the DB, the CSV must have at least 2 lines (header + data)."""
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) >= 2, (
        f"Expected at least header + 1 data row, got {len(lines)} lines"
    )


@pytest.mark.asyncio
async def test_logs_export_requires_auth(client: AsyncClient):
    """Unauthenticated request to /analytics/export/logs must return 401."""
    resp = await client.get("/analytics/export/logs")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logs_export_requires_admin(
    client: AsyncClient,
    sample_log,
    volunteer_auth_headers,
):
    """Volunteer-level auth must not access the admin-only logs export."""
    resp = await client.get("/analytics/export/logs", headers=volunteer_auth_headers)
    assert resp.status_code in (401, 403), (
        f"Expected 401 or 403 for volunteer on admin endpoint, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_logs_export_content_disposition(
    client: AsyncClient,
    sample_log,
    admin_auth_headers,
):
    """The logs export must set Content-Disposition with a filename."""
    resp = await client.get("/analytics/export/logs", headers=admin_auth_headers)
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd or "filename" in cd, (
        f"Expected Content-Disposition with attachment/filename, got: {cd!r}"
    )
