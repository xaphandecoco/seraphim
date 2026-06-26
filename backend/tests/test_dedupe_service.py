"""S11 — Unit & integration tests for dedupe_service + dedupe router.

Coverage:
  Detection    — email dup pair, no-self-pair, first-name-only no-pair, dedup pairs
  Preview      — zero writes, counts match FK row counts
  Merge guards — 422 self, 404 missing, 409 deleted, 409 same-name w/o confirm ZERO writes,
                 409 second-merge (loser already deleted)
  Merge atomic — mid-txn error → rollback, no is_deleted, no audit
  Rule-set CRUD — create, update, delete-only-active 409, delete-default 409,
                  is_default clears others, field/weight/threshold validation
  RBAC         — viewer → 403 everywhere; volunteer → 403 on rule-set writes
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.main  # ensure all models registered  # noqa: F401


# ---------------------------------------------------------------------------
# Engine-dispose autouse (mirrors test_t06 pattern)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine(db_session: AsyncSession) -> AsyncGenerator[None, None]:
    yield
    from app.database import engine
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fresh_rule_set(db: AsyncSession, **kwargs: Any):
    """Insert a DedupeRuleSet, return it."""
    from app.models import DedupeRuleSet

    defaults = {
        "name": "TestRS",
        "is_default": True,
        "is_active": True,
        "threshold": 50,
        "rules": [{"field": "email", "weight": 100}],
    }
    defaults.update(kwargs)
    rs = DedupeRuleSet(**defaults)
    db.add(rs)
    await db.commit()
    await db.refresh(rs)
    return rs


async def _make_contact(db: AsyncSession, **kwargs: Any):
    from app.models import Contact

    defaults = {
        "first_name": "Test",
        "last_name": "User",
        "contact_type": "individual",
    }
    defaults.update(kwargs)
    c = Contact(**defaults)
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_email_dup_one_pair(db_session: AsyncSession) -> None:
    """Two contacts with same email → exactly 1 pair with score ≥ threshold."""
    from app.services.dedupe_service import detect_candidates

    rs = await _fresh_rule_set(db_session, threshold=50)
    a = await _make_contact(db_session, email="dup@test.com", last_name="Smith")
    b = await _make_contact(db_session, email="dup@test.com", last_name="Smith")

    result = await detect_candidates(db_session, rule_set_id=rs.id)

    assert result["total"] >= 1
    found = [
        item
        for item in result["items"]
        if {item["contact_a"]["id"], item["contact_b"]["id"]} == {a.id, b.id}
    ]
    assert found, "Expected a matching pair for the dup email"
    assert found[0]["score"] >= rs.threshold


@pytest.mark.asyncio
async def test_detect_no_self_pair(db_session: AsyncSession) -> None:
    """Single contact must not pair with itself."""
    from app.services.dedupe_service import detect_candidates

    rs = await _fresh_rule_set(db_session)
    await _make_contact(db_session, email="solo@test.com")

    result = await detect_candidates(db_session, rule_set_id=rs.id)
    for item in result["items"]:
        assert item["contact_a"]["id"] != item["contact_b"]["id"]


@pytest.mark.asyncio
async def test_detect_below_threshold_not_returned(db_session: AsyncSession) -> None:
    """Contacts with no blocking-field match should produce no pairs."""
    from app.services.dedupe_service import detect_candidates

    # Set high threshold so weak similarities are filtered out
    rs = await _fresh_rule_set(db_session, threshold=95)
    # Completely different emails, last names, phones, and short first names
    await _make_contact(
        db_session, first_name="Aa", last_name="Zzz", email="aaa@test.com"
    )
    await _make_contact(
        db_session, first_name="Bb", last_name="Yyy", email="bbb@test.com"
    )

    result = await detect_candidates(db_session, rule_set_id=rs.id)
    # no shared blocking bucket → 0 pairs
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_detect_dedup_pairs_unique(db_session: AsyncSession) -> None:
    """Same pair found by multiple blocking buckets appears only once."""
    from app.services.dedupe_service import detect_candidates

    rs = await _fresh_rule_set(db_session, threshold=1)
    a = await _make_contact(
        db_session,
        first_name="Alice",
        last_name="Smith",
        email="alice@test.com",
        phone="555-1234",
    )
    b = await _make_contact(
        db_session,
        first_name="Alice",
        last_name="Smith",
        email="alice@test.com",
        phone="555-1234",
    )

    result = await detect_candidates(db_session, rule_set_id=rs.id)
    pair_key = (min(a.id, b.id), max(a.id, b.id))
    found = [
        item
        for item in result["items"]
        if (item["contact_a"]["id"], item["contact_b"]["id"]) == pair_key
    ]
    assert len(found) == 1, "Same pair appeared more than once"


# ---------------------------------------------------------------------------
# Preview tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_zero_writes(db_session: AsyncSession) -> None:
    """Preview must not create any audit rows or mutate data."""
    from app.models import AuditLog, Contact
    from app.services.dedupe_service import merge_preview

    rs = await _fresh_rule_set(db_session)
    a = await _make_contact(db_session, email="a@test.com")
    b = await _make_contact(db_session, email="b@test.com")

    audit_before = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0

    _ = await merge_preview(db_session, a.id, b.id)

    audit_after = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0
    assert audit_after == audit_before, "Preview must not write audit rows"

    # contacts unchanged
    a_row = await db_session.get(Contact, a.id)
    b_row = await db_session.get(Contact, b.id)
    assert a_row is not None and not a_row.is_deleted
    assert b_row is not None and not b_row.is_deleted


@pytest.mark.asyncio
async def test_preview_participant_counts(db_session: AsyncSession) -> None:
    """Preview reassignment counts match actual FK row counts."""
    from app.models import Event, Participant
    from app.services.dedupe_service import merge_preview

    await _fresh_rule_set(db_session)
    survivor = await _make_contact(db_session, email="s@test.com")
    loser = await _make_contact(db_session, email="l@test.com")

    from datetime import datetime, timezone

    ev1 = Event(title="Ev1", start_at=datetime.now(timezone.utc).replace(tzinfo=None))
    ev2 = Event(title="Ev2", start_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db_session.add_all([ev1, ev2])
    await db_session.flush()

    db_session.add(Participant(
        contact_id=loser.id, event_id=ev1.id, status="attended", source="manual"
    ))
    db_session.add(Participant(
        contact_id=loser.id, event_id=ev2.id, status="registered", source="manual"
    ))
    await db_session.commit()

    preview = await merge_preview(db_session, survivor.id, loser.id)

    total_parts = (await db_session.execute(
        select(func.count(Participant.id)).where(Participant.contact_id == loser.id)
    )).scalar() or 0

    p_stat = preview["reassignments"]["participants.contact_id"]
    assert p_stat["reassigned"] + p_stat["deleted"] == total_parts


# ---------------------------------------------------------------------------
# Merge guard tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_self_422(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(db_session, survivor_id=a.id, loser_id=a.id, actor_id=None)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_merge_missing_survivor_404(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    b = await _make_contact(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(db_session, survivor_id=99999, loser_id=b.id, actor_id=None)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_merge_missing_loser_404(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session)
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(db_session, survivor_id=a.id, loser_id=99999, actor_id=None)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_merge_deleted_contact_409(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session)
    b = await _make_contact(db_session, is_deleted=True)
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(db_session, survivor_id=a.id, loser_id=b.id, actor_id=None)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_merge_same_name_without_confirm_zero_writes(db_session: AsyncSession) -> None:
    """409 when names match and confirm_same_name=False; ZERO writes."""
    from fastapi import HTTPException
    from app.models import AuditLog, Contact
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session, first_name="John", last_name="Doe")
    b = await _make_contact(db_session, first_name="John", last_name="Doe")

    audit_before = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0

    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(
            db_session,
            survivor_id=a.id,
            loser_id=b.id,
            actor_id=None,
            confirm_same_name=False,
        )
    assert exc_info.value.status_code == 409
    assert "explicit confirmation" in exc_info.value.detail.lower() or "same name" in exc_info.value.detail.lower()

    # ZERO writes: no audit rows added
    audit_after = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0
    assert audit_after == audit_before

    # Neither contact is deleted — save IDs before expire_all to avoid
    # MissingGreenlet when accessing expired PK attributes in sync context.
    a_id = a.id
    b_id = b.id
    db_session.expire_all()
    a_row = await db_session.get(Contact, a_id)
    b_row = await db_session.get(Contact, b_id)
    assert a_row is not None and not a_row.is_deleted
    assert b_row is not None and not b_row.is_deleted


@pytest.mark.asyncio
async def test_merge_second_attempt_409(db_session: AsyncSession) -> None:
    """Merging the same pair a second time → 409 (loser is_deleted)."""
    from fastapi import HTTPException
    from app.services.dedupe_service import merge_contacts

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session, last_name="Unique1")
    b = await _make_contact(db_session, last_name="Unique2")
    # Save IDs before any commit that would expire the ORM objects.
    a_id = a.id
    b_id = b.id

    with (
        patch("app.services.member_status_service.recompute_contacts", AsyncMock()),
        patch(
            "app.services.dedupe_service.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=db_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ),
    ):
        await merge_contacts(db_session, survivor_id=a_id, loser_id=b_id, actor_id=None)

    db_session.expire_all()
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(
            db_session,
            survivor_id=a_id,
            loser_id=b_id,
            actor_id=None,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_post_lock_recheck_no_duplicate_audit(db_session: AsyncSession) -> None:
    """Regression (TOCTOU): post-lock re-validation raises 409 with ZERO new audit rows.

    The post-lock re-check (populate_existing=True on the with_for_update() SELECT)
    catches a loser that was soft-deleted between the pre-lock Phase-1 guard and the
    lock acquisition in Phase 2 — e.g. a concurrent double-merge.

    The true race cannot be reproduced in a single-session test.  We verify the
    observable contract instead: after the first successful merge sets loser.is_deleted,
    a second merge attempt raises 409 and writes ZERO additional audit rows.
    """
    from app.models import AuditLog, Contact
    from app.services.dedupe_service import merge_contacts
    from fastapi import HTTPException

    await _fresh_rule_set(db_session)
    survivor = await _make_contact(db_session, last_name="TocTou1")
    loser = await _make_contact(db_session, last_name="TocTou2")
    sid = survivor.id
    lid = loser.id

    # First merge succeeds.
    with (
        patch("app.services.member_status_service.recompute_contacts", AsyncMock()),
        patch(
            "app.services.dedupe_service.async_session",
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=db_session),
                __aexit__=AsyncMock(return_value=False),
            ),
        ),
    ):
        await merge_contacts(db_session, survivor_id=sid, loser_id=lid, actor_id=None)

    # Count audit rows after the FIRST (successful) merge.
    db_session.expire_all()
    audit_after_first = (await db_session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "contact_merge", AuditLog.entity_id == sid
        )
    )).scalar() or 0
    assert audit_after_first == 1, "Exactly one audit row from the first merge"

    # Second merge attempt — loser is already is_deleted=True.
    # The post-lock re-check must catch this and raise 409 before writing any audit row.
    with pytest.raises(HTTPException) as exc_info:
        await merge_contacts(db_session, survivor_id=sid, loser_id=lid, actor_id=None)
    assert exc_info.value.status_code == 409

    db_session.expire_all()
    audit_after_second = (await db_session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.action == "contact_merge", AuditLog.entity_id == sid
        )
    )).scalar() or 0
    assert audit_after_second == audit_after_first, (
        "409 rejection must produce zero new audit rows (TOCTOU guard)"
    )


# ---------------------------------------------------------------------------
# Atomicity test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_mid_txn_error_rollback(db_session: AsyncSession) -> None:
    """If _reassign_all raises, the DB must be rolled back (no is_deleted, no audit)."""
    from app.models import AuditLog, Contact
    from app.services.dedupe_service import merge_contacts
    from fastapi import HTTPException

    await _fresh_rule_set(db_session)
    a = await _make_contact(db_session, last_name="Atomic1")
    b = await _make_contact(db_session, last_name="Atomic2")
    # Save IDs before the rollback expires/clears session objects.
    a_id = a.id
    b_id = b.id

    audit_before = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0

    with patch(
        "app.services.dedupe_service._reassign_all",
        side_effect=RuntimeError("simulated mid-txn error"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await merge_contacts(
                db_session,
                survivor_id=a_id,
                loser_id=b_id,
                actor_id=None,
            )
    assert exc_info.value.status_code == 500

    # After rollback the session is in a clean state — re-fetch using saved int IDs.
    db_session.expire_all()
    a_row = await db_session.get(Contact, a_id)
    b_row = await db_session.get(Contact, b_id)
    assert a_row is not None and not a_row.is_deleted
    assert b_row is not None and not b_row.is_deleted

    audit_after = (await db_session.execute(
        select(func.count(AuditLog.id))
    )).scalar() or 0
    assert audit_after == audit_before, "No audit row should have been written"


# ---------------------------------------------------------------------------
# Rule-set CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rule_set_is_default_clears_others(db_session: AsyncSession) -> None:
    from app.models import DedupeRuleSet
    from app.services.dedupe_service import create_rule_set

    rs1 = await _fresh_rule_set(db_session, name="RS1", is_default=True)
    rs1_id = rs1.id  # save before create_rule_set commits and expires rs1

    from app.schemas import RuleSetIn, RuleWeightItem
    data = RuleSetIn(
        name="RS2",
        is_default=True,
        is_active=True,
        threshold=60,
        rules=[RuleWeightItem(field="email", weight=100)],
    )
    rs2 = await create_rule_set(db_session, data)
    rs2_id = rs2.id  # save before expire_all

    # rs1 should have is_default=False now; verify via fresh get() using saved int IDs.
    db_session.expire_all()
    rs1_row = await db_session.get(DedupeRuleSet, rs1_id)
    rs2_row = await db_session.get(DedupeRuleSet, rs2_id)
    assert rs1_row is not None
    assert rs1_row.is_default is False
    assert rs2_row is not None
    assert rs2_row.is_default is True


@pytest.mark.asyncio
async def test_update_rule_set_is_default_clears_others(db_session: AsyncSession) -> None:
    from app.models import DedupeRuleSet
    from app.services.dedupe_service import update_rule_set

    rs1 = await _fresh_rule_set(db_session, name="RS1", is_default=True)
    rs1_id = rs1.id  # save before second commit expires rs1

    rs2 = await _fresh_rule_set(db_session, name="RS2", is_default=False)
    rs2_id = rs2.id  # save before expire_all

    from app.schemas import RuleSetIn, RuleWeightItem
    data = RuleSetIn(
        name="RS2-updated",
        is_default=True,
        is_active=True,
        threshold=70,
        rules=[RuleWeightItem(field="email", weight=50)],
    )
    await update_rule_set(db_session, rs2_id, data)

    db_session.expire_all()
    rs1_row = await db_session.get(DedupeRuleSet, rs1_id)
    rs2_row = await db_session.get(DedupeRuleSet, rs2_id)
    assert rs1_row is not None and rs1_row.is_default is False
    assert rs2_row is not None and rs2_row.is_default is True


@pytest.mark.asyncio
async def test_delete_rule_set_only_active_409(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import delete_rule_set

    # Only one active rule set — deletion must be rejected
    rs = await _fresh_rule_set(db_session, name="OnlyActive", is_default=False, is_active=True)

    with pytest.raises(HTTPException) as exc_info:
        await delete_rule_set(db_session, rs.id)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_rule_set_default_409(db_session: AsyncSession) -> None:
    from fastapi import HTTPException
    from app.services.dedupe_service import delete_rule_set

    rs1 = await _fresh_rule_set(db_session, name="Default", is_default=True, is_active=True)
    # Add a second active non-default set so the "only active" guard doesn't fire first
    await _fresh_rule_set(db_session, name="Other", is_default=False, is_active=True)

    with pytest.raises(HTTPException) as exc_info:
        await delete_rule_set(db_session, rs1.id)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_rule_set_success(db_session: AsyncSession) -> None:
    from app.models import DedupeRuleSet
    from app.services.dedupe_service import delete_rule_set

    # Two active sets; delete the non-default one
    await _fresh_rule_set(db_session, name="Keep", is_default=True, is_active=True)
    rs2 = await _fresh_rule_set(db_session, name="Delete", is_default=False, is_active=True)

    await delete_rule_set(db_session, rs2.id)

    db_session.expire_all()
    gone = await db_session.get(DedupeRuleSet, rs2.id)
    assert gone is None


# ---------------------------------------------------------------------------
# Pydantic schema validation (422 path — via router tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_set_invalid_field_422(client: AsyncClient, admin_auth_headers: dict) -> None:
    resp = await client.post(
        "/dedupe/rule-sets",
        json={
            "name": "Bad",
            "is_default": False,
            "is_active": True,
            "threshold": 70,
            "rules": [{"field": "INVALID_FIELD", "weight": 10}],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rule_set_zero_weight_422(client: AsyncClient, admin_auth_headers: dict) -> None:
    resp = await client.post(
        "/dedupe/rule-sets",
        json={
            "name": "Bad",
            "is_default": False,
            "is_active": True,
            "threshold": 70,
            "rules": [{"field": "email", "weight": 0}],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rule_set_threshold_zero_422(client: AsyncClient, admin_auth_headers: dict) -> None:
    resp = await client.post(
        "/dedupe/rule-sets",
        json={
            "name": "Bad",
            "is_default": False,
            "is_active": True,
            "threshold": 0,
            "rules": [{"field": "email", "weight": 10}],
        },
        headers=admin_auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# RBAC tests (viewer → 403; volunteer → 403 on rule-set writes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_403_candidates(
    client: AsyncClient, viewer_auth_headers: dict
) -> None:
    resp = await client.post(
        "/dedupe/candidates",
        json={"page": 1, "page_size": 25},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_403_merge_preview(
    client: AsyncClient, viewer_auth_headers: dict
) -> None:
    resp = await client.post(
        "/dedupe/merge/preview",
        json={"survivor_id": 1, "loser_id": 2},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_403_merge(
    client: AsyncClient, viewer_auth_headers: dict
) -> None:
    resp = await client.post(
        "/dedupe/merge",
        json={"survivor_id": 1, "loser_id": 2},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_403_history(
    client: AsyncClient, viewer_auth_headers: dict
) -> None:
    resp = await client.get("/dedupe/merge/history", headers=viewer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_403_rule_sets(
    client: AsyncClient, viewer_auth_headers: dict
) -> None:
    resp = await client.get("/dedupe/rule-sets", headers=viewer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_volunteer_403_create_rule_set(
    client: AsyncClient, volunteer_auth_headers: dict
) -> None:
    resp = await client.post(
        "/dedupe/rule-sets",
        json={
            "name": "Attempt",
            "is_default": False,
            "is_active": True,
            "threshold": 70,
            "rules": [{"field": "email", "weight": 10}],
        },
        headers=volunteer_auth_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_volunteer_403_delete_rule_set(
    client: AsyncClient, volunteer_auth_headers: dict
) -> None:
    resp = await client.delete("/dedupe/rule-sets/1", headers=volunteer_auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_rule_sets_bare_array(
    client: AsyncClient,
    admin_auth_headers: dict,
    db_session: AsyncSession,
) -> None:
    """GET /dedupe/rule-sets returns a bare JSON array (not wrapped)."""
    await _fresh_rule_set(db_session, name="ArrayTest")

    resp = await client.get("/dedupe/rule-sets", headers=admin_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list), f"Expected bare array, got {type(body).__name__}"
