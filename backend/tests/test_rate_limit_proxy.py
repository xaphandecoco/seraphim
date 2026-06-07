"""D5 — Proxy-aware rate-limit regression test.

Verifies that `slowapi.util.get_remote_address` keys on the rewritten
`request.client.host` produced by uvicorn's `ProxyHeadersMiddleware`, NOT on the
transport-level peer address.  This is the mechanism that makes per-client-IP rate
limiting work correctly behind nginx/Cloudflare in production.

Root mechanism (confirmed against source):
  - `get_remote_address` returns `request.client.host`.
  - The XFF→client rewrite is performed by uvicorn's `ProxyHeadersMiddleware`,
    a server-level middleware enabled by `--proxy-headers --forwarded-allow-ips=*`.
  - This middleware is NOT part of the FastAPI app object; tests driven through
    `httpx.ASGITransport(app=app)` therefore bypass it.
  - This test wraps a minimal ASGI app in `ProxyHeadersMiddleware(trusted_hosts="*")`
    — mirroring the production configuration — to exercise the XFF rewrite.

Pin robustness:
  - Production/CI pins `uvicorn[standard]==0.32.1`; local dev may have a newer
    version.  The `trusted_hosts="*"` ("always trust") path returns the leftmost
    single-value XFF entry on both 0.32.1 and ≥0.40.  This test asserts ONLY the
    single-value behavior that is identical on both versions.

Cloudflare nuance (documented here, not tested — see PRODUCTION_RUNBOOK.md §7a):
  - With `*`-trust the leftmost XFF value is client-spoofable (attacker crafts a
    fake `X-Forwarded-For`; nginx appends its real peer but the fake stays
    leftmost).  This is an accepted tradeoff for the LAN/single-tunnel church
    deployment.  See the runbook for the stricter escalation path.

The 429-after-5 path for `/auth/login` is already covered separately in
`test_auth_full.py::test_login_rate_limit_returns_429_after_5_attempts`.
"""

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from slowapi.util import get_remote_address
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# ---------------------------------------------------------------------------
# Minimal ASGI app that echoes the resolved client address back to the caller.
# Using Starlette's Request/PlainTextResponse keeps this dependency-free w.r.t
# the Seraphim app object so there is no DB / settings / JWT setup needed.
# ---------------------------------------------------------------------------

async def _echo_client_app(scope, receive, send):
    """Minimal ASGI app: returns the client address as resolved by get_remote_address."""
    request = Request(scope, receive)
    addr = get_remote_address(request)
    response = PlainTextResponse(addr or "")
    await response(scope, receive, send)


# Wrap in ProxyHeadersMiddleware with trusted_hosts="*" — mirrors production's
# uvicorn --proxy-headers --forwarded-allow-ips=* flag.
_wrapped_app = ProxyHeadersMiddleware(_echo_client_app, trusted_hosts="*")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_different_xff_headers_produce_different_keys():
    """Two requests with distinct single X-Forwarded-For values must resolve to
    different get_remote_address keys (independent rate-limit buckets)."""
    client = TestClient(_wrapped_app, raise_server_exceptions=True)

    resp_a = client.get("/", headers={"X-Forwarded-For": "10.0.0.1"})
    resp_b = client.get("/", headers={"X-Forwarded-For": "10.0.0.2"})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    key_a = resp_a.text
    key_b = resp_b.text

    assert key_a != "", "get_remote_address returned empty string for 10.0.0.1"
    assert key_b != "", "get_remote_address returned empty string for 10.0.0.2"
    assert key_a != key_b, (
        f"Expected distinct rate-limit keys for different XFF values, "
        f"got key_a={key_a!r} key_b={key_b!r}. "
        f"ProxyHeadersMiddleware may not be rewriting request.client.host."
    )


def test_same_xff_header_produces_same_key():
    """Two requests with the same single X-Forwarded-For value must resolve to
    the same get_remote_address key (shared rate-limit bucket)."""
    client = TestClient(_wrapped_app, raise_server_exceptions=True)

    resp_a = client.get("/", headers={"X-Forwarded-For": "10.0.0.5"})
    resp_b = client.get("/", headers={"X-Forwarded-For": "10.0.0.5"})

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    key_a = resp_a.text
    key_b = resp_b.text

    assert key_a != "", "get_remote_address returned empty string"
    assert key_a == key_b, (
        f"Expected the same rate-limit key for identical XFF values, "
        f"got key_a={key_a!r} key_b={key_b!r}."
    )


def test_xff_value_is_leftmost_entry_for_single_value():
    """The resolved address must equal the single XFF value (leftmost = client IP)."""
    client = TestClient(_wrapped_app, raise_server_exceptions=True)

    resp = client.get("/", headers={"X-Forwarded-For": "203.0.113.42"})
    assert resp.status_code == 200
    assert resp.text == "203.0.113.42", (
        f"Expected client IP 203.0.113.42, got {resp.text!r}"
    )
