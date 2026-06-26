"""S24 — FR Transition verification endpoint tests.

Acceptance criteria:
- GET /admin/fr-transition/status returns correct shape.
- In test env, smoke_test section has status='skip' and detail='test-env'.
- Non-admin returns 403.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import make_token


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _make_admin_token(db_session: AsyncSession) -> str:
    from app.models import User
    from app.utils.auth import hash_password

    u = User(
        email="ver_admin@test.com",
        password_hash=hash_password("pass"),
        role="admin",
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return make_token(u.id, u.email, "admin")


async def _make_volunteer_token(db_session: AsyncSession) -> str:
    from app.models import User
    from app.utils.auth import hash_password

    u = User(
        email="ver_vol@test.com",
        password_hash=hash_password("pass"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return make_token(u.id, u.email, "volunteer")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fr_transition_status_shape(client: AsyncClient, db_session: AsyncSession):
    """GET /admin/fr-transition/status returns all expected top-level keys."""
    token = await _make_admin_token(db_session)

    resp = await client.get(
        "/admin/fr-transition/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level keys
    assert "remap" in body
    assert "participants_count" in body
    assert "consent" in body
    assert "smoke_test" in body

    # remap section
    remap = body["remap"]
    assert "subjects_total" in remap
    assert "subjects_remapped" in remap
    assert "subjects_orphaned" in remap
    assert isinstance(remap["subjects_total"], int)

    # consent section
    consent = body["consent"]
    assert "active_subjects_total" in consent
    assert "consent_rows_created" in consent
    assert "subjects_missing_consent" in consent
    assert "enroll_without_consent" in consent
    assert isinstance(consent["enroll_without_consent"], bool)

    # smoke_test section
    smoke = body["smoke_test"]
    assert "status" in smoke
    assert "detail" in smoke


@pytest.mark.asyncio
async def test_get_fr_transition_status_smoke_skip_in_test_env(
    client: AsyncClient, db_session: AsyncSession
):
    """In ENVIRONMENT=test, smoke_test status must be 'skip' with detail='test-env'."""
    token = await _make_admin_token(db_session)

    resp = await client.get(
        "/admin/fr-transition/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    smoke = body["smoke_test"]

    assert smoke["status"] == "skip"
    assert smoke["detail"] == "test-env"


@pytest.mark.asyncio
async def test_get_fr_transition_status_non_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
):
    """Non-admin (volunteer) must receive 403 from GET /admin/fr-transition/status."""
    token = await _make_volunteer_token(db_session)

    resp = await client.get(
        "/admin/fr-transition/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403, (
        f"Expected 403 for volunteer, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_get_fr_transition_status_unauthenticated_returns_401(
    client: AsyncClient, db_session: AsyncSession
):
    """No auth header → 401 (or 403 depending on guard chain)."""
    resp = await client.get("/admin/fr-transition/status")
    assert resp.status_code in (401, 403), (
        f"Expected 401 or 403 without auth, got {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_get_fr_transition_status_participants_count_is_int(
    client: AsyncClient, db_session: AsyncSession
):
    """participants_count field must be an integer."""
    token = await _make_admin_token(db_session)

    resp = await client.get(
        "/admin/fr-transition/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["participants_count"], int)


@pytest.mark.asyncio
async def test_post_remap_admin_succeeds(client: AsyncClient, db_session: AsyncSession):
    """POST /admin/fr-transition/remap returns 200 for admin."""
    token = await _make_admin_token(db_session)

    resp = await client.post(
        "/admin/fr-transition/remap",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "subjects_remapped" in body
    assert "subjects_orphaned" in body


@pytest.mark.asyncio
async def test_post_remap_non_admin_returns_403(client: AsyncClient, db_session: AsyncSession):
    """POST /admin/fr-transition/remap returns 403 for volunteer."""
    token = await _make_volunteer_token(db_session)

    resp = await client.post(
        "/admin/fr-transition/remap",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403, (
        f"Expected 403 for volunteer, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_post_consent_backfill_admin_succeeds(client: AsyncClient, db_session: AsyncSession):
    """POST /admin/fr-transition/consent-backfill returns 200 for admin."""
    token = await _make_admin_token(db_session)

    resp = await client.post(
        "/admin/fr-transition/consent-backfill",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "consent_rows_created" in body
    assert "enroll_without_consent_set" in body


@pytest.mark.asyncio
async def test_post_consent_backfill_non_admin_returns_403(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /admin/fr-transition/consent-backfill returns 403 for volunteer."""
    token = await _make_volunteer_token(db_session)

    resp = await client.post(
        "/admin/fr-transition/consent-backfill",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403, (
        f"Expected 403 for volunteer, got {resp.status_code}: {resp.text}"
    )
