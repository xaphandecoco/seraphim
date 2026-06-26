"""QA tests for T07 — backend/app/routers/migration.py

Validates all acceptance criteria:
1. Router prefix/tags and 5 endpoints exist.
2. Every endpoint gated with Depends(require_admin).
3. GET /batches: optional filters; limit clamped <=100; offset; started_at.desc(); ImportBatchListResponse with total.
4. GET /batches/{batch_id}: ImportBatchDetailResponse with pending_review_count; 404 on missing; dialect branch.
5. GET /batches/{batch_id}/rows: optional outcome filter; limit clamped <=200; ordered by row_number; ImportRowResultListResponse with total.
6. GET /batches/{batch_id}/report.csv: StreamingResponse text/csv with correct Content-Disposition.
7. GET /summary: latest completed per entity + total pending_reviews; MigrationSummaryResponse.
8. 401 unauthenticated, 403 volunteer, 200 admin on all endpoints.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.models import ImportBatch, ImportRowResult, NameMatchReviewQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_batch(db_session, entity="contact", mode="create", status="done", **kw):
    b = ImportBatch(
        entity=entity,
        mode=mode,
        status=status,
        total_rows=kw.get("total_rows", 10),
        created_count=kw.get("created_count", 5),
        updated_count=kw.get("updated_count", 2),
        skipped_count=kw.get("skipped_count", 1),
        error_count=kw.get("error_count", 0),
        started_at=kw.get("started_at", utc_now()),
    )
    db_session.add(b)
    await db_session.commit()
    await db_session.refresh(b)
    return b


async def _make_row(db_session, batch_id, row_number=1, outcome="created", entity_id=None, message=None):
    r = ImportRowResult(
        batch_id=batch_id,
        row_number=row_number,
        outcome=outcome,
        entity_id=entity_id,
        message=message,
    )
    db_session.add(r)
    await db_session.commit()
    await db_session.refresh(r)
    return r


# ---------------------------------------------------------------------------
# AC1: Router structure — prefix, tags, 5 endpoints
# ---------------------------------------------------------------------------

class TestRouterStructure:
    def test_prefix_and_tags(self):
        from app.routers.migration import router
        assert router.prefix == "/migration"
        assert "migration" in router.tags

    def test_five_endpoints_exist(self):
        from app.routers.migration import router
        paths = [r.path for r in router.routes if hasattr(r, "endpoint")]
        assert "/migration/batches" in paths
        assert "/migration/batches/{batch_id}" in paths
        assert "/migration/batches/{batch_id}/rows" in paths
        assert "/migration/batches/{batch_id}/report.csv" in paths
        assert "/migration/summary" in paths
        assert len([p for p in paths if p.startswith("/migration/")]) == 5

    def test_all_are_get_methods(self):
        from app.routers.migration import router
        for r in router.routes:
            if hasattr(r, "methods"):
                assert r.methods == {"GET"}, f"Expected GET only on {r.path}, got {r.methods}"


# ---------------------------------------------------------------------------
# AC2: All endpoints gated with require_admin
# ---------------------------------------------------------------------------

class TestRequireAdminGating:
    def test_all_endpoints_have_require_admin(self):
        from app.routers.migration import router
        from app.dependencies import require_admin

        for route in router.routes:
            if not hasattr(route, "endpoint"):
                continue
            sig = inspect.signature(route.endpoint)
            found = False
            for param_name, param in sig.parameters.items():
                if param.default is not inspect.Parameter.empty:
                    dep = param.default
                    if hasattr(dep, "dependency") and dep.dependency is require_admin:
                        found = True
                        break
            assert found, f"Endpoint {route.path} does not have Depends(require_admin)"


# ---------------------------------------------------------------------------
# AC8: Auth — 401 unauthenticated, 403 volunteer, 200 admin
# ---------------------------------------------------------------------------

ENDPOINTS_NO_BATCH = ["/migration/batches", "/migration/summary"]
ENDPOINTS_WITH_BATCH = [
    "/migration/batches/999",
    "/migration/batches/999/rows",
    "/migration/batches/999/report.csv",
]
ALL_ENDPOINTS = ENDPOINTS_NO_BATCH + ENDPOINTS_WITH_BATCH


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
async def test_401_unauthenticated(client, endpoint):
    """No auth header → 401."""
    resp = await client.get(endpoint)
    assert resp.status_code == 401, f"{endpoint} should return 401 without auth, got {resp.status_code}"


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ALL_ENDPOINTS)
async def test_403_volunteer(client, volunteer_auth_headers, endpoint):
    """Volunteer auth → 403."""
    resp = await client.get(endpoint, headers=volunteer_auth_headers)
    assert resp.status_code == 403, f"{endpoint} should return 403 for volunteer, got {resp.status_code}"


@pytest.mark.asyncio
async def test_200_admin_list_batches(client, admin_auth_headers):
    """Admin can list batches."""
    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_200_admin_summary(client, admin_auth_headers):
    """Admin can access summary."""
    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AC3: GET /batches — filters, limit, offset, order, response shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_batches_empty(client, admin_auth_headers):
    """Empty DB returns empty list with total=0."""
    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_list_batches_default_limit(client, admin_auth_headers, db_session):
    """Default limit is 50 (<=100)."""
    # Just check default in query params acceptance
    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] <= 100


@pytest.mark.asyncio
async def test_list_batches_limit_enforced_at_100(client, admin_auth_headers, db_session):
    """Limit is clamped to <=100: limit=100 is accepted; limit=101 is rejected or clamped.

    The router enforces this via Query(le=100) which returns 422 for >100,
    plus an internal min() as defense-in-depth. Either behavior satisfies the spec.
    """
    # Limit = 100 (max) must be accepted
    resp100 = await client.get("/migration/batches?limit=100", headers=admin_auth_headers)
    assert resp100.status_code == 200
    assert resp100.json()["limit"] == 100

    # Limit > 100: router uses le=100 Query validation → 422 (correct enforcement)
    resp_over = await client.get("/migration/batches?limit=101", headers=admin_auth_headers)
    # Either 422 (validation rejects) or 200 with clamped 100 is acceptable
    assert resp_over.status_code in (200, 422), (
        f"Expected 200 (clamped) or 422 (validation), got {resp_over.status_code}"
    )
    if resp_over.status_code == 200:
        assert resp_over.json()["limit"] == 100


@pytest.mark.asyncio
async def test_list_batches_returns_items_with_total(client, admin_auth_headers, db_session):
    """Returns items with correct total count."""
    await _make_batch(db_session, entity="contact", status="done")
    await _make_batch(db_session, entity="event", status="running")
    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_batches_filter_entity(client, admin_auth_headers, db_session):
    """entity filter narrows results."""
    await _make_batch(db_session, entity="contact")
    await _make_batch(db_session, entity="event")
    resp = await client.get("/migration/batches?entity=contact", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_batches_filter_mode(client, admin_auth_headers, db_session):
    """mode filter narrows results."""
    await _make_batch(db_session, mode="create")
    await _make_batch(db_session, mode="upsert")
    resp = await client.get("/migration/batches?mode=create", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_batches_filter_status(client, admin_auth_headers, db_session):
    """status filter narrows results."""
    await _make_batch(db_session, status="done")
    await _make_batch(db_session, status="running")
    resp = await client.get("/migration/batches?status=running", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_batches_newest_first(client, admin_auth_headers, db_session):
    """Results are ordered by started_at desc (newest first)."""
    from datetime import timedelta
    now = utc_now()
    b_old = ImportBatch(entity="contact", mode="create", status="done",
                        total_rows=5, created_count=5, updated_count=0,
                        skipped_count=0, error_count=0,
                        started_at=now - timedelta(hours=2))
    b_new = ImportBatch(entity="contact", mode="create", status="done",
                        total_rows=5, created_count=5, updated_count=0,
                        skipped_count=0, error_count=0,
                        started_at=now)
    db_session.add(b_old)
    db_session.add(b_new)
    await db_session.commit()
    await db_session.refresh(b_old)
    await db_session.refresh(b_new)

    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    # First item should be the newer batch
    assert items[0]["id"] == b_new.id
    assert items[1]["id"] == b_old.id


@pytest.mark.asyncio
async def test_list_batches_offset(client, admin_auth_headers, db_session):
    """offset paginates results."""
    for i in range(5):
        await _make_batch(db_session)
    resp = await client.get("/migration/batches?limit=3&offset=3", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["offset"] == 3


@pytest.mark.asyncio
async def test_list_batches_response_shape(client, admin_auth_headers, db_session):
    """Response has ImportBatchListResponse shape."""
    await _make_batch(db_session, entity="contact", status="done")
    resp = await client.get("/migration/batches", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    item = data["items"][0]
    for field in ["id", "entity", "mode", "status", "total_rows", "created_count",
                  "updated_count", "skipped_count", "error_count", "review_count",
                  "started_at", "finished_at"]:
        assert field in item, f"Missing field {field} in batch item"


# ---------------------------------------------------------------------------
# AC4: GET /batches/{batch_id} — detail, pending_review_count, 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_batch_404(client, admin_auth_headers):
    """Missing batch_id returns 404."""
    resp = await client.get("/migration/batches/99999", headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_batch_detail_shape(client, admin_auth_headers, db_session):
    """Returns ImportBatchDetailResponse with pending_review_count field."""
    batch = await _make_batch(db_session)
    resp = await client.get(f"/migration/batches/{batch.id}", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "pending_review_count" in data
    assert data["id"] == batch.id


@pytest.mark.asyncio
async def test_get_batch_pending_review_count_zero_when_no_queue(client, admin_auth_headers, db_session):
    """pending_review_count is 0 when no NameMatchReviewQueue rows exist."""
    batch = await _make_batch(db_session)
    resp = await client.get(f"/migration/batches/{batch.id}", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pending_review_count"] == 0


@pytest.mark.asyncio
async def test_get_batch_pending_review_count_counts_pending(client, admin_auth_headers, db_session):
    """pending_review_count counts only status='pending' rows matching batch_id."""
    batch = await _make_batch(db_session)
    batch2 = await _make_batch(db_session)

    # pending for batch
    q1 = NameMatchReviewQueue(raw_name="Alice", status="pending",
                               raw_payload={"batch_id": batch.id})
    q2 = NameMatchReviewQueue(raw_name="Bob", status="pending",
                               raw_payload={"batch_id": batch.id})
    # non-pending for batch (should NOT be counted)
    q3 = NameMatchReviewQueue(raw_name="Carol", status="accepted",
                               raw_payload={"batch_id": batch.id})
    # pending for different batch (should NOT be counted for batch)
    q4 = NameMatchReviewQueue(raw_name="Dave", status="pending",
                               raw_payload={"batch_id": batch2.id})

    for q in [q1, q2, q3, q4]:
        db_session.add(q)
    await db_session.commit()

    resp = await client.get(f"/migration/batches/{batch.id}", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pending_review_count"] == 2


# ---------------------------------------------------------------------------
# AC5: GET /batches/{batch_id}/rows — outcome filter, limit, order, 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_rows_404_on_missing_batch(client, admin_auth_headers):
    """Returns 404 when batch does not exist."""
    resp = await client.get("/migration/batches/99999/rows", headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_rows_empty(client, admin_auth_headers, db_session):
    """Empty rows returns total=0."""
    batch = await _make_batch(db_session)
    resp = await client.get(f"/migration/batches/{batch.id}/rows", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_list_rows_limit_enforced_at_200(client, admin_auth_headers, db_session):
    """Limit is clamped to <=200: limit=200 is accepted; limit>200 is rejected or clamped."""
    batch = await _make_batch(db_session)

    # Limit = 200 (max) must be accepted
    resp200 = await client.get(f"/migration/batches/{batch.id}/rows?limit=200",
                               headers=admin_auth_headers)
    assert resp200.status_code == 200
    assert resp200.json()["limit"] == 200

    # Limit > 200: router uses le=200 Query validation → 422 (correct enforcement)
    resp_over = await client.get(f"/migration/batches/{batch.id}/rows?limit=201",
                                 headers=admin_auth_headers)
    # Either 422 (validation rejects) or 200 with clamped 200 is acceptable
    assert resp_over.status_code in (200, 422), (
        f"Expected 200 (clamped) or 422 (validation), got {resp_over.status_code}"
    )


@pytest.mark.asyncio
async def test_list_rows_outcome_filter(client, admin_auth_headers, db_session):
    """outcome query param filters results."""
    batch = await _make_batch(db_session)
    await _make_row(db_session, batch.id, row_number=1, outcome="created")
    await _make_row(db_session, batch.id, row_number=2, outcome="error")
    await _make_row(db_session, batch.id, row_number=3, outcome="created")

    resp = await client.get(f"/migration/batches/{batch.id}/rows?outcome=created",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(item["outcome"] == "created" for item in data["items"])


@pytest.mark.asyncio
async def test_list_rows_ordered_by_row_number(client, admin_auth_headers, db_session):
    """Rows are ordered by row_number ascending."""
    batch = await _make_batch(db_session)
    # Insert out of order
    await _make_row(db_session, batch.id, row_number=5, outcome="created")
    await _make_row(db_session, batch.id, row_number=2, outcome="created")
    await _make_row(db_session, batch.id, row_number=1, outcome="created")

    resp = await client.get(f"/migration/batches/{batch.id}/rows",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    row_numbers = [item["row_number"] for item in items]
    assert row_numbers == sorted(row_numbers)


@pytest.mark.asyncio
async def test_list_rows_response_shape(client, admin_auth_headers, db_session):
    """Response has ImportRowResultListResponse shape."""
    batch = await _make_batch(db_session)
    await _make_row(db_session, batch.id, row_number=1, outcome="created", entity_id=42)
    resp = await client.get(f"/migration/batches/{batch.id}/rows",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data and "total" in data and "limit" in data and "offset" in data
    item = data["items"][0]
    for field in ["id", "batch_id", "row_number", "outcome", "entity_id", "created_at"]:
        assert field in item, f"Missing field {field} in row item"


# ---------------------------------------------------------------------------
# AC6: GET /batches/{batch_id}/report.csv — streaming CSV
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_csv_endpoint_404_on_missing_batch(client, admin_auth_headers):
    """Returns 404 when batch does not exist."""
    resp = await client.get("/migration/batches/99999/report.csv",
                            headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_csv_endpoint_returns_csv(client, admin_auth_headers, db_session):
    """Returns 200 with text/csv media type."""
    batch = await _make_batch(db_session)
    await _make_row(db_session, batch.id, row_number=1, outcome="created")
    resp = await client.get(f"/migration/batches/{batch.id}/report.csv",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_csv_endpoint_content_disposition(client, admin_auth_headers, db_session):
    """Content-Disposition header: attachment; filename='batch_{id}_report.csv'."""
    batch = await _make_batch(db_session)
    resp = await client.get(f"/migration/batches/{batch.id}/report.csv",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f"batch_{batch.id}_report.csv" in cd


@pytest.mark.asyncio
async def test_csv_endpoint_has_header_row(client, admin_auth_headers, db_session):
    """CSV response includes a header row."""
    batch = await _make_batch(db_session)
    await _make_row(db_session, batch.id, row_number=1, outcome="created")
    resp = await client.get(f"/migration/batches/{batch.id}/report.csv",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    lines = resp.text.strip().split("\n")
    # First line is header
    assert "row_number" in lines[0]
    assert "outcome" in lines[0]


@pytest.mark.asyncio
async def test_csv_endpoint_includes_data_rows(client, admin_auth_headers, db_session):
    """CSV response includes one row per ImportRowResult."""
    batch = await _make_batch(db_session)
    await _make_row(db_session, batch.id, row_number=1, outcome="created", message="ok")
    await _make_row(db_session, batch.id, row_number=2, outcome="error", message="fail")
    resp = await client.get(f"/migration/batches/{batch.id}/report.csv",
                            headers=admin_auth_headers)
    assert resp.status_code == 200
    lines = [l for l in resp.text.strip().split("\n") if l]
    # 1 header + 2 data rows
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# AC7: GET /summary — latest completed per entity + pending_reviews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_empty(client, admin_auth_headers):
    """Empty DB returns None for all entities and 0 pending_reviews."""
    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["contacts"] is None
    assert data["events"] is None
    assert data["participants"] is None
    assert data["links"] is None
    assert data["pending_reviews"] == 0


@pytest.mark.asyncio
async def test_summary_response_shape(client, admin_auth_headers):
    """Response has MigrationSummaryResponse shape."""
    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    for key in ["contacts", "events", "participants", "links", "pending_reviews"]:
        assert key in data, f"Missing key {key} in summary response"


@pytest.mark.asyncio
async def test_summary_latest_completed_per_entity(client, admin_auth_headers, db_session):
    """Returns latest completed batch per entity (not running/error)."""
    from datetime import timedelta
    now = utc_now()
    # contact entity: two done batches; expect the newer one
    b_contact_old = ImportBatch(entity="contact", mode="create", status="done",
                                total_rows=5, created_count=3, updated_count=0,
                                skipped_count=0, error_count=0,
                                started_at=now - timedelta(hours=10))
    b_contact_new = ImportBatch(entity="contact", mode="create", status="done",
                                total_rows=10, created_count=8, updated_count=0,
                                skipped_count=0, error_count=0,
                                started_at=now - timedelta(hours=1))
    # running contact batch (should NOT be returned)
    b_contact_running = ImportBatch(entity="contact", mode="create", status="running",
                                    total_rows=10, created_count=0, updated_count=0,
                                    skipped_count=0, error_count=0,
                                    started_at=now)
    # event entity: one completed batch
    b_event = ImportBatch(entity="event", mode="create", status="done",
                          total_rows=20, created_count=20, updated_count=0,
                          skipped_count=0, error_count=0,
                          started_at=now - timedelta(hours=5))

    for b in [b_contact_old, b_contact_new, b_contact_running, b_event]:
        db_session.add(b)
    await db_session.commit()
    for b in [b_contact_old, b_contact_new, b_contact_running, b_event]:
        await db_session.refresh(b)

    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["contacts"] is not None
    assert data["contacts"]["id"] == b_contact_new.id, (
        f"Expected newest contact batch {b_contact_new.id}, got {data['contacts']['id']}"
    )
    assert data["events"] is not None
    assert data["events"]["id"] == b_event.id
    assert data["participants"] is None
    assert data["links"] is None


@pytest.mark.asyncio
async def test_summary_pending_reviews_count(client, admin_auth_headers, db_session):
    """pending_reviews counts all NameMatchReviewQueue rows with status='pending'."""
    q1 = NameMatchReviewQueue(raw_name="Alice", status="pending")
    q2 = NameMatchReviewQueue(raw_name="Bob", status="pending")
    q3 = NameMatchReviewQueue(raw_name="Carol", status="accepted")  # not pending
    for q in [q1, q2, q3]:
        db_session.add(q)
    await db_session.commit()

    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pending_reviews"] == 2


@pytest.mark.asyncio
async def test_summary_entity_label_mapping(client, admin_auth_headers, db_session):
    """Summary maps ORM entity names to schema keys (contact→contacts, etc.)."""
    batch_contact = await _make_batch(db_session, entity="contact", status="done")
    batch_event = await _make_batch(db_session, entity="event", status="done")
    batch_participant = await _make_batch(db_session, entity="participant", status="done")
    batch_link = await _make_batch(db_session, entity="link", status="done")

    resp = await client.get("/migration/summary", headers=admin_auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["contacts"]["id"] == batch_contact.id
    assert data["events"]["id"] == batch_event.id
    assert data["participants"]["id"] == batch_participant.id
    assert data["links"]["id"] == batch_link.id


# ---------------------------------------------------------------------------
# AC4 (dialect): _pending_review_count dialect branching tested via full path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_review_count_sqlite_dialect(client, admin_auth_headers, db_session):
    """SQLite path: json_extract correctly identifies batch_id in raw_payload."""
    batch = await _make_batch(db_session)
    # SQLite: raw_payload stores int batch_id; the router branch uses ==batch_id (int)
    q = NameMatchReviewQueue(raw_name="Test", status="pending",
                              raw_payload={"batch_id": batch.id})
    db_session.add(q)
    await db_session.commit()

    resp = await client.get(f"/migration/batches/{batch.id}", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json()["pending_review_count"] == 1


# ---------------------------------------------------------------------------
# AC3/AC5: limit minimum (>=1 enforced by Query validation)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_batches_limit_0_returns_422(client, admin_auth_headers):
    """limit=0 violates ge=1 constraint → 422."""
    resp = await client.get("/migration/batches?limit=0", headers=admin_auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_rows_limit_0_returns_422(client, admin_auth_headers, db_session):
    """limit=0 for rows violates ge=1 constraint → 422."""
    batch = await _make_batch(db_session)
    resp = await client.get(f"/migration/batches/{batch.id}/rows?limit=0",
                            headers=admin_auth_headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Async all handlers
# ---------------------------------------------------------------------------

def test_all_handlers_are_async():
    """All endpoint handlers must be async (coroutine functions)."""
    from app.routers.migration import (
        list_batches, get_batch, list_batch_rows,
        download_batch_report, get_summary,
    )
    for fn in [list_batches, get_batch, list_batch_rows, download_batch_report, get_summary]:
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} must be async"


def test_all_handlers_are_type_hinted():
    """All public handlers must have return type annotations."""
    from app.routers.migration import (
        list_batches, get_batch, list_batch_rows,
        download_batch_report, get_summary,
    )
    for fn in [list_batches, get_batch, list_batch_rows, download_batch_report, get_summary]:
        hints = fn.__annotations__
        assert "return" in hints, f"{fn.__name__} missing return type annotation"
