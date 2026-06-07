"""Tests for S1 token-type lockdown on the /storage/* route (NEW file).

AC:
  - Refresh token via Bearer header -> 401 (type="refresh" blocked)
  - Refresh token via ?_t= query param -> 401 (type="refresh" blocked)
  - Refresh token via HttpOnly cookie -> serves the file (or 404 if missing; not 401)
  - Access token via Bearer -> passes auth (serves or 404 for missing file)
  - Access token via ?_t= -> passes auth (serves or 404 for missing file)
  - No token -> 401

These tests exercise the _authenticate() function inside storage.py without
needing actual files on disk — auth rejection happens before the file lookup.
"""
from datetime import timedelta

import pytest
from httpx import AsyncClient

from conftest import TEST_JWT_SECRET, make_token


def make_refresh_token(user_id: int, email: str, role: str) -> str:
    """Mint a valid refresh token (type='refresh') for testing type-rejection."""
    from app.utils.auth import create_refresh_token
    return create_refresh_token(
        {"sub": str(user_id), "email": email, "role": role, "name": "Test"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(days=7),
    )


# ---------------------------------------------------------------------------
# Bearer header paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_refresh_token_via_bearer_returns_401(client: AsyncClient, admin_user):
    """A refresh token in the Authorization header must be rejected (S1/Design B)."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(
        "/storage/some/image.jpg",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_storage_access_token_via_bearer_passes_auth(client: AsyncClient, admin_user):
    """A valid access token in Bearer should pass auth (404 = file not found, not 401)."""
    tok = make_token(admin_user.id, admin_user.email, admin_user.role, "Admin")
    resp = await client.get(
        "/storage/nonexistent_file.jpg",
        headers={"Authorization": f"Bearer {tok}"},
    )
    # Auth passed — the storage root doesn't have this file, so 404 (not 401)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# ?_t= query-param paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_refresh_token_via_query_param_returns_401(client: AsyncClient, admin_user):
    """A refresh token via ?_t= must be rejected (S1/Design B)."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(f"/storage/some/image.jpg?_t={tok}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_storage_access_token_via_query_param_passes_auth(client: AsyncClient, admin_user):
    """A valid access token via ?_t= should pass auth (404 = file not found, not 401)."""
    tok = make_token(admin_user.id, admin_user.email, admin_user.role, "Admin")
    resp = await client.get(f"/storage/nonexistent_file.jpg?_t={tok}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cookie path (intentionally permissive — browser <img> tags)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_refresh_token_via_cookie_passes_auth(client: AsyncClient, admin_user):
    """Refresh token via HttpOnly cookie must be accepted (browser <img> src path)."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(
        "/storage/nonexistent_file.jpg",
        cookies={"refresh_token": tok},
    )
    # Auth passed — 404 (no such file) not 401
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# No token at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_no_token_returns_401(client: AsyncClient):
    resp = await client.get("/storage/some/image.jpg")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cookie path revocation parity — a deny-listed refresh JTI must not read files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_storage_denylisted_refresh_cookie_returns_401(
    client: AsyncClient, admin_user, monkeypatch
):
    """A deny-listed (logged-out/rotated) refresh cookie must NOT read files.

    Brings /storage/* to revocation parity with /auth/refresh. Under memory:// the
    real denylist is fail-open, so we patch is_jti_denied to assert the wiring.
    """
    async def _denied(_jti):
        return True

    monkeypatch.setattr("app.routers.storage.is_jti_denied", _denied)
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(
        "/storage/nonexistent_file.jpg",
        cookies={"refresh_token": tok},
    )
    assert resp.status_code == 401
