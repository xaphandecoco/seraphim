"""Story S5 / B-S5 — setup connectivity test parity.

AC: 'setup connectivity test targets the same CiviCRM endpoint the client uses'.
The wizard's /setup/test-services must probe extern/rest.php with System.check
(the runtime path), NOT the old /civicrm/ajax/rest path, and treat 200/401/403 as
reachable. CompreFace is a keyless /api/v1/health probe.

We mock httpx.AsyncClient so no real network is touched and we can assert the URL
+ params the endpoint requests. These endpoints are locked once setup is complete
(bootstrap file present) → 410; we point BOOTSTRAP_PATH at a non-existent temp path
so the endpoint runs.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import AsyncClient

import app.routers.setup as setup_mod


@pytest.fixture
def bootstrap_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_mod, "BOOTSTRAP_PATH", tmp_path / "nope.json")
    yield


class _FakeAsyncClient:
    """Context-manager stand-in for httpx.AsyncClient that records GET calls and
    returns a canned response."""

    last_calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        _FakeAsyncClient.last_calls.append({"url": url, "params": params})
        # Return 200 for any health/system endpoint.
        resp = MagicMock()
        resp.status_code = 200
        return resp


@pytest.fixture(autouse=True)
def _reset_calls():
    _FakeAsyncClient.last_calls = []
    yield
    _FakeAsyncClient.last_calls = []


# ---------------------------------------------------------------------------
# CiviCRM probe path parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_civicrm_probe_uses_extern_rest_php(client: AsyncClient, bootstrap_absent):
    with patch.object(setup_mod.httpx, "AsyncClient", _FakeAsyncClient):
        resp = await client.post(
            "/setup/test-services",
            json={"civicrm_url": "https://crm.example.org"},
        )
    assert resp.status_code == 200, resp.text
    civicrm_calls = [c for c in _FakeAsyncClient.last_calls if "crm.example.org" in c["url"]]
    assert civicrm_calls, "no CiviCRM probe was made"
    call = civicrm_calls[0]
    assert call["url"].endswith("/extern/rest.php"), (
        f"CiviCRM probe must hit extern/rest.php (runtime path), got {call['url']}"
    )
    # And it must NOT use the stale ajax/rest path.
    assert "ajax/rest" not in call["url"]
    assert call["params"].get("entity") == "System"
    assert call["params"].get("action") == "check"


@pytest.mark.asyncio
async def test_civicrm_unauthorized_still_counts_as_reachable(
    client: AsyncClient, bootstrap_absent
):
    """401/403 from extern/rest.php means CiviCRM is up — must report reachable."""

    class _Unauthorized(_FakeAsyncClient):
        async def get(self, url, params=None):
            _FakeAsyncClient.last_calls.append({"url": url, "params": params})
            resp = MagicMock()
            resp.status_code = 401
            return resp

    with patch.object(setup_mod.httpx, "AsyncClient", _Unauthorized):
        resp = await client.post(
            "/setup/test-services",
            json={"civicrm_url": "https://crm.example.org"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["civicrm_ok"] is True


# ---------------------------------------------------------------------------
# CompreFace health probe (keyless)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compreface_probe_hits_health_endpoint(client: AsyncClient, bootstrap_absent):
    with patch.object(setup_mod.httpx, "AsyncClient", _FakeAsyncClient):
        resp = await client.post(
            "/setup/test-services",
            json={"compreface_url": "http://compreface:8000"},
        )
    assert resp.status_code == 200, resp.text
    cf_calls = [c for c in _FakeAsyncClient.last_calls if "compreface" in c["url"]]
    assert cf_calls, "no CompreFace probe made"
    assert cf_calls[0]["url"].endswith("/api/v1/health")


@pytest.mark.asyncio
async def test_compreface_probe_ignores_service_keys(client: AsyncClient, bootstrap_absent):
    """The detect/recognize keys are accepted by the schema but intentionally unused
    by the health probe (Self-Review Issue 1, AI/ML). Passing them must not change
    the request — still a keyless /api/v1/health GET."""
    with patch.object(setup_mod.httpx, "AsyncClient", _FakeAsyncClient):
        resp = await client.post(
            "/setup/test-services",
            json={
                "compreface_url": "http://compreface:8000",
                "compreface_detect_api_key": "ignored-detect",
                "compreface_recognize_api_key": "ignored-recognize",
            },
        )
    assert resp.status_code == 200
    cf_calls = [c for c in _FakeAsyncClient.last_calls if "compreface" in c["url"]]
    assert cf_calls[0]["params"] is None  # keyless health probe, no params


@pytest.mark.asyncio
async def test_test_services_no_urls_reports_no_url(client: AsyncClient, bootstrap_absent):
    resp = await client.post("/setup/test-services", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["compreface_ok"] is False
    assert "No URL" in body["compreface_message"]


# ---------------------------------------------------------------------------
# Regression — endpoints are locked once setup is complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_services_locked_after_setup(client: AsyncClient, tmp_path, monkeypatch):
    bpath = tmp_path / "bootstrap.json"
    bpath.write_text("{}")
    monkeypatch.setattr(setup_mod, "BOOTSTRAP_PATH", bpath)
    resp = await client.post(
        "/setup/test-services", json={"compreface_url": "http://x"}
    )
    assert resp.status_code == 410
