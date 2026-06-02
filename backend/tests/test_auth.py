import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_user):
    resp = await client.post("/auth/login", json={
        "email": "admin@lightnc.org",
        "password": "adminpass123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, admin_user):
    resp = await client.post("/auth/login", json={
        "email": "admin@lightnc.org",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_deactivated(client: AsyncClient, db_session: AsyncSession):
    from app.models import User
    from app.utils.auth import hash_password

    user = User(
        email="inactive@lightnc.org",
        password_hash=hash_password("password123"),
        role="volunteer",
        is_active=False,
    )
    db_session.add(user)
    await db_session.commit()

    resp = await client.post("/auth/login", json={
        "email": "inactive@lightnc.org",
        "password": "password123",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_me_endpoint(client: AsyncClient, admin_user):
    # Login first
    login_resp = await client.post("/auth/login", json={
        "email": "admin@lightnc.org",
        "password": "adminpass123",
    })
    token = login_resp.json()["access_token"]

    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "admin@lightnc.org"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_add_volunteer_admin_only(client: AsyncClient, volunteer_user):
    # Login as volunteer
    login_resp = await client.post("/auth/login", json={
        "email": "volunteer@lightnc.org",
        "password": "volpass123",
    })
    token = login_resp.json()["access_token"]

    resp = await client.post("/auth/add-volunteer", json={
        "email": "new@lightnc.org",
        "name": "New Volunteer",
        "role": "volunteer",
        "temporary_password": "TempPass123!",
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_volunteer_success(client: AsyncClient, admin_user):
    login_resp = await client.post("/auth/login", json={
        "email": "admin@lightnc.org",
        "password": "adminpass123",
    })
    token = login_resp.json()["access_token"]

    resp = await client.post("/auth/add-volunteer", json={
        "email": "new@lightnc.org",
        "name": "New Volunteer",
        "role": "volunteer",
        "temporary_password": "TempPass123!",
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "new@lightnc.org"
    assert data["role"] == "volunteer"
