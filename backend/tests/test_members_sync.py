"""Contract + auth tests for POST /members/sync (API contract endpoint #1).

This endpoint is what the I4 frontend "Sync now" button consumes.  The sprint
plan's API contract pins the full status matrix for it; these tests assert that
matrix end-to-end through the HTTP layer:

  - 200 + {"message": "Synced N members", "synced_count": N}  (admin, success)
  - 401  no credentials
  - 401  refresh token presented as a Bearer credential (S1 token-type lockdown)
  - 403  "Admin access required"  (authenticated volunteer, not admin)
  - 503  "CiviCRM not configured: ..."  (CiviCRM unconfigured — the test default)

The success path mocks CiviCRMClient so no real CiviCRM endpoint is contacted;
all other paths exercise the genuine dependency/error wiring.
"""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from conftest import TEST_JWT_SECRET, make_token


def _make_refresh_token(user_id: int, email: str, role: str) -> str:
    """Mint a valid refresh token (type='refresh') to prove Bearer-path rejection."""
    from app.utils.auth import create_refresh_token

    return create_refresh_token(
        {"sub": str(user_id), "email": email, "role": role, "name": "Test"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(days=7),
    )


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_no_token_returns_401(client: AsyncClient):
    """No credentials → 401 (get_current_user rejects missing Bearer)."""
    resp = await client.post("/members/sync")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_refresh_token_as_bearer_returns_401(client: AsyncClient, admin_user):
    """A refresh token replayed as a Bearer credential must be rejected (S1 lockdown).

    Even though the user is an admin, a type='refresh' token on the Bearer path
    is invalid for the active API surface, so this is 401 (not 403/200).
    """
    tok = _make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.post(
        "/members/sync", headers={"Authorization": f"Bearer {tok}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_volunteer_returns_403(client: AsyncClient, volunteer_auth_headers):
    """An authenticated non-admin (volunteer) is forbidden (require_admin)."""
    resp = await client.post("/members/sync", headers=volunteer_auth_headers)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required"


# ---------------------------------------------------------------------------
# Business outcomes (admin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_unconfigured_civicrm_returns_503(client: AsyncClient, admin_auth_headers):
    """With CiviCRM unconfigured (test default), sync surfaces a 503 with the
    'CiviCRM not configured: ...' detail (RuntimeError → 503 in the router)."""
    resp = await client.post("/members/sync", headers=admin_auth_headers)
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("CiviCRM not configured")


@pytest.mark.asyncio
async def test_sync_admin_success_returns_envelope_and_persists(
    client: AsyncClient, admin_auth_headers, db_session
):
    """Admin sync with a mocked CiviCRM client returns the contract envelope
    {"message": "Synced N members", "synced_count": N} and writes the members."""
    fake_members = [
        {"id": "5001", "first_name": "Juan", "last_name": "dela Cruz",
         "nick_name": "JC", "email": "juan@lightnc.org"},
        {"id": "5002", "first_name": "Maria", "last_name": "Santos",
         "nick_name": None, "email": "maria@lightnc.org"},
    ]

    mock_client = MagicMock()
    mock_client.sync_members = AsyncMock(return_value=fake_members)
    mock_client.close = AsyncMock()

    with patch("app.routers.members.CiviCRMClient", return_value=mock_client):
        resp = await client.post("/members/sync", headers=admin_auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"message": "Synced 2 members", "synced_count": 2}

    # The members were actually persisted.
    from app.models import CiviCRMMember

    persisted = await db_session.get(CiviCRMMember, 5001)
    assert persisted is not None
    assert persisted.first_name == "Juan"
    assert persisted.nickname == "JC"


@pytest.mark.asyncio
async def test_sync_admin_success_skips_rows_without_contact_id(
    client: AsyncClient, admin_auth_headers
):
    """Rows with a missing/zero id are skipped; synced_count counts only valid rows
    (regression guard for the `if not contact_id: continue` branch)."""
    fake_members = [
        {"id": "6001", "first_name": "Valid", "last_name": "One", "email": "v1@lightnc.org"},
        {"id": "0", "first_name": "Skipme", "last_name": "Zero"},   # contact_id 0 → skip
        {"first_name": "NoId", "last_name": "AtAll"},               # missing id → skip
    ]
    mock_client = MagicMock()
    mock_client.sync_members = AsyncMock(return_value=fake_members)
    mock_client.close = AsyncMock()

    with patch("app.routers.members.CiviCRMClient", return_value=mock_client):
        resp = await client.post("/members/sync", headers=admin_auth_headers)

    assert resp.status_code == 200
    assert resp.json()["synced_count"] == 1


@pytest.mark.asyncio
async def test_sync_civicrm_runtime_error_maps_to_503(
    client: AsyncClient, admin_auth_headers
):
    """A RuntimeError from the client (e.g. misconfiguration) maps to 503, not 500."""
    mock_client = MagicMock()
    mock_client.sync_members = AsyncMock(side_effect=RuntimeError("URL not configured"))
    mock_client.close = AsyncMock()

    with patch("app.routers.members.CiviCRMClient", return_value=mock_client):
        resp = await client.post("/members/sync", headers=admin_auth_headers)

    assert resp.status_code == 503
    assert "CiviCRM not configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_civicrm_unexpected_error_maps_to_502(
    client: AsyncClient, admin_auth_headers
):
    """A non-RuntimeError failure (e.g. an upstream HTTP error) maps to 502 with a
    generic message — the server does not leak internal detail to the client."""
    mock_client = MagicMock()
    mock_client.sync_members = AsyncMock(side_effect=ValueError("boom"))
    mock_client.close = AsyncMock()

    with patch("app.routers.members.CiviCRMClient", return_value=mock_client):
        resp = await client.post("/members/sync", headers=admin_auth_headers)

    assert resp.status_code == 502
    assert resp.json()["detail"] == "CiviCRM sync failed. Check server logs."
