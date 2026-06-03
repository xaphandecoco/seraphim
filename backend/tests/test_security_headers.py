"""Tests for SecurityHeadersMiddleware HSTS gating (Story S5 / B-S5).

AC: 'HSTS not asserted on plain-HTTP responses'. The middleware emits
Strict-Transport-Security only when request.url.scheme == 'https'. Over the
ASGI test transport the scheme is taken from base_url, so we exercise both an
http:// and an https:// base_url against the same app.

Also verifies the always-on headers (CSP default-src 'self', X-Frame-Options,
etc.) are present on both origins — these protect both access paths and must not
regress.
"""
import httpx
import pytest

from app.main import app


async def _get(base_url: str, path: str = "/health"):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as ac:
        return await ac.get(path)


# ---------------------------------------------------------------------------
# HSTS gating (the core B-S5 change)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_hsts_header_on_plain_http():
    resp = await _get("http://lan-host:3000")
    assert "strict-transport-security" not in {k.lower() for k in resp.headers}


@pytest.mark.asyncio
async def test_hsts_header_present_on_https():
    resp = await _get("https://seraphim.example.org")
    hsts = resp.headers.get("strict-transport-security")
    assert hsts is not None
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


# ---------------------------------------------------------------------------
# Always-on headers — must be identical for both access paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://lan-host:3000", "https://seraphim.example.org"])
async def test_baseline_security_headers_present(base_url):
    resp = await _get(base_url)
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
@pytest.mark.parametrize("base_url", ["http://lan-host:3000", "https://seraphim.example.org"])
async def test_csp_is_same_origin_default_src(base_url):
    """CSP default-src 'self' already covers SPA → same-origin /api + SSE on both
    origins (Security Considerations: no connect-src widening needed)."""
    resp = await _get(base_url)
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.asyncio
async def test_csp_does_not_contain_cloudflare_domain():
    """Guard: do not widen CSP to a Cloudflare domain — no cross-origin calls exist."""
    resp = await _get("https://seraphim.example.org")
    csp = resp.headers.get("content-security-policy", "")
    assert "cloudflare" not in csp.lower()
