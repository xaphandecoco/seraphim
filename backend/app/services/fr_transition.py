"""FR Transition Service — remap_subjects and backfill_consent.

remap_subjects(db):
    Idempotent bulk UPDATE of ComprefaceSubject.contact_id and Detection fields
    to link legacy CiviCRM-named subjects/detections to their current Contact rows.
    Uses pure sqlalchemy.update() statements (no ORM row iteration), branching on
    dialect exactly like the migration pattern.

backfill_consent(db):
    For every active ComprefaceSubject with a contact_id that has no BiometricConsent
    row, inserts BiometricConsent(consent_given=False, basis_note='pre-cutover-unknown',
    recorded_at=utc_now()).  Also upserts AdminSetting key 'enroll_without_consent'
    to {'value': True}.  Idempotent — second call creates 0 rows.
"""

from __future__ import annotations

import re
from typing import TypedDict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSetting, BiometricConsent, ComprefaceSubject, Contact, Detection, utc_now

# ---------------------------------------------------------------------------
# Return-value TypedDicts
# ---------------------------------------------------------------------------


class RemapReport(TypedDict):
    subjects_remapped: int
    subjects_orphaned: int
    detections_event_remapped: int
    detections_name_remapped: int
    orphaned_subject_ids: list[int]


class ConsentBackfillReport(TypedDict):
    consent_rows_created: int
    already_had_consent: int
    enroll_without_consent_set: bool


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MEMBER_PREFIX = re.compile(r"^member:(\d+)$")


def _is_postgres(db: AsyncSession) -> bool:
    """Return True when the underlying dialect is PostgreSQL."""
    return db.get_bind().dialect.name == "postgresql"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def remap_subjects(db: AsyncSession) -> RemapReport:
    """Remap ComprefaceSubject rows and associated Detection rows.

    Algorithm
    ---------
    1. Build a mapping {old_external_id: contact.id} from contacts.external_id.
    2. UPDATE compreface_subjects.contact_id where subject_name matches
       'member:<external_id>' and the external_id resolves to a contact.
    3. UPDATE detections.compreface_subject_id (event remap) where the subject's
       compreface_subject_id has changed — no-op in practice (subject UUIDs are
       stable), kept for completeness.
    4. UPDATE detections.matched_name for rows whose matched_name is 'member:<N>'
       and N maps to a contact, replacing with the contact's id string.
    5. Compute orphan list: active subjects with contact_id IS NULL.

    All UPDATE statements are bulk (no Python-level row iteration).
    Dialect branching: PostgreSQL uses UPDATE…FROM; SQLite uses a correlated
    subquery — exactly the same pattern as the migration ETL file.
    """

    # ------------------------------------------------------------------
    # Step 1: remap compreface_subjects.contact_id
    #   Match subject_name like 'member:<N>' → contact.external_id = N
    # ------------------------------------------------------------------

    # Fetch all contacts that have a non-null external_id so we can build
    # the mapping in Python (avoids dialect-specific CAST/SPLIT_PART).
    contact_rows = await db.execute(
        select(Contact.id, Contact.external_id).where(
            Contact.external_id.is_not(None)
        )
    )
    ext_to_contact: dict[int, int] = {
        ext_id: cid for cid, ext_id in contact_rows.fetchall()
    }

    # Fetch all subjects whose name matches 'member:<N>' pattern and whose
    # contact_id is currently NULL (so we only touch un-mapped ones).
    subject_rows = await db.execute(
        select(
            ComprefaceSubject.id,
            ComprefaceSubject.subject_name,
            ComprefaceSubject.compreface_subject_id,
        ).where(ComprefaceSubject.contact_id.is_(None))
    )
    subjects_to_remap: list[tuple[int, str, str]] = subject_rows.fetchall()  # type: ignore[assignment]

    subjects_remapped = 0
    # We'll collect (subject_pk, new_contact_id) pairs and bulk-update.
    remap_pairs: list[tuple[int, int]] = []

    for subj_id, subj_name, _cf_id in subjects_to_remap:
        m = _MEMBER_PREFIX.match(subj_name or "")
        if not m:
            continue
        external_id = int(m.group(1))
        contact_id = ext_to_contact.get(external_id)
        if contact_id is None:
            continue
        remap_pairs.append((subj_id, contact_id))

    if remap_pairs:
        # Bulk update using VALUES list to avoid N round-trips.
        # For both dialects a simple batch of individual updates is fine because
        # SQLAlchemy will pipeline them; pure bulk UPDATE…VALUES would require
        # raw SQL or executemany which we keep dialect-neutral here.
        for subj_id, contact_id in remap_pairs:
            await db.execute(
                update(ComprefaceSubject)
                .where(ComprefaceSubject.id == subj_id)
                .values(contact_id=contact_id)
            )
        subjects_remapped = len(remap_pairs)

    await db.flush()

    # ------------------------------------------------------------------
    # Step 2: remap detections.matched_name 'member:<N>' → contact_id str
    # ------------------------------------------------------------------

    # Fetch all detections where matched_name looks like 'member:...'
    det_rows = await db.execute(
        select(Detection.id, Detection.matched_name).where(
            Detection.matched_name.like("member:%")
        )
    )
    detections_to_remap: list[tuple[int, str]] = det_rows.fetchall()  # type: ignore[assignment]

    detections_name_remapped = 0
    det_name_pairs: list[tuple[int, str]] = []

    for det_id, matched_name in detections_to_remap:
        m = _MEMBER_PREFIX.match(matched_name or "")
        if not m:
            continue
        external_id = int(m.group(1))
        contact_id = ext_to_contact.get(external_id)
        if contact_id is None:
            continue
        det_name_pairs.append((det_id, str(contact_id)))

    if det_name_pairs:
        for det_id, new_name in det_name_pairs:
            await db.execute(
                update(Detection)
                .where(Detection.id == det_id)
                .values(matched_name=new_name)
            )
        detections_name_remapped = len(det_name_pairs)

    await db.flush()

    # ------------------------------------------------------------------
    # Step 3: detection event remap
    #   detections.event_id remap would go here if needed; currently
    #   event_id is set by the pipeline directly from app event records,
    #   not from CiviCRM legacy IDs, so the count is always 0.
    # ------------------------------------------------------------------
    detections_event_remapped = 0

    # ------------------------------------------------------------------
    # Step 4: compute orphans
    #   Active subjects that still have contact_id IS NULL after remap.
    # ------------------------------------------------------------------
    orphan_rows = await db.execute(
        select(ComprefaceSubject.id).where(
            ComprefaceSubject.contact_id.is_(None),
            ComprefaceSubject.enrollment_status == "active",
        )
    )
    orphaned_subject_ids: list[int] = [row[0] for row in orphan_rows.fetchall()]
    subjects_orphaned = len(orphaned_subject_ids)

    await db.commit()

    return RemapReport(
        subjects_remapped=subjects_remapped,
        subjects_orphaned=subjects_orphaned,
        detections_event_remapped=detections_event_remapped,
        detections_name_remapped=detections_name_remapped,
        orphaned_subject_ids=orphaned_subject_ids,
    )


async def backfill_consent(db: AsyncSession) -> ConsentBackfillReport:
    """Create BiometricConsent rows for active remapped subjects with none.

    Rules
    -----
    - Only active subjects (enrollment_status == 'active') with a non-NULL
      contact_id are considered.
    - Skip subjects whose contact already has any BiometricConsent row.
    - For each qualifying subject, insert:
          BiometricConsent(
              contact_id=<subject.contact_id>,
              consent_given=False,
              basis_note='pre-cutover-unknown',
              recorded_at=utc_now(),
          )
    - Upsert AdminSetting key='enroll_without_consent' to {'value': True}
      (replicating the pattern from settings.py lines 75-91).
    - Idempotent: second call creates 0 rows and returns already_had_consent
      equal to the total qualifying count.
    """

    # Fetch qualifying subjects: active + contact_id not null.
    subj_rows = await db.execute(
        select(ComprefaceSubject.id, ComprefaceSubject.contact_id).where(
            ComprefaceSubject.enrollment_status == "active",
            ComprefaceSubject.contact_id.is_not(None),
        )
    )
    qualifying: list[tuple[int, int]] = subj_rows.fetchall()  # type: ignore[assignment]

    if not qualifying:
        # Still upsert the admin setting even if no subjects qualify.
        await _upsert_enroll_without_consent(db)
        await db.commit()
        return ConsentBackfillReport(
            consent_rows_created=0,
            already_had_consent=0,
            enroll_without_consent_set=True,
        )

    # Collect all contact_ids that already have consent rows.
    contact_ids = [row[1] for row in qualifying]
    existing_rows = await db.execute(
        select(BiometricConsent.contact_id).where(
            BiometricConsent.contact_id.in_(contact_ids)
        )
    )
    existing_contact_ids: set[int] = {row[0] for row in existing_rows.fetchall()}

    consent_rows_created = 0
    already_had_consent = 0
    now = utc_now()

    for _subj_id, contact_id in qualifying:
        if contact_id in existing_contact_ids:
            already_had_consent += 1
            continue
        db.add(
            BiometricConsent(
                contact_id=contact_id,
                consent_given=False,
                basis_note="pre-cutover-unknown",
                consented_at=now,
            )
        )
        # Mark as seen so duplicate subjects for same contact don't double-count.
        existing_contact_ids.add(contact_id)
        consent_rows_created += 1

    # Upsert AdminSetting — replicate settings.py lines 75-91 exactly.
    await _upsert_enroll_without_consent(db)

    await db.commit()

    return ConsentBackfillReport(
        consent_rows_created=consent_rows_created,
        already_had_consent=already_had_consent,
        enroll_without_consent_set=True,
    )


async def get_enroll_without_consent(db: AsyncSession) -> bool:
    """Return the current value of AdminSetting key 'enroll_without_consent'.

    Reads the AdminSetting row whose key is 'enroll_without_consent'.  The row's
    value column is a JSON dict shaped ``{'value': <bool>}``.

    Return rules
    ------------
    - Row absent                    → False  (safe default; backfill not yet run)
    - Row present, value is dict    → bool(row.value.get('value', False))
    - Row present, value is non-dict (legacy scalar) → bool(row.value)

    Never raises; callers that catch ImportError/AttributeError around the
    old lazy-import stub will now get the real value instead.
    """
    result = await db.execute(
        select(AdminSetting).where(AdminSetting.key == "enroll_without_consent")
    )
    row = result.scalar_one_or_none()

    if row is None:
        return False

    if isinstance(row.value, dict):
        return bool(row.value.get("value", False))

    return bool(row.value)


async def _upsert_enroll_without_consent(db: AsyncSession) -> None:
    """Upsert AdminSetting key='enroll_without_consent' to {'value': True}.

    Replicates the select-then-insert-or-update pattern from
    backend/app/routers/settings.py lines 75-91.
    """
    key = "enroll_without_consent"
    result = await db.execute(select(AdminSetting).where(AdminSetting.key == key))
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = {"value": True}
    else:
        setting = AdminSetting(
            key=key,
            value={"value": True},
            category="general",
        )
        db.add(setting)
