"""Tests for S16 settings endpoints.

Covers:
  - GET /settings/jobs pagination + filtering
  - POST /settings/jobs/{job_name}/trigger (202/404/409/403)
  - GET /settings/system-status (ok/degraded/down)
  - GET /settings/config-checklist shape
  - PUT /settings/{key} Fernet round-trip + masked no-op
  - POST /settings/test/{service} stubs
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AdminSetting, JobRun


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _insert_job_run(db_session, job_name: str, status: str = "success") -> JobRun:
    jr = JobRun(
        job_name=job_name,
        status=status,
        started_at=_utcnow_naive(),
        finished_at=_utcnow_naive() if status != "running" else None,
        duration_ms=100 if status != "running" else None,
    )
    db_session.add(jr)
    await db_session.commit()
    await db_session.refresh(jr)
    return jr


# ---------------------------------------------------------------------------
# GET /settings/jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jobs_list_returns_200(client, admin_auth_headers, db_session):
    """Empty job_runs table → 200 with items=[], total=0."""
    resp = await client.get("/settings/jobs", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_jobs_list_pagination(client, admin_auth_headers, db_session):
    """Rows are returned in started_at DESC order with correct pagination."""
    for i in range(5):
        await _insert_job_run(db_session, "test_job")

    resp = await client.get("/settings/jobs?page=1&page_size=3", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 3
    assert data["page"] == 1
    assert data["page_size"] == 3


@pytest.mark.asyncio
async def test_jobs_list_filter_by_name(client, admin_auth_headers, db_session):
    """job_name filter returns only matching rows."""
    await _insert_job_run(db_session, "alpha_job")
    await _insert_job_run(db_session, "beta_job")

    resp = await client.get("/settings/jobs?job_name=alpha_job", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["job_name"] == "alpha_job"


@pytest.mark.asyncio
async def test_jobs_list_403_for_volunteer(client, volunteer_auth_headers):
    """Non-admin users receive 403."""
    resp = await client.get("/settings/jobs", headers=volunteer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_jobs_list_duration_ms_for_running_row(client, admin_auth_headers, db_session):
    """Running rows have duration_ms computed dynamically (not null)."""
    await _insert_job_run(db_session, "slow_job", status="running")

    resp = await client.get("/settings/jobs?job_name=slow_job", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    # duration_ms should be non-null (live elapsed time)
    assert data["items"][0]["duration_ms"] is not None
    assert data["items"][0]["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# POST /settings/jobs/{job_name}/trigger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_unknown_job_returns_404(client, admin_auth_headers):
    """Triggering an unregistered job name → 404."""
    resp = await client.post("/settings/jobs/nonexistent_job/trigger", headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_trigger_returns_202(client, admin_auth_headers, db_session):
    """Triggering a known job → 202 Accepted."""
    # Patch _run_tracked_job so it doesn't run real job logic
    with patch("app.services.scheduler._run_tracked_job", new=AsyncMock()):
        resp = await client.post(
            "/settings/jobs/biometric_retention/trigger",
            headers=admin_auth_headers,
        )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_trigger_409_when_already_running(client, admin_auth_headers, db_session):
    """Triggering a job that has a 'running' row → 409 Conflict."""
    await _insert_job_run(db_session, "biometric_retention", status="running")

    resp = await client.post(
        "/settings/jobs/biometric_retention/trigger",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409, resp.text
    assert "running" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_trigger_403_for_volunteer(client, volunteer_auth_headers):
    """Non-admin users receive 403."""
    resp = await client.post(
        "/settings/jobs/biometric_retention/trigger",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /settings/system-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_status_ok(client, admin_auth_headers, db_session):
    """When postgres+redis ok → overall='ok'."""
    with (
        patch("app.services.settings_service.probe_postgres", new=AsyncMock(return_value=True)),
        patch("app.services.settings_service.probe_redis", new=AsyncMock(return_value=True)),
        patch("app.services.settings_service.probe_compreface", new=AsyncMock(return_value=True)),
    ):
        resp = await client.get("/settings/system-status", headers=admin_auth_headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["overall"] == "ok"
    assert data["services"]["postgres"] is True
    assert data["services"]["redis"] is True
    assert "queue_depth" in data
    assert "safe_mode" in data
    assert "jobs" in data


@pytest.mark.asyncio
async def test_system_status_degraded_when_compreface_down(client, admin_auth_headers):
    """Postgres+Redis ok but CompreFace down → overall='degraded'."""
    with (
        patch("app.services.settings_service.probe_postgres", new=AsyncMock(return_value=True)),
        patch("app.services.settings_service.probe_redis", new=AsyncMock(return_value=True)),
        patch("app.services.settings_service.probe_compreface", new=AsyncMock(return_value=False)),
    ):
        resp = await client.get("/settings/system-status", headers=admin_auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    # compreface down does not affect overall (only pg+redis drive it)
    assert data["overall"] == "ok"
    assert data["services"]["compreface"] is False


@pytest.mark.asyncio
async def test_system_status_down_when_postgres_fails(client, admin_auth_headers):
    """Postgres down → overall='down'."""
    with (
        patch("app.services.settings_service.probe_postgres", new=AsyncMock(return_value=False)),
        patch("app.services.settings_service.probe_redis", new=AsyncMock(return_value=False)),
        patch("app.services.settings_service.probe_compreface", new=AsyncMock(return_value=False)),
    ):
        resp = await client.get("/settings/system-status", headers=admin_auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["overall"] == "down"


@pytest.mark.asyncio
async def test_system_status_403_volunteer(client, volunteer_auth_headers):
    resp = await client.get("/settings/system-status", headers=volunteer_auth_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /settings/config-checklist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_checklist_shape(client, admin_auth_headers, db_session):
    """Config checklist returns the expected keys and shape."""
    resp = await client.get("/settings/config-checklist", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "items" in data
    assert "required_complete" in data
    assert "recommended_complete" in data
    assert "all_complete" in data

    keys = [item["key"] for item in data["items"]]
    # Required keys must appear
    assert "sunday_event_series_id" in keys
    assert "powerhouse_event_series_id" in keys
    assert "jwt_secret" in keys

    # Each item has the expected fields
    for item in data["items"]:
        assert "key" in item
        assert "label" in item
        assert "is_set" in item
        assert "required" in item


@pytest.mark.asyncio
async def test_config_checklist_required_complete_when_all_required_set(
    client, admin_auth_headers, db_session
):
    """required_complete is True when all required keys have values."""
    # Seed all required keys into admin_settings
    from app.services.settings_service import _CHECKLIST_DEFS
    for key, label, required in _CHECKLIST_DEFS:
        if required:
            setting = AdminSetting(
                key=key,
                value={"value": "test_value_123"},
                category="general",
            )
            db_session.add(setting)
    await db_session.commit()

    resp = await client.get("/settings/config-checklist", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["required_complete"] is True


# ---------------------------------------------------------------------------
# PUT /settings/{key} — single-key update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_setting_by_key_basic(client, admin_auth_headers, db_session):
    """PUT /settings/{key} updates the value and returns the key+value."""
    # Seed a setting
    setting = AdminSetting(
        key="sse_heartbeat_seconds",
        value={"value": "15"},
        category="general",
    )
    db_session.add(setting)
    await db_session.commit()

    resp = await client.put(
        "/settings/sse_heartbeat_seconds",
        json={"value": "30"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["key"] == "sse_heartbeat_seconds"
    assert data["value"] == "30"


@pytest.mark.asyncio
async def test_update_setting_by_key_404_for_unknown(client, admin_auth_headers):
    """PUT /settings/{key} → 404 for a key that does not exist."""
    resp = await client.put(
        "/settings/nonexistent_key_xyz",
        json={"value": "something"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_immutable_key_jwt_secret_returns_403(client, admin_auth_headers, db_session):
    """PUT /settings/jwt_secret → 403: jwt_secret is fully immutable via settings API."""
    # Seed the row so it exists (tests the overwrite path, not missing-key path)
    setting = AdminSetting(
        key="jwt_secret",
        value={"value": "existing-secret"},
        category="general",
    )
    db_session.add(setting)
    await db_session.commit()

    resp = await client.put(
        "/settings/jwt_secret",
        json={"value": "attacker-controlled-secret"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 403, (
        f"Expected 403 for jwt_secret overwrite, got {resp.status_code}: {resp.text}"
    )
    assert "immutable" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_immutable_key_database_url_returns_403(client, admin_auth_headers, db_session):
    """PUT /settings/database_url → 403: database_url is fully immutable."""
    setting = AdminSetting(
        key="database_url",
        value={"value": "postgresql://original"},
        category="general",
    )
    db_session.add(setting)
    await db_session.commit()

    resp = await client.put(
        "/settings/database_url",
        json={"value": "postgresql://attacker-host/evil"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bulk_update_immutable_key_jwt_secret_returns_403(client, admin_auth_headers):
    """Bulk PUT /settings with jwt_secret in payload → 403."""
    resp = await client.put(
        "/settings",
        json={"settings": {"jwt_secret": "new-secret"}},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 403
    assert "immutable" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_update_setting_by_key_403_for_volunteer(client, volunteer_auth_headers, db_session):
    """Non-admin users → 403."""
    setting = AdminSetting(key="test_vol_key", value={"value": "v"}, category="general")
    db_session.add(setting)
    await db_session.commit()

    resp = await client.put(
        "/settings/test_vol_key",
        json={"value": "new"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_setting_sensitive_fernet_roundtrip(client, admin_auth_headers, db_session):
    """Sensitive value is encrypted at rest; masked on read; empty masked = no-op."""
    from app.services.settings_service import MASKED_SENTINEL, decrypt_value

    # Seed jwt_secret into DB so dynamic_settings.reload() doesn't wipe it
    # (the endpoint calls reload after writing, and Fernet derivation needs jwt_secret).
    from tests.conftest import TEST_JWT_SECRET
    jwt_row = AdminSetting(
        key="jwt_secret",
        value={"value": TEST_JWT_SECRET},
        category="general",
    )
    db_session.add(jwt_row)

    # Create a sensitive-marked setting
    setting = AdminSetting(
        key="compreface_api_key",
        value={"value": "old_secret"},
        category="general",
        sensitive=True,
    )
    db_session.add(setting)
    await db_session.commit()

    # PUT a new sensitive value
    resp = await client.put(
        "/settings/compreface_api_key",
        json={"value": "new_secret_value"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Response should be masked
    assert data["value"] == MASKED_SENTINEL

    # Check DB: value should be encrypted
    await db_session.refresh(setting)
    stored = setting.value
    assert stored.get("encrypted") is True
    ciphertext = stored["value"]
    decrypted = decrypt_value(ciphertext)
    assert decrypted == "new_secret_value"

    # Re-submit the masked sentinel → no-op (value preserved)
    resp2 = await client.put(
        "/settings/compreface_api_key",
        json={"value": MASKED_SENTINEL},
        headers=admin_auth_headers,
    )
    assert resp2.status_code == 200
    # Value on disk unchanged
    await db_session.refresh(setting)
    assert decrypt_value(setting.value["value"]) == "new_secret_value"


# ---------------------------------------------------------------------------
# POST /settings/test/{service}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_test_google_chat_stub(client, admin_auth_headers):
    resp = await client.post("/settings/test/google-chat", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is False
    assert "not configured" in data["detail"].lower()


@pytest.mark.asyncio
async def test_connection_test_zoom_stub(client, admin_auth_headers):
    resp = await client.post("/settings/test/zoom", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_connection_test_gmail_stub(client, admin_auth_headers):
    resp = await client.post("/settings/test/gmail", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_connection_test_unknown_service_404(client, admin_auth_headers):
    resp = await client.post("/settings/test/unknown_service", headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_connection_test_403_volunteer(client, volunteer_auth_headers):
    resp = await client.post("/settings/test/zoom", headers=volunteer_auth_headers)
    assert resp.status_code == 403
