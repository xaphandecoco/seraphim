"""Tests for GET /attendance (repointed to Participant model)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_participants_returns_records(
    client: AsyncClient, sample_participant, volunteer_auth_headers
):
    resp = await client.get("/attendance", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_list_participants_filter_by_event_id(
    client: AsyncClient, sample_participant, volunteer_auth_headers
):
    resp = await client.get(
        f"/attendance?event_id={sample_participant.event_id}",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200
    assert all(r["event_id"] == sample_participant.event_id for r in resp.json())


@pytest.mark.asyncio
async def test_list_participants_filter_by_status(
    client: AsyncClient, sample_participant, volunteer_auth_headers
):
    resp = await client.get("/attendance?status=attended", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    assert all(r["status"] == "attended" for r in resp.json())


@pytest.mark.asyncio
async def test_list_participants_invalid_date_returns_400(
    client: AsyncClient, volunteer_auth_headers
):
    resp = await client.get("/attendance?date=not-a-date", headers=volunteer_auth_headers)
    assert resp.status_code == 400
    assert "Invalid date format" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_participants_requires_auth(client: AsyncClient):
    resp = await client.get("/attendance")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_participant_record_has_source_not_push_status(
    client: AsyncClient, sample_participant, volunteer_auth_headers
):
    resp = await client.get("/attendance", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    item = resp.json()[0]
    assert "source" in item
    assert "push_status" not in item


@pytest.mark.asyncio
async def test_push_preview_endpoint_removed(
    client: AsyncClient, admin_auth_headers, sample_event
):
    resp = await client.post(
        f"/attendance/push-preview?event_id={sample_event.id}",
        headers=admin_auth_headers,
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_push_endpoint_removed(
    client: AsyncClient, admin_auth_headers, sample_event
):
    resp = await client.post(
        f"/attendance/push?event_id={sample_event.id}", headers=admin_auth_headers
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_dead_letter_endpoint_removed(
    client: AsyncClient, admin_auth_headers
):
    resp = await client.get("/attendance/dead-letter", headers=admin_auth_headers)
    assert resp.status_code in (404, 405)
