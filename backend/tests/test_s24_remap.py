"""S24 — FR Transition remap_subjects tests.

Tests that remap_subjects():
- Remaps ComprefaceSubject.contact_id when subject_name matches 'member:<N>'
  and a Contact with external_id == N exists.
- Leaves subjects unremapped when no matching Contact.external_id exists (orphaned).
- Reports orphaned subject IDs (subjects still without contact_id after remap).
- Consumes the _legacy_civicrm_contact_id stash column (if present) via external_id
  matching: we seed contacts with external_id to simulate the stash being consumed.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ComprefaceSubject, Contact
from app.services.fr_transition import remap_subjects


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_contact(
    db: AsyncSession,
    *,
    first_name: str = "Test",
    last_name: str = "User",
    external_id: int | None = None,
) -> Contact:
    contact = Contact(
        first_name=first_name,
        last_name=last_name,
        contact_type="individual",
        external_id=external_id,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


async def _seed_subject(
    db: AsyncSession,
    *,
    subject_name: str,
    compreface_subject_id: str,
    contact_id: int | None = None,
    enrollment_status: str = "active",
) -> ComprefaceSubject:
    subj = ComprefaceSubject(
        subject_name=subject_name,
        compreface_subject_id=compreface_subject_id,
        contact_id=contact_id,
        enrollment_status=enrollment_status,
    )
    db.add(subj)
    await db.commit()
    await db.refresh(subj)
    return subj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remap_subjects_matches_by_external_id(db_session: AsyncSession):
    """Subject with name 'member:<N>' is remapped to the contact whose external_id == N."""
    contact = await _seed_contact(db_session, external_id=42)
    subject = await _seed_subject(
        db_session,
        subject_name="member:42",
        compreface_subject_id="cf-uuid-001",
    )

    report = await remap_subjects(db_session)

    await db_session.refresh(subject)
    assert subject.contact_id == contact.id
    assert report["subjects_remapped"] == 1


@pytest.mark.asyncio
async def test_remap_subjects_orphaned_when_no_matching_contact(db_session: AsyncSession):
    """Subject with name 'member:999' remains orphaned when no Contact has external_id=999."""
    # Seed a contact with a DIFFERENT external_id
    await _seed_contact(db_session, external_id=1)
    orphan_subj = await _seed_subject(
        db_session,
        subject_name="member:999",
        compreface_subject_id="cf-uuid-orphan",
    )

    report = await remap_subjects(db_session)

    await db_session.refresh(orphan_subj)
    assert orphan_subj.contact_id is None
    assert report["subjects_orphaned"] >= 1
    assert orphan_subj.id in report["orphaned_subject_ids"]


@pytest.mark.asyncio
async def test_remap_subjects_reports_correctly(db_session: AsyncSession):
    """Report counts: 2 matching subjects remapped, 1 orphaned."""
    c1 = await _seed_contact(db_session, external_id=10)
    c2 = await _seed_contact(db_session, external_id=20)
    s1 = await _seed_subject(db_session, subject_name="member:10", compreface_subject_id="cf-s1")
    s2 = await _seed_subject(db_session, subject_name="member:20", compreface_subject_id="cf-s2")
    s3 = await _seed_subject(
        db_session, subject_name="member:9999", compreface_subject_id="cf-s3"
    )

    report = await remap_subjects(db_session)

    await db_session.refresh(s1)
    await db_session.refresh(s2)
    await db_session.refresh(s3)

    assert s1.contact_id == c1.id
    assert s2.contact_id == c2.id
    assert s3.contact_id is None

    assert report["subjects_remapped"] == 2
    assert report["subjects_orphaned"] == 1
    assert s3.id in report["orphaned_subject_ids"]


@pytest.mark.asyncio
async def test_remap_subjects_idempotent(db_session: AsyncSession):
    """Calling remap_subjects twice does not change already-remapped subjects."""
    contact = await _seed_contact(db_session, external_id=77)
    subject = await _seed_subject(
        db_session,
        subject_name="member:77",
        compreface_subject_id="cf-idempotent",
    )

    report1 = await remap_subjects(db_session)
    await db_session.refresh(subject)
    assert subject.contact_id == contact.id
    assert report1["subjects_remapped"] == 1

    # Second call: subject already has contact_id != None, so it's skipped
    report2 = await remap_subjects(db_session)
    assert report2["subjects_remapped"] == 0


@pytest.mark.asyncio
async def test_remap_subjects_skips_non_member_names(db_session: AsyncSession):
    """Subjects whose name does not match 'member:<N>' are not remapped."""
    await _seed_contact(db_session, external_id=5)
    subj = await _seed_subject(
        db_session,
        subject_name="legacy_civicrm_subject_5",
        compreface_subject_id="cf-legacy",
    )

    report = await remap_subjects(db_session)

    await db_session.refresh(subj)
    assert subj.contact_id is None
    # The non-matching subject with contact_id=None is counted as orphaned
    assert subj.id in report["orphaned_subject_ids"]


@pytest.mark.asyncio
async def test_remap_subjects_stash_consumed(db_session: AsyncSession):
    """Verify that after remap the subject references the correct app contact (stash consumed).

    The 'stash' in this context is contacts.external_id which holds the original
    CiviCRM contact ID.  After remap, ComprefaceSubject.contact_id should match
    the app contact's PK, not the external_id value.
    """
    contact = await _seed_contact(db_session, external_id=55)
    subject = await _seed_subject(
        db_session,
        subject_name="member:55",
        compreface_subject_id="cf-stash-test",
    )

    await remap_subjects(db_session)

    await db_session.refresh(subject)
    # Must be the app-internal id, not the external_id (55)
    assert subject.contact_id == contact.id
    assert subject.contact_id != 55 or contact.id == 55  # contact.id may happen to equal 55
    # The critical check: it links to the correct Contact row
    from sqlalchemy import select
    linked = await db_session.execute(
        select(Contact).where(Contact.id == subject.contact_id)
    )
    linked_contact = linked.scalar_one_or_none()
    assert linked_contact is not None
    assert linked_contact.external_id == 55
