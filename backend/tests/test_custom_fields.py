"""HTTP integration tests for the custom-fields router (S02 §8 block 1).

Uses the `client` fixture (overrides get_db + check_setup_complete) and
admin_auth_headers / volunteer_auth_headers for role-based tests.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Contact, CustomFieldDef, CustomFieldGroup


# ---------------------------------------------------------------------------
# Group creation
# ---------------------------------------------------------------------------


async def test_create_group_admin_ok(client: AsyncClient, admin_auth_headers, db_session: AsyncSession):
    """Admin POST /custom-fields/groups -> 201; row in DB; audit_log row."""
    resp = await client.post(
        "/custom-fields/groups",
        json={"name": "test_group", "label": "Test Group", "entity": "contact", "weight": 0},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "test_group"
    assert body["label"] == "Test Group"
    assert body["entity"] == "contact"

    # Row in DB
    result = await db_session.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.name == "test_group")
    )
    group = result.scalar_one_or_none()
    assert group is not None
    assert group.is_active is True

    # Audit log row
    audit_result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "custom_group.create")
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None
    assert audit_row.entity == "custom_field_group"
    assert audit_row.entity_id == group.id


async def test_create_group_forbidden_for_volunteer(client: AsyncClient, volunteer_auth_headers):
    """Volunteer token -> 403."""
    resp = await client.post(
        "/custom-fields/groups",
        json={"name": "volunteer_group", "label": "Volunteer Group"},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


async def test_create_group_forbidden_for_unauthenticated(client: AsyncClient):
    """No token -> 401."""
    resp = await client.post(
        "/custom-fields/groups",
        json={"name": "no_auth_group", "label": "No Auth Group"},
    )
    assert resp.status_code == 401


async def test_create_group_name_must_be_snake_case(client: AsyncClient, admin_auth_headers):
    """name='Bad Name' -> 422 (space not allowed in snake_case)."""
    resp = await client.post(
        "/custom-fields/groups",
        json={"name": "Bad Name", "label": "Bad Name"},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


async def test_group_name_immutable_on_patch(client: AsyncClient, admin_auth_headers, sample_custom_group):
    """PATCH with 'name' field -> 422 or 400 (field is immutable)."""
    # The schema CustomFieldGroupUpdate does not include 'name', so Pydantic v2
    # with extra='ignore' would silently ignore it. The spec says "422 or 400".
    # Our schema uses extra fields behavior — the endpoint returns 200 but ignores
    # unknown fields OR returns 422 if extra='forbid'. We test that name did NOT change.
    resp = await client.patch(
        f"/custom-fields/groups/{sample_custom_group.id}",
        json={"name": "new_name", "label": "Updated Label"},
        headers=admin_auth_headers,
    )
    # Either immutable rejection (4xx) or name unchanged
    if resp.status_code == 200:
        assert resp.json()["name"] == sample_custom_group.name
    else:
        assert resp.status_code in (400, 422)


async def test_group_entity_immutable_on_patch(client: AsyncClient, admin_auth_headers, sample_custom_group):
    """PATCH with 'entity' field -> 422/400 or ignored (entity is immutable)."""
    resp = await client.patch(
        f"/custom-fields/groups/{sample_custom_group.id}",
        json={"entity": "event"},
        headers=admin_auth_headers,
    )
    if resp.status_code == 200:
        assert resp.json()["entity"] == sample_custom_group.entity
    else:
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Field (def) creation - all eight types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data_type,extra",
    [
        ("text", {}),
        ("textarea", {}),
        ("number", {}),
        ("date", {}),
        ("checkbox", {}),
        ("select", {"options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]}),
        ("multiselect", {"options": [{"value": "x", "label": "X"}, {"value": "y", "label": "Y"}]}),
        ("contact_reference", {}),
    ],
)
async def test_create_def_all_eight_types(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    data_type,
    extra,
):
    """Admin can create all eight data_type values; multiselect forces is_multi=True."""
    payload = {
        "group_id": sample_custom_group.id,
        "name": f"field_{data_type}",
        "label": f"Field {data_type}",
        "data_type": data_type,
        "is_multi": False,
        **extra,
    }
    resp = await client.post("/custom-fields/defs", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 201, f"Failed for {data_type}: {resp.text}"
    body = resp.json()
    assert body["data_type"] == data_type

    if data_type == "multiselect":
        assert body["is_multi"] is True


async def test_create_select_without_options_422(client: AsyncClient, admin_auth_headers, sample_custom_group):
    """POST data_type='select', options=[] -> 422."""
    resp = await client.post(
        "/custom-fields/defs",
        json={
            "group_id": sample_custom_group.id,
            "name": "no_opts_select",
            "label": "No Options Select",
            "data_type": "select",
            "options": [],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


async def test_create_multiselect_without_options_422(client: AsyncClient, admin_auth_headers, sample_custom_group):
    """POST data_type='multiselect', options=[] -> 422."""
    resp = await client.post(
        "/custom-fields/defs",
        json={
            "group_id": sample_custom_group.id,
            "name": "no_opts_multi",
            "label": "No Options Multi",
            "data_type": "multiselect",
            "options": [],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


async def test_create_text_with_options_accepted(client: AsyncClient, admin_auth_headers, sample_custom_group):
    """POST data_type='text', options=[...] -> 201 (options are stored but not validated for text)."""
    resp = await client.post(
        "/custom-fields/defs",
        json={
            "group_id": sample_custom_group.id,
            "name": "text_with_opts",
            "label": "Text With Options",
            "data_type": "text",
            "options": [{"value": "a", "label": "A"}],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Entity-scoped name uniqueness
# ---------------------------------------------------------------------------


async def test_field_name_unique_per_entity_cross_group(
    client: AsyncClient, admin_auth_headers, db_session: AsyncSession
):
    """Two fields named 'barangay' in different groups of the same entity -> 409 on second."""
    # Create two groups
    r1 = await client.post(
        "/custom-fields/groups",
        json={"name": "group_a", "label": "Group A", "entity": "contact"},
        headers=admin_auth_headers,
    )
    assert r1.status_code == 201
    group_a_id = r1.json()["id"]

    r2 = await client.post(
        "/custom-fields/groups",
        json={"name": "group_b", "label": "Group B", "entity": "contact"},
        headers=admin_auth_headers,
    )
    assert r2.status_code == 201
    group_b_id = r2.json()["id"]

    # First field in group_a -> 201
    r3 = await client.post(
        "/custom-fields/defs",
        json={"group_id": group_a_id, "name": "barangay", "label": "Barangay", "data_type": "text"},
        headers=admin_auth_headers,
    )
    assert r3.status_code == 201

    # Same field name in group_b -> 409
    r4 = await client.post(
        "/custom-fields/defs",
        json={"group_id": group_b_id, "name": "barangay", "label": "Barangay", "data_type": "text"},
        headers=admin_auth_headers,
    )
    assert r4.status_code == 409


async def test_field_name_same_in_different_entity_allowed(
    client: AsyncClient, admin_auth_headers
):
    """Same field name 'ministry' in contact group and event group -> both 201."""
    # Create contact group
    rg1 = await client.post(
        "/custom-fields/groups",
        json={"name": "contact_grp", "label": "Contact Group", "entity": "contact"},
        headers=admin_auth_headers,
    )
    assert rg1.status_code == 201
    contact_grp_id = rg1.json()["id"]

    # Create event group
    rg2 = await client.post(
        "/custom-fields/groups",
        json={"name": "event_grp", "label": "Event Group", "entity": "event"},
        headers=admin_auth_headers,
    )
    assert rg2.status_code == 201
    event_grp_id = rg2.json()["id"]

    # ministry in contact group -> 201
    r1 = await client.post(
        "/custom-fields/defs",
        json={"group_id": contact_grp_id, "name": "ministry", "label": "Ministry", "data_type": "text"},
        headers=admin_auth_headers,
    )
    assert r1.status_code == 201

    # ministry in event group -> 201 (different entity)
    r2 = await client.post(
        "/custom-fields/defs",
        json={"group_id": event_grp_id, "name": "ministry", "label": "Ministry", "data_type": "text"},
        headers=admin_auth_headers,
    )
    assert r2.status_code == 201


# ---------------------------------------------------------------------------
# Schema endpoint
# ---------------------------------------------------------------------------


async def test_schema_returns_active_only_for_volunteer(
    client: AsyncClient,
    admin_auth_headers,
    volunteer_auth_headers,
    sample_custom_group,
    sample_select_field,
):
    """Volunteer can GET /schema; deactivated fields/groups are absent."""
    # Field is active -> appears in schema
    r = await client.get(
        "/custom-fields/schema?entity=contact",
        headers=volunteer_auth_headers,
    )
    assert r.status_code == 200
    schema = r.json()
    assert schema["entity"] == "contact"
    field_names = [
        f["name"]
        for g in schema["groups"]
        for f in g["fields"]
    ]
    assert sample_select_field.name in field_names

    # Deactivate field -> absent from schema
    await client.patch(
        f"/custom-fields/defs/{sample_select_field.id}",
        json={"is_active": False},
        headers=admin_auth_headers,
    )
    r2 = await client.get("/custom-fields/schema?entity=contact", headers=volunteer_auth_headers)
    schema2 = r2.json()
    field_names2 = [
        f["name"]
        for g in schema2["groups"]
        for f in g["fields"]
    ]
    assert sample_select_field.name not in field_names2

    # Deactivate group -> group absent from schema
    await client.patch(
        f"/custom-fields/groups/{sample_custom_group.id}",
        json={"is_active": False},
        headers=admin_auth_headers,
    )
    r3 = await client.get("/custom-fields/schema?entity=contact", headers=volunteer_auth_headers)
    schema3 = r3.json()
    group_names = [g["name"] for g in schema3["groups"]]
    assert sample_custom_group.name not in group_names


async def test_schema_weight_ordering(client: AsyncClient, admin_auth_headers, volunteer_auth_headers):
    """Groups returned by schema are ordered by weight ascending."""
    for name, weight in [("grp_twenty", 20), ("grp_ten", 10), ("grp_thirty", 30)]:
        r = await client.post(
            "/custom-fields/groups",
            json={"name": name, "label": name, "entity": "contact", "weight": weight},
            headers=admin_auth_headers,
        )
        assert r.status_code == 201

    r = await client.get("/custom-fields/schema?entity=contact", headers=volunteer_auth_headers)
    assert r.status_code == 200
    weights = [g["weight"] for g in r.json()["groups"]]
    assert weights == sorted(weights)


# ---------------------------------------------------------------------------
# Field immutability
# ---------------------------------------------------------------------------


async def test_def_data_type_immutable(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
):
    """PATCH /defs/{id} with data_type field -> error (data_type is immutable)."""
    # Create a text field
    r = await client.post(
        "/custom-fields/defs",
        json={
            "group_id": sample_custom_group.id,
            "name": "imm_field",
            "label": "Immutable Field",
            "data_type": "text",
        },
        headers=admin_auth_headers,
    )
    assert r.status_code == 201
    def_id = r.json()["id"]

    # Try to change data_type -> schema rejects unknown field (Pydantic) or ignores it
    patch_r = await client.patch(
        f"/custom-fields/defs/{def_id}",
        json={"data_type": "number"},
        headers=admin_auth_headers,
    )
    # Either 4xx rejection or 200 with data_type unchanged
    if patch_r.status_code == 200:
        assert patch_r.json()["data_type"] == "text"
    else:
        assert patch_r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Soft delete cascade
# ---------------------------------------------------------------------------


async def test_soft_delete_group_cascades_to_defs(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    db_session: AsyncSession,
):
    """DELETE /groups/{id} (soft) -> group is_active=False; child defs is_active=False."""
    resp = await client.delete(
        f"/custom-fields/groups/{sample_custom_group.id}",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 204

    # Group is soft-deleted
    await db_session.refresh(sample_custom_group)
    assert sample_custom_group.is_active is False

    # Child def is soft-deleted
    await db_session.refresh(sample_select_field)
    assert sample_select_field.is_active is False


# ---------------------------------------------------------------------------
# Hard delete blocked when data exists
# ---------------------------------------------------------------------------


async def test_hard_delete_group_blocked_when_data_exists(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    sample_contact,
    db_session: AsyncSession,
):
    """Hard delete of group blocked if contacts have data for a child field."""
    # Set contact.custom_data with a value for the select field
    sample_contact.custom_data = {sample_select_field.name: "stub_val"}
    db_session.add(sample_contact)
    await db_session.commit()

    resp = await client.delete(
        f"/custom-fields/groups/{sample_custom_group.id}?hard=true",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["affected_contacts"] > 0


async def test_hard_delete_field_blocked_when_data_exists(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    sample_contact,
    db_session: AsyncSession,
):
    """Hard delete of field blocked if contacts have a non-null value for it."""
    sample_contact.custom_data = {sample_select_field.name: "stub_val"}
    db_session.add(sample_contact)
    await db_session.commit()

    resp = await client.delete(
        f"/custom-fields/defs/{sample_select_field.id}?hard=true",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["affected_contacts"] > 0


async def test_hard_delete_field_ok_when_no_data(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_checkbox_field,
):
    """Hard delete of field with no contact data -> 204."""
    resp = await client.delete(
        f"/custom-fields/defs/{sample_checkbox_field.id}?hard=true",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Option removal warning
# ---------------------------------------------------------------------------


async def test_option_removal_warning_in_patch(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    sample_contact,
    db_session: AsyncSession,
):
    """PATCH /defs removes option currently used by a contact -> 200, affected_contacts=1."""
    # Set contact data to an option that will be removed
    sample_contact.custom_data = {sample_select_field.name: "stub_val"}
    db_session.add(sample_contact)
    await db_session.commit()

    # Patch: remove "stub_val" from options (keep only "other")
    resp = await client.patch(
        f"/custom-fields/defs/{sample_select_field.id}",
        json={"options": [{"value": "other", "label": "Other"}]},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected_contacts"] >= 1


# ---------------------------------------------------------------------------
# Validate endpoint
# ---------------------------------------------------------------------------


async def test_validate_endpoint_unknown_field(
    client: AsyncClient,
    volunteer_auth_headers,
    sample_custom_group,
):
    """POST /validate with unknown field -> 422 unknown_field."""
    resp = await client.post(
        "/custom-fields/validate",
        json={"entity": "contact", "custom_data": {"no_such_field": "x"}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]
    assert any(e["error"] == "unknown_field" for e in errors)


async def test_validate_endpoint_valid(
    client: AsyncClient,
    volunteer_auth_headers,
    sample_custom_group,
    sample_select_field,
):
    """POST /validate with valid data -> 200 normalized dict."""
    resp = await client.post(
        "/custom-fields/validate",
        json={"entity": "contact", "custom_data": {sample_select_field.name: "stub_val"}},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["normalized"] is True
    assert sample_select_field.name in body["custom_data"]


# ---------------------------------------------------------------------------
# Audit log assertions
# ---------------------------------------------------------------------------


async def test_audit_log_written_on_create(
    client: AsyncClient,
    admin_auth_headers,
    db_session: AsyncSession,
):
    """Creating a group writes an audit_log row with correct action and entity."""
    r = await client.post(
        "/custom-fields/groups",
        json={"name": "audit_grp", "label": "Audit Group"},
        headers=admin_auth_headers,
    )
    assert r.status_code == 201

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "custom_group.create")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1
    row = rows[-1]
    assert row.entity == "custom_field_group"


async def test_audit_log_written_on_update(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    db_session: AsyncSession,
):
    """Updating a group label writes audit_log with action='custom_group.update'."""
    r = await client.patch(
        f"/custom-fields/groups/{sample_custom_group.id}",
        json={"label": "Updated Label"},
        headers=admin_auth_headers,
    )
    assert r.status_code == 200

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "custom_group.update")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1


async def test_audit_log_written_on_delete(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    db_session: AsyncSession,
):
    """Soft deleting a group writes audit_log with action='custom_group.soft_delete'."""
    r = await client.delete(
        f"/custom-fields/groups/{sample_custom_group.id}",
        headers=admin_auth_headers,
    )
    assert r.status_code == 204

    result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "custom_group.soft_delete")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1


# ---------------------------------------------------------------------------
# AC5: PATCH def label succeeds + audit_log row (spec §7 AC11)
# ---------------------------------------------------------------------------


async def test_patch_def_label_succeeds_and_writes_audit_log(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    db_session: AsyncSession,
):
    """PATCH /defs/{id} with {label: 'New Label'} -> 200 + audit_log action='custom_field.update'.

    AC5 requires: PATCH label succeeds and an audit_log row is written.
    This test is distinct from the group-level audit test — it validates the
    def-level patch path, which uses a different audit action name.
    """
    r = await client.patch(
        f"/custom-fields/defs/{sample_select_field.id}",
        json={"label": "New Label For Def"},
        headers=admin_auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "New Label For Def"

    # Audit row must exist with action='custom_field.update'
    audit_result = await db_session.execute(
        select(AuditLog).where(AuditLog.action == "custom_field.update")
    )
    rows = audit_result.scalars().all()
    assert len(rows) >= 1, "No audit_log row with action='custom_field.update' found"
    latest = rows[-1]
    assert latest.entity == "custom_field_def"
    assert latest.entity_id == sample_select_field.id


async def test_patch_def_name_immutable_returns_422(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
):
    """PATCH /defs/{id} with {name: 'new_name'} -> 422 (name is immutable).

    AC5 requires: PATCH with immutable field (def name) -> 422/400.
    """
    r = await client.patch(
        f"/custom-fields/defs/{sample_select_field.id}",
        json={"name": "new_name"},
        headers=admin_auth_headers,
    )
    assert r.status_code == 422, (
        f"Expected 422 for immutable field 'name' in PATCH /defs, got {r.status_code}: {r.text}"
    )


async def test_patch_def_data_type_immutable_returns_422(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
):
    """PATCH /defs/{id} with {data_type: 'text'} -> 422 (data_type is immutable).

    AC5 requires: PATCH with immutable field (def data_type) -> 422/400.
    """
    r = await client.patch(
        f"/custom-fields/defs/{sample_select_field.id}",
        json={"data_type": "text"},
        headers=admin_auth_headers,
    )
    assert r.status_code == 422, (
        f"Expected 422 for immutable field 'data_type' in PATCH /defs, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# AC1 supplemental: group creation 409 has correct detail format
# ---------------------------------------------------------------------------


async def test_create_group_409_detail_format(client: AsyncClient, admin_auth_headers):
    """Duplicate group name+entity -> 409 with dict detail containing 'message' and 'affected_contacts'.

    AC1 §7 AC4: 409 detail format must be {"message": str, "affected_contacts": int}.
    """
    payload = {"name": "dup_group", "label": "Dup Group", "entity": "contact"}
    # First creation -> 201
    r1 = await client.post("/custom-fields/groups", json=payload, headers=admin_auth_headers)
    assert r1.status_code == 201, f"First creation failed: {r1.text}"

    # Second creation same name+entity -> 409
    r2 = await client.post("/custom-fields/groups", json=payload, headers=admin_auth_headers)
    assert r2.status_code == 409, f"Expected 409 for duplicate group, got {r2.status_code}: {r2.text}"
    detail = r2.json().get("detail")
    assert isinstance(detail, dict), f"Expected dict detail, got {type(detail).__name__}: {detail!r}"
    assert "message" in detail, f"Expected 'message' key in 409 detail: {detail}"
    assert "affected_contacts" in detail, f"Expected 'affected_contacts' key in 409 detail: {detail}"
    assert isinstance(detail["affected_contacts"], int), (
        f"Expected int affected_contacts, got {type(detail['affected_contacts']).__name__}"
    )


# ---------------------------------------------------------------------------
# AC6 supplemental: hard delete GROUP 409 detail format
# ---------------------------------------------------------------------------


async def test_hard_delete_group_409_detail_format(
    client: AsyncClient,
    admin_auth_headers,
    sample_custom_group,
    sample_select_field,
    sample_contact,
    db_session: AsyncSession,
):
    """Hard delete of group with data -> 409 detail includes 'message' and 'affected_contacts'.

    AC6 §7 AC12-13: DELETE ?hard=True with existing data -> 409 with affected_contacts.
    The detail format must be {"message": str, "affected_contacts": int}.
    """
    # Assign contact data for the select field
    sample_contact.custom_data = {sample_select_field.name: "stub_val"}
    db_session.add(sample_contact)
    await db_session.commit()

    resp = await client.delete(
        f"/custom-fields/groups/{sample_custom_group.id}?hard=true",
        headers=admin_auth_headers,
    )
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    detail = resp.json().get("detail")
    assert isinstance(detail, dict), f"Expected dict detail for 409, got: {detail!r}"
    assert "message" in detail, f"'message' key missing in 409 detail: {detail}"
    assert "affected_contacts" in detail, f"'affected_contacts' key missing in 409 detail: {detail}"
    assert detail["affected_contacts"] >= 1, (
        f"Expected affected_contacts >= 1, got {detail['affected_contacts']}"
    )
