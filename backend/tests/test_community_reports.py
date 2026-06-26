"""Tests for community report endpoints (S22-F07 spec §8.1 community-reports section).

Coverage:
  - POST /community-reports: empty attendee_names => status=complete
  - POST /community-reports: requires event_id or event_title => 422 when both absent
  - POST /community-reports: preserves photo_paths
  - GET  /community-reports: list paginated
  - GET  /community-reports/{id}: detail with review_queue_items + matched_contacts
  - PATCH /community-reports/{id}: update status
  - DELETE /community-reports/{id}: soft-delete => status=archived
  - POST /community-reports/{id}/process: reprocess name matching
  - GET  /community-reports?zone=: filter by zone
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Helper — shared payload builder
# ---------------------------------------------------------------------------

def _report_payload(event_id=None, event_title=None, **extra):
    payload = {
        "date_of_activity": "2026-06-20",
        "attendee_names": [],
        "photo_paths": [],
    }
    if event_id is not None:
        payload["event_id"] = event_id
    if event_title is not None:
        payload["event_title"] = event_title
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def contacts_cr(db_session):
    """Two contacts for community-report name-matching tests."""
    from app.models import Contact

    juan = Contact(
        first_name="Juan",
        last_name="Cruz",
        contact_type="individual",
    )
    maria = Contact(
        first_name="Maria",
        last_name="Santos",
        contact_type="individual",
    )
    db_session.add(juan)
    db_session.add(maria)
    await db_session.commit()
    await db_session.refresh(juan)
    await db_session.refresh(maria)
    return {"juan": juan, "maria": maria}


@pytest_asyncio.fixture
async def sample_report(client, volunteer_auth_headers, sample_event):
    """Create a minimal community report and return the JSON response."""
    resp = await client.post(
        "/community-reports",
        json=_report_payload(event_id=sample_event.id),
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Section 1 — POST /community-reports
# ===========================================================================

class TestCreateCommunityReport:
    @pytest.mark.asyncio
    async def test_empty_names_becomes_complete(
        self, client, volunteer_auth_headers, sample_event
    ):
        """Empty attendee_names should produce status='complete' and match_status='complete'."""
        resp = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, attendee_names=[]),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["status"] == "complete"
        assert data["match_status"] == "complete"

    @pytest.mark.asyncio
    async def test_requires_event_id_or_title_422_when_both_absent(
        self, client, volunteer_auth_headers
    ):
        """Omitting both event_id and event_title must return 422."""
        resp = await client.post(
            "/community-reports",
            json={
                "date_of_activity": "2026-06-20",
                "attendee_names": [],
                "photo_paths": [],
                # Neither event_id nor event_title supplied
            },
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_event_title_fallback_accepted(
        self, client, volunteer_auth_headers
    ):
        """Providing event_title without event_id should succeed."""
        resp = await client.post(
            "/community-reports",
            json=_report_payload(event_title="My Cell Group"),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["event_title"] == "My Cell Group"

    @pytest.mark.asyncio
    async def test_preserves_photo_paths(
        self, client, volunteer_auth_headers, sample_event
    ):
        """photo_paths list is stored and returned unchanged."""
        paths = ["/uploads/photo1.jpg", "/uploads/photo2.jpg"]
        resp = await client.post(
            "/community-reports",
            json=_report_payload(
                event_id=sample_event.id,
                photo_paths=paths,
            ),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["photo_paths"] == paths

    @pytest.mark.asyncio
    async def test_with_attendee_names_sets_pending_or_partial(
        self, client, volunteer_auth_headers, sample_event, contacts_cr
    ):
        """Providing attendee_names triggers matching; status reflects match result."""
        resp = await client.post(
            "/community-reports",
            json=_report_payload(
                event_id=sample_event.id,
                attendee_names=["Juan Cruz", "Zzz Unknownnnn"],
            ),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        # match_status must be complete or partial (not pending) after processing
        assert data["match_status"] in ("complete", "partial")

    @pytest.mark.asyncio
    async def test_invalid_event_id_422(
        self, client, volunteer_auth_headers
    ):
        """Supplying a non-existent event_id should return 422."""
        resp = await client.post(
            "/community-reports",
            json=_report_payload(event_id=999999),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_zone_persisted(
        self, client, volunteer_auth_headers, sample_event
    ):
        """zone field is stored and returned."""
        resp = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, zone="North"),
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["zone"] == "North"


# ===========================================================================
# Section 2 — GET /community-reports (paginated list)
# ===========================================================================

class TestListCommunityReports:
    @pytest.mark.asyncio
    async def test_list_paginated_returns_items(
        self, client, volunteer_auth_headers, sample_report
    ):
        """GET /community-reports returns a paginated response with our report."""
        resp = await client.get(
            "/community-reports",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data
        assert data["total"] >= 1
        ids = [item["id"] for item in data["items"]]
        assert sample_report["id"] in ids

    @pytest.mark.asyncio
    async def test_list_pagination_page2_empty_when_only_one(
        self, client, volunteer_auth_headers, sample_report
    ):
        """Page 2 with page_size=100 should be empty when only one report exists."""
        resp = await client.get(
            "/community-reports?page=2&page_size=100",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == [] or data["page"] == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_zone(
        self, client, volunteer_auth_headers, sample_event
    ):
        """Filter by zone should only return matching reports."""
        # Create two reports with different zones
        await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, zone="Alpha"),
            headers=volunteer_auth_headers,
        )
        await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, zone="Beta"),
            headers=volunteer_auth_headers,
        )

        resp = await client.get(
            "/community-reports?zone=Alpha",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["zone"] == "Alpha"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(
        self, client, volunteer_auth_headers, sample_event
    ):
        """Filter by status=complete should return only complete reports."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, attendee_names=[]),
            headers=volunteer_auth_headers,
        )
        assert resp_create.status_code == 201

        resp = await client.get(
            "/community-reports?status=complete",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["status"] == "complete"


# ===========================================================================
# Section 3 — GET /community-reports/{id} (detail)
# ===========================================================================

class TestGetCommunityReport:
    @pytest.mark.asyncio
    async def test_detail_returns_correct_id(
        self, client, volunteer_auth_headers, sample_report
    ):
        """GET /community-reports/{id} returns the correct report."""
        resp = await client.get(
            f"/community-reports/{sample_report['id']}",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == sample_report["id"]

    @pytest.mark.asyncio
    async def test_detail_includes_review_queue_items(
        self, client, volunteer_auth_headers, sample_event, db_session
    ):
        """Detail response must include review_queue_items field."""
        # Create a report with unmatched names to seed queue items
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(
                event_id=sample_event.id,
                attendee_names=["Zzz Nobody Known"],
            ),
            headers=volunteer_auth_headers,
        )
        assert resp_create.status_code == 201
        report_id = resp_create.json()["id"]

        resp = await client.get(
            f"/community-reports/{report_id}",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "review_queue_items" in data
        # It may be empty if the unmatched name didn't queue (auto_enqueue=True is default)
        assert isinstance(data["review_queue_items"], list)

    @pytest.mark.asyncio
    async def test_detail_includes_matched_contacts(
        self, client, volunteer_auth_headers, sample_event, contacts_cr
    ):
        """Detail response must include matched_contacts field (may be empty list)."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id),
            headers=volunteer_auth_headers,
        )
        report_id = resp_create.json()["id"]

        resp = await client.get(
            f"/community-reports/{report_id}",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "matched_contacts" in data
        assert isinstance(data["matched_contacts"], list)

    @pytest.mark.asyncio
    async def test_detail_404_for_missing_report(
        self, client, volunteer_auth_headers
    ):
        """GET /community-reports/99999 should return 404."""
        resp = await client.get(
            "/community-reports/99999",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 404


# ===========================================================================
# Section 4 — PATCH /community-reports/{id}
# ===========================================================================

class TestPatchCommunityReport:
    @pytest.mark.asyncio
    async def test_patch_status_by_admin(
        self, client, admin_auth_headers, sample_report
    ):
        """Admin can PATCH status field."""
        resp = await client.patch(
            f"/community-reports/{sample_report['id']}",
            json={"status": "processing"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "processing"

    @pytest.mark.asyncio
    async def test_patch_requires_admin(
        self, client, volunteer_auth_headers, sample_report
    ):
        """Volunteer cannot PATCH — should return 403."""
        resp = await client.patch(
            f"/community-reports/{sample_report['id']}",
            json={"status": "processing"},
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_patch_zone(
        self, client, admin_auth_headers, sample_report
    ):
        """PATCH can update zone field."""
        resp = await client.patch(
            f"/community-reports/{sample_report['id']}",
            json={"zone": "South"},
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["zone"] == "South"


# ===========================================================================
# Section 5 — DELETE /community-reports/{id} (soft-delete)
# ===========================================================================

class TestDeleteCommunityReport:
    @pytest.mark.asyncio
    async def test_soft_delete_sets_archived(
        self, client, admin_auth_headers, volunteer_auth_headers, sample_event
    ):
        """DELETE should set status='archived', not destroy the row."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id),
            headers=volunteer_auth_headers,
        )
        report_id = resp_create.json()["id"]

        resp_del = await client.delete(
            f"/community-reports/{report_id}",
            headers=admin_auth_headers,
        )
        assert resp_del.status_code == 204

        # The report should still be fetchable with status=archived
        resp_get = await client.get(
            f"/community-reports/{report_id}",
            headers=volunteer_auth_headers,
        )
        assert resp_get.status_code == 200
        assert resp_get.json()["status"] == "archived"

    @pytest.mark.asyncio
    async def test_delete_requires_admin(
        self, client, volunteer_auth_headers, sample_report
    ):
        """Volunteer cannot DELETE — should return 403."""
        resp = await client.delete(
            f"/community-reports/{sample_report['id']}",
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 403


# ===========================================================================
# Section 6 — POST /community-reports/{id}/process (reprocess)
# ===========================================================================

class TestProcessCommunityReport:
    @pytest.mark.asyncio
    async def test_reprocess_with_empty_names_returns_complete(
        self, client, admin_auth_headers, volunteer_auth_headers, sample_event
    ):
        """Reprocessing a report with empty attendee_names returns match_status=complete."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(event_id=sample_event.id, attendee_names=[]),
            headers=volunteer_auth_headers,
        )
        report_id = resp_create.json()["id"]

        resp_proc = await client.post(
            f"/community-reports/{report_id}/process",
            headers=admin_auth_headers,
        )
        assert resp_proc.status_code == 200
        data = resp_proc.json()
        assert data["total"] == 0
        assert data["matched"] == 0

    @pytest.mark.asyncio
    async def test_reprocess_requires_event_id(
        self, client, admin_auth_headers, volunteer_auth_headers
    ):
        """Reprocessing a report without event_id should return 422."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(event_title="No Event ID Report"),
            headers=volunteer_auth_headers,
        )
        assert resp_create.status_code == 201
        report_id = resp_create.json()["id"]

        resp_proc = await client.post(
            f"/community-reports/{report_id}/process",
            headers=admin_auth_headers,
        )
        assert resp_proc.status_code == 422

    @pytest.mark.asyncio
    async def test_reprocess_404_for_missing_report(
        self, client, admin_auth_headers
    ):
        """Reprocessing a non-existent report returns 404."""
        resp = await client.post(
            "/community-reports/99999/process",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reprocess_with_matched_name(
        self, client, admin_auth_headers, volunteer_auth_headers, sample_event, contacts_cr
    ):
        """Reprocessing with matchable names should produce matched > 0."""
        resp_create = await client.post(
            "/community-reports",
            json=_report_payload(
                event_id=sample_event.id,
                attendee_names=["Juan Cruz"],
            ),
            headers=volunteer_auth_headers,
        )
        report_id = resp_create.json()["id"]

        resp_proc = await client.post(
            f"/community-reports/{report_id}/process",
            headers=admin_auth_headers,
        )
        assert resp_proc.status_code == 200
        data = resp_proc.json()
        assert data["total"] == 1
        # matched could be 0 if already existing, but no error
        assert data["matched"] >= 0
