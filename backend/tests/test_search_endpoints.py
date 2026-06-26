"""HTTP integration tests for the search API endpoints (S09).

Coverage:
  test_get_fields_shape               — GET /search/fields returns expected structure
  test_search_paginated_shape         — POST /search returns paginated ContactListItem
  test_search_total_accurate          — total reflects actual match count
  test_search_validate_200            — POST /search/validate with valid criteria → 200
  test_search_validate_422            — POST /search/validate with invalid → 422
  test_search_custom_field_filter     — filtering on custom.pepsol field
  test_search_require_volunteer_401   — unauthenticated → 401
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact


# ---------------------------------------------------------------------------
# GET /search/fields
# ---------------------------------------------------------------------------


async def test_get_fields_shape(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """GET /search/fields must return a list of field descriptors with required keys."""
    resp = await client.get("/search/fields", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "fields" in body
    fields = body["fields"]
    assert isinstance(fields, list)
    assert len(fields) > 0

    # Verify required keys on every entry
    for f in fields:
        assert "key" in f
        assert "label" in f
        assert "kind" in f
        assert "type" in f
        assert "ops" in f
        assert "nullable" in f
        assert isinstance(f["ops"], list)
        assert f["kind"] in ("core", "derived", "custom")

    # Some well-known core fields must be present
    keys = {f["key"] for f in fields}
    assert "first_name" in keys
    assert "last_name" in keys
    assert "email" in keys
    # Derived fields
    assert "tier" in keys
    assert "is_active" in keys


async def test_get_fields_includes_custom_fields(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_custom_group,
    sample_select_field,
):
    """After a custom field is defined, GET /search/fields must include it."""
    resp = await client.get("/search/fields", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    fields = resp.json()["fields"]
    keys = {f["key"] for f in fields}
    assert "custom.pepsol" in keys

    # Check that the custom field spec has options
    pepsol = next(f for f in fields if f["key"] == "custom.pepsol")
    assert pepsol["type"] == "enum"
    assert pepsol["kind"] == "custom"
    assert isinstance(pepsol["options"], list)
    assert len(pepsol["options"]) > 0


# ---------------------------------------------------------------------------
# POST /search/validate
# ---------------------------------------------------------------------------


async def test_search_validate_valid_criteria_200(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/validate with valid criteria returns 200."""
    resp = await client.post(
        "/search/validate",
        json={"criteria": {"field": "first_name", "op": "contains", "value": "Juan"}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def test_search_validate_invalid_field_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/validate with unknown field returns 422."""
    resp = await client.post(
        "/search/validate",
        json={"criteria": {"field": "nonexistent_xyz", "op": "eq", "value": "x"}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_search_validate_bad_op_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """POST /search/validate with disallowed operator returns 422."""
    resp = await client.post(
        "/search/validate",
        json={"criteria": {"field": "first_name", "op": "between", "value": [1, 2]}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------


async def test_search_paginated_shape(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_contact,
):
    """POST /search returns PaginatedContactResponse shape."""
    resp = await client.post(
        "/search",
        json={"criteria": None, "page": 1, "page_size": 10},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total" in body
    assert "page" in body
    assert "page_size" in body
    assert "items" in body
    assert isinstance(body["items"], list)

    if body["items"]:
        item = body["items"][0]
        assert "id" in item
        assert "display_name" in item
        assert "contact_type" in item


async def test_search_total_accurate(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """The total field must match the actual count of matching contacts."""
    # Create 3 contacts with distinct first_name prefix
    for i in range(3):
        c = Contact(
            first_name=f"Searchable{i}",
            last_name="Person",
            contact_type="individual",
        )
        db_session.add(c)
    await db_session.commit()

    resp = await client.post(
        "/search",
        json={
            "criteria": {
                "field": "first_name",
                "op": "starts_with",
                "value": "Searchable",
            },
            "page": 1,
            "page_size": 10,
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


async def test_search_excludes_deleted_by_default(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_deleted_contact,
    sample_contact,
):
    """POST /search must exclude soft-deleted contacts by default."""
    resp = await client.post(
        "/search",
        json={"criteria": None, "page": 1, "page_size": 100},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert sample_deleted_contact.id not in ids
    assert sample_contact.id in ids


async def test_search_include_deleted_volunteer_ignored(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_deleted_contact,
):
    """Volunteers cannot use include_deleted=True — it must be silently ignored."""
    resp = await client.post(
        "/search",
        json={"criteria": None, "page": 1, "page_size": 100, "include_deleted": True},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    # Deleted contact must NOT appear for volunteer even with include_deleted=True
    assert sample_deleted_contact.id not in ids


async def test_search_include_deleted_admin_works(
    client: AsyncClient,
    admin_auth_headers: dict,
    db_session: AsyncSession,
    sample_deleted_contact,
):
    """Admins can use include_deleted=True to see soft-deleted contacts."""
    resp = await client.post(
        "/search",
        json={"criteria": None, "page": 1, "page_size": 100, "include_deleted": True},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert sample_deleted_contact.id in ids


async def test_search_pagination_page_size_clamped(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """page_size > 100 must be rejected by schema validation (422)."""
    resp = await client.post(
        "/search",
        json={"criteria": None, "page": 1, "page_size": 500},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Custom field JSONB filter
# ---------------------------------------------------------------------------


async def test_search_custom_field_filter(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_custom_group,
    sample_select_field,
):
    """Filtering on a custom select field must return only matching contacts."""
    # Create a contact with pepsol=stub_val
    c_match = Contact(
        first_name="Match",
        last_name="Person",
        contact_type="individual",
        custom_data={"pepsol": "stub_val"},
    )
    c_no_match = Contact(
        first_name="NoMatch",
        last_name="Person",
        contact_type="individual",
        custom_data={"pepsol": "other"},
    )
    db_session.add(c_match)
    db_session.add(c_no_match)
    await db_session.commit()
    await db_session.refresh(c_match)
    await db_session.refresh(c_no_match)

    resp = await client.post(
        "/search",
        json={
            "criteria": {"field": "custom.pepsol", "op": "eq", "value": "stub_val"},
            "page": 1,
            "page_size": 50,
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert c_match.id in ids
    assert c_no_match.id not in ids


# ---------------------------------------------------------------------------
# Auth requirement
# ---------------------------------------------------------------------------


async def test_search_fields_require_auth_401(client: AsyncClient, db_session: AsyncSession):
    """GET /search/fields without a token must return 401."""
    resp = await client.get("/search/fields")
    assert resp.status_code == 401, resp.text


async def test_search_require_volunteer_401(client: AsyncClient, db_session: AsyncSession):
    """POST /search without a token must return 401."""
    resp = await client.post("/search", json={"criteria": None})
    assert resp.status_code == 401, resp.text


async def test_search_viewer_gets_403(
    client: AsyncClient,
    viewer_auth_headers: dict,
    db_session: AsyncSession,
):
    """A viewer-role user must receive 403 on POST /search."""
    resp = await client.post(
        "/search",
        json={"criteria": None},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403, resp.text
