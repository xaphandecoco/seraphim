"""Tests for app.services.member_status_service (S23).

Fixtures used from conftest: db_session, sample_contact, sample_event,
sample_participant, sample_multiselect_field.
Extra rows are created inline.  No seeded_contact_id / admin_token needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AuditLog, Contact, Event, Participant, utc_now
from app.services.member_status_service import (
    ContactSnapshot,
    RecomputeResult,
    _tier_from_weeks_absent,
    compute_snapshot_for_contacts,
    recompute_all,
    recompute_contacts,
)


# ---------------------------------------------------------------------------
# _tier_from_weeks_absent — parametric boundary tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weeks,expected",
    [
        (None, None),
        (0, "tier0"),
        (1, "tier1"),
        (4, "tier1"),
        (5, "tier2"),
        (8, "tier2"),
        (9, "tier3"),
        (12, "tier3"),
        (13, "inactive"),
        (52, "inactive"),
    ],
)
def test_tier_from_weeks_absent_boundaries(weeks, expected):
    assert _tier_from_weeks_absent(weeks) == expected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(title: str, days_ago: int) -> Event:
    start_at = datetime.utcnow().replace(
        hour=10, minute=0, second=0, microsecond=0
    ) - timedelta(days=days_ago)
    return Event(title=title, start_at=start_at)


# A stable "today" that is 14 days after the test event created by sample_event.
# sample_event.start_at ≈ utcnow(), so 14 days from now gives weeks_absent = 2.
def _today_14_days_out(event_start_at: datetime) -> datetime:
    return (event_start_at + timedelta(days=14)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


# ---------------------------------------------------------------------------
# Basic compute / snapshot values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_recompute_snapshot_fields(
    db_session, sample_contact, sample_event, sample_participant
):
    """last_attended_at = MAX(event.start_at), attendance_count correct."""
    today = _today_14_days_out(sample_event.start_at)
    snapshots = await compute_snapshot_for_contacts(
        db_session, None, today=today
    )
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.contact_id == sample_contact.id
    assert snap.attendance_count == 1
    assert snap.last_attended_at is not None
    # 14 days = 2 weeks → tier1
    assert snap.weeks_absent == 2
    assert snap.tier == "tier1"
    assert snap.is_active is True
    assert snap.is_regular is False


@pytest.mark.asyncio
async def test_last_attended_at_is_max(db_session, sample_contact):
    """MAX(start_at) is selected when multiple attended events exist."""
    early = datetime(2025, 1, 1, 9, 0)
    late = datetime(2025, 6, 1, 9, 0)

    e1 = Event(title="Early", start_at=early)
    e2 = Event(title="Late", start_at=late)
    db_session.add_all([e1, e2])
    await db_session.flush()

    db_session.add_all([
        Participant(contact_id=sample_contact.id, event_id=e1.id, status="attended", source="face"),
        Participant(contact_id=sample_contact.id, event_id=e2.id, status="attended", source="manual"),
    ])
    await db_session.commit()

    today = datetime(2025, 6, 8, 0, 0, 0)  # 7 days after late → 1 week → tier1
    snapshots = await compute_snapshot_for_contacts(db_session, None, today=today)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.attendance_count == 2
    assert snap.last_attended_at == late
    assert snap.weeks_absent == 1
    assert snap.tier == "tier1"


# ---------------------------------------------------------------------------
# All 4 attendance sources are counted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_four_sources_counted(db_session, sample_contact):
    """face, manual, zoom, community_report all count toward attendance_count."""
    events = [Event(title=f"E{i}", start_at=datetime(2025, 1, i + 1, 9, 0)) for i in range(4)]
    db_session.add_all(events)
    await db_session.flush()

    sources = ["face", "manual", "zoom", "community_report"]
    for ev, src in zip(events, sources):
        db_session.add(
            Participant(
                contact_id=sample_contact.id,
                event_id=ev.id,
                status="attended",
                source=src,
            )
        )
    await db_session.commit()

    snapshots = await compute_snapshot_for_contacts(db_session, None, today=datetime(2025, 2, 1, 0, 0))
    assert len(snapshots) == 1
    assert snapshots[0].attendance_count == 4


# ---------------------------------------------------------------------------
# Never-attended contact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_attended_contact(db_session, sample_contact):
    """Contact with no Participant rows has all None/zero/False snapshot fields."""
    snapshots = await compute_snapshot_for_contacts(db_session, None)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.last_attended_at is None
    assert snap.attendance_count == 0
    assert snap.weeks_absent is None
    assert snap.tier is None
    assert snap.is_active is None
    assert snap.is_regular is False


# ---------------------------------------------------------------------------
# is_regular boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_regular_boundary(db_session, sample_contact):
    """9 events → is_regular False; 10 events → is_regular True."""
    for count in (9, 10):
        # Create events and participants fresh
        events = [
            Event(title=f"R{i}", start_at=datetime(2025, 3, 1, 9, 0) + timedelta(days=i))
            for i in range(count)
        ]
        db_session.add_all(events)
        await db_session.flush()

        # Remove any existing participants for this contact first
        existing = (await db_session.execute(
            select(Participant).where(Participant.contact_id == sample_contact.id)
        )).scalars().all()
        for p in existing:
            await db_session.delete(p)
        await db_session.flush()

        for ev in events:
            db_session.add(
                Participant(
                    contact_id=sample_contact.id,
                    event_id=ev.id,
                    status="attended",
                    source="manual",
                )
            )
        await db_session.flush()

        snapshots = await compute_snapshot_for_contacts(db_session, None, today=datetime(2025, 4, 1))
        assert len(snapshots) == 1
        expected = count >= 10
        assert snapshots[0].is_regular is expected, f"is_regular wrong for count={count}"


# ---------------------------------------------------------------------------
# is_connected variants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "custom_data,expected",
    [
        ({"community_leader": 42}, True),
        ({"community_leader": None}, False),
        ({}, False),
        ({"community_leader": ""}, False),
        ({"community_leader": 0}, False),
        ({"community_leader": []}, False),
        ({"community": [5]}, True),
    ],
)
async def test_is_connected_variants(db_session, custom_data, expected):
    contact = Contact(
        first_name="Test",
        last_name="Connected",
        contact_type="individual",
        custom_data=custom_data,
    )
    db_session.add(contact)
    await db_session.commit()

    snapshots = await compute_snapshot_for_contacts(db_session, [contact.id])
    assert len(snapshots) == 1
    assert snapshots[0].is_connected is expected


# ---------------------------------------------------------------------------
# Non-individual contacts are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_individual_skipped(db_session):
    household = Contact(
        first_name="Smith",
        last_name="Family",
        contact_type="household",
    )
    org = Contact(
        first_name="ACME",
        last_name="Corp",
        contact_type="organization",
    )
    individual = Contact(
        first_name="Jane",
        last_name="Doe",
        contact_type="individual",
    )
    db_session.add_all([household, org, individual])
    await db_session.commit()

    snapshots = await compute_snapshot_for_contacts(db_session, None)
    assert len(snapshots) == 1
    assert snapshots[0].contact_id == individual.id


# ---------------------------------------------------------------------------
# Deleted contacts are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_contact_skipped(db_session):
    active = Contact(first_name="Active", last_name="Person", contact_type="individual")
    deleted = Contact(first_name="Deleted", last_name="Person", contact_type="individual", is_deleted=True)
    db_session.add_all([active, deleted])
    await db_session.commit()

    snapshots = await compute_snapshot_for_contacts(db_session, None)
    ids = {s.contact_id for s in snapshots}
    assert active.id in ids
    assert deleted.id not in ids


# ---------------------------------------------------------------------------
# Idempotent: running recompute_all twice produces identical snapshot values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_all_is_idempotent(db_session, sample_contact, sample_event, sample_participant):
    today = _today_14_days_out(sample_event.start_at)

    r1 = await recompute_all(db_session, today=today)
    # Expire identity map so second run gets fresh DB values
    await db_session.run_sync(lambda s: s.expire_all())

    r2 = await recompute_all(db_session, today=today)

    assert r1.contact_count == r2.contact_count

    contact = (await db_session.execute(
        select(Contact).where(Contact.id == sample_contact.id)
    )).scalar_one()
    assert contact.tier == "tier1"
    assert contact.attendance_count == 1


# ---------------------------------------------------------------------------
# Partial recompute: only named IDs written, job_run_id is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_recompute_only_named_ids(db_session):
    c1 = Contact(first_name="Alice", last_name="A", contact_type="individual")
    c2 = Contact(first_name="Bob", last_name="B", contact_type="individual")
    db_session.add_all([c1, c2])
    await db_session.commit()

    # Capture PKs while objects are still fresh (before any expire)
    c1_id = c1.id
    c2_id = c2.id

    ev = Event(title="Test", start_at=datetime(2025, 1, 1, 9, 0))
    db_session.add(ev)
    await db_session.flush()
    db_session.add(Participant(contact_id=c1_id, event_id=ev.id, status="attended", source="manual"))
    await db_session.commit()

    result = await recompute_contacts(db_session, [c1_id], today=datetime(2025, 1, 8, 0, 0))
    assert result.contact_count == 1
    # partial recompute (recompute_contacts) never writes a job_runs row — always None
    assert result.job_run_id is None

    # refresh() issues a fresh SELECT and updates the in-memory ORM objects
    # so we see the Core-UPDATE values rather than stale identity-map cache
    await db_session.refresh(c1)
    await db_session.refresh(c2)

    # c1 should have snapshot updated
    assert c1.attendance_count == 1
    # c2 was not included — snapshot columns remain None
    assert c2.attendance_count is None


# ---------------------------------------------------------------------------
# One audit_log row per full recompute_all call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_audit_log_row_per_full_run(db_session, sample_contact):
    await recompute_all(db_session)

    logs = (await db_session.execute(
        select(AuditLog).where(AuditLog.action == "member_status.recompute")
    )).scalars().all()
    assert len(logs) == 1
    assert logs[0].actor_id is None
    assert logs[0].entity == "contact"
    assert logs[0].entity_id is None
    assert "contact_count" in (logs[0].after or {})


# ---------------------------------------------------------------------------
# job_run_id is a real int after full recompute (job_runs table exists via create_all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_run_id_is_int_after_full_recompute(db_session, sample_contact):
    """Full recompute writes a job_runs row; job_run_id must be a non-None integer."""
    result = await recompute_all(db_session)
    assert result.job_run_id is not None, (
        "recompute_all should write a job_runs row now that the table exists"
    )
    assert isinstance(result.job_run_id, int)


# ---------------------------------------------------------------------------
# duration_ms is non-negative
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duration_ms_non_negative(db_session, sample_contact):
    result = await recompute_all(db_session)
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# RecomputeResult.contact_count matches actual in-scope contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_count_in_result(db_session):
    for i in range(3):
        db_session.add(Contact(first_name=f"F{i}", last_name="L", contact_type="individual"))
    # Non-individual — must not count
    db_session.add(Contact(first_name="Org", last_name="X", contact_type="organization"))
    await db_session.commit()

    result = await recompute_all(db_session)
    assert result.contact_count == 3
