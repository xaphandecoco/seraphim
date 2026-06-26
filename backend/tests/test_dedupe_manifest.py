"""S11 — Introspection test: _REASSIGNMENT_TARGETS covers ALL contacts.id FKs.

Two test classes:
  TestManifestCompleteness  — pure metadata check (no DB needed)
  TestMergeAllFKs           — BIG integration test seeding every FK table
"""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Ensure app.main is imported FIRST so all ORM models register on Base.metadata
# ---------------------------------------------------------------------------
import app.main  # noqa: F401


# ---------------------------------------------------------------------------
# Introspection: assert manifest covers every contacts.id FK
# ---------------------------------------------------------------------------


class TestManifestCompleteness:
    """Pure-Python checks; no database required."""

    def test_manifest_covers_all_contact_fks(self) -> None:
        """Every Column with a FK targeting contacts.id must be in _REASSIGNMENT_TARGETS."""
        from app.database import Base
        from app.services.dedupe_service import _REASSIGNMENT_TARGETS

        contact_fks: set[tuple[str, str]] = set()
        for tname, table in Base.metadata.tables.items():
            for col in table.columns:
                for fk in col.foreign_keys:
                    if fk.target_fullname == "contacts.id":
                        contact_fks.add((tname, col.name))

        missing = contact_fks - _REASSIGNMENT_TARGETS
        assert not missing, (
            f"The following contacts.id FK columns are NOT in _REASSIGNMENT_TARGETS: {missing}\n"
            "Add them to the manifest in dedupe_service.py."
        )

    def test_non_fk_paths_in_registry(self) -> None:
        """The two non-FK rewrite paths must appear in _REASSIGNMENT_TARGETS."""
        from app.services.dedupe_service import _REASSIGNMENT_TARGETS

        assert "detections.matched_name" in _REASSIGNMENT_TARGETS, (
            "'detections.matched_name' not in _REASSIGNMENT_TARGETS"
        )
        assert "contacts.custom_data:contact_reference" in _REASSIGNMENT_TARGETS, (
            "'contacts.custom_data:contact_reference' not in _REASSIGNMENT_TARGETS"
        )


# ---------------------------------------------------------------------------
# Big integration fixture + merge test
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_test(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    """Dispose the engine pool after each test (mirrors test_t06 pattern)."""
    yield
    from app.database import engine
    await engine.dispose()


@pytest_asyncio.fixture
async def default_rule_set(db_session: AsyncSession):
    """Seed a default DedupeRuleSet (create_all doesn't run migration seeds)."""
    from app.models import DedupeRuleSet

    rs = DedupeRuleSet(
        name="Default",
        is_default=True,
        is_active=True,
        threshold=50,
        rules=[
            {"field": "email", "weight": 100},
            {"field": "last_name", "weight": 50},
        ],
    )
    db_session.add(rs)
    await db_session.commit()
    await db_session.refresh(rs)
    return rs


@pytest_asyncio.fixture
async def seed_all_fk_tables(db_session: AsyncSession, default_rule_set):
    """
    Create survivor + loser contacts plus a row in EVERY FK table, including
    collision rows where the unique constraint would be violated.

    Returns a dict with the contact objects and fixture metadata.
    """
    from datetime import datetime, timezone
    from app.models import (
        BiometricConsent,
        CommunityReport,
        ComprefaceSubject,
        Contact,
        CustomFieldDef,
        CustomFieldGroup,
        Detection,
        Event,
        FaceSample,
        Group,
        GroupMember,
        Log,
        NameAlias,
        NameMatchReviewQueue,
        Participant,
        utc_now,
    )

    def _now():
        return datetime.now(timezone.utc).replace(tzinfo=None)

    # ── contacts ─────────────────────────────────────────────────────────────
    survivor = Contact(
        first_name="Alice", last_name="Survivor", email="alice@test.com",
        contact_type="individual",
    )
    loser = Contact(
        first_name="Alice", last_name="Loser", email="alice2@test.com",
        contact_type="individual",
    )
    third = Contact(
        first_name="Third", last_name="Contact", contact_type="individual"
    )
    db_session.add_all([survivor, loser, third])
    await db_session.flush()

    # ── events ────────────────────────────────────────────────────────────────
    event_unique = Event(title="UniqueEvent", start_at=_now())
    event_collision = Event(title="CollisionEvent", start_at=_now())
    db_session.add_all([event_unique, event_collision])
    await db_session.flush()

    # ── groups ───────────────────────────────────────────────────────────────
    grp_unique = Group(name="UniqueGroup", group_type="static")
    grp_collision = Group(name="CollisionGroup", group_type="static")
    db_session.add_all([grp_unique, grp_collision])
    await db_session.flush()

    # ── participants ──────────────────────────────────────────────────────────
    # Loser only: event_unique
    db_session.add(Participant(
        contact_id=loser.id, event_id=event_unique.id, status="attended", source="manual"
    ))
    # Collision: BOTH survivor and loser in event_collision
    db_session.add(Participant(
        contact_id=survivor.id, event_id=event_collision.id, status="registered", source="manual"
    ))
    db_session.add(Participant(
        contact_id=loser.id, event_id=event_collision.id, status="attended", source="manual"
    ))
    await db_session.flush()

    # ── group_members ─────────────────────────────────────────────────────────
    # Loser only: grp_unique
    db_session.add(GroupMember(group_id=grp_unique.id, contact_id=loser.id))
    # Collision: BOTH in grp_collision
    db_session.add(GroupMember(group_id=grp_collision.id, contact_id=survivor.id))
    db_session.add(GroupMember(group_id=grp_collision.id, contact_id=loser.id))
    await db_session.flush()

    # ── biometric_consent ─────────────────────────────────────────────────────
    # Both survivor and loser have consent rows → collision → loser's will be deleted
    db_session.add(BiometricConsent(contact_id=survivor.id, consent_given=True))
    db_session.add(BiometricConsent(contact_id=loser.id, consent_given=False))
    await db_session.flush()

    # ── name_alias ────────────────────────────────────────────────────────────
    # Non-colliding alias for loser
    db_session.add(NameAlias(
        alias_text="loser_unique_alias", contact_id=loser.id, source="admin", meta={}
    ))
    # Colliding: the same alias_text already exists for survivor
    db_session.add(NameAlias(
        alias_text="shared_alias", contact_id=survivor.id, source="admin", meta={}
    ))
    db_session.add(NameAlias(
        alias_text="shared_alias_loser", contact_id=loser.id, source="admin", meta={}
    ))
    # Make shared_alias_loser collide with an existing alias
    # Actually: let's add a separate alias_text that is the SAME as an existing one
    # We'll add a second loser alias whose text is already on another contact
    db_session.add(NameAlias(
        alias_text="third_alias", contact_id=third.id, source="admin", meta={}
    ))
    db_session.add(NameAlias(
        alias_text="third_alias_loser", contact_id=loser.id, source="admin", meta={}
    ))
    # third_alias_loser is not taken → will be reassigned to survivor
    # shared_alias_loser is not taken by third → will be reassigned to survivor
    # NOTE: alias_text is globally unique (uq_name_alias_text); we can only
    # test the "collision via same text already on survivor" path by checking
    # loser's "shared_alias_loser" vs "shared_alias" owned by survivor are
    # different texts — no duplicate seeding needed.
    await db_session.flush()

    # ── compreface_subjects ───────────────────────────────────────────────────
    # Survivor has an active subject → loser's subject should be detached
    s_subject = ComprefaceSubject(
        subject_name="alice_survivor",
        compreface_subject_id="cf-survivor-uuid",
        contact_id=survivor.id,
        enrollment_status="active",
    )
    l_subject = ComprefaceSubject(
        subject_name="alice_loser",
        compreface_subject_id="cf-loser-uuid",
        contact_id=loser.id,
        enrollment_status="active",
    )
    db_session.add_all([s_subject, l_subject])
    await db_session.flush()

    # ── face_samples ──────────────────────────────────────────────────────────
    fs = FaceSample(
        compreface_subject_id="cf-loser-uuid",
        contact_id=loser.id,
        image_path="/img/loser.jpg",
        thumb_path="/thumb/loser.jpg",
        source="manual",
    )
    db_session.add(fs)
    await db_session.flush()

    # ── community_report ─────────────────────────────────────────────────────
    cr = CommunityReport(
        event_title="Test Event",
        submitted_by_contact_id=loser.id,
        event_leader_contact_id=loser.id,
        date_of_activity=_now(),
    )
    db_session.add(cr)
    await db_session.flush()

    # ── name_match_review_queue ───────────────────────────────────────────────
    nmq = NameMatchReviewQueue(
        raw_name="Alice Loser",
        contact_id=loser.id,
        candidate_contact_id=loser.id,
        status="pending",
    )
    db_session.add(nmq)
    await db_session.flush()

    # ── contact_reference custom field ────────────────────────────────────────
    # third contact's custom_data points to loser_id (contact_reference)
    grp = CustomFieldGroup(
        name="refs_group", label="Refs", entity="contact", weight=0
    )
    db_session.add(grp)
    await db_session.flush()

    cfd = CustomFieldDef(
        group_id=grp.id,
        name="ref_field",
        label="Ref Field",
        data_type="contact_reference",
        is_multi=False,
    )
    db_session.add(cfd)
    await db_session.flush()

    third.custom_data = {"ref_field": loser.id}
    await db_session.flush()

    # ── detections + logs with member:{loser.id} ─────────────────────────────
    det = Detection(
        image_path="/img/det.jpg",
        matched_name=f"member:{loser.id}",
        status="auto_logged",
    )
    db_session.add(det)
    await db_session.flush()

    log = Log(
        matched_name=f"member:{loser.id}",
        action="auto",
        timestamp=_now(),
    )
    db_session.add(log)
    await db_session.flush()

    await db_session.commit()

    return {
        "survivor": survivor,
        "loser": loser,
        "third": third,
        "event_unique": event_unique,
        "event_collision": event_collision,
        "grp_unique": grp_unique,
        "grp_collision": grp_collision,
        "l_subject": l_subject,
        "s_subject": s_subject,
        "det": det,
        "log": log,
        "cr": cr,
        "nmq": nmq,
        "cfd": cfd,
        "third": third,
    }


@pytest.mark.asyncio
async def test_merge_covers_all_fks(
    db_session: AsyncSession,
    seed_all_fk_tables: dict,
) -> None:
    """After merge, NO FK column should still point to the loser's id."""
    from app.models import (
        BiometricConsent,
        CommunityReport,
        ComprefaceSubject,
        Contact,
        Detection,
        FaceSample,
        GroupMember,
        Log,
        NameAlias,
        NameMatchReviewQueue,
        Participant,
        AuditLog,
    )
    from app.services.dedupe_service import merge_contacts

    survivor = seed_all_fk_tables["survivor"]
    loser = seed_all_fk_tables["loser"]
    sid = survivor.id
    lid = loser.id

    # Mock post-commit calls so they don't open new sessions or hit CompreFace
    recompute_mock = AsyncMock()
    with (
        patch("app.services.member_status_service.recompute_contacts", recompute_mock),
        patch(
            "app.services.dedupe_service.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=db_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ),
    ):
        result = await merge_contacts(
            db_session,
            survivor_id=sid,
            loser_id=lid,
            actor_id=None,
            confirm_same_name=True,  # names differ (Survivor vs Loser) but confirm just in case
        )

    assert result["survivor_id"] == sid

    # Save all fixture IDs as plain ints BEFORE expire_all so that accessing them
    # later doesn't trigger sync lazy-loading on expired ORM objects (aiosqlite
    # raises greenlet_spawn when _load_expired is called outside await).
    event_collision_id: int = seed_all_fk_tables["event_collision"].id
    l_subject_id: int = seed_all_fk_tables["l_subject"].id
    det_id: int = seed_all_fk_tables["det"].id
    log_id: int = seed_all_fk_tables["log"].id
    third_id: int = seed_all_fk_tables["third"].id

    # Expire all cached ORM objects so re-fetches hit the DB
    db_session.expire_all()

    # ── Assert loser is soft-deleted ─────────────────────────────────────────
    loser_row = await db_session.get(Contact, lid)
    assert loser_row is not None
    assert loser_row.is_deleted is True

    # ── Assert survivor intact ────────────────────────────────────────────────
    survivor_row = await db_session.get(Contact, sid)
    assert survivor_row is not None
    assert survivor_row.is_deleted is False

    # ── Assert NO FK still points to loser_id ─────────────────────────────────
    async def _count_fk(col, val):
        return (await db_session.execute(
            select(col).where(col == val)
        )).scalar() or 0

    from sqlalchemy import func

    assert (await db_session.execute(
        select(func.count(FaceSample.id)).where(FaceSample.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(CommunityReport.id)).where(CommunityReport.submitted_by_contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(CommunityReport.id)).where(CommunityReport.event_leader_contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(NameMatchReviewQueue.id)).where(NameMatchReviewQueue.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(NameMatchReviewQueue.id)).where(
            NameMatchReviewQueue.candidate_contact_id == lid
        )
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(Participant.id)).where(Participant.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(GroupMember.id)).where(GroupMember.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(BiometricConsent.id)).where(BiometricConsent.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(NameAlias.id)).where(NameAlias.contact_id == lid)
    )).scalar() == 0

    assert (await db_session.execute(
        select(func.count(ComprefaceSubject.id)).where(ComprefaceSubject.contact_id == lid)
    )).scalar() == 0

    # ── No UNIQUE violations ──────────────────────────────────────────────────
    # Participant uniqueness: survivor has only ONE row per event
    parts = (await db_session.execute(
        select(Participant).where(Participant.contact_id == sid)
    )).scalars().all()
    event_ids_seen: set[int] = set()
    for p in parts:
        assert p.event_id not in event_ids_seen, (
            f"Duplicate participant row for event_id={p.event_id}"
        )
        event_ids_seen.add(p.event_id)

    # ── Status promotion: collision event should have 'attended' ─────────────
    collision_part = (await db_session.execute(
        select(Participant).where(
            Participant.contact_id == sid,
            Participant.event_id == event_collision_id,
        )
    )).scalar_one_or_none()
    assert collision_part is not None
    assert collision_part.status == "attended"  # loser had 'attended' > survivor's 'registered'

    # ── Compreface subject detached (contact_id=NULL) ─────────────────────────
    l_subj = await db_session.get(ComprefaceSubject, l_subject_id)
    assert l_subj is not None
    assert l_subj.contact_id is None

    # ── Non-FK: detections.matched_name rewritten ────────────────────────────
    det = await db_session.get(Detection, det_id)
    assert det is not None
    assert det.matched_name == f"member:{sid}"

    # ── Non-FK: logs.matched_name rewritten ──────────────────────────────────
    from app.models import Log as LogModel
    log = await db_session.get(LogModel, log_id)
    assert log is not None
    assert log.matched_name == f"member:{sid}"

    # ── custom_data contact_reference rewritten ────────────────────────────────
    third_row = await db_session.get(Contact, third_id)
    assert third_row is not None
    cd = third_row.custom_data or {}
    assert cd.get("ref_field") == sid

    # ── ONE audit row written ─────────────────────────────────────────────────
    audit_rows = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.action == "contact_merge",
            AuditLog.entity_id == sid,
        )
    )).scalars().all()
    assert len(audit_rows) == 1
