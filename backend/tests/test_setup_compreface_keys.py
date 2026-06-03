"""Integration tests for setup persistence + masking of CompreFace dual keys.

Covers:
  - B-S3 setup persistence: POST /setup writes compreface_detect_api_key and
    compreface_recognize_api_key (plus legacy compreface_api_key) into admin_settings.
  - Security/D-S3: all three CompreFace keys are flagged sensitive=True (masked on
    GET /settings, preserved on '********' update).
  - Schema (ML-S3 / Self-Review): SetupRequest.compreface_api_key is optional;
    cameras is typed (List[CameraCreateRequest]) so an invalid rtsp:// is rejected
    at setup time, and a valid one is accepted.

The setup endpoint refuses to run if the bootstrap file already exists (410), and
writes one on success. We point BOOTSTRAP_PATH at a tmp file that does NOT exist,
and clean it up. We also reset the dynamic_settings singleton after each test since
create_setup calls dynamic_settings.reload().
"""
import json

import httpx
import pytest
from httpx import AsyncClient

import app.routers.setup as setup_mod
from app.config import DynamicSettings
from app.database import get_db
from app.dependencies import check_setup_complete
from app.main import app
from app.models import AdminSetting, User
from sqlalchemy import select

from conftest import make_token, TEST_JWT_SECRET


async def _admin_headers_from_setup(db_session) -> dict:
    """After /setup creates the admin row, mint a Bearer header for that user.

    Avoids colliding with the `admin_user` fixture (which would create a second row
    with the same email and trip the users.email UNIQUE constraint)."""
    admin = (
        await db_session.execute(select(User).where(User.email == "admin@lightnc.org"))
    ).scalar_one()
    token = make_token(admin.id, admin.email, admin.role, "Admin")
    return {"Authorization": f"Bearer {token}"}


_STRONG_PW = "Str0ng!Passw0rd"


@pytest.fixture
def bootstrap_tmp(tmp_path, monkeypatch):
    """Point setup at a non-existent temp bootstrap path; restore settings after."""
    bpath = tmp_path / "bootstrap.json"
    monkeypatch.setattr(setup_mod, "BOOTSTRAP_PATH", bpath)
    snapshot = dict(DynamicSettings._settings)
    try:
        yield bpath
    finally:
        DynamicSettings._settings = snapshot
        if bpath.exists():
            bpath.unlink()


def _base_payload(**overrides):
    payload = {
        "database_url": "sqlite+aiosqlite:///./test_seraphim.db",
        "redis_url": "memory://",
        "compreface_url": "http://compreface:8000",
        "admin_email": "admin@lightnc.org",
        "admin_password": _STRONG_PW,
        "admin_name": "Admin",
        "cameras": [],
        # Keep the post-reload jwt_secret equal to what make_token signs with, so
        # tokens still verify after create_setup() calls dynamic_settings.reload().
        # (bootstrap_tmp restores the singleton afterwards.)
        "jwt_secret": TEST_JWT_SECRET,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Persistence of the two service keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_persists_both_service_keys(client: AsyncClient, db_session, bootstrap_tmp):
    resp = await client.post(
        "/setup",
        json=_base_payload(
            compreface_detect_api_key="detect-uuid",
            compreface_recognize_api_key="recognize-uuid",
        ),
    )
    assert resp.status_code == 201, resp.text

    rows = (await db_session.execute(select(AdminSetting))).scalars().all()
    by_key = {r.key: r.value.get("value") for r in rows}
    assert by_key.get("compreface_detect_api_key") == "detect-uuid"
    assert by_key.get("compreface_recognize_api_key") == "recognize-uuid"


@pytest.mark.asyncio
async def test_setup_keeps_legacy_key_for_fallback(client, db_session, bootstrap_tmp):
    resp = await client.post(
        "/setup",
        json=_base_payload(compreface_api_key="legacy-uuid"),
    )
    assert resp.status_code == 201, resp.text

    rows = (await db_session.execute(select(AdminSetting))).scalars().all()
    by_key = {r.key: r.value.get("value") for r in rows}
    assert by_key.get("compreface_api_key") == "legacy-uuid"
    # Service keys default to empty string (not missing) so the `or`-fallback works.
    assert by_key.get("compreface_detect_api_key") == ""
    assert by_key.get("compreface_recognize_api_key") == ""


@pytest.mark.asyncio
async def test_setup_marks_all_three_keys_sensitive(client, db_session, bootstrap_tmp):
    """Security AC: leaking the Recognition key exposes enrollment CRUD — all three
    CompreFace keys MUST be sensitive=True."""
    resp = await client.post(
        "/setup",
        json=_base_payload(
            compreface_api_key="legacy-uuid",
            compreface_detect_api_key="detect-uuid",
            compreface_recognize_api_key="recognize-uuid",
        ),
    )
    assert resp.status_code == 201, resp.text

    rows = (await db_session.execute(select(AdminSetting))).scalars().all()
    sensitive = {r.key for r in rows if r.sensitive}
    for k in (
        "compreface_api_key",
        "compreface_detect_api_key",
        "compreface_recognize_api_key",
    ):
        assert k in sensitive, f"{k} must be marked sensitive"


# ---------------------------------------------------------------------------
# Masking on GET /settings + preserve-on-mask via PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_keys_masked_on_get_settings(client, db_session, bootstrap_tmp):
    setup_resp = await client.post(
        "/setup",
        json=_base_payload(
            compreface_detect_api_key="detect-uuid",
            compreface_recognize_api_key="recognize-uuid",
        ),
    )
    assert setup_resp.status_code == 201, setup_resp.text

    admin_auth_headers = await _admin_headers_from_setup(db_session)
    resp = await client.get("/settings", headers=admin_auth_headers)
    assert resp.status_code == 200, resp.text
    items = {s["key"]: s for s in resp.json()["settings"]}
    assert items["compreface_detect_api_key"]["value"]["value"] == "********"
    assert items["compreface_recognize_api_key"]["value"]["value"] == "********"


@pytest.mark.asyncio
async def test_masked_update_preserves_service_key(client, db_session, bootstrap_tmp):
    """PUT /settings with '********' must NOT overwrite the stored key."""
    setup_resp = await client.post(
        "/setup",
        json=_base_payload(compreface_recognize_api_key="recognize-uuid"),
    )
    assert setup_resp.status_code == 201, setup_resp.text

    admin_auth_headers = await _admin_headers_from_setup(db_session)
    # Submit the masked sentinel — server should skip the write.
    put = await client.put(
        "/settings",
        headers=admin_auth_headers,
        json={"settings": {"compreface_recognize_api_key": "********"}},
    )
    assert put.status_code == 200, put.text

    row = (
        await db_session.execute(
            select(AdminSetting).where(
                AdminSetting.key == "compreface_recognize_api_key"
            )
        )
    ).scalar_one()
    assert row.value.get("value") == "recognize-uuid", "masked update wiped the key"


@pytest.mark.asyncio
async def test_real_update_rotates_service_key(client, db_session, bootstrap_tmp):
    setup_resp = await client.post(
        "/setup",
        json=_base_payload(compreface_detect_api_key="old-detect"),
    )
    assert setup_resp.status_code == 201, setup_resp.text

    admin_auth_headers = await _admin_headers_from_setup(db_session)
    put = await client.put(
        "/settings",
        headers=admin_auth_headers,
        json={"settings": {"compreface_detect_api_key": "new-detect"}},
    )
    assert put.status_code == 200, put.text

    row = (
        await db_session.execute(
            select(AdminSetting).where(AdminSetting.key == "compreface_detect_api_key")
        )
    ).scalar_one()
    assert row.value.get("value") == "new-detect"


# ---------------------------------------------------------------------------
# Schema validation (cameras typed; legacy key optional)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_rejects_invalid_rtsp_camera(client, db_session, bootstrap_tmp):
    """cameras is List[CameraCreateRequest]; a non-rtsp URL fails at setup time (422)."""
    resp = await client.post(
        "/setup",
        json=_base_payload(
            cameras=[{"name": "Cam", "rtsp_url": "http://not-rtsp/stream"}]
        ),
    )
    assert resp.status_code == 422, resp.text
    # Bootstrap must NOT have been written on a validation failure.
    assert not bootstrap_tmp.exists()


@pytest.mark.asyncio
async def test_setup_accepts_valid_rtsp_camera(client, db_session, bootstrap_tmp):
    resp = await client.post(
        "/setup",
        json=_base_payload(
            cameras=[{"name": "Cam", "rtsp_url": "rtsp://cam-1:8554/cam1"}]
        ),
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_setup_succeeds_without_any_compreface_key(client, db_session, bootstrap_tmp):
    """compreface_api_key is optional (str = ''); omitting all keys still works."""
    resp = await client.post("/setup", json=_base_payload())
    assert resp.status_code == 201, resp.text
