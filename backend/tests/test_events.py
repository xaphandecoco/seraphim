"""Tests for events router and event_generator service — S04-F08 acceptance criteria.

Test names match exactly the spec identifiers:
  create_event_with_session_time
  create_event_invalid_session_time (422)
  create_event_invalid_type (422)
  soft_delete_event
  event_series_generate_idempotent
  event_series_generate_powerhouse
  add_participant_manual (201)
  add_participant_duplicate_409
  update_participant_status
  set_active_event_native (404 'Event {id} not found.')
  events_pagination (25→20 + total 25)
  events_filter_by_type
  volunteer_cannot_create_event (403)
  generate_sunday_events_pht_utc (00:00/02:00/07:00 UTC)
  test_viewer_can_read_events (skipped — S15)
"""

from datetime import date, datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_str() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# create_event_with_session_time
# POST /events with event_type='Sunday Celebration' and session_time='10AM' → 201
# Response body must contain participant_counts with unique_count=0, total_count=0.
# ---------------------------------------------------------------------------


async def test_create_event_with_session_time(
    client: AsyncClient, admin_auth_headers
):
    payload = {
        "title": "Sunday Celebration Service",
        "event_type": "Sunday Celebration",
        "session_time": "10AM",
        "start_at": _utcnow_str(),
    }
    resp = await client.post("/events", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_type"] == "Sunday Celebration"
    assert body["session_time"] == "10AM"
    counts = body["participant_counts"]
    assert counts["unique_count"] == 0
    assert counts["total_count"] == 0


# ---------------------------------------------------------------------------
# create_event_invalid_session_time
# POST /events with event_type that disallows session_time → 422
# Detail must mention 'session_time'.
# ---------------------------------------------------------------------------


async def test_create_event_invalid_session_time(
    client: AsyncClient, admin_auth_headers
):
    payload = {
        "title": "Prayer Meeting",
        "event_type": "Prayer Meeting",
        "session_time": "8AM",
        "start_at": _utcnow_str(),
    }
    resp = await client.post("/events", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "session_time" in str(detail)


# ---------------------------------------------------------------------------
# create_event_invalid_type
# POST /events with an unrecognised event_type → 422
# ---------------------------------------------------------------------------


async def test_create_event_invalid_type(
    client: AsyncClient, admin_auth_headers
):
    payload = {
        "title": "Mystery Event",
        "event_type": "not_a_valid_type_xyz",
        "start_at": _utcnow_str(),
    }
    resp = await client.post("/events", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# soft_delete_event
# DELETE /events/{id} → 200, event hidden from default list, visible with ?is_active=false
# ---------------------------------------------------------------------------


async def test_soft_delete_event(
    client: AsyncClient, admin_auth_headers
):
    # Create event
    create_resp = await client.post(
        "/events",
        json={"title": "Event To Soft Delete", "start_at": _utcnow_str()},
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    event_id = create_resp.json()["id"]

    # Soft-delete it
    del_resp = await client.delete(f"/events/{event_id}", headers=admin_auth_headers)
    assert del_resp.status_code == 200

    # Default list (is_active=true by default) must not include it
    list_resp = await client.get("/events", headers=admin_auth_headers)
    assert list_resp.status_code == 200
    active_ids = [item["id"] for item in list_resp.json()["items"]]
    assert event_id not in active_ids

    # With is_active=false it must appear
    inactive_resp = await client.get("/events?is_active=false", headers=admin_auth_headers)
    assert inactive_resp.status_code == 200
    inactive_ids = [item["id"] for item in inactive_resp.json()["items"]]
    assert event_id in inactive_ids


# ---------------------------------------------------------------------------
# event_series_generate_idempotent
# POST /event-series/{id}/generate called twice with the same target_date:
#   first call → created=3, skipped=0
#   second call → created=0, skipped=3
# ---------------------------------------------------------------------------


async def test_event_series_generate_idempotent(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    from app.models import EventSeries

    series = EventSeries(
        title="Sunday Service Series",
        event_type="Sunday Celebration",
        session_time=None,
        is_active=True,
    )
    db_session.add(series)
    await db_session.commit()
    await db_session.refresh(series)

    target = "2025-06-01"  # known Sunday

    # First generate — should create 3 sessions
    resp1 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    assert body1["created"] == 3
    assert body1["skipped"] == 0

    # Second generate (idempotency) — should skip all 3
    resp2 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["created"] == 0
    assert body2["skipped"] == 3


# ---------------------------------------------------------------------------
# event_series_generate_powerhouse
# POST /event-series/{id}/generate for a Powerhouse series:
#   first call → created=1, skipped=0
#   second call → created=0, skipped=1
# ---------------------------------------------------------------------------


async def test_event_series_generate_powerhouse(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    from app.models import EventSeries

    series = EventSeries(
        title="Powerhouse Series",
        event_type="Powerhouse",
        session_time=None,
        is_active=True,
    )
    db_session.add(series)
    await db_session.commit()
    await db_session.refresh(series)

    target = "2025-06-04"  # a Wednesday

    # First generate
    resp1 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp1.status_code == 200, resp1.text
    body1 = resp1.json()
    assert body1["created"] == 1
    assert body1["skipped"] == 0

    # Second generate — idempotent
    resp2 = await client.post(
        f"/event-series/{series.id}/generate",
        json={"target_date": target},
        headers=admin_auth_headers,
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["created"] == 0
    assert body2["skipped"] == 1


# ---------------------------------------------------------------------------
# add_participant_manual
# POST /events/{id}/participants → 201, source='manual'
# ---------------------------------------------------------------------------


async def test_add_participant_manual(
    client: AsyncClient, admin_auth_headers, sample_event, sample_contact
):
    resp = await client.post(
        f"/events/{sample_event.id}/participants",
        json={"contact_id": sample_contact.id, "status": "attended"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "manual"
    assert body["contact_id"] == sample_contact.id
    assert "participant_id" in body


# ---------------------------------------------------------------------------
# add_participant_duplicate_409
# Adding the same contact twice to the same event → 409
# ---------------------------------------------------------------------------


async def test_add_participant_duplicate_409(
    client: AsyncClient, admin_auth_headers, sample_event, sample_contact
):
    # First add — must succeed
    resp = await client.post(
        f"/events/{sample_event.id}/participants",
        json={"contact_id": sample_contact.id, "status": "attended"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201

    # Duplicate add — must return 409
    resp = await client.post(
        f"/events/{sample_event.id}/participants",
        json={"contact_id": sample_contact.id, "status": "attended"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# update_participant_status
# PATCH /events/{id}/participants/{pid} → 200, status updated
# ---------------------------------------------------------------------------


async def test_update_participant_status(
    client: AsyncClient, admin_auth_headers, sample_event, sample_contact
):
    # Add participant
    add_resp = await client.post(
        f"/events/{sample_event.id}/participants",
        json={"contact_id": sample_contact.id, "status": "attended"},
        headers=admin_auth_headers,
    )
    assert add_resp.status_code == 201
    pid = add_resp.json()["participant_id"]

    # Update status to no_show
    patch_resp = await client.patch(
        f"/events/{sample_event.id}/participants/{pid}",
        json={"status": "no_show"},
        headers=admin_auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["status"] == "no_show"


# ---------------------------------------------------------------------------
# set_active_event_native
# POST /events/set-active?event_id=<nonexistent> → 404
# detail must be exactly 'Event {id} not found.'
# ---------------------------------------------------------------------------


async def test_set_active_event_native(
    client: AsyncClient, admin_auth_headers
):
    resp = await client.post(
        "/events/set-active?event_id=999999", headers=admin_auth_headers
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Event 999999 not found."


# ---------------------------------------------------------------------------
# events_pagination
# Bulk-insert 25 events; GET /events?page_size=20&page=1 → 20 items + total=25
# ---------------------------------------------------------------------------


async def test_events_pagination(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    from app.models import Event

    for i in range(25):
        db_session.add(Event(title=f"Bulk Event {i}", is_active=True))
    await db_session.commit()

    resp = await client.get("/events?page_size=20&page=1", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 25
    assert len(body["items"]) == 20


# ---------------------------------------------------------------------------
# events_filter_by_type
# GET /events?type=Prayer+Meeting returns only Prayer Meeting events
# ---------------------------------------------------------------------------


async def test_events_filter_by_type(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    from app.models import Event

    # Create events of mixed types
    db_session.add(Event(title="PM Event 1", event_type="Prayer Meeting", is_active=True))
    db_session.add(Event(title="PM Event 2", event_type="Prayer Meeting", is_active=True))
    db_session.add(Event(title="Other Event", event_type="other", is_active=True))
    await db_session.commit()

    resp = await client.get("/events?type=Prayer+Meeting", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert all(item["event_type"] == "Prayer Meeting" for item in items)
    assert len(items) == 2


# ---------------------------------------------------------------------------
# volunteer_cannot_create_event
# POST /events with volunteer credentials → 403
# ---------------------------------------------------------------------------


async def test_volunteer_cannot_create_event(
    client: AsyncClient, volunteer_auth_headers
):
    payload = {"title": "Volunteer Created Event", "start_at": _utcnow_str()}
    resp = await client.post("/events", json=payload, headers=volunteer_auth_headers)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# test_soft_delete_active_event (AC5)
# DELETE /events/{id} when the event is the currently-active event
# → 200 with a "warning" key in the response body.
# ---------------------------------------------------------------------------


async def test_soft_delete_active_event(
    client: AsyncClient, admin_auth_headers
):
    """AC5 — soft-delete of the currently-active event returns 200 with a warning key.

    The set-active endpoint reloads dynamic_settings from DB (wiping the test
    jwt_secret).  To avoid auth failures on subsequent requests, we inject the
    active_event_id directly into dynamic_settings._settings instead of going
    through the /set-active endpoint.
    """
    from app.config import dynamic_settings

    # Create an event
    create_resp = await client.post(
        "/events",
        json={"title": "Active Event To Delete", "start_at": _utcnow_str()},
        headers=admin_auth_headers,
    )
    assert create_resp.status_code == 201
    event_id = create_resp.json()["id"]

    # Inject the active_event_id so the delete handler sees it without
    # triggering a settings reload that would clobber the test jwt_secret.
    dynamic_settings._settings["active_event_id"] = event_id

    # Now soft-delete it — should return 200 with a warning
    del_resp = await client.delete(f"/events/{event_id}", headers=admin_auth_headers)
    assert del_resp.status_code == 200, del_resp.text
    body = del_resp.json()
    assert body.get("is_active") is False
    assert "warning" in body, (
        f"Expected 'warning' key in response, got: {list(body.keys())}"
    )


# ---------------------------------------------------------------------------
# test_get_event_detail_with_participant_counts
# GET /events/{id} returns EventDetailResponse with participant_counts from service
# ---------------------------------------------------------------------------


async def test_get_event_detail_with_participant_counts(
    client: AsyncClient, admin_auth_headers, sample_event, sample_contact
):
    # Initially participant_counts should be zeros
    resp = await client.get(
        f"/events/{sample_event.id}", headers=admin_auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "participant_counts" in body, f"Missing 'participant_counts' in response: {list(body.keys())}"
    counts = body["participant_counts"]
    assert counts["unique_count"] == 0
    assert counts["total_count"] == 0

    # Add a participant
    add_resp = await client.post(
        f"/events/{sample_event.id}/participants",
        json={"contact_id": sample_contact.id, "status": "attended"},
        headers=admin_auth_headers,
    )
    assert add_resp.status_code == 201

    # Now participant_counts should reflect the addition
    resp2 = await client.get(
        f"/events/{sample_event.id}", headers=admin_auth_headers
    )
    assert resp2.status_code == 200, resp2.text
    counts2 = resp2.json()["participant_counts"]
    assert counts2["unique_count"] == 1
    assert counts2["total_count"] == 1


# ---------------------------------------------------------------------------
# generate_sunday_events_pht_utc
# Verify PHT→UTC conversion:
#   8AM PHT  → 00:00 UTC
#   10AM PHT → 02:00 UTC
#   3PM PHT  → 07:00 UTC
#
# Target: Sunday 2025-06-01 (verified as Sunday)
# ---------------------------------------------------------------------------


async def test_generate_sunday_events_pht_utc(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    from app.models import Event, EventSeries
    from app.services.event_generator import generate_sunday_events

    series = EventSeries(
        title="Sunday Service PHT Test",
        event_type="Sunday Celebration",
        session_time=None,
        is_active=True,
    )
    db_session.add(series)
    await db_session.commit()
    await db_session.refresh(series)

    target = date(2025, 6, 1)  # known Sunday
    created_events = await generate_sunday_events(series.id, db_session, target)
    await db_session.commit()

    # Should have created exactly 3 events
    assert len(created_events) == 3, f"Expected 3 events, got {len(created_events)}"

    # Reload from DB to get committed values
    for ev in created_events:
        await db_session.refresh(ev)

    # Sort by start_at to check in order: 00:00, 02:00, 07:00
    events_sorted = sorted(created_events, key=lambda e: e.start_at)

    # 8AM PHT = 00:00 UTC on 2025-06-01
    assert events_sorted[0].start_at == datetime(2025, 6, 1, 0, 0, 0), (
        f"Expected 00:00 UTC for 8AM PHT, got {events_sorted[0].start_at}"
    )
    assert events_sorted[0].session_time == "8AM"

    # 10AM PHT = 02:00 UTC on 2025-06-01
    assert events_sorted[1].start_at == datetime(2025, 6, 1, 2, 0, 0), (
        f"Expected 02:00 UTC for 10AM PHT, got {events_sorted[1].start_at}"
    )
    assert events_sorted[1].session_time == "10AM"

    # 3PM PHT = 07:00 UTC on 2025-06-01
    assert events_sorted[2].start_at == datetime(2025, 6, 1, 7, 0, 0), (
        f"Expected 07:00 UTC for 3PM PHT, got {events_sorted[2].start_at}"
    )
    assert events_sorted[2].session_time == "3PM"


# ---------------------------------------------------------------------------
# test_viewer_can_read_events — skipped: viewer role added in S15
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="S15 adds viewer role")
async def test_viewer_can_read_events(client: AsyncClient, viewer_auth_headers):
    resp = await client.get("/events", headers=viewer_auth_headers)
    assert resp.status_code == 200
