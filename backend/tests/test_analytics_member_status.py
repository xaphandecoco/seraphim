"""Tests for S23 analytics endpoints — /analytics/recompute-member-status
and /analytics/member-status-summary.

Fixtures used from conftest: client, admin_auth_headers, volunteer_auth_headers.
"""

from __future__ import annotations

import pytest

from app.models import Contact, Event, Participant
from datetime import datetime


# ---------------------------------------------------------------------------
# POST /analytics/recompute-member-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_admin_returns_200(client, admin_auth_headers, db_session):
    """Admin POST returns 200 with contact_count, duration_ms >= 0, job_run_id."""
    # Insert one in-scope contact so contact_count > 0
    contact = Contact(first_name="Maria", last_name="Santos", contact_type="individual")
    db_session.add(contact)
    await db_session.commit()

    resp = await client.post("/analytics/recompute-member-status", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "contact_count" in data
    assert data["contact_count"] >= 0
    assert "duration_ms" in data
    assert data["duration_ms"] >= 0
    assert "job_run_id" in data  # value may be None when job_runs table absent


@pytest.mark.asyncio
async def test_recompute_with_contact_ids(client, admin_auth_headers, db_session):
    """POST with contact_ids list limits the recompute scope."""
    c1 = Contact(first_name="A", last_name="B", contact_type="individual")
    c2 = Contact(first_name="C", last_name="D", contact_type="individual")
    db_session.add_all([c1, c2])
    await db_session.commit()

    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": [c1.id]},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["contact_count"] == 1


@pytest.mark.asyncio
async def test_recompute_volunteer_returns_403(client, volunteer_auth_headers):
    """Volunteer users must receive 403."""
    resp = await client.post("/analytics/recompute-member-status", headers=volunteer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_recompute_unauthenticated_returns_401(client):
    """Unauthenticated requests must receive 401."""
    resp = await client.post("/analytics/recompute-member-status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_recompute_over_500_ids_returns_422(client, admin_auth_headers):
    """More than 500 contact_ids must be rejected with 422 before any DB work."""
    big_list = list(range(1, 502))  # 501 IDs
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": big_list},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_recompute_exactly_500_ids_accepted(client, admin_auth_headers):
    """Exactly 500 contact_ids is within limit — must not return 422."""
    ids_500 = list(range(1, 501))  # 500 IDs (contacts need not exist — result is 0)
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": ids_500},
        headers=admin_auth_headers,
    )
    # 200 OK (no matching in-scope contacts → contact_count = 0)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recompute_empty_body_treated_as_all(client, admin_auth_headers):
    """Omitting body triggers recompute_all (contact_ids treated as None)."""
    resp = await client.post("/analytics/recompute-member-status", headers=admin_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recompute_null_contact_ids_treated_as_all(client, admin_auth_headers):
    """Explicit contact_ids=null triggers recompute_all."""
    resp = await client.post(
        "/analytics/recompute-member-status",
        json={"contact_ids": None},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /analytics/member-status-summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_admin_returns_200(client, admin_auth_headers, db_session):
    """Admin GET returns 200 with expected shape."""
    # Insert a couple of contacts
    for i in range(3):
        db_session.add(Contact(first_name=f"P{i}", last_name="Q", contact_type="individual"))
    # Non-individual — must not appear in total_contacts
    db_session.add(Contact(first_name="Org", last_name="X", contact_type="organization"))
    await db_session.commit()

    resp = await client.get("/analytics/member-status-summary", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Required keys
    assert "tier_counts" in data
    assert "total_contacts" in data
    assert "is_active_count" in data
    assert "is_inactive_count" in data
    assert "is_regular_count" in data
    assert "is_connected_count" in data
    assert "last_recomputed_at" in data

    # tier_counts always includes "null" key
    assert "null" in data["tier_counts"]

    # Values sum to total_contacts
    assert sum(data["tier_counts"].values()) == data["total_contacts"]

    # Total is 3 (the 3 individuals; org excluded)
    assert data["total_contacts"] == 3


@pytest.mark.asyncio
async def test_summary_tier_counts_sum_after_recompute(client, admin_auth_headers, db_session):
    """After a recompute, tier_counts still sum to total_contacts."""
    # Insert contact with known attendance
    contact = Contact(first_name="Jose", last_name="Reyes", contact_type="individual")
    db_session.add(contact)
    await db_session.flush()

    ev = Event(title="Svc", start_at=datetime(2025, 1, 1, 9, 0))
    db_session.add(ev)
    await db_session.flush()

    db_session.add(Participant(contact_id=contact.id, event_id=ev.id, status="attended", source="manual"))
    await db_session.commit()

    # Trigger recompute
    await client.post(
        "/analytics/recompute-member-status",
        headers=admin_auth_headers,
    )

    resp = await client.get("/analytics/member-status-summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "null" in data["tier_counts"]
    assert sum(data["tier_counts"].values()) == data["total_contacts"]


@pytest.mark.asyncio
async def test_summary_last_recomputed_at_null_before_any_recompute(
    client, admin_auth_headers
):
    """last_recomputed_at is null when job_runs table exists but has no rows."""
    resp = await client.get("/analytics/member-status-summary", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # No recompute has been run yet — MAX(started_at) over empty table is NULL
    assert data["last_recomputed_at"] is None


@pytest.mark.asyncio
async def test_summary_last_recomputed_at_non_null_after_recompute(
    client, admin_auth_headers, db_session
):
    """last_recomputed_at is non-null after a full recompute runs."""
    contact = Contact(first_name="Test", last_name="User", contact_type="individual")
    db_session.add(contact)
    await db_session.commit()

    await client.post("/analytics/recompute-member-status", headers=admin_auth_headers)

    resp = await client.get("/analytics/member-status-summary", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["last_recomputed_at"] is not None


@pytest.mark.asyncio
async def test_summary_volunteer_returns_403(client, volunteer_auth_headers):
    """Volunteer users must receive 403."""
    resp = await client.get("/analytics/member-status-summary", headers=volunteer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_summary_unauthenticated_returns_401(client):
    """Unauthenticated requests must receive 401."""
    resp = await client.get("/analytics/member-status-summary")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_summary_empty_db_returns_zero_totals(client, admin_auth_headers):
    """With no contacts, all counts are 0 and tier_counts has 'null' key with 0."""
    resp = await client.get("/analytics/member-status-summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_contacts"] == 0
    assert data["tier_counts"].get("null", -1) == 0
    assert sum(data["tier_counts"].values()) == 0
