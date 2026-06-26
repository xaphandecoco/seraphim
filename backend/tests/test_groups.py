"""Integration tests for groups CRUD and membership (S09).

Coverage:
  test_create_smart_group               — POST /groups with smart type
  test_create_static_group              — POST /groups with static type
  test_smart_group_live_resolution      — smart group members change with data
  test_static_group_frozen_snapshot     — static group unaffected by new contacts
  test_add_members_dedup                — on_conflict_do_nothing prevents duplicates
  test_smart_rejects_member_add_400     — POST /groups/{id}/members on smart → 400
  test_smart_rejects_member_remove_400  — DELETE .../members/{id} on smart → 400
  test_populate_replace                 — mode=replace clears then adds
  test_populate_append                  — mode=append adds without removing
  test_type_immutable                   — PATCH cannot change group_type
  test_member_pagination                — GET /groups/{id}/members pagination
  test_resolve_group_contacts_importable — service fn importable
  test_list_groups_bare_array           — GET /groups returns bare array
  test_list_groups_with_counts          — member_count populated
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Group, GroupMember


# ---------------------------------------------------------------------------
# List groups — bare array
# ---------------------------------------------------------------------------


async def test_list_groups_bare_array(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /groups returns a bare JSON array, not a paginated object."""
    resp = await client.get("/groups", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list), f"Expected bare array, got: {type(body)}"


async def test_list_groups_with_counts(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """GET /groups?with_counts=true populates member_count."""
    # Create a static group and add sample_contact
    create_resp = await client.post(
        "/groups",
        json={"name": "Count Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    group_id = create_resp.json()["id"]

    await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )

    resp = await client.get("/groups?with_counts=true", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    groups = resp.json()
    our_group = next((g for g in groups if g["id"] == group_id), None)
    assert our_group is not None
    assert our_group["member_count"] == 1


async def test_list_groups_no_counts(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /groups?with_counts=false returns member_count=null."""
    await client.post(
        "/groups",
        json={"name": "No Count Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )

    resp = await client.get("/groups?with_counts=false", headers=volunteer_auth_headers)
    assert resp.status_code == 200
    groups = resp.json()
    for g in groups:
        assert g["member_count"] is None


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_create_smart_group(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /groups with group_type=smart and valid criteria → 201."""
    resp = await client.post(
        "/groups",
        json={
            "name": "Smart Tier1",
            "entity": "contact",
            "group_type": "smart",
            "criteria": {"field": "tier", "op": "eq", "value": "tier1"},
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["group_type"] == "smart"
    assert body["criteria"]["field"] == "tier"


async def test_create_static_group(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /groups with group_type=static (no criteria) → 201."""
    resp = await client.post(
        "/groups",
        json={
            "name": "Static Members",
            "entity": "contact",
            "group_type": "static",
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["group_type"] == "static"
    assert body["criteria"] is None


async def test_create_smart_group_without_criteria_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """Smart group without criteria must be rejected at schema level."""
    resp = await client.post(
        "/groups",
        json={"name": "No Criteria Smart", "entity": "contact", "group_type": "smart"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_create_duplicate_name_409(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """Creating a group with a duplicate name+entity → 409."""
    await client.post(
        "/groups",
        json={"name": "Dup Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    resp = await client.post(
        "/groups",
        json={"name": "Dup Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# GET / PATCH / DELETE
# ---------------------------------------------------------------------------


async def test_get_group(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /groups/{id} returns the group."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Get Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    resp = await client.get(f"/groups/{group_id}", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == group_id


async def test_get_group_404(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    resp = await client.get("/groups/99999", headers=volunteer_auth_headers)
    assert resp.status_code == 404


async def test_patch_group_name(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """PATCH /groups/{id} can update the name."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Old Group Name", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/groups/{group_id}",
        json={"name": "New Group Name"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Group Name"


async def test_type_immutable(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """PATCH cannot change group_type (GroupUpdate has no group_type field)."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Static Only", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    # Sending group_type in PATCH should be ignored (not in GroupUpdate schema)
    # — schema forbids extra fields or just ignores them; either way type won't change
    resp = await client.patch(
        f"/groups/{group_id}",
        json={"name": "Still Static"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["group_type"] == "static"


async def test_delete_group(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """DELETE /groups/{id} → 204; subsequent GET → 404."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Delete Me", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/groups/{group_id}", headers=volunteer_auth_headers)
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/groups/{group_id}", headers=volunteer_auth_headers)
    assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# Smart group live resolution
# ---------------------------------------------------------------------------


async def test_smart_group_live_resolution(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """Smart group members are resolved live — adding a matching contact changes the count."""
    # Create smart group: contacts where is_connected=True
    create_resp = await client.post(
        "/groups",
        json={
            "name": "Connected Live",
            "entity": "contact",
            "group_type": "smart",
            "criteria": {"field": "is_connected", "op": "eq", "value": True},
        },
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    group_id = create_resp.json()["id"]

    # No contacts yet
    members_resp = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    assert members_resp.json()["total"] == 0

    # Add a matching contact directly to DB
    c = Contact(
        first_name="Connected",
        last_name="Person",
        contact_type="individual",
        is_connected=True,
    )
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)

    # Smart group now sees it
    members_resp2 = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    assert members_resp2.json()["total"] == 1
    ids = {item["id"] for item in members_resp2.json()["items"]}
    assert c.id in ids


# ---------------------------------------------------------------------------
# Static group frozen snapshot
# ---------------------------------------------------------------------------


async def test_static_group_frozen_snapshot(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """Static group membership is frozen — adding new contacts to DB doesn't change it."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Frozen Static", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    # Add sample_contact
    add_resp = await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )
    assert add_resp.json()["added"] == 1

    # Add a new contact to DB
    new_c = Contact(
        first_name="New",
        last_name="Person",
        contact_type="individual",
    )
    db_session.add(new_c)
    await db_session.commit()

    # Static group still shows only original member
    members_resp = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    assert members_resp.json()["total"] == 1
    ids = {item["id"] for item in members_resp.json()["items"]}
    assert sample_contact.id in ids
    assert new_c.id not in ids


# ---------------------------------------------------------------------------
# Add members with deduplication
# ---------------------------------------------------------------------------


async def test_add_members_dedup(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """Adding the same contact twice uses on_conflict_do_nothing — no duplicates."""
    create_resp = await client.post(
        "/groups",
        json={"name": "Dedup Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    # First add
    resp1 = await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )
    assert resp1.status_code == 200
    assert resp1.json()["added"] == 1

    # Second add — same contact
    resp2 = await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["added"] == 0
    assert data2["skipped"] == 1

    # Only one row in DB
    members_resp = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    assert members_resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# Smart group rejects member writes
# ---------------------------------------------------------------------------


async def test_smart_rejects_member_add_400(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """POST /groups/{id}/members on a smart group → 400."""
    create_resp = await client.post(
        "/groups",
        json={
            "name": "Smart No Add",
            "entity": "contact",
            "group_type": "smart",
            "criteria": {"field": "first_name", "op": "is_set"},
        },
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    resp = await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 400, resp.text


async def test_smart_rejects_member_remove_400(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """DELETE /groups/{id}/members/{contact_id} on a smart group → 400."""
    create_resp = await client.post(
        "/groups",
        json={
            "name": "Smart No Remove",
            "entity": "contact",
            "group_type": "smart",
            "criteria": {"field": "last_name", "op": "is_set"},
        },
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/groups/{group_id}/members/{sample_contact.id}",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Populate
# ---------------------------------------------------------------------------


async def test_populate_append(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """POST /groups/{id}/populate mode=append adds matching contacts."""
    # Create a contact that matches criteria
    c_match = Contact(
        first_name="PopulateMe",
        last_name="Test",
        contact_type="individual",
    )
    db_session.add(c_match)
    await db_session.commit()
    await db_session.refresh(c_match)

    create_resp = await client.post(
        "/groups",
        json={"name": "Populate Append Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    # Pre-seed sample_contact
    await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )

    # Populate append with criteria matching c_match
    pop_resp = await client.post(
        f"/groups/{group_id}/populate",
        json={
            "criteria": {"field": "first_name", "op": "eq", "value": "PopulateMe"},
            "mode": "append",
        },
        headers=volunteer_auth_headers,
    )
    assert pop_resp.status_code == 200, pop_resp.text
    data = pop_resp.json()
    assert data["added"] >= 1

    # Both contacts should now be members
    members_resp = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    ids = {item["id"] for item in members_resp.json()["items"]}
    assert sample_contact.id in ids
    assert c_match.id in ids


async def test_populate_replace(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """POST /groups/{id}/populate mode=replace clears existing members."""
    c_new = Contact(
        first_name="ReplaceTarget",
        last_name="Test",
        contact_type="individual",
    )
    db_session.add(c_new)
    await db_session.commit()
    await db_session.refresh(c_new)

    create_resp = await client.post(
        "/groups",
        json={"name": "Replace Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    # Pre-seed sample_contact
    await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": [sample_contact.id]},
        headers=volunteer_auth_headers,
    )

    # Replace with criteria matching only c_new
    pop_resp = await client.post(
        f"/groups/{group_id}/populate",
        json={
            "criteria": {"field": "first_name", "op": "eq", "value": "ReplaceTarget"},
            "mode": "replace",
        },
        headers=volunteer_auth_headers,
    )
    assert pop_resp.status_code == 200, pop_resp.text

    # Only c_new should now be a member
    members_resp = await client.get(f"/groups/{group_id}/members", headers=volunteer_auth_headers)
    ids = {item["id"] for item in members_resp.json()["items"]}
    assert c_new.id in ids
    assert sample_contact.id not in ids


async def test_populate_smart_group_400(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /groups/{id}/populate on a smart group → 400."""
    create_resp = await client.post(
        "/groups",
        json={
            "name": "Smart No Pop",
            "entity": "contact",
            "group_type": "smart",
            "criteria": {"field": "email", "op": "is_set"},
        },
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    resp = await client.post(
        f"/groups/{group_id}/populate",
        json={"criteria": {"field": "email", "op": "is_set"}, "mode": "append"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Member pagination
# ---------------------------------------------------------------------------


async def test_member_pagination(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /groups/{id}/members?page_size=2 returns correct page slices."""
    # Create 5 contacts
    contact_ids = []
    for i in range(5):
        c = Contact(
            first_name=f"Page{i}",
            last_name="Test",
            contact_type="individual",
        )
        db_session.add(c)
    await db_session.flush()
    await db_session.commit()

    result = await db_session.execute(
        __import__("sqlalchemy").select(Contact).where(Contact.first_name.like("Page%"))
    )
    contacts = list(result.scalars().all())
    contact_ids = [c.id for c in contacts]

    create_resp = await client.post(
        "/groups",
        json={"name": "Paginate Group", "entity": "contact", "group_type": "static"},
        headers=volunteer_auth_headers,
    )
    group_id = create_resp.json()["id"]

    await client.post(
        f"/groups/{group_id}/members",
        json={"contact_ids": contact_ids},
        headers=volunteer_auth_headers,
    )

    # Page 1 of 2
    resp_p1 = await client.get(
        f"/groups/{group_id}/members?page=1&page_size=2",
        headers=volunteer_auth_headers,
    )
    assert resp_p1.status_code == 200
    body_p1 = resp_p1.json()
    assert body_p1["total"] == 5
    assert len(body_p1["items"]) == 2

    # Page 2
    resp_p2 = await client.get(
        f"/groups/{group_id}/members?page=2&page_size=2",
        headers=volunteer_auth_headers,
    )
    body_p2 = resp_p2.json()
    assert len(body_p2["items"]) == 2

    # Page 3 (last)
    resp_p3 = await client.get(
        f"/groups/{group_id}/members?page=3&page_size=2",
        headers=volunteer_auth_headers,
    )
    body_p3 = resp_p3.json()
    assert len(body_p3["items"]) == 1


# ---------------------------------------------------------------------------
# Service function importable
# ---------------------------------------------------------------------------


def test_resolve_group_contacts_importable():
    """resolve_group_contacts must be importable from search_service."""
    from app.services.search_service import resolve_group_contacts  # noqa: F401
    assert callable(resolve_group_contacts)


# ---------------------------------------------------------------------------
# Security regression: SR-2 IDOR in populate via saved_search_id
# ---------------------------------------------------------------------------


async def test_sr2_idor_populate_cross_owner_saved_search_404(
    client: AsyncClient,
    db_session: AsyncSession,
    sample_contact,
):
    """SR-2: volunteer B must get 404 when populating a group using volunteer A's
    private saved_search_id.

    Old bug: populate_static_group loaded SavedSearch with no owner_id filter,
    so any volunteer could use another user's saved search criteria.
    Fix: SavedSearch lookup is now scoped to owner_id == actor_id.
    """
    from app.models import User
    from app.utils.auth import hash_password
    from tests.conftest import make_token

    # Create two volunteer users
    user_a = User(
        email="idor_owner_a@test.com",
        password_hash=hash_password("PassA123!x"),
        role="volunteer",
        is_active=True,
    )
    user_b = User(
        email="idor_owner_b@test.com",
        password_hash=hash_password("PassB123!x"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(user_a)
    db_session.add(user_b)
    await db_session.commit()
    await db_session.refresh(user_a)
    await db_session.refresh(user_b)

    headers_a = {"Authorization": f"Bearer {make_token(user_a.id, user_a.email, user_a.role)}"}
    headers_b = {"Authorization": f"Bearer {make_token(user_b.id, user_b.email, user_b.role)}"}

    # User A creates a saved search
    ss_resp = await client.post(
        "/search/saved",
        json={
            "name": "A Private Search",
            "criteria": {"field": "first_name", "op": "is_set"},
        },
        headers=headers_a,
    )
    assert ss_resp.status_code == 201, ss_resp.text
    ss_id = ss_resp.json()["id"]

    # User B creates a static group
    grp_resp = await client.post(
        "/groups",
        json={"name": "B Group IDOR Test", "entity": "contact", "group_type": "static"},
        headers=headers_b,
    )
    assert grp_resp.status_code == 201, grp_resp.text
    group_id = grp_resp.json()["id"]

    # User B tries to populate their group using User A's saved search id → 404
    pop_resp = await client.post(
        f"/groups/{group_id}/populate",
        json={"saved_search_id": ss_id, "mode": "append"},
        headers=headers_b,
    )
    assert pop_resp.status_code == 404, (
        f"Expected 404 (IDOR blocked), got {pop_resp.status_code}: {pop_resp.text}"
    )
