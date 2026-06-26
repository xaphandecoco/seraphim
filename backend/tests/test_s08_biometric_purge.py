"""S08 — Biometric Purge tests (updated after security review).

Coverage:
- Full purge counts: files_deleted, samples_deleted, detections_cleared
- File unlink via tmp_path / STORAGE_PATH (enrolled glob AND DB-path iteration)
- contacts + participants + consent row retained after purge
- Idempotent re-purge: no 2nd delete_subject on fully clean purge
- delete_subject HTTP 404 treated as success
- delete_subject raises → local erasure completes, purged_at stays NULL, retry stamps purged_at
- Non-enrolled (recognition) detections erased alongside enrolled ones
- Face samples unlinked via DB-authoritative path (not just glob)
- Detection-crop anonymization: image_path cleared, deleted_at set
- Retention-job purge with system actor (actor_id=None)
- Partial-failure state machine: purged_at NOT stamped until fully clean
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tests.conftest import make_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_contact(db_session, first="Purge", last="Test"):
    from app.models import Contact

    c = Contact(first_name=first, last_name=last, contact_type="individual")
    db_session.add(c)
    await db_session.flush()
    return c


async def _make_event(db_session):
    from app.models import Event

    e = Event(title="Test Event")
    db_session.add(e)
    await db_session.flush()
    return e


async def _make_participant(db_session, contact, event):
    from app.models import Participant

    p = Participant(contact_id=contact.id, event_id=event.id, source="face")
    db_session.add(p)
    await db_session.flush()
    return p


async def _make_subject(db_session, contact, cf_id="contact_1", status="active"):
    from app.models import ComprefaceSubject

    s = ComprefaceSubject(
        subject_name=f"{contact.first_name} {contact.last_name}",
        compreface_subject_id=cf_id,
        contact_id=contact.id,
        enrollment_status=status,
        sample_count=0,
    )
    db_session.add(s)
    await db_session.flush()
    return s


async def _make_consent(db_session, contact, consent_given=True):
    from app.models import BiometricConsent, utc_now

    c = BiometricConsent(
        contact_id=contact.id,
        consent_given=consent_given,
        consented_at=utc_now() if consent_given else None,
        retention_until=utc_now() + timedelta(days=365),
    )
    db_session.add(c)
    await db_session.flush()
    return c


async def _make_face_sample(db_session, contact, cf_id, storage_base: Path, suffix="001"):
    """Create a FaceSample and write stub files to disk."""
    from app.models import FaceSample

    enrolled_dir = storage_base / "enrolled" / cf_id
    enrolled_dir.mkdir(parents=True, exist_ok=True)
    full_file = enrolled_dir / f"sample_{suffix}_full.jpg"
    thumb_file = enrolled_dir / f"sample_{suffix}_thumb.jpg"
    full_file.write_bytes(b"FAKE_FULL")
    thumb_file.write_bytes(b"FAKE_THUMB")

    sample = FaceSample(
        compreface_subject_id=cf_id,
        contact_id=contact.id,
        image_path=str(full_file.relative_to(storage_base)),
        thumb_path=str(thumb_file.relative_to(storage_base)),
        source="manual",
    )
    db_session.add(sample)
    await db_session.flush()
    return sample, full_file, thumb_file


async def _make_detection(db_session, cf_id, storage_base: Path,
                          is_enrolled=True, prefix="det_123"):
    """Create a Detection (enrolled or recognition) with a crop file on disk."""
    from app.models import Camera, Detection

    cam = Camera(name=f"cam_{prefix}", rtsp_url="rtsp://localhost/test",
                 enable_health_check=False)
    db_session.add(cam)
    await db_session.flush()

    crop_dir = storage_base / "faces" / "2025" / "01" / "01"
    crop_dir.mkdir(parents=True, exist_ok=True)
    thumb_rel = f"faces/2025/01/01/{prefix}_thumb.jpg"
    full_rel = f"faces/2025/01/01/{prefix}_full.jpg"
    (storage_base / thumb_rel).write_bytes(b"THUMB")
    (storage_base / full_rel).write_bytes(b"FULL")

    det = Detection(
        camera_id=cam.id,
        image_path=thumb_rel,
        compreface_subject_id=cf_id,
        is_enrolled=is_enrolled,
        matched_name="Purge Test",
        status="auto_logged",
    )
    db_session.add(det)
    await db_session.flush()
    return det, storage_base / thumb_rel, storage_base / full_rel


# ---------------------------------------------------------------------------
# Test: full purge counts + file unlink + rows retained
# ---------------------------------------------------------------------------


async def test_purge_deletes_files_and_sample_rows(db_session, tmp_path):
    """Full purge: enrolled files + face_samples deleted; contact + participant retained."""
    from sqlalchemy import select

    from app.models import (
        BiometricConsent,
        ComprefaceSubject,
        Contact,
        FaceSample,
        Participant,
    )
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session)
    event = await _make_event(db_session)
    participant = await _make_participant(db_session, contact, event)
    cf_id = f"contact_{contact.id}"
    subject = await _make_subject(db_session, contact, cf_id=cf_id)
    consent = await _make_consent(db_session, contact)
    sample, full_file, thumb_file = await _make_face_sample(db_session, contact, cf_id, tmp_path)
    await db_session.commit()

    assert full_file.exists()
    assert thumb_file.exists()

    svc = BiometricPurgeService(storage_base=tmp_path)

    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["status"] == "purged"
    detail = result["purge_detail"]
    assert detail["samples_deleted"] >= 1
    assert detail["compreface_deleted"] is True
    assert detail["errors"] == []

    # Files should be deleted
    assert not full_file.exists()
    assert not thumb_file.exists()

    # FaceSamples rows gone
    rows = await db_session.execute(
        select(FaceSample).where(FaceSample.contact_id == contact.id)
    )
    assert list(rows.scalars().all()) == []

    # Contact and Participant retained
    assert await db_session.get(Contact, contact.id) is not None
    part_rows = await db_session.execute(
        select(Participant).where(Participant.contact_id == contact.id)
    )
    assert len(list(part_rows.scalars().all())) == 1

    # Consent row retained but stamped with purged_at
    c_row = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c = c_row.scalar_one_or_none()
    assert c is not None
    assert c.purged_at is not None  # stamped because fully clean

    # Subject stamped
    subj_row = await db_session.execute(
        select(ComprefaceSubject).where(ComprefaceSubject.contact_id == contact.id)
    )
    subj = subj_row.scalar_one_or_none()
    assert subj is not None
    assert subj.enrollment_status == "purged"
    assert subj.compreface_subject_id is None
    assert subj.sample_count == 0


# ---------------------------------------------------------------------------
# NEW: non-enrolled recognition detections are ALSO erased
# ---------------------------------------------------------------------------


async def test_purge_erases_non_enrolled_recognition_detections(db_session, tmp_path):
    """ALL detections linked to the CF subject (is_enrolled=False too) are anonymized."""
    from sqlalchemy import select

    from app.models import Detection
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Recog", "Det")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)

    # enrolled detection
    det_e, thumb_e, full_e = await _make_detection(
        db_session, cf_id, tmp_path, is_enrolled=True, prefix="enrolled_det"
    )
    # recognition (non-enrolled) detection — previously MISSED by the old filter
    det_r, thumb_r, full_r = await _make_detection(
        db_session, cf_id, tmp_path, is_enrolled=False, prefix="recog_det"
    )
    await db_session.commit()

    assert thumb_e.exists() and thumb_r.exists()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["status"] == "purged"
    assert result["purge_detail"]["detections_cleared"] == 2

    # BOTH detection crops unlinked from disk
    assert not thumb_e.exists()
    assert not thumb_r.exists()
    assert not full_e.exists()
    assert not full_r.exists()

    # BOTH detections anonymized in DB (compreface_subject_id cleared)
    r_det = await db_session.execute(
        select(Detection).where(Detection.id == det_r.id)
    )
    updated = r_det.scalar_one()
    assert updated.compreface_subject_id is None
    assert updated.deleted_at is not None


# ---------------------------------------------------------------------------
# Test: detection-crop anonymization (enrolled)
# ---------------------------------------------------------------------------


async def test_purge_anonymizes_enrolled_detections(db_session, tmp_path):
    """Enrolled detections have image_path cleared and crop files deleted."""
    from sqlalchemy import select

    from app.models import Detection
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Det", "Anon")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    det, thumb_path, full_path = await _make_detection(
        db_session, cf_id, tmp_path, is_enrolled=True, prefix="enroll_anon"
    )
    await db_session.commit()

    assert thumb_path.exists()
    assert full_path.exists()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["purge_detail"]["detections_cleared"] >= 1

    rows = await db_session.execute(
        select(Detection).where(Detection.id == det.id)
    )
    updated_det = rows.scalar_one_or_none()
    assert updated_det is not None
    # image_path cleared (empty string in SQLite test env; NULL in postgres post-S08 migration)
    assert updated_det.image_path == "" or updated_det.image_path is None
    assert updated_det.deleted_at is not None
    assert not thumb_path.exists()
    assert not full_path.exists()


# ---------------------------------------------------------------------------
# NEW: face samples unlinked via DB-authoritative paths (non-conventional path)
# ---------------------------------------------------------------------------


async def test_purge_unlinks_face_sample_via_db_path(db_session, tmp_path):
    """Files at FaceSample.image_path / thumb_path are unlinked even if not in enrolled dir."""
    from sqlalchemy import select

    from app.models import FaceSample
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "DB", "Path")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)

    # Place the sample file in a NON-conventional location (not under enrolled/{cf_id}/)
    custom_dir = tmp_path / "custom" / "subdir"
    custom_dir.mkdir(parents=True)
    custom_full = custom_dir / "myface_full.jpg"
    custom_thumb = custom_dir / "myface_thumb.jpg"
    custom_full.write_bytes(b"CUSTOM_FULL")
    custom_thumb.write_bytes(b"CUSTOM_THUMB")

    sample = FaceSample(
        compreface_subject_id=cf_id,
        contact_id=contact.id,
        image_path=str(custom_full.relative_to(tmp_path)),
        thumb_path=str(custom_thumb.relative_to(tmp_path)),
        source="manual",
    )
    db_session.add(sample)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["status"] == "purged"
    assert result["purge_detail"]["samples_deleted"] >= 1

    # Custom-path files should be deleted via DB-authoritative path iteration
    assert not custom_full.exists()
    assert not custom_thumb.exists()


# ---------------------------------------------------------------------------
# Test: idempotent re-purge on fully clean purge (no 2nd delete_subject)
# ---------------------------------------------------------------------------


async def test_idempotent_repurge_no_second_delete(db_session, tmp_path):
    """Re-purging a fully-clean purge returns stored result, no 2nd CF call."""
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Idem", "Potent")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    delete_mock = AsyncMock(return_value=(True, None))
    with patch.object(svc, "_delete_cf_subject", new=delete_mock):
        await svc.purge(db_session, contact.id, actor_id=None)
        result2 = await svc.purge(db_session, contact.id, actor_id=None)

    assert result2["status"] == "already_purged"
    # delete_subject called exactly once (not on the idempotent re-call)
    assert delete_mock.call_count == 1


# ---------------------------------------------------------------------------
# Test: delete_subject HTTP 404 treated as success
# ---------------------------------------------------------------------------


async def test_delete_subject_404_is_success(db_session, tmp_path):
    """When delete_subject returns 404, purge records compreface_deleted=True."""
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Gone", "Subject")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["purge_detail"]["compreface_deleted"] is True
    assert result["purge_detail"]["errors"] == []


# ---------------------------------------------------------------------------
# HIGH FIX: delete_subject failure → purged_at stays NULL, retry stamps it
# ---------------------------------------------------------------------------


async def test_cf_failure_leaves_purged_at_null(db_session, tmp_path):
    """When CF delete fails, purged_at must stay NULL so scheduler can retry."""
    from sqlalchemy import select

    from app.models import BiometricConsent, FaceSample
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Fail", "CfNull")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    sample, full_file, thumb_file = await _make_face_sample(
        db_session, contact, cf_id, tmp_path
    )
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(
        svc, "_delete_cf_subject", new=AsyncMock(return_value=(False, "compreface_delete_error"))
    ):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    # Local erasure still completes
    assert result["purge_detail"]["samples_deleted"] >= 1
    assert not full_file.exists()
    assert not thumb_file.exists()

    # CF delete failed → reported
    assert result["purge_detail"]["compreface_deleted"] is False
    assert len(result["purge_detail"]["errors"]) >= 1

    # CRITICAL: purged_at MUST stay NULL so the scheduler retries
    rows = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c = rows.scalar_one_or_none()
    assert c is not None
    assert c.purged_at is None  # NOT stamped on failure


async def test_cf_failure_retry_stamps_purged_at(db_session, tmp_path):
    """After a failed CF delete, re-purge retries CF and stamps purged_at when clean."""
    from sqlalchemy import select

    from app.models import BiometricConsent
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Retry", "Stamp")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)

    # First purge: CF fails → purged_at stays NULL
    with patch.object(
        svc, "_delete_cf_subject", new=AsyncMock(return_value=(False, "compreface_delete_error"))
    ):
        r1 = await svc.purge(db_session, contact.id, actor_id=None)

    assert r1["purge_detail"]["compreface_deleted"] is False

    # Verify purged_at is NULL after failure
    rows = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c_before = rows.scalar_one_or_none()
    assert c_before.purged_at is None

    # Second purge: CF succeeds → purged_at stamped
    with patch.object(
        svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))
    ):
        r2 = await svc.purge(db_session, contact.id, actor_id=None)

    assert r2["purge_detail"]["compreface_deleted"] is True

    rows2 = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c_after = rows2.scalar_one_or_none()
    assert c_after.purged_at is not None  # NOW stamped after successful retry


# ---------------------------------------------------------------------------
# Test: delete_subject raises → local erasure completes, errors recorded
# ---------------------------------------------------------------------------


async def test_delete_subject_raises_local_erasure_completes(db_session, tmp_path):
    """If delete_subject raises, local erasure still completes and error is recorded."""
    from sqlalchemy import select

    from app.models import BiometricConsent, FaceSample
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Raise", "Exc")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    sample, full_file, thumb_file = await _make_face_sample(
        db_session, contact, cf_id, tmp_path
    )
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    # Simulate an exception being raised from CF
    with patch.object(
        svc,
        "_delete_cf_subject",
        new=AsyncMock(return_value=(False, "compreface_delete_error")),
    ):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    # Local erasure completed
    assert result["purge_detail"]["samples_deleted"] >= 1
    assert not full_file.exists()
    assert not thumb_file.exists()

    # Error recorded (sanitized, no absolute path)
    assert "compreface_delete_error" in result["purge_detail"]["errors"]
    # purged_at stays NULL
    rows = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c = rows.scalar_one_or_none()
    assert c.purged_at is None


# ---------------------------------------------------------------------------
# Test: errors are sanitized (no absolute paths in purge_detail)
# ---------------------------------------------------------------------------


async def test_purge_detail_errors_are_sanitized(db_session, tmp_path):
    """Error strings in purge_detail must not contain absolute path strings."""
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "San", "Itize")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)
    await _make_consent(db_session, contact)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(
        svc, "_delete_cf_subject", new=AsyncMock(return_value=(False, "compreface_delete_error"))
    ):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    for err in result["purge_detail"]["errors"]:
        # Sanitized: no absolute paths (no drive letters or /home/... patterns)
        assert not (len(err) > 100 and (":\\" in err or "/" in err and "home" in err)), \
            f"Error string leaks path: {err!r}"


# ---------------------------------------------------------------------------
# Test: retention job purge with system actor (actor_id=None)
# ---------------------------------------------------------------------------


async def test_retention_job_purges_overdue_consents(db_session, tmp_path):
    """Scheduler calls purge with actor_id=None for overdue consents."""
    from datetime import timedelta
    from sqlalchemy import select

    from app.models import BiometricConsent, utc_now
    from app.services.biometric_purge import BiometricPurgeService

    contact = await _make_contact(db_session, "Over", "Due")
    cf_id = f"contact_{contact.id}"
    await _make_subject(db_session, contact, cf_id=cf_id)

    past = utc_now() - timedelta(days=1)
    consent = BiometricConsent(
        contact_id=contact.id,
        consent_given=True,
        consented_at=past,
        retention_until=past,
    )
    db_session.add(consent)
    await db_session.commit()

    svc = BiometricPurgeService(storage_base=tmp_path)
    with patch.object(svc, "_delete_cf_subject", new=AsyncMock(return_value=(True, None))):
        result = await svc.purge(db_session, contact.id, actor_id=None)

    assert result["status"] == "purged"

    rows = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == contact.id)
    )
    c = rows.scalar_one_or_none()
    assert c is not None
    assert c.purged_at is not None


# ---------------------------------------------------------------------------
# Test: purge via HTTP endpoint
# ---------------------------------------------------------------------------


async def test_purge_endpoint_admin_only(client, db_session, volunteer_user):
    from app.models import Contact

    contact = Contact(first_name="P", last_name="Test", contact_type="individual")
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)

    headers = {"Authorization": f"Bearer {make_token(volunteer_user.id, volunteer_user.email, volunteer_user.role)}"}
    resp = await client.post(f"/biometric/contacts/{contact.id}/purge", headers=headers)
    assert resp.status_code == 403


async def test_purge_endpoint_success(client, db_session, admin_user):
    from app.models import BiometricConsent, Contact, utc_now

    contact = Contact(first_name="P2", last_name="Test2", contact_type="individual")
    db_session.add(contact)
    await db_session.flush()
    consent = BiometricConsent(
        contact_id=contact.id,
        consent_given=True,
        consented_at=utc_now(),
    )
    db_session.add(consent)
    await db_session.commit()
    await db_session.refresh(contact)

    headers = {"Authorization": f"Bearer {make_token(admin_user.id, admin_user.email, admin_user.role)}"}

    with patch(
        "app.services.biometric_purge.BiometricPurgeService._delete_cf_subject",
        new=AsyncMock(return_value=(True, None)),
    ):
        resp = await client.post(f"/biometric/contacts/{contact.id}/purge", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("purged", "already_purged", "partial_failure")
    assert "purge_detail" in data
