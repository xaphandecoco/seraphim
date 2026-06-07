"""Unit tests for app.utils.token_denylist — L5 (QA gap-closing).

The existing suite only patches ``is_jti_denied`` at the router import sites
(test_storage_auth.py, test_auth_full.py).  The denylist module's own
behavior — the lazy singleton, the mandatory fail-open under memory://, the
deny/check round-trip, and aclose_redis() — was never exercised directly.

These tests pin the L5 acceptance criteria:
  - fail-open under memory://: _get_client() -> None, is_jti_denied -> False,
    deny_jti is a silent no-op (Security Considerations: fail-open is MANDATORY).
  - lazy singleton: redis.from_url is called at most ONCE across many calls;
    the same client instance is reused (no per-call connection).
  - deny_jti -> is_jti_denied round-trip works against a fake client.
  - deny_jti with a non-positive TTL is a no-op (never writes).
  - errors inside set()/exists() are swallowed (fail-open) and never raise.
  - aclose_redis() closes the client and resets _client to None; safe when
    _client was never created.

We never add a real Redis (or fakeredis) dependency — a tiny in-test fake is
injected by monkeypatching redis.asyncio.from_url, matching the project's
"no new deps" constraint.
"""
import asyncio

import pytest

from app.utils import token_denylist as td


# ---------------------------------------------------------------------------
# A minimal in-test Redis double (only the methods the module uses)
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.closed = False
        self.set_calls = 0

    async def set(self, key, value, ex=None):
        self.set_calls += 1
        self.store[key] = value

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def aclose(self):
        self.closed = True


@pytest.fixture
def reset_denylist_singleton():
    """Ensure each test starts and ends with a clean module singleton."""
    td._client = None
    yield
    td._client = None


@pytest.fixture
def fake_redis(monkeypatch, reset_denylist_singleton):
    """Patch redis.asyncio.from_url to return a single shared _FakeRedis and
    count how many times it is constructed (singleton proof)."""
    import redis.asyncio as aioredis

    created: list[_FakeRedis] = []

    def _from_url(url, **kwargs):
        client = _FakeRedis()
        created.append(client)
        return client

    monkeypatch.setattr(aioredis, "from_url", _from_url)
    return created


# ---------------------------------------------------------------------------
# Fail-open under memory:// (the test/CI default — MANDATORY behavior)
# ---------------------------------------------------------------------------


async def test_get_client_returns_none_under_memory_url(reset_denylist_singleton):
    """REDIS_URL=memory:// => from_url raises ValueError => fail-open None."""
    # legacy_settings.REDIS_URL is 'memory://' in the test process (conftest).
    client = await td._get_client()
    assert client is None
    assert td._client is None


async def test_is_jti_denied_fails_open_to_false(reset_denylist_singleton):
    assert await td.is_jti_denied("any-jti") is False


async def test_deny_jti_is_noop_when_redis_unavailable(reset_denylist_singleton):
    # Must not raise even though there is no Redis.
    await td.deny_jti("some-jti", 9_999_999_999)
    # Still fail-open on the read side.
    assert await td.is_jti_denied("some-jti") is False


# ---------------------------------------------------------------------------
# Lazy singleton — from_url called at most once, instance reused
# ---------------------------------------------------------------------------


async def test_client_is_lazy_singleton(fake_redis):
    c1 = await td._get_client()
    c2 = await td._get_client()
    c3 = await td._get_client()
    assert c1 is c2 is c3, "denylist must reuse one client (no per-call connection)"
    assert len(fake_redis) == 1, f"from_url called {len(fake_redis)}x; expected exactly 1"


async def test_concurrent_calls_do_not_explode_connections(fake_redis):
    """Even under concurrent first-use, the singleton must not open a flood of
    connections (best-effort: well under one-per-call)."""
    await asyncio.gather(*[td._get_client() for _ in range(20)])
    # The module has no lock, so a tiny race is theoretically possible, but the
    # cached assignment means it must be far fewer than 20 — assert <= 2 to catch
    # a regression that creates a client on every call.
    assert len(fake_redis) <= 2, f"too many clients created: {len(fake_redis)}"


# ---------------------------------------------------------------------------
# deny_jti -> is_jti_denied round-trip against the fake client
# ---------------------------------------------------------------------------


async def test_deny_then_denied_roundtrip(fake_redis):
    await td.deny_jti("jti-abc", 9_999_999_999)
    assert await td.is_jti_denied("jti-abc") is True
    assert await td.is_jti_denied("jti-not-denied") is False


async def test_deny_jti_writes_with_positive_ttl(fake_redis):
    await td.deny_jti("jti-ttl", 9_999_999_999)
    client = await td._get_client()
    assert client.set_calls == 1
    assert td._redis_key("jti-ttl") in client.store


async def test_deny_jti_skips_write_when_ttl_non_positive(fake_redis):
    """exp in the past => ttl <= 0 => no write (and no client even needs to exist)."""
    await td.deny_jti("expired-jti", 1)  # epoch 1 => long expired
    # _get_client may still be lazily callable; assert nothing was stored.
    client = await td._get_client()
    assert client.set_calls == 0
    assert td._redis_key("expired-jti") not in client.store


# ---------------------------------------------------------------------------
# Errors inside the client are swallowed (fail-open), never raised
# ---------------------------------------------------------------------------


async def test_is_jti_denied_swallows_client_errors(monkeypatch, reset_denylist_singleton):
    class _BoomRedis(_FakeRedis):
        async def exists(self, key):
            raise RuntimeError("connection reset")

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda url, **kw: _BoomRedis())

    # Must fail open, not propagate.
    assert await td.is_jti_denied("x") is False


async def test_deny_jti_swallows_client_errors(monkeypatch, reset_denylist_singleton):
    class _BoomRedis(_FakeRedis):
        async def set(self, key, value, ex=None):
            raise RuntimeError("connection reset")

    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda url, **kw: _BoomRedis())

    # Must not raise.
    await td.deny_jti("x", 9_999_999_999)


# ---------------------------------------------------------------------------
# aclose_redis() lifecycle helper
# ---------------------------------------------------------------------------


async def test_aclose_redis_closes_and_resets_singleton(fake_redis):
    client = await td._get_client()
    assert client is not None
    await td.aclose_redis()
    assert client.closed is True
    assert td._client is None, "aclose_redis must reset the singleton so it re-creates next time"


async def test_aclose_redis_safe_when_never_initialised(reset_denylist_singleton):
    assert td._client is None
    # Should be a harmless no-op, not raise.
    await td.aclose_redis()
    assert td._client is None


async def test_client_recreated_after_aclose(fake_redis):
    c1 = await td._get_client()
    await td.aclose_redis()
    c2 = await td._get_client()
    assert c2 is not None
    assert c2 is not c1, "after aclose the next call must build a fresh client"
    assert len(fake_redis) == 2
