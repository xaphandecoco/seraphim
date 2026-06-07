"""Tests for S1 token-type lockdown — the Critical leg.

Covers:
  - Refresh token via Bearer on a require_volunteer / require_admin route -> 401
  - Access token via Bearer on those routes -> 200/403 (not 401)
  - Refresh token via Bearer on /tasks/feed -> 401
  - Refresh token via ?_t= on /tasks/feed -> 401
  - Refresh token via cookie on /tasks/feed -> 200 (permissive path)

The Critical finding is that get_current_user() (dependencies.py) gates every
route using Depends(require_volunteer) or Depends(require_admin), and previously
accepted refresh tokens as valid Bearer credentials.
"""
from datetime import timedelta

import pytest
from httpx import AsyncClient

from conftest import TEST_JWT_SECRET, make_token


def make_refresh_token(user_id: int, email: str, role: str) -> str:
    from app.utils.auth import create_refresh_token
    return create_refresh_token(
        {"sub": str(user_id), "email": email, "role": role, "name": "Test"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(days=7),
    )


# ---------------------------------------------------------------------------
# require_volunteer / require_admin routes (via get_current_user)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_via_bearer_on_protected_route_returns_401(
    client: AsyncClient, admin_user
):
    """Refresh token in Bearer header must be rejected by get_current_user() (S1 Critical)."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    # /tasks is a require_volunteer route; 401 = type=refresh blocked
    resp = await client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_access_token_via_bearer_on_protected_route_passes(
    client: AsyncClient, admin_user
):
    """Access token in Bearer header should pass auth on a require_volunteer route."""
    tok = make_token(admin_user.id, admin_user.email, admin_user.role, "Admin")
    resp = await client.get(
        "/tasks",
        headers={"Authorization": f"Bearer {tok}"},
    )
    # 200 or 422 (bad query params) — the key thing is it's not 401
    assert resp.status_code != 401


# ---------------------------------------------------------------------------
# /tasks/feed (SSE endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_refresh_token_via_bearer_returns_401(client: AsyncClient, admin_user):
    """Refresh token in Bearer header must be rejected on /tasks/feed."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(
        "/tasks/feed",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_feed_refresh_token_via_query_param_returns_401(client: AsyncClient, admin_user):
    """Refresh token via ?_t= must be rejected on /tasks/feed."""
    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)
    resp = await client.get(f"/tasks/feed?_t={tok}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_feed_refresh_token_via_cookie_returns_streaming(client: AsyncClient, admin_user):
    """Refresh token via HttpOnly cookie must be accepted on /tasks/feed (permissive path).

    The response will be 200 with text/event-stream content type. We don't try to
    consume the streaming body here — just verify the auth gate passes.
    """
    from unittest.mock import AsyncMock, MagicMock, patch
    import asyncio

    tok = make_refresh_token(admin_user.id, admin_user.email, admin_user.role)

    async def _silent():
        await asyncio.sleep(5)
        return
        yield  # noqa: make async-gen

    with patch("app.main.broadcaster") as mock_b:
        mock_b.subscribe.return_value = _silent()
        resp = await client.get(
            "/tasks/feed",
            cookies={"refresh_token": tok},
        )
    # Cookie refresh path is permissive — 200 means auth passed
    assert resp.status_code == 200
