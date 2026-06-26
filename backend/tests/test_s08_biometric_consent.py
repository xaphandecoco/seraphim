"""S08 — Biometric Consent API tests.

Coverage:
- Status synthesis: none / pending / given / revoked / purged
- CRUD: record, update, revoke, deletion-request
- RBAC matrix: viewer 200 GET / 403 mutations; volunteer can't set retention_until → 403;
  409 after purge; 404 on update/revoke when no row
- GET-never-404 (contact with no consent row returns 200 with status='none')
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient

from tests.conftest import make_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _viewer_headers(user) -> dict:
    token = make_token(user.id, user.email, user.role, "Viewer")
    return {"Authorization": f"Bearer {token}"}


def _vol_headers(user) -> dict:
    token = make_token(user.id, user.email, user.role, "Vol")
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(user) -> dict:
    token = make_token(user.id, user.email, user.role, "Admin")
    return {"Authorization": f"Bearer {token}"}


async def _make_contact(db_session) -> Any:
    from app.models import Contact

    c = Contact(first_name="Test", last_name="User", contact_type="individual")
    db_session.add(c)
    await db_session.commit()
    await db_session.refresh(c)
    return c


# ---------------------------------------------------------------------------
# Test: GET-never-404 — contact with no consent row
# ---------------------------------------------------------------------------


async def test_get_consent_no_row_returns_none_status(client, db_session, admin_user):
    contact = await _make_contact(db_session)
    headers = _admin_headers(admin_user)
    resp = await client.get(f"/biometric/contacts/{contact.id}/consent", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "none"
    assert data["consent_given"] is False
    assert data["enrolled_photo_count"] == 0
    assert data["subject_active"] is False


async def test_get_consent_nonexistent_contact_returns_none_status(client, db_session, admin_user):
    """GET on a contact that doesn't exist still returns status=none (never 404)."""
    headers = _admin_headers(admin_user)
    resp = await client.get("/biometric/contacts/99999/consent", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "none"


# ---------------------------------------------------------------------------
# Test: RBAC — viewer can GET but cannot mutate
# ---------------------------------------------------------------------------


async def test_viewer_can_get_consent(client, db_session, viewer_user):
    contact = await _make_contact(db_session)
    headers = _viewer_headers(viewer_user)
    resp = await client.get(f"/biometric/contacts/{contact.id}/consent", headers=headers)
    assert resp.status_code == 200


async def test_viewer_cannot_record_consent(client, db_session, viewer_user):
    contact = await _make_contact(db_session)
    headers = _viewer_headers(viewer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent",
        json={},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_viewer_cannot_revoke_consent(client, db_session, viewer_user):
    contact = await _make_contact(db_session)
    headers = _viewer_headers(viewer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent/revoke",
        headers=headers,
    )
    assert resp.status_code == 403


async def test_viewer_cannot_request_deletion(client, db_session, viewer_user):
    contact = await _make_contact(db_session)
    headers = _viewer_headers(viewer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/deletion-request",
        json={"immediate": False},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_viewer_cannot_purge(client, db_session, viewer_user):
    contact = await _make_contact(db_session)
    headers = _viewer_headers(viewer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/purge",
        headers=headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Test: record → status = given
# ---------------------------------------------------------------------------


async def test_record_consent_sets_given_status(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    headers = _vol_headers(volunteer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent",
        json={"basis_note": "Signed form", "retention_years": 5},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "given"
    assert data["consent_given"] is True
    assert data["basis_note"] == "Signed form"
    assert data["retention_until"] is not None


async def test_record_consent_default_retention(client, db_session, volunteer_user):
    """No retention_years → uses config default (7 years)."""
    contact = await _make_contact(db_session)
    headers = _vol_headers(volunteer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    retention = datetime.fromisoformat(data["retention_until"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Should be roughly 7 years out (±7 days tolerance)
    assert retention > now + timedelta(days=365 * 7 - 7)


# ---------------------------------------------------------------------------
# Test: revoke → status = revoked
# ---------------------------------------------------------------------------


async def test_revoke_consent_sets_revoked_status(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    headers = _vol_headers(volunteer_user)

    # First grant
    await client.post(
        f"/biometric/contacts/{contact.id}/consent",
        json={},
        headers=headers,
    )

    # Now revoke
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent/revoke",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "revoked"


async def test_revoke_404_when_no_row(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    headers = _vol_headers(volunteer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/consent/revoke",
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: update — volunteer cannot set retention_until → 403
# ---------------------------------------------------------------------------


async def test_volunteer_cannot_set_retention_until(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    headers = _vol_headers(volunteer_user)

    # Create a row first
    await client.post(f"/biometric/contacts/{contact.id}/consent", json={}, headers=headers)

    future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    resp = await client.patch(
        f"/biometric/contacts/{contact.id}/consent",
        json={"retention_until": future},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_admin_can_set_retention_until(client, db_session, admin_user, volunteer_user):
    contact = await _make_contact(db_session)
    vol_headers = _vol_headers(volunteer_user)
    admin_headers = _admin_headers(admin_user)

    # Volunteer creates the row
    await client.post(f"/biometric/contacts/{contact.id}/consent", json={}, headers=vol_headers)

    # Admin updates retention_until
    future = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    resp = await client.patch(
        f"/biometric/contacts/{contact.id}/consent",
        json={"retention_until": future},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["retention_until"] is not None


async def test_update_404_when_no_row(client, db_session, admin_user):
    contact = await _make_contact(db_session)
    headers = _admin_headers(admin_user)
    resp = await client.patch(
        f"/biometric/contacts/{contact.id}/consent",
        json={"basis_note": "note"},
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: 409 after purge
# ---------------------------------------------------------------------------


async def test_409_on_record_after_purge(client, db_session, volunteer_user, admin_user):
    """Recording consent on a purged contact raises 409."""
    contact = await _make_contact(db_session)
    vol_headers = _vol_headers(volunteer_user)
    admin_headers = _admin_headers(admin_user)

    # Grant consent
    await client.post(
        f"/biometric/contacts/{contact.id}/consent", json={}, headers=vol_headers
    )

    # Purge (admin)
    with patch(
        "app.services.biometric_purge.BiometricPurgeService._delete_cf_subject",
        new=AsyncMock(return_value=(True, None)),
    ):
        resp = await client.post(
            f"/biometric/contacts/{contact.id}/purge", headers=admin_headers
        )
    assert resp.status_code == 200

    # Attempt to re-record → 409
    resp2 = await client.post(
        f"/biometric/contacts/{contact.id}/consent", json={}, headers=vol_headers
    )
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# Test: deletion-request (non-immediate)
# ---------------------------------------------------------------------------


async def test_deletion_request_queues_flag(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    vol_headers = _vol_headers(volunteer_user)

    await client.post(f"/biometric/contacts/{contact.id}/consent", json={}, headers=vol_headers)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/deletion-request",
        json={"immediate": False},
        headers=vol_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "deletion_requested"

    # Verify the flag is set
    get_resp = await client.get(
        f"/biometric/contacts/{contact.id}/consent", headers=vol_headers
    )
    assert get_resp.json()["deletion_requested_at"] is not None


async def test_deletion_request_without_row_404(client, db_session, volunteer_user):
    contact = await _make_contact(db_session)
    vol_headers = _vol_headers(volunteer_user)
    resp = await client.post(
        f"/biometric/contacts/{contact.id}/deletion-request",
        json={"immediate": False},
        headers=vol_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: status synthesis from DB state
# ---------------------------------------------------------------------------


async def test_status_synthesis_pending(db_session):
    """consent_given=False, consented_at=None → pending."""
    from app.models import BiometricConsent
    from app.services import biometric_consent as svc

    contact_id = 99001
    consent = BiometricConsent(
        contact_id=contact_id,
        consent_given=False,
        consented_at=None,
    )
    db_session.add(consent)
    await db_session.commit()

    result = await svc.get_status(db_session, contact_id)
    assert result["status"] == "pending"


async def test_status_synthesis_given(db_session):
    """consent_given=True, purged_at=None → given."""
    from app.models import BiometricConsent
    from app.services import biometric_consent as svc
    from app.models import utc_now

    contact_id = 99002
    consent = BiometricConsent(
        contact_id=contact_id,
        consent_given=True,
        consented_at=utc_now(),
    )
    db_session.add(consent)
    await db_session.commit()

    result = await svc.get_status(db_session, contact_id)
    assert result["status"] == "given"


async def test_status_synthesis_revoked(db_session):
    """consent_given=False, consented_at IS NOT NULL → revoked."""
    from app.models import BiometricConsent, utc_now
    from app.services import biometric_consent as svc

    contact_id = 99003
    consent = BiometricConsent(
        contact_id=contact_id,
        consent_given=False,
        consented_at=utc_now(),  # was previously given
    )
    db_session.add(consent)
    await db_session.commit()

    result = await svc.get_status(db_session, contact_id)
    assert result["status"] == "revoked"


async def test_status_synthesis_purged(db_session):
    """purged_at IS NOT NULL → purged regardless of other fields."""
    from app.models import BiometricConsent, utc_now
    from app.services import biometric_consent as svc

    contact_id = 99004
    consent = BiometricConsent(
        contact_id=contact_id,
        consent_given=True,
        consented_at=utc_now(),
        purged_at=utc_now(),
    )
    db_session.add(consent)
    await db_session.commit()

    result = await svc.get_status(db_session, contact_id)
    assert result["status"] == "purged"


async def test_status_synthesis_none(db_session):
    """No row → status none."""
    from app.services import biometric_consent as svc

    result = await svc.get_status(db_session, contact_id=99999)
    assert result["status"] == "none"


# ---------------------------------------------------------------------------
# Test: contact-not-found → clean 404 (not FK 500)
# ---------------------------------------------------------------------------


async def test_record_consent_nonexistent_contact_404(client, db_session, volunteer_user):
    """POST consent for a contact that doesn't exist → 404, not 500."""
    headers = _vol_headers(volunteer_user)
    resp = await client.post(
        "/biometric/contacts/999999/consent",
        json={},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_update_consent_nonexistent_contact_404(client, db_session, admin_user):
    """PATCH consent for a contact that doesn't exist → 404, not 500."""
    headers = _admin_headers(admin_user)
    resp = await client.patch(
        "/biometric/contacts/999999/consent",
        json={"basis_note": "test"},
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test: retention report (admin only)
# ---------------------------------------------------------------------------


async def test_retention_report_requires_admin(client, db_session, volunteer_user):
    headers = _vol_headers(volunteer_user)
    resp = await client.get("/biometric/retention/report", headers=headers)
    assert resp.status_code == 403


async def test_retention_report_returns_paginated(client, db_session, admin_user):
    from app.models import BiometricConsent, Contact, utc_now
    from datetime import timedelta

    # Create a contact with a consent row
    contact = Contact(first_name="Report", last_name="User", contact_type="individual")
    db_session.add(contact)
    await db_session.flush()
    consent = BiometricConsent(
        contact_id=contact.id,
        consent_given=True,
        consented_at=utc_now(),
        retention_until=utc_now() + timedelta(days=365),
    )
    db_session.add(consent)
    await db_session.commit()

    headers = _admin_headers(admin_user)
    resp = await client.get(
        "/biometric/retention/report?page=1&page_size=10",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1
    item = next(i for i in data["items"] if i["contact_id"] == contact.id)
    assert item["status"] == "given"
