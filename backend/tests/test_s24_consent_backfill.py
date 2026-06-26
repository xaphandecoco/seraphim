"""S24 — Consent backfill tests.

Acceptance criteria:
- 3 active remapped subjects → 3 BiometricConsent rows + enroll_without_consent=True.
- Rerun → 0 new rows (idempotent).
- Only active subjects with non-null contact_id get consent rows.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSetting, BiometricConsent, ComprefaceSubject, Contact
from app.services.fr_transition import backfill_consent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_contact(db: AsyncSession, *, email_suffix: str = "") -> Contact:
    c = Contact(
        first_name="BC",
        last_name="Test",
        contact_type="individual",
        email=f"bc{email_suffix}@test.com",
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _seed_active_subject(
    db: AsyncSession,
    contact_id: int,
    cf_id: str,
) -> ComprefaceSubject:
    subj = ComprefaceSubject(
        subject_name=f"member:{contact_id}",
        compreface_subject_id=cf_id,
        contact_id=contact_id,
        enrollment_status="active",
    )
    db.add(subj)
    await db.commit()
    await db.refresh(subj)
    return subj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_consent_creates_rows_for_active_subjects(db_session: AsyncSession):
    """3 active remapped subjects → 3 consent rows."""
    c1 = await _seed_contact(db_session, email_suffix="1")
    c2 = await _seed_contact(db_session, email_suffix="2")
    c3 = await _seed_contact(db_session, email_suffix="3")

    await _seed_active_subject(db_session, c1.id, "cf-bc1")
    await _seed_active_subject(db_session, c2.id, "cf-bc2")
    await _seed_active_subject(db_session, c3.id, "cf-bc3")

    report = await backfill_consent(db_session)

    assert report["consent_rows_created"] == 3
    assert report["already_had_consent"] == 0
    assert report["enroll_without_consent_set"] is True

    # Verify DB rows
    rows = await db_session.execute(select(BiometricConsent))
    consent_rows = rows.scalars().all()
    assert len(consent_rows) == 3

    contact_ids_with_consent = {r.contact_id for r in consent_rows}
    assert c1.id in contact_ids_with_consent
    assert c2.id in contact_ids_with_consent
    assert c3.id in contact_ids_with_consent


@pytest.mark.asyncio
async def test_backfill_consent_sets_enroll_without_consent_true(db_session: AsyncSession):
    """backfill_consent upserts AdminSetting enroll_without_consent = {'value': True}."""
    c = await _seed_contact(db_session, email_suffix="ewc")
    await _seed_active_subject(db_session, c.id, "cf-ewc")

    await backfill_consent(db_session)

    setting = await db_session.get(AdminSetting, "enroll_without_consent")
    assert setting is not None
    assert setting.value == {"value": True}


@pytest.mark.asyncio
async def test_backfill_consent_idempotent_second_run_creates_zero_rows(
    db_session: AsyncSession,
):
    """Second call to backfill_consent must create 0 new rows."""
    c1 = await _seed_contact(db_session, email_suffix="idem1")
    c2 = await _seed_contact(db_session, email_suffix="idem2")
    c3 = await _seed_contact(db_session, email_suffix="idem3")

    await _seed_active_subject(db_session, c1.id, "cf-idem1")
    await _seed_active_subject(db_session, c2.id, "cf-idem2")
    await _seed_active_subject(db_session, c3.id, "cf-idem3")

    # First run
    first = await backfill_consent(db_session)
    assert first["consent_rows_created"] == 3

    # Second run (idempotent)
    second = await backfill_consent(db_session)
    assert second["consent_rows_created"] == 0
    assert second["already_had_consent"] == 3
    assert second["enroll_without_consent_set"] is True

    # Total consent rows still 3
    rows = await db_session.execute(select(BiometricConsent))
    all_rows = rows.scalars().all()
    assert len(all_rows) == 3


@pytest.mark.asyncio
async def test_backfill_consent_skips_subjects_without_contact_id(db_session: AsyncSession):
    """Subjects with contact_id=None (not yet remapped) are skipped."""
    c = await _seed_contact(db_session, email_suffix="skip_null")

    # One active subject with contact, one without
    await _seed_active_subject(db_session, c.id, "cf-with-contact")
    null_subj = ComprefaceSubject(
        subject_name="member:9999",
        compreface_subject_id="cf-no-contact",
        contact_id=None,
        enrollment_status="active",
    )
    db_session.add(null_subj)
    await db_session.commit()

    report = await backfill_consent(db_session)

    # Only 1 consent row for the subject with a contact
    assert report["consent_rows_created"] == 1

    rows = await db_session.execute(select(BiometricConsent))
    consent_rows = rows.scalars().all()
    assert len(consent_rows) == 1
    assert consent_rows[0].contact_id == c.id


@pytest.mark.asyncio
async def test_backfill_consent_skips_inactive_subjects(db_session: AsyncSession):
    """Subjects with enrollment_status != 'active' are skipped."""
    c1 = await _seed_contact(db_session, email_suffix="active_s24")
    c2 = await _seed_contact(db_session, email_suffix="pending_s24")

    await _seed_active_subject(db_session, c1.id, "cf-active-subj")

    # Pending subject — should be skipped
    pending_subj = ComprefaceSubject(
        subject_name=f"member:{c2.id}",
        compreface_subject_id="cf-pending-subj",
        contact_id=c2.id,
        enrollment_status="pending",
    )
    db_session.add(pending_subj)
    await db_session.commit()

    report = await backfill_consent(db_session)

    assert report["consent_rows_created"] == 1

    rows = await db_session.execute(select(BiometricConsent))
    consent_rows = rows.scalars().all()
    assert len(consent_rows) == 1
    assert consent_rows[0].contact_id == c1.id


@pytest.mark.asyncio
async def test_backfill_consent_consent_row_has_correct_fields(db_session: AsyncSession):
    """Consent rows created by backfill have consent_given=False and correct basis_note."""
    c = await _seed_contact(db_session, email_suffix="fields")
    await _seed_active_subject(db_session, c.id, "cf-fields")

    await backfill_consent(db_session)

    rows = await db_session.execute(
        select(BiometricConsent).where(BiometricConsent.contact_id == c.id)
    )
    row = rows.scalar_one_or_none()
    assert row is not None
    assert row.consent_given is False
    assert row.basis_note == "pre-cutover-unknown"
