"""
Attendance endpoint tests.

Coverage:
  - GET  /attendance              → returns list of records
  - GET  /attendance?event_id=    → filters by event
  - GET  /attendance?date=        → filters by date
  - GET  /attendance?date=INVALID → 400 bad date format
  - GET  /attendance?status=      → filters by status
  - POST /attendance/push-preview (admin) → returns PushDiff
  - POST /attendance/push-preview for missing event → 404
  - POST /attendance/push-preview (non-admin) → 403
  - POST /attendance/push (admin) → queues pending records
  - POST /attendance/push no pending records → message + 0 pushed
  - POST /attendance/push for missing event → 404
  - GET  /attendance/dead-letter (admin) → returns list + total
  - GET  /attendance/dead-letter (non-admin) → 403
  - POST /attendance/dead-letter/{id}/retry (admin) → status reset to pending
  - POST /attendance/dead-letter/{id}/retry for non-existent id → 404
  - POST /attendance/dead-letter/{id}/retry for non-dead-letter record → 400
  - GET  /attendance requires auth → 401
"""

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# GET /attendance (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_attendance_returns_records(
    client: AsyncClient,
    sample_attendance,
    volunteer_auth_headers,
):
    resp = await client.get("/attendance", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_list_attendance_filter_by_event_id(
    client: AsyncClient,
    sample_attendance,
    volunteer_auth_headers,
):
    event_id = sample_attendance.event_id
    resp = await client.get(
        f"/attendance?event_id={event_id}", headers=volunteer_auth_headers
    )
    assert resp.status_code == 200
    records = resp.json()
    assert all(r["event_id"] == event_id for r in records)


@pytest.mark.asyncio
async def test_list_attendance_filter_by_date(
    client: AsyncClient,
    sample_attendance,
    volunteer_auth_headers,
):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    resp = await client.get(
        f"/attendance?date={today}", headers=volunteer_auth_headers
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_attendance_invalid_date_returns_400(
    client: AsyncClient,
    volunteer_auth_headers,
):
    resp = await client.get(
        "/attendance?date=not-a-date", headers=volunteer_auth_headers
    )
    assert resp.status_code == 400
    assert "Invalid date format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_attendance_filter_by_status(
    client: AsyncClient,
    sample_attendance,
    volunteer_auth_headers,
):
    resp = await client.get(
        "/attendance?status=confirmed", headers=volunteer_auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["status"] == "confirmed" for r in data)


@pytest.mark.asyncio
async def test_list_attendance_requires_auth(client: AsyncClient):
    resp = await client.get("/attendance")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /attendance/push-preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_preview_returns_diff(
    client: AsyncClient,
    sample_attendance,
    sample_event,
    admin_auth_headers,
):
    resp = await client.post(
        f"/attendance/push-preview?event_id={sample_event.event_id}",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "event_id" in data
    assert "will_attend" in data
    assert "missing" in data
    assert "duplicates_warn" in data
    assert data["event_id"] == sample_event.event_id
    assert data["event_title"] == sample_event.title


@pytest.mark.asyncio
async def test_push_preview_event_not_found_returns_404(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.post(
        "/attendance/push-preview?event_id=999999",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404
    assert "Event not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_push_preview_requires_admin(
    client: AsyncClient,
    sample_event,
    volunteer_auth_headers,
):
    resp = await client.post(
        f"/attendance/push-preview?event_id={sample_event.event_id}",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /attendance/push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_attendance_queues_pending_records(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_event,
    sample_member,
    sample_detection,
    admin_auth_headers,
):
    from app.models import Attendance

    # Create a fresh pending attendance record for this test
    record = Attendance(
        contact_id=sample_member.contact_id,
        event_id=sample_event.event_id,
        detection_id=sample_detection.id,
        status="confirmed",
        push_status="pending",
        push_attempts=0,
    )
    db_session.add(record)
    await db_session.commit()

    resp = await client.post(
        f"/attendance/push?event_id={sample_event.event_id}",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] >= 1
    assert "Queued" in data["message"]

    await db_session.refresh(record)
    assert record.push_status == "queued"


@pytest.mark.asyncio
async def test_push_attendance_no_pending_returns_zero(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_event,
    admin_auth_headers,
):
    """No pending records for event → returns message with 0 pushed."""
    resp = await client.post(
        f"/attendance/push?event_id={sample_event.event_id}",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pushed"] == 0
    assert "No pending records" in data["message"]


@pytest.mark.asyncio
async def test_push_attendance_event_not_found_returns_404(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.post(
        "/attendance/push?event_id=888888",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_push_attendance_requires_admin(
    client: AsyncClient,
    sample_event,
    volunteer_auth_headers,
):
    resp = await client.post(
        f"/attendance/push?event_id={sample_event.event_id}",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /attendance/dead-letter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_list_returns_records_for_admin(
    client: AsyncClient,
    dead_letter_attendance,
    admin_auth_headers,
):
    resp = await client.get("/attendance/dead-letter", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "items" in data
    assert data["total"] >= 1

    ids = [item["id"] for item in data["items"]]
    assert dead_letter_attendance.id in ids


@pytest.mark.asyncio
async def test_dead_letter_list_all_items_have_dead_letter_status(
    client: AsyncClient,
    dead_letter_attendance,
    admin_auth_headers,
):
    resp = await client.get("/attendance/dead-letter", headers=admin_auth_headers)
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["push_status"] == "dead_letter"


@pytest.mark.asyncio
async def test_dead_letter_list_non_admin_returns_403(
    client: AsyncClient,
    volunteer_auth_headers,
):
    resp = await client.get("/attendance/dead-letter", headers=volunteer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dead_letter_list_requires_auth(client: AsyncClient):
    resp = await client.get("/attendance/dead-letter")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /attendance/dead-letter/{id}/retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_letter_retry_resets_push_status_to_pending(
    client: AsyncClient,
    dead_letter_attendance,
    admin_auth_headers,
    db_session: AsyncSession,
):
    record_id = dead_letter_attendance.id

    resp = await client.post(
        f"/attendance/dead-letter/{record_id}/retry",
        headers=admin_auth_headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == record_id
    assert data["push_status"] == "pending"
    assert data["push_attempts"] == 0
    assert "reset to 'pending'" in data["message"]

    # Verify DB state updated
    await db_session.refresh(dead_letter_attendance)
    assert dead_letter_attendance.push_status == "pending"
    assert dead_letter_attendance.push_attempts == 0
    assert dead_letter_attendance.last_push_error is None


@pytest.mark.asyncio
async def test_dead_letter_retry_nonexistent_record_returns_404(
    client: AsyncClient,
    admin_auth_headers,
):
    resp = await client.post(
        "/attendance/dead-letter/999999/retry",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_dead_letter_retry_non_dead_letter_record_returns_400(
    client: AsyncClient,
    sample_attendance,  # push_status="pending", not "dead_letter"
    admin_auth_headers,
):
    resp = await client.post(
        f"/attendance/dead-letter/{sample_attendance.id}/retry",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 400
    assert "not in dead_letter state" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dead_letter_retry_requires_admin(
    client: AsyncClient,
    dead_letter_attendance,
    volunteer_auth_headers,
):
    resp = await client.post(
        f"/attendance/dead-letter/{dead_letter_attendance.id}/retry",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dead_letter_retry_requires_auth(
    client: AsyncClient,
    dead_letter_attendance,
):
    resp = await client.post(
        f"/attendance/dead-letter/{dead_letter_attendance.id}/retry"
    )
    assert resp.status_code == 401
