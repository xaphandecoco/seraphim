"""Tests for the event_series router — S04-F05 acceptance criteria.

Test names match exactly the spec identifiers:
  create_series
  get_series
  update_series
  delete_series_soft
  list_series_volunteer_access
  list_series_is_active_filter
  audit_series_create
  audit_series_update
  audit_series_delete
  delete_does_not_remove_linked_events
  generate_sunday_series_created_skipped
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helper — valid series payload
# ---------------------------------------------------------------------------

def _series_payload(**overrides) -> dict:
    base = {
        "name": "Sunday Service Series",
        "event_type": "Sunday Celebration",
        "default_session_time": "10AM",
        "default_location": "Main Hall",
        "is_active": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# create_series
# POST /event-series → 201, response includes id, name, is_active
# ---------------------------------------------------------------------------


async def test_create_series(
    client: AsyncClient, admin_auth_headers
):
    payload = _series_payload()
    resp = await client.post("/event-series", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert body["name"] == payload["name"]
    assert body["event_type"] == payload["event_type"]
    assert body["default_session_time"] == payload["default_session_time"]
    assert body["default_location"] == payload["default_location"]
    assert body["is_active"] is True


# ---------------------------------------------------------------------------
# get_series
# GET /event-series/{id} → 200 with correct fields
# GET /event-series/{id} for non-existent id → 404
# ---------------------------------------------------------------------------


async def test_get_series(
    client: AsyncClient, admin_auth_headers
):
    # Create first
    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="Powerhouse Weekly"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    series_id = create_resp.json()["id"]

    # Get it back
    get_resp = await client.get(f"/event-series/{series_id}", headers=admin_auth_headers)
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["id"] == series_id
    assert body["name"] == "Powerhouse Weekly"
    assert body["is_active"] is True


async def test_get_series_not_found(
    client: AsyncClient, admin_auth_headers
):
    resp = await client.get("/event-series/999999", headers=admin_auth_headers)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# update_series
# PATCH /event-series/{id} → 200 with updated fields
# ---------------------------------------------------------------------------


async def test_update_series(
    client: AsyncClient, admin_auth_headers
):
    # Create
    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="Original Name"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    series_id = create_resp.json()["id"]

    # Update name and location
    patch_resp = await client.patch(
        f"/event-series/{series_id}",
        json={"name": "Updated Name", "default_location": "New Hall"},
        headers=admin_auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["name"] == "Updated Name"
    assert body["default_location"] == "New Hall"
    # is_active should remain unchanged
    assert body["is_active"] is True


# ---------------------------------------------------------------------------
# delete_series_soft
# DELETE /event-series/{id} → 200, is_active becomes False
# Series still visible with GET (soft delete, not hard delete)
# ---------------------------------------------------------------------------


async def test_delete_series_soft(
    client: AsyncClient, admin_auth_headers
):
    # Create
    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="To Be Soft Deleted"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    series_id = create_resp.json()["id"]

    # Soft-delete
    del_resp = await client.delete(
        f"/event-series/{series_id}", headers=admin_auth_headers
    )
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()
    assert body["id"] == series_id
    assert body["is_active"] is False

    # Series must still be retrievable (soft delete, not hard delete)
    get_resp = await client.get(f"/event-series/{series_id}", headers=admin_auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["is_active"] is False

    # Active-only list must not include it
    list_resp = await client.get(
        "/event-series?is_active=true", headers=admin_auth_headers
    )
    assert list_resp.status_code == 200
    active_ids = [s["id"] for s in list_resp.json()]
    assert series_id not in active_ids


# ---------------------------------------------------------------------------
# list_series_volunteer_access
# GET /event-series with volunteer token → 200 (not forbidden)
# ---------------------------------------------------------------------------


async def test_list_series_volunteer_access(
    client: AsyncClient, volunteer_auth_headers
):
    """Volunteers should be allowed to list event series (require_volunteer)."""
    resp = await client.get("/event-series", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# list_series_no_auth
# GET /event-series without auth → 401  (S04-F07 acceptance criterion b)
# ---------------------------------------------------------------------------


async def test_list_series_no_auth(client: AsyncClient):
    """Unauthenticated GET /event-series must return 401 (not 200 or 403)."""
    resp = await client.get("/event-series")
    assert resp.status_code == 401, (
        f"Expected 401 for unauthenticated request, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# list_series_is_active_filter
# GET /event-series?is_active=true only returns active series
# GET /event-series?is_active=false only returns inactive series
# ---------------------------------------------------------------------------


async def test_list_series_is_active_filter(
    client: AsyncClient, admin_auth_headers
):
    """?is_active filter must partition the result set correctly."""
    # Create one active and one inactive series
    r1 = await client.post(
        "/event-series",
        json=_series_payload(name="Active Series"),
        headers=admin_auth_headers,
    )
    assert r1.status_code == 201
    active_id = r1.json()["id"]

    r2 = await client.post(
        "/event-series",
        json=_series_payload(name="Inactive Series", is_active=False),
        headers=admin_auth_headers,
    )
    assert r2.status_code == 201
    inactive_id = r2.json()["id"]

    # ?is_active=true → only active
    resp_active = await client.get("/event-series?is_active=true", headers=admin_auth_headers)
    assert resp_active.status_code == 200
    ids_active = [s["id"] for s in resp_active.json()]
    assert active_id in ids_active
    assert inactive_id not in ids_active

    # ?is_active=false → only inactive
    resp_inactive = await client.get("/event-series?is_active=false", headers=admin_auth_headers)
    assert resp_inactive.status_code == 200
    ids_inactive = [s["id"] for s in resp_inactive.json()]
    assert inactive_id in ids_inactive
    assert active_id not in ids_inactive


# ---------------------------------------------------------------------------
# audit_series_create
# POST /event-series → audit_log has action='series.create'
# ---------------------------------------------------------------------------


async def test_audit_series_create(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """POST /event-series must write a 'series.create' audit log entry."""
    from app.models import AuditLog

    resp = await client.post(
        "/event-series",
        json=_series_payload(name="Audit Create Test"),
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201
    series_id = resp.json()["id"]

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "series.create",
            AuditLog.entity == "event_series",
            AuditLog.entity_id == series_id,
        )
    )
    log = result.scalars().first()
    assert log is not None, "No 'series.create' audit log found"
    assert log.after is not None
    assert log.before is None


# ---------------------------------------------------------------------------
# audit_series_update
# PATCH /event-series/{id} → audit_log has action='series.update'
# ---------------------------------------------------------------------------


async def test_audit_series_update(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """PATCH /event-series must write a 'series.update' audit log entry."""
    from app.models import AuditLog

    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="Audit Update Before"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    series_id = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/event-series/{series_id}",
        json={"name": "Audit Update After"},
        headers=admin_auth_headers,
    )
    assert patch_resp.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "series.update",
            AuditLog.entity == "event_series",
            AuditLog.entity_id == series_id,
        )
    )
    log = result.scalars().first()
    assert log is not None, "No 'series.update' audit log found"
    assert log.before is not None
    assert log.after is not None


# ---------------------------------------------------------------------------
# audit_series_delete
# DELETE /event-series/{id} → audit_log has action='series.delete'
# ---------------------------------------------------------------------------


async def test_audit_series_delete(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """DELETE /event-series must write a 'series.delete' audit log entry."""
    from app.models import AuditLog

    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="Audit Delete Test"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    series_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/event-series/{series_id}", headers=admin_auth_headers
    )
    assert del_resp.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "series.delete",
            AuditLog.entity == "event_series",
            AuditLog.entity_id == series_id,
        )
    )
    log = result.scalars().first()
    assert log is not None, "No 'series.delete' audit log found"
    assert log.before == {"is_active": True}
    assert log.after == {"is_active": False}


# ---------------------------------------------------------------------------
# delete_does_not_remove_linked_events
# DELETE /event-series/{id} must NOT cascade-delete linked events
# ---------------------------------------------------------------------------


async def test_delete_does_not_remove_linked_events(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """Soft-deleting a series must NOT remove linked Event rows."""
    from app.models import Event, EventSeries

    # Insert a series directly
    series = EventSeries(
        title="Linked Event Test Series",
        event_type="Sunday Celebration",
        session_time=None,
        is_active=True,
    )
    db_session.add(series)
    await db_session.commit()
    await db_session.refresh(series)

    # Create two linked events
    from datetime import datetime
    ev1 = Event(
        title="Linked Event 1",
        event_type="Sunday Celebration",
        recurring_series_id=series.id,
        start_at=datetime(2025, 6, 1, 0, 0),
    )
    ev2 = Event(
        title="Linked Event 2",
        event_type="Sunday Celebration",
        recurring_series_id=series.id,
        start_at=datetime(2025, 6, 8, 0, 0),
    )
    db_session.add(ev1)
    db_session.add(ev2)
    await db_session.commit()
    await db_session.refresh(ev1)
    await db_session.refresh(ev2)

    ev1_id = ev1.id
    ev2_id = ev2.id

    # Soft-delete the series
    del_resp = await client.delete(
        f"/event-series/{series.id}", headers=admin_auth_headers
    )
    assert del_resp.status_code == 200

    # Linked events must still exist in the DB
    result = await db_session.execute(
        select(Event).where(Event.id.in_([ev1_id, ev2_id]))
    )
    remaining = result.scalars().all()
    assert len(remaining) == 2, (
        f"Expected 2 linked events to survive soft-delete; found {len(remaining)}"
    )
    for ev in remaining:
        # recurring_series_id must still be set (FK is SET NULL only on hard delete)
        assert ev.recurring_series_id == series.id


# ---------------------------------------------------------------------------
# generate_sunday_series_created_skipped
# POST /event-series/{id}/generate for Sunday Celebration:
#   first call → created=3, skipped=0
#   second call → created=0, skipped=3
# ---------------------------------------------------------------------------


async def test_generate_sunday_series_created_skipped(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """Generate for a Sunday Celebration series: first 3 created, then 3 skipped."""
    from app.models import EventSeries

    series = EventSeries(
        title="Sunday Service Gen Test",
        event_type="Sunday Celebration",
        session_time=None,
        is_active=True,
    )
    db_session.add(series)
    await db_session.commit()
    await db_session.refresh(series)

    target = "2025-06-01"  # A known Sunday

    # First call → created=3, skipped=0
    resp1 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    assert body1["created"] == 3, f"Expected created=3, got {body1['created']}"
    assert body1["skipped"] == 0, f"Expected skipped=0, got {body1['skipped']}"
    assert len(body1["events"]) == 3

    # Second call (idempotency) → created=0, skipped=3
    resp2 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["created"] == 0, f"Expected created=0, got {body2['created']}"
    assert body2["skipped"] == 3, f"Expected skipped=3, got {body2['skipped']}"


# ---------------------------------------------------------------------------
# generate_via_api_sunday_celebration (end-to-end test)
# POST /event-series with event_type='Sunday Celebration' then generate →
# must work end-to-end: created=3, skipped=0
# ---------------------------------------------------------------------------


async def test_generate_via_api_sunday_service_event_type(
    client: AsyncClient, admin_auth_headers
):
    """Generate endpoint works end-to-end for series created via POST /event-series."""
    # Create via API using the spec event_type
    create_resp = await client.post(
        "/event-series",
        json=_series_payload(name="API Sunday Series"),
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    series_id = create_resp.json()["id"]

    gen_resp = await client.post(
        f"/event-series/{series_id}/generate",
        json={"target_date": "2025-06-01"},
        headers=admin_auth_headers,
    )
    assert gen_resp.status_code == 200, gen_resp.text
    body = gen_resp.json()
    assert body["created"] == 3
    assert body["skipped"] == 0
