"""D5 — Proxy-aware rate-limit keying: regression + negative-control coverage.

This complements `test_rate_limit_proxy.py` (which asserts the leaf behavior that
`get_remote_address` returns the ProxyHeadersMiddleware-rewritten client host).
Here we close the gaps that the sprint plan's Risk R1 ("the test could pass for
the wrong reason") and the runbook §7a security note call out:

  1. Negative control — WITHOUT ProxyHeadersMiddleware, X-Forwarded-For is
     ignored and the resolved key is the transport peer (proves the middleware
     is what performs the rewrite; the test cannot pass for the wrong reason).
  2. The production limiter's key_func IS slowapi.util.get_remote_address — the
     exact function whose proxy behavior we are pinning (ties the test to the
     real wiring in app.rate_limit).
  3. Documented spoofability / leftmost-client semantics under trusted_hosts="*"
     (the accepted-tradeoff behavior the runbook records): a forged leftmost XFF
     value becomes the rate-limit bucket. Locking this in prevents a silent
     change in trust behavior on an upstream pin bump.

All assertions use only single-value-leftmost behavior that uvicorn 0.32.1
(pinned/CI) and the local newer version agree on.
"""
import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from slowapi.util import get_remote_address
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


# ---------------------------------------------------------------------------
# Minimal ASGI app that echoes the resolved rate-limit key (get_remote_address).
# ---------------------------------------------------------------------------

async def _echo_key_app(scope, receive, send):
    request = Request(scope, receive)
    response = PlainTextResponse(get_remote_address(request) or "")
    await response(scope, receive, send)


# Production wiring: trusted_hosts="*" mirrors uvicorn --forwarded-allow-ips=*.
_wrapped = ProxyHeadersMiddleware(_echo_key_app, trusted_hosts="*")


# ---------------------------------------------------------------------------
# 1. Negative control — without the middleware, XFF must be ignored.
# ---------------------------------------------------------------------------

def test_without_proxy_middleware_xff_is_ignored():
    """The bare ASGI app (no ProxyHeadersMiddleware) must NOT honor XFF.

    Two requests with *different* XFF values resolve to the SAME key (the
    transport peer), proving the XFF→client rewrite comes from the middleware,
    not from get_remote_address or Starlette itself. This is the guard against
    the test 'passing for the wrong reason' (Risk R1).
    """
    client = TestClient(_echo_key_app)  # no ProxyHeadersMiddleware
    a = client.get("/", headers={"X-Forwarded-For": "10.1.1.1"}).text
    b = client.get("/", headers={"X-Forwarded-For": "10.2.2.2"}).text
    assert a == b, (
        "Bare app unexpectedly varied the key by XFF — get_remote_address must "
        f"not read XFF on its own (got a={a!r} b={b!r})."
    )


def test_with_proxy_middleware_xff_is_honored():
    """Positive counterpart: with the middleware, the same two distinct XFF
    values now resolve to DIFFERENT keys (independent rate-limit buckets)."""
    client = TestClient(_wrapped)
    a = client.get("/", headers={"X-Forwarded-For": "10.1.1.1"}).text
    b = client.get("/", headers={"X-Forwarded-For": "10.2.2.2"}).text
    assert a == "10.1.1.1"
    assert b == "10.2.2.2"
    assert a != b


# ---------------------------------------------------------------------------
# 2. The production limiter keys on exactly this function.
# ---------------------------------------------------------------------------

def test_production_limiter_uses_get_remote_address_keyfunc():
    """app.rate_limit.limiter.key_func is slowapi.util.get_remote_address — the
    function whose proxy behavior the D5 tests pin. If a refactor swaps the
    key_func, this fails and the proxy-keying guarantees must be re-verified."""
    from app.rate_limit import limiter

    assert limiter._key_func is get_remote_address


# ---------------------------------------------------------------------------
# 3. Spoofability / leftmost-client semantics (documented accepted tradeoff).
# ---------------------------------------------------------------------------

def test_leftmost_xff_value_becomes_the_bucket_when_spoofed():
    """Under trusted_hosts='*' the leftmost XFF entry is taken as the client IP.

    The runbook §7a records that this makes the leftmost value client-spoofable
    on this single-tunnel/LAN deployment (an attacker who sets X-Forwarded-For
    controls their own bucket). This test pins that behavior so a pin bump that
    silently changes trust handling is caught.

    A request whose leftmost XFF is an arbitrary attacker-chosen value resolves
    to that exact value as the rate-limit key.
    """
    client = TestClient(_wrapped)
    forged = "198.51.100.77"
    key = client.get("/", headers={"X-Forwarded-For": forged}).text
    assert key == forged


def test_two_clients_sharing_an_xff_value_share_a_bucket():
    """Operational consequence of keying on the resolved client IP: two callers
    that present the same X-Forwarded-For share one rate-limit bucket (e.g. NAT),
    while distinct values stay independent — the property login throttling needs.
    """
    client = TestClient(_wrapped)
    same_a = client.get("/", headers={"X-Forwarded-For": "203.0.113.9"}).text
    same_b = client.get("/", headers={"X-Forwarded-For": "203.0.113.9"}).text
    distinct = client.get("/", headers={"X-Forwarded-For": "203.0.113.10"}).text
    assert same_a == same_b
    assert same_a != distinct
