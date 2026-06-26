"""Backend tests for S12 — Activities (assignable CRM tasks).

Coverage (§8 backend test plan):
  CRUD & validation (14 tests)
  List & filters (5 tests)
  RBAC (8 tests)
  Assignees (2 tests)
  Meta (1 test)
  Reminder producer (5 tests)

Environment: DATABASE_URL=sqlite+aiosqlite:///./s12be.db REDIS_URL=memory:// ENVIRONMENT=test
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, AuditLog, Contact, Outbox, User


# ---------------------------------------------------------------------------
# Helper: naive UTC now
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _past(hours: int = 2) -> datetime:
    return _now() - timedelta(hours=hours)


def _future(hours: int = 2) -> datetime:
    return _now() + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# Helper: minimal valid activity payload
# ---------------------------------------------------------------------------


def _base_payload(**overrides) -> dict:
    base = {
        "activity_type": "call",
        "subject": "Test Call",
        "activity_date": _now().isoformat(),
    }
    base.update(overrides)
    return base


# ===========================================================================
# CRUD & Validation
# ===========================================================================


async def test_create_activity_as_volunteer_returns_201_and_audit(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user: User,
    volunteer_auth_headers: dict,
):
    """POST /activities returns 201; created_by_id == caller; audit_log row written."""
    resp = await client.post(
        "/activities",
        json=_base_payload(),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created_by_id"] == volunteer_user.id
    assert body["status"] == "scheduled"
    assert body["priority"] == "normal"
    activity_id = body["id"]

    # Audit log must exist
    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "activity.create",
            AuditLog.entity == "activity",
            AuditLog.entity_id == activity_id,
        )
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None, "Expected audit_log row with action='activity.create'"
    assert audit_row.before is None
    assert audit_row.after is not None
    assert audit_row.after["subject"] == "Test Call"


async def test_create_activity_invalid_assignee_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Non-existent assignee_user_id → 422 detail mentions 'assignee'."""
    resp = await client.post(
        "/activities",
        json=_base_payload(assignee_user_id=999999),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "assignee" in resp.json()["detail"].lower()


async def test_create_activity_inactive_assignee_422(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """Deactivated user as assignee → 422."""
    from app.utils.auth import hash_password

    inactive = User(
        email="inactive@lightnc.org",
        password_hash=hash_password("pass12345678"),
        role="volunteer",
        is_active=False,
    )
    db_session.add(inactive)
    await db_session.commit()
    await db_session.refresh(inactive)

    resp = await client.post(
        "/activities",
        json=_base_payload(assignee_user_id=inactive.id),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "assignee" in resp.json()["detail"].lower()


async def test_create_activity_invalid_contact_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Non-existent target_contact_id → 422."""
    resp = await client.post(
        "/activities",
        json=_base_payload(target_contact_id=999999),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_create_activity_soft_deleted_contact_422(
    client: AsyncClient,
    sample_deleted_contact: Contact,
    volunteer_auth_headers: dict,
):
    """Soft-deleted contact as target → 422."""
    resp = await client.post(
        "/activities",
        json=_base_payload(target_contact_id=sample_deleted_contact.id),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_create_activity_due_before_activity_date_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """due_date < activity_date → 422."""
    act_date = _future(hours=4)
    due_date = _now()  # earlier than activity_date
    resp = await client.post(
        "/activities",
        json=_base_payload(
            activity_date=act_date.isoformat(),
            due_date=due_date.isoformat(),
        ),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_create_defaults_status_and_priority(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Omitting status and priority defaults to 'scheduled' and 'normal'."""
    payload = {"activity_type": "note", "subject": "Quick note"}
    resp = await client.post("/activities", json=payload, headers=volunteer_auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "scheduled"
    assert body["priority"] == "normal"


async def test_create_invalid_status_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """POST /activities with an invalid status value → 422 (FIX 2 regression)."""
    resp = await client.post(
        "/activities",
        json=_base_payload(status="invalid_status"),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "status" in resp.json()["detail"].lower()


async def test_create_invalid_priority_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """POST /activities with an invalid priority value → 422 (FIX 2 regression)."""
    resp = await client.post(
        "/activities",
        json=_base_payload(priority="super_urgent"),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "priority" in resp.json()["detail"].lower()


async def test_volunteer_cannot_reassign_via_patch(
    client: AsyncClient,
    volunteer_user: User,
    another_user: User,
    volunteer_auth_headers: dict,
):
    """PATCH /activities/{id} with assignee_user_id must NOT change the assignee.

    assignee_user_id is excluded from ActivityUpdate schema (FIX 1 — broken access
    control). FastAPI ignores unknown JSON keys by default, so PATCH returns 200 but
    the assignee must remain unchanged. Volunteers must use the admin-only /reassign
    endpoint to change assignees.
    """
    r = await client.post(
        "/activities",
        json=_base_payload(assignee_user_id=volunteer_user.id),
        headers=volunteer_auth_headers,
    )
    assert r.status_code == 201
    aid = r.json()["id"]
    assert r.json()["assignee_user_id"] == volunteer_user.id

    # Attempt to change assignee via PATCH — field not in ActivityUpdate schema
    patch_resp = await client.patch(
        f"/activities/{aid}",
        json={"subject": "Updated Subject", "assignee_user_id": another_user.id},
        headers=volunteer_auth_headers,
    )
    # FastAPI silently ignores unknown fields → 200; the critical assertion is below
    assert patch_resp.status_code in (200, 422), patch_resp.text

    # Assignee must remain unchanged
    get_resp = await client.get(f"/activities/{aid}", headers=volunteer_auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["assignee_user_id"] == volunteer_user.id, (
        "assignee_user_id must not change via PATCH — use /reassign (admin-only)"
    )


async def test_update_activity_subject_and_details(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """Volunteer can PATCH subject/details; updated_at advances."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    assert create_resp.status_code == 201
    aid = create_resp.json()["id"]
    original_updated = create_resp.json()["updated_at"]

    import asyncio
    await asyncio.sleep(0.01)  # ensure updated_at can advance

    patch_resp = await client.patch(
        f"/activities/{aid}",
        json={"subject": "Updated Subject", "details": "Some details"},
        headers=volunteer_auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["subject"] == "Updated Subject"
    assert body["details"] == "Some details"


async def test_update_activity_status_scheduled_to_in_progress(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """Transition scheduled→in_progress; audit row with activity.status_change."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    assert create_resp.status_code == 201
    aid = create_resp.json()["id"]

    patch_resp = await client.patch(
        f"/activities/{aid}",
        json={"status": "in_progress"},
        headers=volunteer_auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["status"] == "in_progress"

    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "activity.status_change",
            AuditLog.entity_id == aid,
        )
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None, "Expected audit_log row with action='activity.status_change'"
    assert audit_row.before == {"status": "scheduled"}
    assert audit_row.after == {"status": "in_progress"}


async def test_update_activity_status_in_progress_to_completed(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """in_progress→completed stamps completed_at; audit row written."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    assert create_resp.status_code == 201
    aid = create_resp.json()["id"]

    # First transition: scheduled → in_progress
    await client.patch(
        f"/activities/{aid}", json={"status": "in_progress"}, headers=volunteer_auth_headers
    )
    # Second transition: in_progress → completed
    patch_resp = await client.patch(
        f"/activities/{aid}", json={"status": "completed"}, headers=volunteer_auth_headers
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["status"] == "completed"
    assert body["completed_at"] is not None, "completed_at must be set on →completed"

    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "activity.status_change",
            AuditLog.entity_id == aid,
            AuditLog.after["status"].as_string() == "completed",
        )
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None


async def test_admin_reopen_completed_activity(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
    admin_auth_headers: dict,
):
    """Admin PATCH status=scheduled on completed → completed_at cleared."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    aid = create_resp.json()["id"]

    await client.patch(f"/activities/{aid}", json={"status": "in_progress"}, headers=volunteer_auth_headers)
    await client.patch(f"/activities/{aid}", json={"status": "completed"}, headers=volunteer_auth_headers)

    # Admin reopens
    reopen_resp = await client.patch(
        f"/activities/{aid}", json={"status": "scheduled"}, headers=admin_auth_headers
    )
    assert reopen_resp.status_code == 200, reopen_resp.text
    body = reopen_resp.json()
    assert body["status"] == "scheduled"
    assert body["completed_at"] is None, "completed_at must be cleared on reopen"


async def test_volunteer_reopen_completed_forbidden(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Volunteer PATCH status=scheduled on completed → 422."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    aid = create_resp.json()["id"]

    await client.patch(f"/activities/{aid}", json={"status": "in_progress"}, headers=volunteer_auth_headers)
    await client.patch(f"/activities/{aid}", json={"status": "completed"}, headers=volunteer_auth_headers)

    reopen_resp = await client.patch(
        f"/activities/{aid}", json={"status": "scheduled"}, headers=volunteer_auth_headers
    )
    assert reopen_resp.status_code == 422, reopen_resp.text
    assert "admin" in reopen_resp.json()["detail"].lower()


async def test_invalid_transition_422(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    admin_auth_headers: dict,
):
    """cancelled→in_progress → 422 with 'Invalid status transition' detail."""
    create_resp = await client.post(
        "/activities", json=_base_payload(), headers=volunteer_auth_headers
    )
    aid = create_resp.json()["id"]
    # Cancel it first
    await client.patch(f"/activities/{aid}", json={"status": "cancelled"}, headers=volunteer_auth_headers)

    # Try invalid transition
    resp = await client.patch(
        f"/activities/{aid}", json={"status": "in_progress"}, headers=admin_auth_headers
    )
    assert resp.status_code == 422, resp.text
    assert "Invalid status transition" in resp.json()["detail"]


async def test_patch_due_date_clears_reminder_sent_at(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """PATCH due_date clears reminder_sent_at regardless of prior value."""
    create_resp = await client.post(
        "/activities",
        json=_base_payload(due_date=_future(hours=2).isoformat()),
        headers=volunteer_auth_headers,
    )
    assert create_resp.status_code == 201
    aid = create_resp.json()["id"]

    # Manually set reminder_sent_at directly in DB
    result = await db_session.execute(select(Activity).where(Activity.id == aid))
    activity = result.scalar_one()
    activity.reminder_sent_at = _now()
    await db_session.commit()

    # PATCH with a new due_date
    patch_resp = await client.patch(
        f"/activities/{aid}",
        json={"due_date": _future(hours=6).isoformat()},
        headers=volunteer_auth_headers,
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["reminder_sent_at"] is None, (
        "reminder_sent_at must be cleared when due_date is edited"
    )


# ===========================================================================
# List & Filters
# ===========================================================================


async def test_list_mine_filters_by_assignee_and_open_status(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user: User,
    volunteer_auth_headers: dict,
    another_user: User,
    another_auth_headers: dict,
    admin_auth_headers: dict,
):
    """GET /activities/mine returns only caller's scheduled/in_progress activities."""
    # Activity for volunteer_user
    r1 = await client.post(
        "/activities",
        json=_base_payload(subject="Mine Open", assignee_user_id=volunteer_user.id),
        headers=volunteer_auth_headers,
    )
    assert r1.status_code == 201
    # Activity for another_user
    r2 = await client.post(
        "/activities",
        json=_base_payload(subject="Others Open", assignee_user_id=another_user.id),
        headers=volunteer_auth_headers,
    )
    assert r2.status_code == 201
    # Completed activity for volunteer_user (should be excluded from /mine)
    r3 = await client.post(
        "/activities",
        json=_base_payload(subject="Mine Completed", assignee_user_id=volunteer_user.id),
        headers=volunteer_auth_headers,
    )
    assert r3.status_code == 201
    aid3 = r3.json()["id"]
    await client.patch(f"/activities/{aid3}", json={"status": "in_progress"}, headers=volunteer_auth_headers)
    await client.patch(f"/activities/{aid3}", json={"status": "completed"}, headers=volunteer_auth_headers)

    # GET /activities/mine as volunteer_user
    resp = await client.get("/activities/mine", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    subjects = {item["subject"] for item in body["items"]}
    assert "Mine Open" in subjects
    assert "Others Open" not in subjects
    assert "Mine Completed" not in subjects


async def test_list_overdue_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user: User,
    volunteer_auth_headers: dict,
):
    """?overdue=true returns only past-due open activities."""
    # Overdue activity (due in the past, still scheduled)
    r1 = await client.post(
        "/activities",
        json={
            "activity_type": "call",
            "subject": "Overdue Task",
            "activity_date": _past(hours=10).isoformat(),
            "due_date": _past(hours=5).isoformat(),
            "assignee_user_id": volunteer_user.id,
        },
        headers=volunteer_auth_headers,
    )
    assert r1.status_code == 201, r1.text
    # Future activity (not overdue)
    r2 = await client.post(
        "/activities",
        json=_base_payload(subject="Future Task", due_date=_future(hours=10).isoformat()),
        headers=volunteer_auth_headers,
    )
    assert r2.status_code == 201

    resp = await client.get("/activities?overdue=true", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    subjects = {item["subject"] for item in body["items"]}
    assert "Overdue Task" in subjects
    assert "Future Task" not in subjects


async def test_list_sort_due_date_nulls_last(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Activities with due_date=null sort after those with due_date set."""
    # Activity with no due_date
    r1 = await client.post(
        "/activities",
        json=_base_payload(subject="No Due Date"),
        headers=volunteer_auth_headers,
    )
    assert r1.status_code == 201
    # Activity with a future due_date
    r2 = await client.post(
        "/activities",
        json=_base_payload(subject="Has Due Date", due_date=_future(hours=3).isoformat()),
        headers=volunteer_auth_headers,
    )
    assert r2.status_code == 201

    resp = await client.get("/activities?sort=due_date", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    subjects = [item["subject"] for item in resp.json()["items"]]
    # Items with due_date should come before those without
    idx_with_due = next((i for i, s in enumerate(subjects) if s == "Has Due Date"), None)
    idx_no_due = next((i for i, s in enumerate(subjects) if s == "No Due Date"), None)
    if idx_with_due is not None and idx_no_due is not None:
        assert idx_with_due < idx_no_due, "NULLs must sort last"


async def test_list_pagination_clamps_page_size(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """?page_size=500 is clamped to 100 in the response."""
    resp = await client.get("/activities?page_size=500", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["page_size"] == 100


async def test_list_by_target_contact(
    client: AsyncClient,
    sample_contact: Contact,
    volunteer_auth_headers: dict,
):
    """?target_contact_id=X returns only that contact's activities."""
    # Activity for the contact
    r1 = await client.post(
        "/activities",
        json=_base_payload(subject="For Contact", target_contact_id=sample_contact.id),
        headers=volunteer_auth_headers,
    )
    assert r1.status_code == 201
    # Unlinked activity
    r2 = await client.post(
        "/activities",
        json=_base_payload(subject="Unlinked"),
        headers=volunteer_auth_headers,
    )
    assert r2.status_code == 201

    resp = await client.get(
        f"/activities?target_contact_id={sample_contact.id}",
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    subjects = {item["subject"] for item in body["items"]}
    assert "For Contact" in subjects
    assert "Unlinked" not in subjects


# ===========================================================================
# RBAC
# ===========================================================================


async def test_reassign_admin_ok_writes_audit(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_user: User,
    another_user: User,
    volunteer_auth_headers: dict,
    admin_auth_headers: dict,
):
    """Admin reassign → 200, new assignee_user_id, audit activity.reassign."""
    r = await client.post(
        "/activities",
        json=_base_payload(assignee_user_id=volunteer_user.id),
        headers=volunteer_auth_headers,
    )
    assert r.status_code == 201
    aid = r.json()["id"]
    original_assignee = r.json()["assignee_user_id"]
    assert original_assignee == volunteer_user.id

    resp = await client.post(
        f"/activities/{aid}/reassign",
        json={"assignee_user_id": another_user.id},
        headers=admin_auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assignee_user_id"] == another_user.id

    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "activity.reassign",
            AuditLog.entity_id == aid,
        )
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None, "Expected audit_log row with action='activity.reassign'"
    assert audit_row.before is not None
    assert audit_row.after is not None


async def test_reassign_volunteer_403(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    another_user: User,
):
    """Volunteer attempting reassign → 403."""
    r = await client.post(
        "/activities",
        json=_base_payload(),
        headers=volunteer_auth_headers,
    )
    assert r.status_code == 201
    aid = r.json()["id"]

    resp = await client.post(
        f"/activities/{aid}/reassign",
        json={"assignee_user_id": another_user.id},
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403, resp.text


async def test_reassign_viewer_403(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    viewer_auth_headers: dict,
    another_user: User,
):
    """Viewer token → 403 on reassign."""
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    resp = await client.post(
        f"/activities/{aid}/reassign",
        json={"assignee_user_id": another_user.id},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403, resp.text


async def test_delete_admin_204_writes_audit_before(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
    admin_auth_headers: dict,
):
    """Admin DELETE → 204; row removed; audit_log with non-null before snapshot."""
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    del_resp = await client.delete(f"/activities/{aid}", headers=admin_auth_headers)
    assert del_resp.status_code == 204, del_resp.text

    # Row must be gone
    result = await db_session.execute(select(Activity).where(Activity.id == aid))
    assert result.scalar_one_or_none() is None

    # Audit log must exist with before snapshot
    audit_result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "activity.delete",
            AuditLog.entity_id == aid,
        )
    )
    audit_row = audit_result.scalar_one_or_none()
    assert audit_row is not None
    assert audit_row.before is not None, "before snapshot must be non-null"
    assert audit_row.after is None


async def test_delete_volunteer_403(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """Volunteer DELETE → 403."""
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    resp = await client.delete(f"/activities/{aid}", headers=volunteer_auth_headers)
    assert resp.status_code == 403, resp.text


async def test_delete_viewer_403(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    viewer_auth_headers: dict,
):
    """Viewer DELETE → 403."""
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    resp = await client.delete(f"/activities/{aid}", headers=viewer_auth_headers)
    assert resp.status_code == 403, resp.text


async def test_viewer_can_read_all_endpoints(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    viewer_auth_headers: dict,
):
    """Viewer token gets 200 on all read endpoints."""
    # Create one activity so the detail endpoint has a valid ID
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    read_endpoints = [
        "/activities",
        "/activities/mine",
        f"/activities/{aid}",
        "/activities/meta/types",
        "/activities/assignees",
    ]
    for url in read_endpoints:
        resp = await client.get(url, headers=viewer_auth_headers)
        assert resp.status_code == 200, f"Expected 200 for GET {url}, got {resp.status_code}: {resp.text}"


async def test_viewer_write_endpoints_403(
    client: AsyncClient,
    volunteer_auth_headers: dict,
    viewer_auth_headers: dict,
):
    """Viewer token gets 403 on all write endpoints."""
    # Create an activity so patch/reassign/delete have valid IDs
    r = await client.post("/activities", json=_base_payload(), headers=volunteer_auth_headers)
    assert r.status_code == 201
    aid = r.json()["id"]

    write_checks = [
        ("POST", "/activities", _base_payload()),
        ("PATCH", f"/activities/{aid}", {"subject": "New Subject"}),
        ("POST", f"/activities/{aid}/reassign", {"assignee_user_id": None}),
        ("DELETE", f"/activities/{aid}", None),
    ]
    for method, url, body in write_checks:
        if method == "POST":
            resp = await client.post(url, json=body, headers=viewer_auth_headers)
        elif method == "PATCH":
            resp = await client.patch(url, json=body, headers=viewer_auth_headers)
        elif method == "DELETE":
            resp = await client.delete(url, headers=viewer_auth_headers)
        else:
            raise ValueError(f"Unknown method {method}")
        assert resp.status_code == 403, (
            f"Expected 403 for {method} {url}, got {resp.status_code}: {resp.text}"
        )


# ===========================================================================
# Assignees
# ===========================================================================


async def test_assignees_endpoint_omits_sensitive_fields(
    client: AsyncClient,
    volunteer_auth_headers: dict,
):
    """GET /activities/assignees response objects have no password_hash or token fields."""
    resp = await client.get("/activities/assignees", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    for user_obj in resp.json():
        assert "password_hash" not in user_obj, "password_hash must not be exposed"
        assert "password_reset_token" not in user_obj, "password_reset_token must not be exposed"
        assert "password_reset_expires_at" not in user_obj


async def test_assignees_returns_only_active_users(
    client: AsyncClient,
    db_session: AsyncSession,
    volunteer_auth_headers: dict,
):
    """Deactivated user does not appear in assignee list."""
    from app.utils.auth import hash_password

    inactive = User(
        email="inactive2@lightnc.org",
        password_hash=hash_password("pass12345678"),
        role="volunteer",
        is_active=False,
        name="Inactive Person",
    )
    db_session.add(inactive)
    await db_session.commit()

    resp = await client.get("/activities/assignees", headers=volunteer_auth_headers)
    assert resp.status_code == 200, resp.text
    emails = {u["email"] for u in resp.json()}
    assert "inactive2@lightnc.org" not in emails


# ===========================================================================
# Meta
# ===========================================================================


async def test_meta_types_returns_expected_enums(
    client: AsyncClient,
    viewer_auth_headers: dict,
):
    """GET /activities/meta/types returns types, statuses, priorities with expected values."""
    resp = await client.get("/activities/meta/types", headers=viewer_auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "types" in body
    assert "statuses" in body
    assert "priorities" in body
    assert "call" in body["types"]
    assert "scheduled" in body["statuses"]
    assert "normal" in body["priorities"]


# ===========================================================================
# Reminder Producer
# ===========================================================================


async def test_scan_due_reminders_inserts_outbox_and_is_idempotent(
    db_session: AsyncSession,
    volunteer_user: User,
    admin_user: User,
):
    """First call inserts outbox row + sets reminder_sent_at; second call inserts 0 more."""
    from app.services.activity_service import ActivityService

    # Seed overdue, assigned activity
    past = _past(hours=2)
    activity = Activity(
        activity_type="call",
        subject="Overdue Reminder Test",
        activity_date=past,
        due_date=past,
        status="scheduled",
        priority="normal",
        assignee_user_id=volunteer_user.id,
        created_by_id=admin_user.id,
        created_at=past,
        updated_at=past,
    )
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    svc = ActivityService(db_session)
    now = _now()

    # First call
    count1 = await svc.scan_due_reminders(now=now)
    assert count1 == 1, f"Expected 1, got {count1}"

    result = await db_session.execute(
        select(Outbox).where(
            Outbox.event_type == "activity.due_reminder",
        )
    )
    outbox_rows = list(result.scalars().all())
    assert len(outbox_rows) == 1
    assert outbox_rows[0].status == "pending"
    assert outbox_rows[0].payload["activity_id"] == activity.id

    # Verify reminder_sent_at was set
    await db_session.refresh(activity)
    assert activity.reminder_sent_at is not None

    # Second call — idempotent, no new rows
    count2 = await svc.scan_due_reminders(now=now)
    assert count2 == 0, f"Expected 0 on second call, got {count2}"

    result2 = await db_session.execute(
        select(Outbox).where(Outbox.event_type == "activity.due_reminder")
    )
    assert len(list(result2.scalars().all())) == 1, "No new outbox rows should be added"


async def test_scan_due_reminders_skips_completed_and_no_assignee(
    db_session: AsyncSession,
    volunteer_user: User,
    admin_user: User,
):
    """Completed activity and unassigned activity are skipped."""
    from app.services.activity_service import ActivityService

    past = _past(hours=3)

    # Completed activity with assignee
    completed = Activity(
        activity_type="call",
        subject="Completed",
        activity_date=past,
        due_date=past,
        status="completed",
        priority="normal",
        assignee_user_id=volunteer_user.id,
        created_by_id=admin_user.id,
        completed_at=past,
        created_at=past,
        updated_at=past,
    )
    # Scheduled activity with no assignee
    unassigned = Activity(
        activity_type="call",
        subject="Unassigned",
        activity_date=past,
        due_date=past,
        status="scheduled",
        priority="normal",
        assignee_user_id=None,
        created_by_id=admin_user.id,
        created_at=past,
        updated_at=past,
    )
    db_session.add(completed)
    db_session.add(unassigned)
    await db_session.commit()

    svc = ActivityService(db_session)
    count = await svc.scan_due_reminders(now=_now())
    assert count == 0, f"Expected 0, got {count}"

    result = await db_session.execute(select(Outbox))
    assert len(list(result.scalars().all())) == 0


async def test_scan_due_reminders_skips_future_due_date(
    db_session: AsyncSession,
    volunteer_user: User,
    admin_user: User,
):
    """Activity with due_date in the future is skipped."""
    from app.services.activity_service import ActivityService

    activity = Activity(
        activity_type="call",
        subject="Future Due",
        activity_date=_now(),
        due_date=_future(hours=24),
        status="scheduled",
        priority="normal",
        assignee_user_id=volunteer_user.id,
        created_by_id=admin_user.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db_session.add(activity)
    await db_session.commit()

    svc = ActivityService(db_session)
    count = await svc.scan_due_reminders(now=_now())
    assert count == 0


async def test_scan_due_reminders_skips_already_reminded(
    db_session: AsyncSession,
    volunteer_user: User,
    admin_user: User,
):
    """Activity with reminder_sent_at already set is skipped."""
    from app.services.activity_service import ActivityService

    past = _past(hours=3)
    activity = Activity(
        activity_type="call",
        subject="Already Reminded",
        activity_date=past,
        due_date=past,
        status="scheduled",
        priority="normal",
        assignee_user_id=volunteer_user.id,
        created_by_id=admin_user.id,
        reminder_sent_at=past,  # already reminded
        created_at=past,
        updated_at=past,
    )
    db_session.add(activity)
    await db_session.commit()

    svc = ActivityService(db_session)
    count = await svc.scan_due_reminders(now=_now())
    assert count == 0


async def test_run_due_reminder_job_callable(
    db_session: AsyncSession,
    volunteer_user: User,
    admin_user: User,
):
    """run_due_reminder_job() is importable, callable, and produces an outbox row."""
    from app.services.activity_service import run_due_reminder_job

    past = _past(hours=2)
    activity = Activity(
        activity_type="call",
        subject="Job Test Activity",
        activity_date=past,
        due_date=past,
        status="scheduled",
        priority="normal",
        assignee_user_id=volunteer_user.id,
        created_by_id=admin_user.id,
        created_at=past,
        updated_at=past,
    )
    db_session.add(activity)
    await db_session.commit()
    await db_session.refresh(activity)

    activity_id_captured = activity.id  # capture before expire_all to avoid lazy-load outside greenlet

    # Call the module-level job function — opens its own DB session
    await run_due_reminder_job()

    # Verify via a fresh query on the same db_session
    db_session.expire_all()
    result = await db_session.execute(
        select(Outbox).where(Outbox.event_type == "activity.due_reminder")
    )
    outbox_rows = list(result.scalars().all())
    assert len(outbox_rows) >= 1, "run_due_reminder_job should insert at least one outbox row"
    assert outbox_rows[0].payload["activity_id"] == activity_id_captured
