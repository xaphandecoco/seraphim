"""Integration tests for saved searches CRUD and run (S09).

Coverage:
  test_create_saved_search              — POST /search/saved creates and returns
  test_list_saved_searches_owner_scoped — only shows current user's searches
  test_get_saved_search_owner_scoped    — cross-owner → 404
  test_update_saved_search              — PATCH updates name/criteria
  test_delete_saved_search              — DELETE removes and returns 204
  test_cross_owner_get_404              — another user's search → 404
  test_cross_owner_patch_404            — another user's search → 404
  test_cross_owner_delete_404           — another user's search → 404
  test_save_validates_on_create_422     — invalid criteria → 422 (not saved)
  test_run_saved_search_matches_adhoc   — run result matches adhoc search
  test_promote_creates_smart_group      — POST /saved/{id}/promote → smart group
  test_promote_dup_name_409             — promote with existing group name → 409
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Group, User
from app.utils.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from tests.conftest import make_token


async def _make_user(db: AsyncSession, email: str, role: str = "volunteer") -> User:
    user = User(
        email=email,
        password_hash=hash_password("pass123!Pass"),
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def _headers_for(user: User) -> dict:
    token = make_token(user.id, user.email, user.role)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


async def test_create_saved_search(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/saved → 201 with SavedSearchOut shape."""
    resp = await client.post(
        "/search/saved",
        json={
            "name": "Active Tier1",
            "entity": "contact",
            "criteria": {"field": "tier", "op": "eq", "value": "tier1"},
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Active Tier1"
    assert body["entity"] == "contact"
    assert body["criteria"]["field"] == "tier"
    assert "id" in body
    assert "created_at" in body


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


async def test_list_saved_searches_owner_scoped(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /search/saved returns only searches owned by the requesting user."""
    user_a = await _make_user(db_session, "userai@test.com")
    user_b = await _make_user(db_session, "userbi@test.com")
    headers_a = _headers_for(user_a)
    headers_b = _headers_for(user_b)

    # User A creates a search
    await client.post(
        "/search/saved",
        json={"name": "Search A", "entity": "contact", "criteria": {"field": "first_name", "op": "is_set"}},
        headers=headers_a,
    )
    # User B creates a search
    await client.post(
        "/search/saved",
        json={"name": "Search B", "entity": "contact", "criteria": {"field": "last_name", "op": "is_set"}},
        headers=headers_b,
    )

    # User A should only see their own search
    resp_a = await client.get("/search/saved", headers=headers_a)
    assert resp_a.status_code == 200, resp_a.text
    names_a = {s["name"] for s in resp_a.json()}
    assert "Search A" in names_a
    assert "Search B" not in names_a

    # User B should only see their own
    resp_b = await client.get("/search/saved", headers=headers_b)
    names_b = {s["name"] for s in resp_b.json()}
    assert "Search B" in names_b
    assert "Search A" not in names_b


# ---------------------------------------------------------------------------
# GET (owner-scoped)
# ---------------------------------------------------------------------------


async def test_get_saved_search_own(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /search/saved/{id} returns own search."""
    create_resp = await client.post(
        "/search/saved",
        json={"name": "My Search", "criteria": {"field": "email", "op": "is_set"}},
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    search_id = create_resp.json()["id"]

    resp = await client.get(f"/search/saved/{search_id}", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == search_id


async def test_cross_owner_get_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """GET /search/saved/{id} with another user's id → 404."""
    user_a = await _make_user(db_session, "ownera@test.com")
    user_b = await _make_user(db_session, "ownerb@test.com")
    headers_a = _headers_for(user_a)
    headers_b = _headers_for(user_b)

    create_resp = await client.post(
        "/search/saved",
        json={"name": "Owner A Search", "criteria": {"field": "first_name", "op": "is_set"}},
        headers=headers_a,
    )
    assert create_resp.status_code == 201
    search_id = create_resp.json()["id"]

    # User B tries to get User A's search
    resp = await client.get(f"/search/saved/{search_id}", headers=headers_b)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


async def test_update_saved_search(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """PATCH /search/saved/{id} updates name."""
    create_resp = await client.post(
        "/search/saved",
        json={"name": "Old Name", "criteria": {"field": "first_name", "op": "is_set"}},
        headers=volunteer_auth_headers,
    )
    search_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/search/saved/{search_id}",
        json={"name": "New Name"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"


async def test_cross_owner_patch_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """PATCH /search/saved/{id} with another user's id → 404."""
    user_a = await _make_user(db_session, "patchownera@test.com")
    user_b = await _make_user(db_session, "patchownerb@test.com")
    headers_a = _headers_for(user_a)
    headers_b = _headers_for(user_b)

    create_resp = await client.post(
        "/search/saved",
        json={"name": "A Search", "criteria": {"field": "last_name", "op": "is_set"}},
        headers=headers_a,
    )
    search_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/search/saved/{search_id}",
        json={"name": "Hacked"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


async def test_delete_saved_search(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """DELETE /search/saved/{id} → 204; subsequent GET → 404."""
    create_resp = await client.post(
        "/search/saved",
        json={"name": "To Delete", "criteria": {"field": "email", "op": "is_empty"}},
        headers=volunteer_auth_headers,
    )
    search_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/search/saved/{search_id}", headers=volunteer_auth_headers)
    assert del_resp.status_code == 204, del_resp.text

    get_resp = await client.get(f"/search/saved/{search_id}", headers=volunteer_auth_headers)
    assert get_resp.status_code == 404


async def test_cross_owner_delete_404(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """DELETE /search/saved/{id} with another user's id → 404."""
    user_a = await _make_user(db_session, "delownera@test.com")
    user_b = await _make_user(db_session, "delownerb@test.com")
    headers_a = _headers_for(user_a)
    headers_b = _headers_for(user_b)

    create_resp = await client.post(
        "/search/saved",
        json={"name": "A Delete Search", "criteria": {"field": "phone", "op": "is_set"}},
        headers=headers_a,
    )
    search_id = create_resp.json()["id"]

    resp = await client.delete(f"/search/saved/{search_id}", headers=headers_b)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Validate on save
# ---------------------------------------------------------------------------


async def test_save_validates_on_create_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/saved with invalid criteria must return 422 without persisting."""
    resp = await client.post(
        "/search/saved",
        json={
            "name": "Bad Criteria",
            "criteria": {"field": "nonexistent_xyz", "op": "eq", "value": "x"},
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text

    # Verify nothing was saved
    list_resp = await client.get("/search/saved", headers=volunteer_auth_headers)
    names = {s["name"] for s in list_resp.json()}
    assert "Bad Criteria" not in names


async def test_save_validates_bad_op_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/saved with valid field but bad op → 422."""
    resp = await client.post(
        "/search/saved",
        json={
            "name": "Bad Op",
            "criteria": {"field": "first_name", "op": "gt", "value": "x"},
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Run saved search
# ---------------------------------------------------------------------------


async def test_run_saved_search_matches_adhoc(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/saved/{id}/run must return same results as ad-hoc POST /search."""
    # Create a contact with a distinctive first name
    c = Contact(
        first_name="UniqueRunner",
        last_name="Test",
        contact_type="individual",
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)

    criteria = {"field": "first_name", "op": "eq", "value": "UniqueRunner"}

    # Save search
    create_resp = await client.post(
        "/search/saved",
        json={"name": "Runner Search", "criteria": criteria},
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    search_id = create_resp.json()["id"]

    # Run saved search
    run_resp = await client.post(
        f"/search/saved/{search_id}/run",
        headers=volunteer_auth_headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    run_body = run_resp.json()

    # Run adhoc search
    adhoc_resp = await client.post(
        "/search",
        json={"criteria": criteria, "page": 1, "page_size": 25},
        headers=volunteer_auth_headers,
    )
    adhoc_body = adhoc_resp.json()

    assert run_body["total"] == adhoc_body["total"]
    run_ids = {item["id"] for item in run_body["items"]}
    adhoc_ids = {item["id"] for item in adhoc_body["items"]}
    assert run_ids == adhoc_ids
    assert c.id in run_ids


# ---------------------------------------------------------------------------
# Promote to smart group
# ---------------------------------------------------------------------------


async def test_promote_creates_smart_group(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/saved/{id}/promote → 201 with GroupResponse of type 'smart'."""
    criteria = {"field": "is_active", "op": "eq", "value": True}

    create_resp = await client.post(
        "/search/saved",
        json={"name": "Active Members", "criteria": criteria},
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    search_id = create_resp.json()["id"]

    promote_resp = await client.post(
        f"/search/saved/{search_id}/promote",
        json={"group_name": "Active Members Group", "entity": "contact"},
        headers=volunteer_auth_headers,
    )
    assert promote_resp.status_code == 201, promote_resp.text
    body = promote_resp.json()
    assert body["group_type"] == "smart"
    assert body["name"] == "Active Members Group"
    assert body["criteria"] == criteria


async def test_promote_dup_name_409(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """Promoting to an existing group name → 409 Conflict."""
    criteria = {"field": "is_regular", "op": "eq", "value": True}

    # Create first saved search and promote
    create1 = await client.post(
        "/search/saved",
        json={"name": "Regulars Search", "criteria": criteria},
        headers=volunteer_auth_headers,
    )
    search_id_1 = create1.json()["id"]

    promote1 = await client.post(
        f"/search/saved/{search_id_1}/promote",
        json={"group_name": "Regulars Group", "entity": "contact"},
        headers=volunteer_auth_headers,
    )
    assert promote1.status_code == 201

    # Create second saved search and try to promote with same group name
    create2 = await client.post(
        "/search/saved",
        json={"name": "Regulars Search 2", "criteria": criteria},
        headers=volunteer_auth_headers,
    )
    search_id_2 = create2.json()["id"]

    promote2 = await client.post(
        f"/search/saved/{search_id_2}/promote",
        json={"group_name": "Regulars Group", "entity": "contact"},
        headers=volunteer_auth_headers,
    )
    assert promote2.status_code == 409, promote2.text
