"""Integration tests for the real /tasks/feed SSE endpoint (Story S2).

The existing test_sse_heartbeat.py hand-rebuilds the generator to unit-test the
keepalive loop. This file drives the *actual* `task_feed` endpoint and the real
`event_generator` it constructs, covering:

  - SSE auth contract: 401 without a valid token (Bearer / query param / cookie);
    200 + text/event-stream with a valid token. (regression for SSE auth.)
  - Cloudflare-survival response headers: Cache-Control: no-cache and
    X-Accel-Buffering: no MUST be present (Story S2 / B-S2).
  - The real event_generator emits the keepalive frame on idle and re-raises
    CancelledError on disconnect (leak-free teardown) — exercised by patching the
    module-level broadcaster and driving the returned StreamingResponse body.
  - A real broadcast message is delivered as a `data:` frame ahead of any keepalive.
  - Feeder cancellation: when the consumer stops, the feeder task is cancelled and
    awaited (no leaked task) — asserted via the broadcaster's CancelledError path.

We call task_feed() directly (it's an async function) so we control auth and the
Request object. Auth tokens are minted with the conftest TEST_JWT_SECRET, which
is what dynamic_settings.get_jwt_secret() returns in the test process.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from conftest import make_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_request(*, headers=None, query_token=None, cookies=None, disconnected=False):
    """Build a Starlette-Request-like object good enough for task_feed()."""
    req = MagicMock()
    req.headers = headers or {}
    req.cookies = cookies or {}
    req.is_disconnected = AsyncMock(return_value=disconnected)
    return req


async def _collect(body_iter, *, limit, timeout):
    out = []
    try:
        async with asyncio.timeout(timeout):
            async for chunk in body_iter:
                out.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk)
                if len(out) >= limit:
                    break
    except TimeoutError:
        pass
    return out


# ---------------------------------------------------------------------------
# Auth contract (HTTP-level, via the test client)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_without_token_returns_401(client: AsyncClient):
    resp = await client.get("/tasks/feed")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_feed_with_invalid_bearer_returns_401(client: AsyncClient):
    resp = await client.get(
        "/tasks/feed", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_feed_with_query_token_missing_claims_returns_401(client: AsyncClient):
    """A token lacking required claims (sub/email/role) must be rejected."""
    from app.utils.auth import create_access_token
    from conftest import TEST_JWT_SECRET

    bad = create_access_token({"sub": "1"}, secret=TEST_JWT_SECRET)  # no email/role
    resp = await client.get(f"/tasks/feed?_t={bad}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Valid auth → streaming response with the Cloudflare-survival headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_valid_token_returns_event_stream_with_headers():
    """Drive task_feed() directly with a valid Bearer token and a silent broadcaster.

    Asserts media type + the two headers that keep the stream alive behind a
    Cloudflare named tunnel (Cache-Control: no-cache, X-Accel-Buffering: no).
    """
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"})

    async def _silent():
        await asyncio.sleep(5)
        return
        yield  # noqa: make async-gen

    with patch("app.main.broadcaster") as mock_b:
        mock_b.subscribe.return_value = _silent()
        resp = await task_feed(req)

    assert resp.media_type == "text/event-stream"
    assert resp.headers.get("cache-control") == "no-cache"
    assert resp.headers.get("x-accel-buffering") == "no"


# ---------------------------------------------------------------------------
# Real event_generator behavior (heartbeat / data / EOF / cancel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_generator_emits_keepalive_on_idle():
    """With a fast heartbeat and an idle broadcaster, the real endpoint streams
    `: keepalive` frames (Story S2 AC: 'test asserts heartbeat is emitted')."""
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"})

    async def _silent():
        await asyncio.sleep(5)
        return
        yield

    with (
        patch("app.main.broadcaster") as mock_b,
        patch("app.config.dynamic_settings.get_int", return_value=0),
    ):
        mock_b.subscribe.return_value = _silent()
        resp = await task_feed(req)
        chunks = await _collect(resp.body_iterator, limit=3, timeout=2.0)

    assert chunks, "no frames produced — heartbeat broken"
    assert all(c == ": keepalive\n\n" for c in chunks), chunks


@pytest.mark.asyncio
async def test_real_generator_delivers_data_frame_for_event():
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"})
    payload = '{"type":"new_task","task_id":7}'

    async def _one():
        yield payload

    with (
        patch("app.main.broadcaster") as mock_b,
        patch("app.config.dynamic_settings.get_int", return_value=5),
    ):
        mock_b.subscribe.return_value = _one()
        resp = await task_feed(req)
        chunks = await _collect(resp.body_iterator, limit=3, timeout=2.0)

    assert f"data: {payload}\n\n" in chunks
    assert ": keepalive\n\n" not in chunks


@pytest.mark.asyncio
async def test_real_generator_stops_on_broadcaster_eof():
    """When broadcaster.subscribe() ends, the generator must stop (no infinite keepalive)."""
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"})

    async def _empty():
        return
        yield

    with (
        patch("app.main.broadcaster") as mock_b,
        patch("app.config.dynamic_settings.get_int", return_value=5),
    ):
        mock_b.subscribe.return_value = _empty()
        resp = await task_feed(req)
        chunks = await _collect(resp.body_iterator, limit=10, timeout=2.0)

    # Clean stop: no keepalive frames after EOF.
    assert ": keepalive\n\n" not in chunks


@pytest.mark.asyncio
async def test_real_generator_exits_immediately_when_request_disconnected():
    """If the client is already gone, the loop's is_disconnected check exits fast."""
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"}, disconnected=True)

    async def _silent():
        await asyncio.sleep(5)
        return
        yield

    with (
        patch("app.main.broadcaster") as mock_b,
        patch("app.config.dynamic_settings.get_int", return_value=5),
    ):
        mock_b.subscribe.return_value = _silent()
        resp = await task_feed(req)
        chunks = await _collect(resp.body_iterator, limit=5, timeout=2.0)

    assert chunks == [], "disconnected request should yield nothing"


@pytest.mark.asyncio
async def test_real_generator_cancels_feeder_on_consumer_close():
    """Leak-free teardown: closing the consumer cancels the feeder, which triggers
    the broadcaster's CancelledError path (Story S2 AC: 'no leaked pubsub task')."""
    from app.main import task_feed

    token = make_token(1, "admin@lightnc.org", "admin", "Admin")
    req = _fake_request(headers={"Authorization": f"Bearer {token}"})

    cancelled = asyncio.Event()

    async def _subscribe_tracks_cancel():
        try:
            while True:
                await asyncio.sleep(0.01)
                yield  # never actually yields a value before being cancelled
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with (
        patch("app.main.broadcaster") as mock_b,
        patch("app.config.dynamic_settings.get_int", return_value=5),
    ):
        mock_b.subscribe.return_value = _subscribe_tracks_cancel()
        resp = await task_feed(req)
        gen = resp.body_iterator
        # Start the generator (schedules the feeder), then close it.
        start = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)
        start.cancel()
        try:
            await start
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await gen.aclose()
        # The generator's finally must have cancelled+awaited the feeder.
        await asyncio.wait_for(cancelled.wait(), timeout=2.0)

    assert cancelled.is_set(), "feeder task was not cancelled on teardown (leak)"
