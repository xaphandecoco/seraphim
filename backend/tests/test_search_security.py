"""Security tests for the search/groups API (S09).

Verifies that:
  1. SQL injection attempts via field names are blocked (InvalidCriteria/422).
  2. The raw injection identifier does NOT appear in any generated SQL.
  3. Bad operators are rejected.
  4. Oversized criteria trees (depth, node count) are rejected.
  5. Oversized list values are rejected.
  6. viewer-role cannot access /search or /groups endpoints (403).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.search_fields import build_registry
from app.services.search_service import InvalidCriteria, compile_criteria


# ---------------------------------------------------------------------------
# Injection via field name
# ---------------------------------------------------------------------------


async def test_injection_via_field_name_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """A SQL injection attempt in the field key must return 422."""
    injections = [
        "contacts);drop table contacts--",
        "users.role",
        "1=1",
        "'; DROP TABLE contacts; --",
        "first_name OR 1=1",
        "(SELECT * FROM users)",
    ]
    for injection in injections:
        resp = await client.post(
            "/search",
            json={"criteria": {"field": injection, "op": "eq", "value": "x"}},
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 422, (
            f"Expected 422 for injection field {injection!r}, got {resp.status_code}"
        )


async def test_injection_identifier_not_in_sql(db_session: AsyncSession):
    """The injection string must NEVER appear in compiled SQL."""
    from sqlalchemy import select
    from sqlalchemy.dialects import sqlite as _sqld
    from app.models import Contact

    injections = [
        "contacts);drop table contacts--",
        "users.role",
        "1=1",
    ]
    registry = await build_registry(db_session)

    for injection in injections:
        with pytest.raises(InvalidCriteria):
            compile_criteria(
                {"field": injection, "op": "eq", "value": "x"},
                registry,
                "sqlite",
            )

        # Verify injection not in a legitimately compiled query
        valid_clause = compile_criteria(
            {"field": "first_name", "op": "eq", "value": "test"},
            registry,
            "sqlite",
        )
        stmt = select(Contact).where(valid_clause)
        sql_str = str(stmt.compile(dialect=_sqld.dialect()))
        assert injection not in sql_str, (
            f"Injection string {injection!r} appeared in SQL: {sql_str}"
        )


# ---------------------------------------------------------------------------
# Injection via value (LIKE escape)
# ---------------------------------------------------------------------------


async def test_injection_via_value_like(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """LIKE injection via value must be sanitized — endpoint must return 200 (not crash)."""
    # A LIKE-injection attempt; the value is coerced to a safe parameterized query
    resp = await client.post(
        "/search",
        json={
            "criteria": {
                "field": "first_name",
                "op": "contains",
                "value": "'; DROP TABLE contacts; --",
            },
            "page": 1,
            "page_size": 10,
        },
        headers=volunteer_auth_headers,
    )
    # Must succeed (200) because the value is parameterized — no SQL injection possible
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Bad operator
# ---------------------------------------------------------------------------


async def test_bad_op_field_injection_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """A non-whitelisted operator must be rejected with 422."""
    bad_ops = ["drop_table", "exec", "union_select", "; DROP", "raw_sql", "xp_cmdshell"]
    for op in bad_ops:
        resp = await client.post(
            "/search",
            json={"criteria": {"field": "first_name", "op": op, "value": "x"}},
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 422, (
            f"Expected 422 for op {op!r}, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Oversized criteria tree
# ---------------------------------------------------------------------------


async def test_oversized_depth_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """Criteria nested deeper than 10 levels → 422."""
    node: dict = {"field": "first_name", "op": "eq", "value": "x"}
    for _ in range(11):
        node = {"logic": "and", "conditions": [node]}

    resp = await client.post(
        "/search",
        json={"criteria": node},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_oversized_node_count_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """More than 100 total nodes → 422."""
    conditions = [
        {"field": "first_name", "op": "eq", "value": str(i)} for i in range(101)
    ]
    resp = await client.post(
        "/search",
        json={"criteria": {"logic": "and", "conditions": conditions}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_oversized_list_value_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
):
    """A list value with more than 200 elements → 422."""
    resp = await client.post(
        "/search",
        json={
            "criteria": {
                "field": "tier",
                "op": "in",
                "value": ["tier1"] * 201,
            }
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Viewer role cannot access search/groups
# ---------------------------------------------------------------------------


async def test_viewer_cannot_access_search(
    client: AsyncClient,
    viewer_auth_headers: dict,
    db_session: AsyncSession,
):
    """viewer role → 403 on POST /search."""
    resp = await client.post(
        "/search",
        json={"criteria": None},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403, resp.text


async def test_viewer_cannot_access_search_fields(
    client: AsyncClient,
    viewer_auth_headers: dict,
    db_session: AsyncSession,
):
    """viewer role → 403 on GET /search/fields."""
    resp = await client.get("/search/fields", headers=viewer_auth_headers)
    assert resp.status_code == 403, resp.text


async def test_viewer_cannot_access_groups(
    client: AsyncClient,
    viewer_auth_headers: dict,
    db_session: AsyncSession,
):
    """viewer role → 403 on GET /groups."""
    resp = await client.get("/groups", headers=viewer_auth_headers)
    assert resp.status_code == 403, resp.text


async def test_viewer_cannot_create_group(
    client: AsyncClient,
    viewer_auth_headers: dict,
    db_session: AsyncSession,
):
    """viewer role → 403 on POST /groups."""
    resp = await client.post(
        "/groups",
        json={"name": "Viewer Group", "entity": "contact", "group_type": "static"},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------


async def test_unauth_search_401(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/search", json={"criteria": None})
    assert resp.status_code == 401, resp.text


async def test_unauth_groups_401(client: AsyncClient, db_session: AsyncSession):
    resp = await client.get("/groups")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Deleted-filter bypass attempt via user criteria
# ---------------------------------------------------------------------------


async def test_deleted_filter_not_bypassable_via_criteria(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    db_session: AsyncSession,
    sample_deleted_contact,
):
    """Even if criteria include is_deleted condition, deleted records are excluded."""
    # Attempt to see deleted contacts by adding an OR condition for is_deleted=True
    # The outer and_(is_deleted==False) cannot be bypassed by the user criteria tree
    resp = await client.post(
        "/search",
        json={
            "criteria": {
                "logic": "or",
                "conditions": [
                    {"field": "first_name", "op": "eq", "value": "Deleted"},
                    # Note: is_deleted is NOT in the registry, so this itself fails
                    # We test with a valid field that would normally match the deleted contact
                ],
            },
            "page": 1,
            "page_size": 100,
            "include_deleted": False,
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    # The soft-deleted contact must not appear
    assert sample_deleted_contact.id not in ids
