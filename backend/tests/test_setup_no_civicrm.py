"""Tests verifying CiviCRM has been removed from the setup flow."""

import pytest
from httpx import AsyncClient


@pytest.fixture
def bootstrap_absent(monkeypatch, tmp_path):
    """Ensure bootstrap file does not exist so setup endpoints are unlocked."""
    from app.routers import setup as setup_module
    fake_path = tmp_path / "bootstrap.json"
    monkeypatch.setattr(setup_module, "BOOTSTRAP_PATH", fake_path)
    return fake_path


@pytest.mark.asyncio
async def test_setup_test_services_response_has_no_civicrm_keys(
    client: AsyncClient, bootstrap_absent
):
    resp = await client.post(
        "/setup/test-services", json={"compreface_url": "http://x"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "civicrm_ok" not in body
    assert "civicrm_message" not in body
    assert "compreface_ok" in body


@pytest.mark.asyncio
async def test_setup_create_without_civicrm_fields_succeeds(
    client: AsyncClient, bootstrap_absent
):
    payload = {
        "database_url": "sqlite+aiosqlite:///./ci_test.db",
        "redis_url": "redis://localhost/0",
        "compreface_url": "http://compreface:8080",
        "admin_email": "admin@lightnc.org",
        "admin_password": "TestAdmin1!secure",
        "admin_name": "Admin",
    }
    resp = await client.post("/setup", json=payload)
    assert resp.status_code in (200, 201)
