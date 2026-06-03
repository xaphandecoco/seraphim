"""Story S5 — LAN-HTTP correctness for auth cookies.

AC:
  - Login + refresh succeed over http://<lan-ip>:3000 (cookie NOT Secure on HTTP).
  - Over HTTPS the refresh cookie IS Secure (hardened for the Cloudflare path).

`_cookie_secure(request)` returns request.url.scheme == 'https'. The ASGI test
transport derives the scheme from base_url, so we run the real /auth/login flow
against both an http:// and an https:// client bound to the *same app + same DB
session* (we reuse the db_session fixture and the dependency overrides that the
`client` fixture installs).

We read the raw Set-Cookie header (not the cookie jar) because httpx will drop a
Secure cookie received over http:// from the jar — the raw header is the ground
truth of what the server emitted.
"""
import httpx
import pytest

from app.database import get_db
from app.dependencies import check_setup_complete
from app.main import app


def _refresh_set_cookie(resp: httpx.Response) -> str:
    """Return the raw Set-Cookie header line for refresh_token, or '' if absent."""
    for k, v in resp.headers.multi_items():
        if k.lower() == "set-cookie" and v.startswith("refresh_token="):
            return v
    return ""


async def _client_for(base_url: str, db_session):
    """Build an AsyncClient bound to the app with DB + setup overrides, like the
    shared `client` fixture but with a configurable scheme via base_url."""
    async def override_get_db():
        yield db_session

    async def override_setup_complete():
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[check_setup_complete] = override_setup_complete
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=base_url)


# ---------------------------------------------------------------------------
# Plain-HTTP LAN path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_over_http_succeeds_and_cookie_not_secure(db_session, admin_user):
    ac = await _client_for("http://192.168.1.50:3000", db_session)
    try:
        resp = await ac.post(
            "/auth/login",
            json={"email": "admin@lightnc.org", "password": "adminpass123"},
        )
    finally:
        await ac.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()
    cookie = _refresh_set_cookie(resp)
    assert cookie, "no refresh_token Set-Cookie emitted on HTTP login"
    assert "Secure" not in cookie, (
        "refresh cookie must NOT be Secure over plain HTTP, or the browser drops it "
        f"and login silently fails. Got: {cookie}"
    )
    # It must still be HttpOnly + SameSite=Lax regardless of scheme.
    assert "HttpOnly" in cookie
    assert "lax" in cookie.lower()


@pytest.mark.asyncio
async def test_refresh_over_http_succeeds(db_session, admin_user):
    """The refresh endpoint reads the cookie from the request; over HTTP it works."""
    ac = await _client_for("http://192.168.1.50:3000", db_session)
    try:
        login = await ac.post(
            "/auth/login",
            json={"email": "admin@lightnc.org", "password": "adminpass123"},
        )
        assert login.status_code == 200
        # Manually carry the refresh token (raw header) into a refresh call, since
        # httpx won't persist a cookie set over http if it were Secure (it isn't here).
        raw = _refresh_set_cookie(login)
        token_val = raw.split("refresh_token=", 1)[1].split(";", 1)[0]
        resp = await ac.post("/auth/refresh", headers={"Cookie": f"refresh_token={token_val}"})
    finally:
        await ac.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


# ---------------------------------------------------------------------------
# HTTPS (Cloudflare) path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_over_https_sets_secure_cookie(db_session, admin_user):
    ac = await _client_for("https://seraphim.example.org", db_session)
    try:
        resp = await ac.post(
            "/auth/login",
            json={"email": "admin@lightnc.org", "password": "adminpass123"},
        )
    finally:
        await ac.aclose()
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    cookie = _refresh_set_cookie(resp)
    assert cookie, "no refresh_token Set-Cookie emitted on HTTPS login"
    assert "Secure" in cookie, (
        "refresh cookie MUST be Secure over HTTPS (Cloudflare path). "
        f"Got: {cookie}"
    )
    assert "HttpOnly" in cookie


@pytest.mark.asyncio
async def test_no_hsts_on_http_login_response(db_session, admin_user):
    """Cross-check with B-S5: even an auth response over HTTP carries no HSTS."""
    ac = await _client_for("http://192.168.1.50:3000", db_session)
    try:
        resp = await ac.post(
            "/auth/login",
            json={"email": "admin@lightnc.org", "password": "adminpass123"},
        )
    finally:
        await ac.aclose()
        app.dependency_overrides.clear()

    assert "strict-transport-security" not in {k.lower() for k in resp.headers}
