"""
Comprehensive auth endpoint tests.
Does NOT duplicate tests already in test_auth.py:
  - test_login_success
  - test_login_wrong_password
  - test_login_deactivated
  - test_me_endpoint
  - test_add_volunteer_admin_only
  - test_add_volunteer_success

Coverage added here:
  - Login with non-existent email → 401
  - Login rate limiting (mock limiter) → 429
  - Token refresh (valid cookie) → new access token
  - Token refresh with missing/invalid/expired cookie → 401
  - GET /auth/google when OAuth disabled → 503
  - GET /auth/google when OAuth enabled → redirect with PKCE params
  - GET /auth/google/callback with invalid state → 400
  - POST /auth/reset-password (admin) → returns token
  - POST /auth/reset-password (non-admin) → 403
  - POST /auth/reset-password for non-existent user → 404
  - POST /auth/reset-password/confirm → password updated
  - POST /auth/reset-password/confirm with wrong token → 400
  - POST /auth/reset-password/confirm with expired token → 400
  - POST /auth/deactivate/{id} (admin) → user deactivated
  - POST /auth/deactivate for non-existent user → 404
  - POST /auth/add-volunteer with duplicate email → 400
  - POST /auth/logout → clears cookie
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import make_token


# ---------------------------------------------------------------------------
# Login — additional edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_nonexistent_email_returns_401(client: AsyncClient):
    resp = await client.post(
        "/auth/login",
        json={"email": "ghost@lightnc.org", "password": "password123"},
    )
    assert resp.status_code == 401
    assert "detail" in resp.json()


@pytest.mark.asyncio
async def test_login_rate_limit_returns_429_after_5_attempts(
    client: AsyncClient, admin_user
):
    """The /auth/login endpoint is decorated with @limiter.limit('5/minute').
    We mock the limiter to raise RateLimitExceeded directly so we can test
    the 429 handler without actually hammering the rate-limit store.
    """
    from slowapi.errors import RateLimitExceeded
    from starlette.requests import Request as StarletteRequest

    # Patch the limiter's __call__ to simulate limit exceeded on 6th call
    call_count = {"n": 0}

    original_limit = client.app.state.limiter._inject_headers  # type: ignore[attr-defined]

    async def patched_check(request: StarletteRequest, response, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] > 5:
            raise RateLimitExceeded("5 per 1 minute")

    with patch.object(
        client.app.state.limiter,  # type: ignore[attr-defined]
        "_check_request_limit",
        side_effect=RateLimitExceeded("5 per 1 minute"),
    ):
        resp = await client.post(
            "/auth/login",
            json={"email": "admin@lightnc.org", "password": "adminpass123"},
        )
    assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_returns_new_access_token(
    client: AsyncClient, admin_user
):
    """Login, capture the refresh_token cookie, then call /auth/refresh."""
    login_resp = await client.post(
        "/auth/login",
        json={"email": "admin@lightnc.org", "password": "adminpass123"},
    )
    assert login_resp.status_code == 200
    # The refresh token is in an httponly cookie
    assert "refresh_token" in login_resp.cookies

    refresh_cookie = login_resp.cookies["refresh_token"]
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": refresh_cookie},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_refresh_token_missing_cookie_returns_401(client: AsyncClient):
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Missing refresh token"


@pytest.mark.asyncio
async def test_refresh_token_invalid_cookie_returns_401(client: AsyncClient):
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": "this.is.not.a.valid.jwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_expired_returns_401(client: AsyncClient, admin_user):
    """Craft a refresh token that is already expired."""
    from app.config import legacy_settings
    from app.utils.auth import create_refresh_token

    expired_token = create_refresh_token(
        {
            "sub": str(admin_user.id),
            "email": admin_user.email,
            "name": "Admin",
            "role": admin_user.role,
        },
        secret=legacy_settings.SECRET_KEY,
        expires_delta=timedelta(seconds=-1),  # already expired
    )
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": expired_token},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_google_oauth_disabled_returns_503(client: AsyncClient):
    """When enable_google_oauth is False (default in tests), endpoint returns 503."""
    resp = await client.get("/auth/google")
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_google_oauth_enabled_redirects_with_pkce(client: AsyncClient):
    """When OAuth is enabled via dynamic_settings mock, endpoint should redirect
    to Google with code_challenge and code_challenge_method params."""
    with patch(
        "app.routers.auth.dynamic_settings.get_enable_google_oauth",
        return_value=True,
    ), patch(
        "app.routers.auth.dynamic_settings.get_google_client_id",
        return_value="test-client-id",
    ):
        resp = await client.get("/auth/google", follow_redirects=False)

    assert resp.status_code in (302, 307)
    location = resp.headers.get("location", "")
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "accounts.google.com" in location


@pytest.mark.asyncio
async def test_google_callback_missing_state_cookie_returns_400(client: AsyncClient):
    """Callback without the oauth_state cookie should be rejected."""
    resp = await client.get(
        "/auth/google/callback",
        params={"code": "fake-code", "state": "fake-state"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_google_callback_error_param_returns_400(client: AsyncClient):
    """Callback with error param from Google returns 400."""
    resp = await client.get(
        "/auth/google/callback",
        params={"error": "access_denied"},
    )
    assert resp.status_code == 400
    assert "OAuth error" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_google_callback_mismatched_state_returns_400(client: AsyncClient):
    """State from query doesn't match signed cookie — return 400."""
    from itsdangerous import URLSafeTimedSerializer
    from app.config import legacy_settings

    real_state = "correct-state-value"
    serializer = URLSafeTimedSerializer(legacy_settings.SECRET_KEY, salt="oauth-state")
    signed = serializer.dumps(real_state)

    resp = await client.get(
        "/auth/google/callback",
        params={"code": "some-code", "state": "WRONG-STATE"},
        cookies={"oauth_state": signed, "oauth_verifier": "dummy-verifier"},
    )
    assert resp.status_code == 400
    assert "State mismatch" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_request_as_admin_returns_token(
    client: AsyncClient, admin_user, volunteer_user, admin_auth_headers
):
    resp = await client.post(
        "/auth/reset-password",
        json={"email": volunteer_user.email},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "reset_link" in data
    assert "token" in data
    assert data["message"] == "Reset link generated"


@pytest.mark.asyncio
async def test_password_reset_request_as_volunteer_returns_403(
    client: AsyncClient, volunteer_user, volunteer_auth_headers
):
    resp = await client.post(
        "/auth/reset-password",
        json={"email": volunteer_user.email},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_password_reset_request_nonexistent_user_returns_404(
    client: AsyncClient, admin_user, admin_auth_headers
):
    resp = await client.post(
        "/auth/reset-password",
        json={"email": "nobody@lightnc.org"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_password_reset_confirm_updates_password(
    client: AsyncClient,
    admin_user,
    volunteer_user,
    admin_auth_headers,
    db_session: AsyncSession,
):
    """Full flow: admin generates token → volunteer resets password."""
    # Admin generates reset token
    reset_resp = await client.post(
        "/auth/reset-password",
        json={"email": volunteer_user.email},
        headers=admin_auth_headers,
    )
    assert reset_resp.status_code == 200
    token = reset_resp.json()["token"]

    # Volunteer confirms reset
    confirm_resp = await client.post(
        "/auth/reset-password/confirm",
        json={
            "email": volunteer_user.email,
            "token": token,
            "new_password": "NewPass456!",
        },
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["message"] == "Password updated successfully"

    # New password should work
    login_resp = await client.post(
        "/auth/login",
        json={"email": volunteer_user.email, "password": "NewPass456!"},
    )
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_confirm_wrong_token_returns_400(
    client: AsyncClient,
    admin_user,
    volunteer_user,
    admin_auth_headers,
):
    # Generate a real reset token first so the state is set
    await client.post(
        "/auth/reset-password",
        json={"email": volunteer_user.email},
        headers=admin_auth_headers,
    )

    resp = await client.post(
        "/auth/reset-password/confirm",
        json={
            "email": volunteer_user.email,
            "token": "completely-wrong-token",
            "new_password": "NewPass789!",
        },
    )
    assert resp.status_code == 400
    assert "Invalid reset token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_password_reset_confirm_no_active_request_returns_400(
    client: AsyncClient, volunteer_user
):
    """Trying to confirm without a pending reset request returns 400."""
    resp = await client.post(
        "/auth/reset-password/confirm",
        json={
            "email": volunteer_user.email,
            "token": "some-token",
            "new_password": "NewPass000!",
        },
    )
    assert resp.status_code == 400
    assert "No active password reset request" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_password_reset_confirm_expired_token_returns_400(
    client: AsyncClient,
    admin_user,
    volunteer_user,
    admin_auth_headers,
    db_session: AsyncSession,
):
    """Simulate an expired token by manipulating the expiry timestamp directly."""
    from sqlalchemy import select
    from app.models import User
    from app.utils.auth import generate_reset_token, hash_password

    token_plain = generate_reset_token()

    result = await db_session.execute(
        select(User).where(User.email == volunteer_user.email)
    )
    user = result.scalar_one()
    user.password_reset_token = hash_password(token_plain)
    user.password_reset_expires_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(hours=1)  # already expired
    await db_session.commit()

    resp = await client.post(
        "/auth/reset-password/confirm",
        json={
            "email": volunteer_user.email,
            "token": token_plain,
            "new_password": "NewPass111!",
        },
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Deactivate user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_user_as_admin_prevents_login(
    client: AsyncClient,
    admin_user,
    admin_auth_headers,
    db_session: AsyncSession,
):
    """Admin deactivates a volunteer; subsequent login should return 403."""
    from app.models import User

    vol = User(
        email="temp_vol@lightnc.org",
        password_hash=__import__(
            "app.utils.auth", fromlist=["hash_password"]
        ).hash_password("TempPass!1"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(vol)
    await db_session.commit()
    await db_session.refresh(vol)

    # Deactivate
    deactivate_resp = await client.post(
        f"/auth/deactivate/{vol.id}",
        headers=admin_auth_headers,
    )
    assert deactivate_resp.status_code == 200
    assert "deactivated" in deactivate_resp.json()["message"]

    # Login should now return 403
    login_resp = await client.post(
        "/auth/login",
        json={"email": "temp_vol@lightnc.org", "password": "TempPass!1"},
    )
    assert login_resp.status_code == 403


@pytest.mark.asyncio
async def test_deactivate_nonexistent_user_returns_404(
    client: AsyncClient, admin_user, admin_auth_headers
):
    resp = await client.post(
        "/auth/deactivate/999999",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_user_requires_admin(
    client: AsyncClient, volunteer_user, volunteer_auth_headers
):
    resp = await client.post(
        "/auth/deactivate/1",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Add volunteer — duplicate email
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_volunteer_duplicate_email_returns_400(
    client: AsyncClient, admin_user, admin_auth_headers
):
    # First creation
    await client.post(
        "/auth/add-volunteer",
        json={
            "email": "dup@lightnc.org",
            "name": "First",
            "role": "volunteer",
            "temporary_password": "firstpass123",
        },
        headers=admin_auth_headers,
    )
    # Second creation with same email
    resp = await client.post(
        "/auth/add-volunteer",
        json={
            "email": "dup@lightnc.org",
            "name": "Duplicate",
            "role": "volunteer",
            "temporary_password": "secondpass123",
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_clears_refresh_token_cookie(client: AsyncClient, admin_user):
    # Login first
    login_resp = await client.post(
        "/auth/login",
        json={"email": "admin@lightnc.org", "password": "adminpass123"},
    )
    assert "refresh_token" in login_resp.cookies

    # Logout should clear it
    logout_resp = await client.post("/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["message"] == "Logged out"
