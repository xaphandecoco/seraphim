"""Tests for S13 profile CRUD endpoints (GET /public/newcomer/profile is also here)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import CustomFieldDef, CustomFieldGroup, Profile


# ---------------------------------------------------------------------------
# Fixture aliases / helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(db_session: AsyncSession) -> AsyncSession:
    """Alias so tests can use `db` instead of the conftest name `db_session`."""
    return db_session


@pytest_asyncio.fixture
async def public_profile(db_session: AsyncSession) -> Profile:
    profile = Profile(
        name="New Friend Profile",
        entity="contact",
        fields=[],
        settings={
            "contact_subtype_default": "New Friend",
            "notify_google_chat": True,
            "notify_gmail": True,
            "success_message": "Thank you! Your information has been recorded.",
        },
        is_public=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest_asyncio.fixture
async def custom_field_setup(db_session: AsyncSession) -> dict:
    """Seed a CustomFieldGroup + a text CustomFieldDef named 'facebook_name'."""
    group = CustomFieldGroup(
        name="newcomer_fields",
        label="Newcomer Fields",
        entity="contact",
        weight=1,
        is_active=True,
    )
    db_session.add(group)
    await db_session.flush()

    field = CustomFieldDef(
        group_id=group.id,
        name="facebook_name",
        label="Facebook Name",
        data_type="text",
        options=[],
        is_required=False,
        is_multi=False,
        weight=1,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()
    await db_session.refresh(group)
    await db_session.refresh(field)
    return {"group": group, "field": field}


# ---------------------------------------------------------------------------
# Profile CRUD tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_profile_admin(
    client: AsyncClient,
    admin_auth_headers: dict,
) -> None:
    resp = await client.post(
        "/profiles/",
        headers=admin_auth_headers,
        json={
            "name": "Test Profile",
            "entity": "contact",
            "fields": [],
            "settings": {},
            "is_public": False,
        },
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    assert data["name"] == "Test Profile"
    assert data["entity"] == "contact"
    assert data["is_public"] is False


@pytest.mark.asyncio
async def test_create_profile_volunteer_forbidden(
    client: AsyncClient,
    volunteer_auth_headers: dict,
) -> None:
    resp = await client.post(
        "/profiles/",
        headers=volunteer_auth_headers,
        json={
            "name": "Should Fail",
            "entity": "contact",
            "fields": [],
            "settings": {},
            "is_public": False,
        },
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unique_profile_name(
    client: AsyncClient,
    admin_auth_headers: dict,
) -> None:
    body = {
        "name": "Duplicate Profile",
        "entity": "contact",
        "fields": [],
        "settings": {},
        "is_public": False,
    }
    r1 = await client.post("/profiles/", headers=admin_auth_headers, json=body)
    assert r1.status_code == 201, r1.json()
    r2 = await client.post("/profiles/", headers=admin_auth_headers, json=body)
    assert r2.status_code == 422


@pytest.mark.asyncio
async def test_only_one_public_profile(
    client: AsyncClient,
    admin_auth_headers: dict,
) -> None:
    r1 = await client.post(
        "/profiles/",
        headers=admin_auth_headers,
        json={
            "name": "Public A",
            "entity": "contact",
            "fields": [],
            "settings": {},
            "is_public": True,
        },
    )
    assert r1.status_code == 201, r1.json()

    r2 = await client.post(
        "/profiles/",
        headers=admin_auth_headers,
        json={
            "name": "Public B",
            "entity": "contact",
            "fields": [],
            "settings": {},
            "is_public": True,
        },
    )
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_render_profile_resolves_custom_fields(
    client: AsyncClient,
    admin_auth_headers: dict,
    volunteer_auth_headers: dict,
    custom_field_setup: dict,
) -> None:
    # Create a profile referencing the seeded 'facebook_name' custom field
    create_resp = await client.post(
        "/profiles/",
        headers=admin_auth_headers,
        json={
            "name": "Render Test Profile",
            "entity": "contact",
            "fields": [
                {
                    "id": "custom:facebook_name",
                    "field_type": "custom",
                    "custom_field_name": "facebook_name",
                    "weight": 1,
                    "is_required": False,
                }
            ],
            "settings": {},
            "is_public": False,
        },
    )
    assert create_resp.status_code == 201, create_resp.json()
    profile_id = create_resp.json()["id"]

    render_resp = await client.post(
        f"/profiles/{profile_id}/render",
        headers=volunteer_auth_headers,
        json={},
    )
    assert render_resp.status_code == 200, render_resp.json()
    data = render_resp.json()
    assert data["profile_id"] == profile_id
    fields = data["fields"]
    assert len(fields) == 1
    assert fields[0]["label"] == "Facebook Name"
    assert fields[0]["data_type"] == "text"


@pytest.mark.asyncio
async def test_delete_profile(
    client: AsyncClient,
    admin_auth_headers: dict,
) -> None:
    # Create a non-public profile to be deleted
    create_resp = await client.post(
        "/profiles/",
        headers=admin_auth_headers,
        json={
            "name": "Delete Me",
            "entity": "contact",
            "fields": [],
            "settings": {},
            "is_public": False,
        },
    )
    assert create_resp.status_code == 201, create_resp.json()
    profile_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/profiles/{profile_id}", headers=admin_auth_headers
    )
    assert del_resp.status_code == 204

    get_resp = await client.get(
        f"/profiles/{profile_id}", headers=admin_auth_headers
    )
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Public schema endpoint tests (GET /public/newcomer/profile)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_public_schema_no_auth(
    client: AsyncClient,
    public_profile: Profile,
) -> None:
    """Public endpoint requires no auth and returns the is_public profile schema."""
    resp = await client.get("/public/newcomer/profile")
    assert resp.status_code == 200, resp.json()
    data = resp.json()
    assert data["name"] == "New Friend Profile"
    assert "settings" in data
    assert "fields" in data


@pytest.mark.asyncio
async def test_get_public_schema_no_public_profile(client: AsyncClient) -> None:
    """Returns 404 when no profile has is_public=True."""
    resp = await client.get("/public/newcomer/profile")
    assert resp.status_code == 404
