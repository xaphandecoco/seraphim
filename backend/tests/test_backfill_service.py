"""test_backfill_service.py — s07-backfill-service QA

Acceptance criteria tested:
  AC1. dry_run=True returns BackfillReport without committing DB changes
       (face_samples count unchanged before/after).
  AC2. dry_run=False commits orphan flags and seeded face_samples rows.
  AC3. image_path uniqueness checked (SELECT COUNT WHERE image_path=?) —
       re-running dry_run=False does not create duplicate face_samples rows.
  AC4. purged_at IS NOT NULL subjects are skipped entirely.
  AC5. Unregistered CF subjects (CF has them, DB does not) are logged only —
       no DB rows created.
  AC6. Returns BackfillReport with orphans_found, samples_seeded,
       unregistered_cf_subjects, dry_run fields.
  AC7. Idempotent — running twice with dry_run=False produces same result
       as running once.

Bug regression tests:
  BUG1. FaceSample.subject_id does not exist — column is compreface_subject_id.
        _seed_samples_from_disk uses subject_id in both the COUNT query and
        the FaceSample() constructor → AttributeError at runtime.
  BUG2. FaceSample.thumb_path is nullable=False; backfill passes None when
        no _thumb.jpg exists on disk → NOT NULL constraint violation.
"""
from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, Contact, FaceSample
from app.services.backfill import BackfillReport, BackfillService
from app.services.compreface import Subject as CFSubject


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _make_contact(db_session: AsyncSession, *, is_deleted: bool = False) -> Contact:
    contact = Contact(
        first_name="Test",
        last_name="Backfill",
        contact_type="Individual",
        is_deleted=is_deleted,
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact


async def _make_subject(
    db_session: AsyncSession,
    contact_id: Optional[int],
    *,
    cf_id: str = "cf-subj-001",
    purged_at: Optional[datetime] = None,
    is_orphan: bool = False,
) -> ComprefaceSubject:
    subject = ComprefaceSubject(
        subject_name="Test Backfill Subject",
        compreface_subject_id=cf_id,
        contact_id=contact_id,
        enrollment_status="active",
        sample_count=0,
        purged_at=purged_at,
        is_orphan=is_orphan,
    )
    db_session.add(subject)
    await db_session.commit()
    await db_session.refresh(subject)
    return subject


def _make_cf_client(subjects: list[str] | None = None) -> AsyncMock:
    """Return a mock ComprefaceClient whose list_subjects returns the given ids."""
    client = AsyncMock()
    client.list_subjects = AsyncMock(
        return_value=[CFSubject(subject=s) for s in (subjects or [])]
    )
    return client


def _make_storage(base_path: str) -> MagicMock:
    storage = MagicMock()
    storage.base_path = pathlib.Path(base_path)
    return storage


# ---------------------------------------------------------------------------
# AC6 — BackfillReport shape
# ---------------------------------------------------------------------------

async def test_report_has_required_fields(db_session: AsyncSession, sample_contact):
    """BackfillService.run() returns a BackfillReport with all required fields."""
    cf_id = f"contact_{sample_contact.id}"
    await _make_subject(db_session, sample_contact.id, cf_id=cf_id)

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=True,
        )

    assert isinstance(report, BackfillReport)
    assert hasattr(report, "dry_run")
    assert hasattr(report, "orphans_found")
    assert hasattr(report, "samples_seeded")
    assert hasattr(report, "unregistered_cf_subjects")
    assert report.dry_run is True


# ---------------------------------------------------------------------------
# AC1 — dry_run=True does NOT commit DB changes
# ---------------------------------------------------------------------------

async def test_dry_run_true_no_db_changes(db_session: AsyncSession, sample_contact):
    """dry_run=True must rollback all changes — face_samples count unchanged."""
    cf_id = f"contact_{sample_contact.id}"
    await _make_subject(db_session, sample_contact.id, cf_id=cf_id)

    count_before = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create enrolled directory with a sample file so seeding is attempted
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        sample_file = enrolled_dir / "sample_001_full.jpg"
        sample_file.write_bytes(b"\xff\xd8\xff")  # minimal JPEG bytes

        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=True,
        )

    count_after = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    assert report.dry_run is True
    assert count_after == count_before, (
        f"face_samples changed after dry_run=True: {count_before} → {count_after}"
    )


async def test_dry_run_true_orphan_flag_not_persisted(db_session: AsyncSession):
    """dry_run=True: orphan flag set in memory is rolled back — DB row unchanged."""
    contact = await _make_contact(db_session)
    subject = await _make_subject(
        db_session, contact.id, cf_id="cf-orphan-dry", is_orphan=False
    )
    assert subject.is_orphan is False

    # CF client returns empty list — subject will be flagged as orphan in memory
    cf_client = _make_cf_client([])  # no CF subjects

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=True,
        )

    assert report.orphans_found >= 1
    assert report.dry_run is True

    # Reload from DB — must still be False
    await db_session.refresh(subject)
    assert subject.is_orphan is False, (
        "dry_run=True must not persist is_orphan=True to the DB"
    )


# ---------------------------------------------------------------------------
# AC2 — dry_run=False commits orphan flags and seeded face_samples rows
# ---------------------------------------------------------------------------

async def test_dry_run_false_commits_orphan_flag(db_session: AsyncSession):
    """dry_run=False: subject not in CF live list → is_orphan=True committed."""
    contact = await _make_contact(db_session)
    subject = await _make_subject(
        db_session, contact.id, cf_id="cf-orphan-commit", is_orphan=False
    )

    cf_client = _make_cf_client([])  # subject absent from CF → orphan

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    assert report.dry_run is False
    assert report.orphans_found >= 1

    await db_session.refresh(subject)
    assert subject.is_orphan is True, (
        "dry_run=False must persist is_orphan=True to the DB"
    )


async def test_dry_run_false_seeds_face_samples(db_session: AsyncSession):
    """dry_run=False: on-disk sample files → FaceSample rows committed.

    This test directly exposes BUG1 (FaceSample.subject_id → AttributeError)
    and BUG2 (thumb_path NOT NULL violation when thumb absent).
    """
    contact = await _make_contact(db_session)
    cf_id = f"contact_{contact.id}_backfill"
    await _make_subject(db_session, contact.id, cf_id=cf_id)

    cf_client = _make_cf_client([cf_id])

    count_before = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    with tempfile.TemporaryDirectory() as tmpdir:
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        # Create one full sample file; no thumb file (tests BUG2 — thumb may be absent)
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")

        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    count_after = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    assert report.dry_run is False
    assert report.samples_seeded >= 1, (
        f"Expected at least 1 sample seeded, got {report.samples_seeded}"
    )
    assert count_after > count_before, (
        f"face_samples count did not increase after dry_run=False: "
        f"{count_before} → {count_after}"
    )


async def test_dry_run_false_seeds_face_samples_with_thumb(db_session: AsyncSession):
    """dry_run=False with both full and thumb files on disk — thumb_path set correctly."""
    contact = await _make_contact(db_session)
    cf_id = f"contact_{contact.id}_with_thumb"
    await _make_subject(db_session, contact.id, cf_id=cf_id)

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")
        (enrolled_dir / "sample_001_thumb.jpg").write_bytes(b"\xff\xd8\xff")

        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    assert report.samples_seeded == 1

    # The FaceSample row should have both image_path and thumb_path set
    result = await db_session.execute(
        select(FaceSample).where(FaceSample.source == "backfill")
    )
    sample = result.scalar_one_or_none()
    assert sample is not None
    assert sample.image_path is not None and "full" in sample.image_path
    assert sample.thumb_path is not None and "thumb" in sample.thumb_path


# ---------------------------------------------------------------------------
# AC3 — image_path uniqueness prevents duplicates on re-run
# ---------------------------------------------------------------------------

async def test_image_path_uniqueness_prevents_duplicate_on_rerun(
    db_session: AsyncSession,
):
    """Running dry_run=False twice must not create duplicate FaceSample rows."""
    contact = await _make_contact(db_session)
    cf_id = f"contact_{contact.id}_idempotent"
    await _make_subject(db_session, contact.id, cf_id=cf_id)

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")
        (enrolled_dir / "sample_001_thumb.jpg").write_bytes(b"\xff\xd8\xff")

        storage = _make_storage(tmpdir)
        svc = BackfillService()

        # First run
        report1 = await svc.run(
            session=db_session,
            compreface=_make_cf_client([cf_id]),
            storage=storage,
            dry_run=False,
        )

        count_after_first = (
            await db_session.execute(select(func.count(FaceSample.id)))
        ).scalar()

        # Second run — same on-disk files, same CF state
        report2 = await svc.run(
            session=db_session,
            compreface=_make_cf_client([cf_id]),
            storage=storage,
            dry_run=False,
        )

        count_after_second = (
            await db_session.execute(select(func.count(FaceSample.id)))
        ).scalar()

    assert report1.samples_seeded >= 1, "First run must seed at least one row"
    assert report2.samples_seeded == 0, (
        f"Second run must seed 0 new rows (got {report2.samples_seeded}) — "
        "idempotency broken"
    )
    assert count_after_second == count_after_first, (
        f"face_samples count changed between first and second run: "
        f"{count_after_first} → {count_after_second}"
    )


# ---------------------------------------------------------------------------
# AC4 — purged_at IS NOT NULL subjects are skipped entirely
# ---------------------------------------------------------------------------

async def test_purged_subject_is_skipped(db_session: AsyncSession):
    """Subjects with purged_at set must be skipped — no orphan flag, no samples."""
    contact = await _make_contact(db_session)
    cf_id = "cf-purged-subj"
    purged_subject = await _make_subject(
        db_session, contact.id, cf_id=cf_id, purged_at=_utc_now()
    )
    assert purged_subject.purged_at is not None

    # CF does NOT have this subject (so it would be an orphan if not purged)
    cf_client = _make_cf_client([])

    count_before = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create on-disk files that would be seeded if the subject weren't purged
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")

        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    count_after = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    assert report.orphans_found == 0, (
        "Purged subjects must not be counted as orphans "
        f"(got orphans_found={report.orphans_found})"
    )
    assert report.samples_seeded == 0, (
        f"Purged subjects must not seed samples (got {report.samples_seeded})"
    )
    assert count_after == count_before, (
        "No face_samples rows must be created for purged subjects"
    )

    # is_orphan must NOT be set on the purged subject
    await db_session.refresh(purged_subject)
    assert purged_subject.is_orphan is False, (
        "purged_at IS NOT NULL subjects must not be flagged as orphans"
    )


# ---------------------------------------------------------------------------
# AC5 — unregistered CF subjects logged only, no DB rows
# ---------------------------------------------------------------------------

async def test_unregistered_cf_subjects_logged_not_written(
    db_session: AsyncSession,
):
    """CF subjects absent from DB are counted in the report but no DB rows created."""
    # DB has no subjects at all
    # CF has two subjects that are not in our DB
    cf_client = _make_cf_client(["ghost-cf-001", "ghost-cf-002"])

    count_before_subjects = (
        await db_session.execute(
            select(func.count(ComprefaceSubject.id))
        )
    ).scalar()
    count_before_samples = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    count_after_subjects = (
        await db_session.execute(
            select(func.count(ComprefaceSubject.id))
        )
    ).scalar()
    count_after_samples = (
        await db_session.execute(select(func.count(FaceSample.id)))
    ).scalar()

    assert report.unregistered_cf_subjects == 2, (
        f"Expected 2 unregistered CF subjects, got {report.unregistered_cf_subjects}"
    )
    assert count_after_subjects == count_before_subjects, (
        "No ComprefaceSubject rows must be created for unregistered CF subjects"
    )
    assert count_after_samples == count_before_samples, (
        "No FaceSample rows must be created for unregistered CF subjects"
    )


# ---------------------------------------------------------------------------
# AC7 — Idempotent: running twice with dry_run=False == running once
# ---------------------------------------------------------------------------

async def test_idempotent_double_run_orphan_count(db_session: AsyncSession):
    """Running dry_run=False twice must yield same orphan_found count both times.

    The service counts both newly-flagged and already-flagged orphans so
    orphans_found is stable across re-runs.
    """
    contact = await _make_contact(db_session)
    cf_id = "cf-double-orphan"
    await _make_subject(db_session, contact.id, cf_id=cf_id)

    # CF does not have the subject → it will be orphaned
    cf_client_factory = lambda: _make_cf_client([])

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()

        report1 = await svc.run(
            session=db_session,
            compreface=cf_client_factory(),
            storage=storage,
            dry_run=False,
        )
        report2 = await svc.run(
            session=db_session,
            compreface=cf_client_factory(),
            storage=storage,
            dry_run=False,
        )

    assert report1.orphans_found == report2.orphans_found, (
        f"orphans_found not stable across runs: {report1.orphans_found} vs "
        f"{report2.orphans_found}"
    )
    assert report2.samples_seeded == 0, (
        "Second run must not seed additional samples"
    )


# ---------------------------------------------------------------------------
# Additional edge-case: contact_id IS NULL → orphan
# ---------------------------------------------------------------------------

async def test_null_contact_id_flagged_as_orphan(db_session: AsyncSession):
    """Subjects with contact_id=None are flagged as orphans regardless of CF state."""
    subject = await _make_subject(
        db_session, None, cf_id="cf-null-contact", is_orphan=False
    )

    # CF has the subject (so it wouldn't be an orphan from CF absence alone)
    cf_client = _make_cf_client(["cf-null-contact"])

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    assert report.orphans_found >= 1
    await db_session.refresh(subject)
    assert subject.is_orphan is True, (
        "Subject with contact_id=None must be flagged is_orphan=True"
    )


# ---------------------------------------------------------------------------
# Additional edge-case: deleted contact → orphan
# ---------------------------------------------------------------------------

async def test_deleted_contact_flags_subject_as_orphan(db_session: AsyncSession):
    """Subject whose contact has is_deleted=True is flagged as orphan."""
    contact = await _make_contact(db_session, is_deleted=True)
    cf_id = f"cf-deleted-contact-{contact.id}"
    subject = await _make_subject(
        db_session, contact.id, cf_id=cf_id, is_orphan=False
    )

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=False,
        )

    assert report.orphans_found >= 1
    await db_session.refresh(subject)
    assert subject.is_orphan is True, (
        "Subject whose contact is_deleted=True must be flagged is_orphan=True"
    )


# ---------------------------------------------------------------------------
# BUG1 regression: FaceSample.subject_id does not exist
# ---------------------------------------------------------------------------

async def test_seed_samples_uses_correct_column_name(db_session: AsyncSession):
    """Regression: _seed_samples_from_disk must use compreface_subject_id, not subject_id.

    If FaceSample.subject_id is referenced, SQLAlchemy raises:
      AttributeError: type object 'FaceSample' has no mapped attribute 'subject_id'

    This test calls the private method directly to verify it executes without
    AttributeError and uses the correct column in its query.
    """
    contact = await _make_contact(db_session)
    cf_id = f"contact_{contact.id}_bug1"
    subject = await _make_subject(db_session, contact.id, cf_id=cf_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")
        (enrolled_dir / "sample_001_thumb.jpg").write_bytes(b"\xff\xd8\xff")

        enrolled_base = pathlib.Path(tmpdir) / "enrolled"
        svc = BackfillService()

        # Must NOT raise AttributeError
        seeded = await svc._seed_samples_from_disk(
            session=db_session,
            subject=subject,
            enrolled_base=enrolled_base,
        )

    # Flush happened inside _seed_samples_from_disk; verify the row exists
    result = await db_session.execute(
        select(func.count(FaceSample.id)).where(
            FaceSample.compreface_subject_id == cf_id
        )
    )
    count = result.scalar() or 0

    assert seeded == 1, f"Expected 1 sample seeded, got {seeded}"
    assert count == 1, (
        f"Expected 1 FaceSample row for cf_id={cf_id!r}, found {count}. "
        "BUG1: _seed_samples_from_disk used wrong column 'subject_id'."
    )


# ---------------------------------------------------------------------------
# BUG2 regression: thumb_path NOT NULL when thumb file absent
# ---------------------------------------------------------------------------

async def test_seed_samples_no_thumb_does_not_violate_not_null(
    db_session: AsyncSession,
):
    """Regression: thumb_path is NOT NULL in the ORM.

    When no _thumb.jpg file exists, backfill passes thumb_path=None which
    causes a NOT NULL constraint violation at flush time.  The fix is to
    store an empty string or the full_path as fallback (or make thumb_path
    nullable).  This test verifies the call completes without IntegrityError.
    """
    contact = await _make_contact(db_session)
    cf_id = f"contact_{contact.id}_nothumb"
    subject = await _make_subject(db_session, contact.id, cf_id=cf_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        enrolled_dir = pathlib.Path(tmpdir) / "enrolled" / cf_id
        enrolled_dir.mkdir(parents=True)
        # Only full file — NO thumb file
        (enrolled_dir / "sample_001_full.jpg").write_bytes(b"\xff\xd8\xff")

        enrolled_base = pathlib.Path(tmpdir) / "enrolled"
        svc = BackfillService()

        # Must NOT raise IntegrityError or any DB error
        seeded = await svc._seed_samples_from_disk(
            session=db_session,
            subject=subject,
            enrolled_base=enrolled_base,
        )

    assert seeded == 1, (
        f"Expected 1 sample seeded even without thumb file, got {seeded}. "
        "BUG2: thumb_path=None violates NOT NULL constraint."
    )

    result = await db_session.execute(
        select(FaceSample).where(FaceSample.compreface_subject_id == cf_id)
    )
    sample = result.scalar_one_or_none()
    assert sample is not None
    # thumb_path must not be None (NOT NULL column)
    assert sample.thumb_path is not None, (
        "thumb_path must not be None — NOT NULL constraint. "
        "BUG2: backfill must use a fallback (e.g. empty string or full path) "
        "when no thumb file exists."
    )


# ---------------------------------------------------------------------------
# AC1 + AC6 combined: dry_run message in report
# ---------------------------------------------------------------------------

async def test_dry_run_report_message_mentions_dry_run(
    db_session: AsyncSession, sample_contact
):
    """Report message should indicate dry_run state."""
    cf_id = f"contact_{sample_contact.id}_msg"
    await _make_subject(db_session, sample_contact.id, cf_id=cf_id)

    cf_client = _make_cf_client([cf_id])

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        report = await svc.run(
            session=db_session,
            compreface=cf_client,
            storage=storage,
            dry_run=True,
        )

    assert "dry" in report.message.lower() or "run" in report.message.lower(), (
        f"Report message should reference dry_run; got: {report.message!r}"
    )


# ---------------------------------------------------------------------------
# CF client failure propagates
# ---------------------------------------------------------------------------

async def test_compreface_failure_propagates(db_session: AsyncSession, sample_contact):
    """If list_subjects raises, BackfillService.run() re-raises the exception."""
    cf_client = AsyncMock()
    cf_client.list_subjects = AsyncMock(
        side_effect=RuntimeError("CompreFace unavailable")
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = _make_storage(tmpdir)
        svc = BackfillService()
        with pytest.raises(RuntimeError, match="CompreFace unavailable"):
            await svc.run(
                session=db_session,
                compreface=cf_client,
                storage=storage,
                dry_run=True,
            )
