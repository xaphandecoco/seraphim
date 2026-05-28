import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_tasks_empty(client: AsyncClient, admin_user, db_session: AsyncSession):
    from app.models import AdminSetting

    # Mark setup complete
    db_session.add(AdminSetting(key="setup_complete", value={"value": True}))
    await db_session.commit()

    login_resp = await client.post("/auth/login", json={
        "email": "admin@lightnc.org",
        "password": "adminpass123",
    })
    token = login_resp.json()["access_token"]

    resp = await client.get("/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0
