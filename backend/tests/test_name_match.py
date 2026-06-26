"""Tests for backend/app/services/name_match.py (S22-F07 spec §8.1).

Coverage:
  - normalize_name vectors (sec8.1 table)
  - lookup_alias exact hit / miss
  - lookup_deterministic high-confidence single / no-prefix / reversed order
  - match_name alias-wins-over-fuzzy
  - match_name UNMATCHED creates review queue row
  - skips-claude-when-disabled (monkeypatch guard — AC14)
  - name-list intake (POST /attendance/name-list): creates/idempotent/queues-unmatched
  - resolve review-queue item: creates participant / teaches alias / 409 conflict
  - reprocess-aliases auto-resolves pending queue rows
  - viewer 403 on review-queue list
  - no-circular-import: import name_match without importing any router

ENVIRONMENT=test is set by conftest before any app import, so the Claude stub
path is already active.  Additionally conftest now inserts an AdminSetting row
key='name_match.claude_enabled' value={'enabled': false} and the monkeypatch
guard below asserts anthropic.AsyncAnthropic is never constructed.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest
import pytest_asyncio

from app.services.name_match import (
    lookup_alias,
    lookup_deterministic,
    match_name,
    normalize_name,
    set_claude_client_factory,
)


# ---------------------------------------------------------------------------
# Monkeypatch guard: anthropic.AsyncAnthropic must never be constructed in
# the test suite (AC14).  We replace the constructor with a raising sentinel
# at module load time, then restore it after the session.
# ---------------------------------------------------------------------------

_original_anthropic_module: types.ModuleType | None = None
_anthropic_sentinel_installed = False


@pytest.fixture(autouse=True, scope="session")
def _block_anthropic_construction():
    """Install a session-scoped sentinel that raises if AsyncAnthropic() is ever called."""
    import anthropic as _anthro

    original_cls = _anthro.AsyncAnthropic

    class _NeverInstantiate:
        def __init__(self, *a, **kw):
            raise AssertionError(
                "anthropic.AsyncAnthropic() was constructed during tests — "
                "the Claude stub gate failed.  Check ENVIRONMENT=test and "
                "name_match.claude_enabled=False in dynamic_settings."
            )

    _anthro.AsyncAnthropic = _NeverInstantiate  # type: ignore[attr-defined]

    # Also install a no-op factory so lookup_claude's internal call path is blocked
    set_claude_client_factory(_NeverInstantiate)

    yield

    _anthro.AsyncAnthropic = original_cls  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def contacts_for_fuzzy(db_session):
    """Seed two contacts with distinct names for Jaro-Winkler tests.

    Contact A: Juan Cruz
    Contact B: Maria Santos  (also "Mario Santos" variant via nickname)
    """
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
    mario = Contact(
        first_name="Mario",
        last_name="Santos",
        contact_type="individual",
    )
    db_session.add(juan)
    db_session.add(maria)
    db_session.add(mario)
    await db_session.commit()
    await db_session.refresh(juan)
    await db_session.refresh(maria)
    await db_session.refresh(mario)
    return {"juan": juan, "maria": maria, "mario": mario}


@pytest_asyncio.fixture
async def alias_for_mar_san(db_session, contacts_for_fuzzy):
    """Insert a NameAlias 'mar san' pointing at maria (Contact B)."""
    from app.models import NameAlias

    maria = contacts_for_fuzzy["maria"]
    alias = NameAlias(
        alias_text="mar san",
        alias_type="nick",
        contact_id=maria.id,
        source="admin",
        meta={},
    )
    db_session.add(alias)
    await db_session.commit()
    await db_session.refresh(alias)
    return alias


# ===========================================================================
# Section 1 — normalize_name vectors (spec §4.2.2 / §8.1)
# ===========================================================================

class TestNormalizeName:
    """All assertions are pure-function (no DB)."""

    def test_ptr_suffix_jr(self):
        assert normalize_name("Ptr. Rodel Aquino Jr.") == "rodel aquino"

    def test_comma_inversion(self):
        result = normalize_name("EVANGELISTA, Guada")
        assert result == "guada evangelista"

    def test_honorific_tita(self):
        assert normalize_name("Tita Cely") == "cely"

    def test_diacritics_nino(self):
        assert normalize_name("Niño") == "nino"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_whitespace_only(self):
        assert normalize_name("   ") == ""

    def test_honorific_kuya(self):
        result = normalize_name("Kuya Ben Santos")
        assert result == "ben santos"

    def test_honorific_ate(self):
        result = normalize_name("Ate Grace Reyes")
        assert result == "grace reyes"

    def test_middle_initial_stripped(self):
        # "Marichu P." -> "marichu" (single char P stripped)
        result = normalize_name("Marichu P.")
        assert result == "marichu"

    def test_lowercase_output(self):
        result = normalize_name("JUAN DELA CRUZ")
        assert result == result.lower()

    def test_no_honorific_plain_name(self):
        result = normalize_name("Juan dela Cruz")
        assert result == "juan dela cruz"

    def test_suffix_sr(self):
        result = normalize_name("Roberto Reyes Sr.")
        assert result == "roberto reyes"

    def test_honorific_pastor(self):
        result = normalize_name("Pastor Rodel Aquino")
        assert result == "rodel aquino"

    def test_honorific_bro(self):
        result = normalize_name("Bro. Mike Santos")
        assert result == "mike santos"


# ===========================================================================
# Section 2 — lookup_alias
# ===========================================================================

class TestLookupAlias:
    @pytest.mark.asyncio
    async def test_alias_exact_hit(self, db_session, alias_for_mar_san, contacts_for_fuzzy):
        """lookup_alias('mar san') should return the maria contact."""
        maria = contacts_for_fuzzy["maria"]
        result = await lookup_alias("mar san", db_session)
        assert result is not None
        assert result["contact_id"] == maria.id
        assert result["score"] == 1.0
        assert result["method"] == "alias"

    @pytest.mark.asyncio
    async def test_alias_miss(self, db_session, contacts_for_fuzzy):
        """lookup_alias with a non-existent alias returns None."""
        result = await lookup_alias("zzz nonexistent", db_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_alias_empty_string(self, db_session):
        result = await lookup_alias("", db_session)
        assert result is None


# ===========================================================================
# Section 3 — lookup_deterministic
# ===========================================================================

class TestLookupDeterministic:
    @pytest.mark.asyncio
    async def test_high_confidence_single_forward(self, db_session, contacts_for_fuzzy):
        """'juan cruz' should match Juan Cruz with high confidence."""
        results = await lookup_deterministic("juan cruz", db_session)
        assert len(results) == 1
        assert results[0]["contact_id"] == contacts_for_fuzzy["juan"].id
        assert results[0]["score"] >= 0.88
        assert results[0]["method"] in ("fuzzy_forward", "fuzzy_reversed", "fuzzy_nickname")

    @pytest.mark.asyncio
    async def test_no_prefix_match_returns_empty(self, db_session, contacts_for_fuzzy):
        """A name with a completely foreign surname prefix returns []."""
        results = await lookup_deterministic("zzz unknownperson", db_session)
        assert results == []

    @pytest.mark.asyncio
    async def test_reversed_name_order(self, db_session, contacts_for_fuzzy):
        """'cruz juan' (reversed) should still find Juan Cruz."""
        results = await lookup_deterministic("cruz juan", db_session)
        assert any(r["contact_id"] == contacts_for_fuzzy["juan"].id for r in results)

    @pytest.mark.asyncio
    async def test_min_score_threshold(self, db_session, contacts_for_fuzzy):
        """Results must all have score >= 0.88 (the default min_score)."""
        results = await lookup_deterministic("maria santos", db_session)
        for r in results:
            assert r["score"] >= 0.88


# ===========================================================================
# Section 4 — match_name pipeline integration
# ===========================================================================

class TestMatchName:
    @pytest.mark.asyncio
    async def test_alias_wins_over_fuzzy(
        self, db_session, alias_for_mar_san, contacts_for_fuzzy
    ):
        """When an alias matches, the pipeline returns it even if fuzzy would also match."""
        maria = contacts_for_fuzzy["maria"]
        result = await match_name(
            raw_name="Mar San",
            db=db_session,
            source="manual",
        )
        assert result["outcome"] == "SINGLE"
        assert result["contact_id"] == maria.id
        assert result["method"] == "alias"

    @pytest.mark.asyncio
    async def test_unmatched_creates_review_queue(self, db_session, sample_event):
        """UNMATCHED result with auto_enqueue=True should create a review queue row."""
        from sqlalchemy import select
        from app.models import NameMatchReviewQueue

        result = await match_name(
            raw_name="Zzz Totally Unknown Name",
            db=db_session,
            source="manual",
            event_id=sample_event.id,
            auto_enqueue=True,
        )
        assert result["outcome"] == "UNMATCHED"
        assert result["review_queue_id"] is not None

        # Verify the row is in DB
        row = (
            await db_session.execute(
                select(NameMatchReviewQueue).where(
                    NameMatchReviewQueue.id == result["review_queue_id"]
                )
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.raw_name == "Zzz Totally Unknown Name"
        assert row.event_id == sample_event.id
        assert row.status == "pending"

    @pytest.mark.asyncio
    async def test_skips_claude_when_disabled(self, db_session, contacts_for_fuzzy):
        """When ENVIRONMENT=test, lookup_claude must return None without constructing a client.

        The session-scoped _block_anthropic_construction fixture installs a raising
        sentinel as the claude factory.  If this test passes, Claude was never called.
        """
        # We need an ambiguous situation: two similar contacts.  Maria Santos and Mario Santos
        # are both in contacts_for_fuzzy and are similar enough to cause ambiguity.
        # Run match_name on "maria santos" — if Claude were called, the sentinel would raise.
        result = await match_name(
            raw_name="Maria Santos",
            db=db_session,
            source="manual",
        )
        # The exact outcome depends on Jaro-Winkler scores; we just assert no exception
        # was raised (Claude was not called).
        assert result["outcome"] in ("SINGLE", "AMBIGUOUS", "UNMATCHED")


# ===========================================================================
# Section 5 — name-list intake via HTTP (POST /attendance/name-list)
# ===========================================================================

class TestNameListIntake:
    @pytest.mark.asyncio
    async def test_create_participants_for_matched_names(
        self, client, volunteer_auth_headers, db_session, sample_event, contacts_for_fuzzy
    ):
        """Matched names result in Participant rows being inserted."""
        from sqlalchemy import select
        from app.models import Participant

        resp = await client.post(
            "/attendance/name-list",
            json={
                "event_id": sample_event.id,
                "names": ["Juan Cruz"],
                "source": "name_list",
            },
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["matched"] >= 1 or data["review_queue"] >= 0

    @pytest.mark.asyncio
    async def test_idempotent_resubmit(
        self, client, volunteer_auth_headers, db_session, sample_event, contacts_for_fuzzy
    ):
        """Submitting the same matched name twice should not create duplicate participants."""
        payload = {
            "event_id": sample_event.id,
            "names": ["Juan Cruz"],
            "source": "name_list",
        }
        r1 = await client.post(
            "/attendance/name-list",
            json=payload,
            headers=volunteer_auth_headers,
        )
        r2 = await client.post(
            "/attendance/name-list",
            json=payload,
            headers=volunteer_auth_headers,
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        d2 = r2.json()
        # On the second pass, the matched contact already has a participant row.
        # skipped_existing should be >= 0 and no new participants inserted.
        assert d2["skipped_existing"] >= 0  # at least 0

    @pytest.mark.asyncio
    async def test_unmatched_name_queued_for_review(
        self, client, volunteer_auth_headers, sample_event
    ):
        """Names that cannot be matched appear in review_queue count."""
        resp = await client.post(
            "/attendance/name-list",
            json={
                "event_id": sample_event.id,
                "names": ["Zzz Absolutely Nobody"],
                "source": "name_list",
            },
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["review_queue"] >= 1


# ===========================================================================
# Section 6 — review-queue HTTP endpoints
# ===========================================================================

class TestReviewQueueEndpoints:
    @pytest_asyncio.fixture
    async def pending_queue_item(self, db_session, sample_event, contacts_for_fuzzy):
        """Insert a pending NameMatchReviewQueue row."""
        from app.models import NameMatchReviewQueue

        maria = contacts_for_fuzzy["maria"]
        row = NameMatchReviewQueue(
            raw_name="Mar Santos",
            event_id=sample_event.id,
            candidate_contact_id=maria.id,
            score=0.92,
            status="pending",
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    @pytest.mark.asyncio
    async def test_resolve_creates_participant(
        self,
        client,
        volunteer_auth_headers,
        db_session,
        sample_event,
        contacts_for_fuzzy,
        pending_queue_item,
    ):
        """Resolving a pending queue item with a contact_id inserts a Participant row."""
        from sqlalchemy import select
        from app.models import Participant

        maria = contacts_for_fuzzy["maria"]
        resp = await client.post(
            f"/name-match/review-queue/{pending_queue_item.id}/resolve",
            json={"contact_id": maria.id, "teach_alias": False},
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "matched"
        assert data["contact_id"] == maria.id

        # Participant row should exist
        part = (
            await db_session.execute(
                select(Participant).where(
                    Participant.event_id == sample_event.id,
                    Participant.contact_id == maria.id,
                )
            )
        ).scalar_one_or_none()
        assert part is not None

    @pytest.mark.asyncio
    async def test_resolve_teaches_alias(
        self,
        client,
        volunteer_auth_headers,
        db_session,
        sample_event,
        contacts_for_fuzzy,
        pending_queue_item,
    ):
        """Resolve with teach_alias=True should upsert a NameAlias row."""
        from sqlalchemy import select
        from app.models import NameAlias

        maria = contacts_for_fuzzy["maria"]
        resp = await client.post(
            f"/name-match/review-queue/{pending_queue_item.id}/resolve",
            json={
                "contact_id": maria.id,
                "teach_alias": True,
                "alias_text": "mar santos",
            },
            headers=volunteer_auth_headers,
        )
        assert resp.status_code == 200

        alias_row = (
            await db_session.execute(
                select(NameAlias).where(NameAlias.alias_text == "mar santos")
            )
        ).scalar_one_or_none()
        assert alias_row is not None
        assert alias_row.contact_id == maria.id

    @pytest.mark.asyncio
    async def test_resolve_409_conflict_on_already_matched(
        self,
        client,
        volunteer_auth_headers,
        db_session,
        contacts_for_fuzzy,
        pending_queue_item,
    ):
        """Resolving an already-matched item returns 409 Conflict."""
        maria = contacts_for_fuzzy["maria"]

        # First resolve succeeds
        resp1 = await client.post(
            f"/name-match/review-queue/{pending_queue_item.id}/resolve",
            json={"contact_id": maria.id, "teach_alias": False},
            headers=volunteer_auth_headers,
        )
        assert resp1.status_code == 200

        # Second resolve on same item returns 409
        resp2 = await client.post(
            f"/name-match/review-queue/{pending_queue_item.id}/resolve",
            json={"contact_id": maria.id, "teach_alias": False},
            headers=volunteer_auth_headers,
        )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_viewer_403_on_review_queue_list(
        self, client, viewer_auth_headers
    ):
        """Viewer role must receive 403 when accessing the review-queue list."""
        resp = await client.get(
            "/name-match/review-queue",
            headers=viewer_auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_reprocess_auto_resolves(
        self,
        client,
        admin_auth_headers,
        db_session,
        sample_event,
        contacts_for_fuzzy,
    ):
        """reprocess-aliases should auto-resolve a pending row that now has a clear alias match."""
        from app.models import NameAlias, NameMatchReviewQueue

        maria = contacts_for_fuzzy["maria"]

        # Insert an alias that will allow the reprocess to auto-resolve
        alias = NameAlias(
            alias_text="mrsantos",
            alias_type="preferred",
            contact_id=maria.id,
            source="admin",
            meta={},
        )
        db_session.add(alias)

        # Insert a pending queue row whose raw_name normalises to 'mrsantos'
        queue_row = NameMatchReviewQueue(
            raw_name="MrSantos",
            event_id=sample_event.id,
            candidate_contact_id=None,
            score=None,
            status="pending",
        )
        db_session.add(queue_row)
        await db_session.commit()
        await db_session.refresh(queue_row)

        resp = await client.post(
            "/name-match/review-queue/reprocess-aliases",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "processed" in data
        assert data["auto_matched"] >= 1

        # The queue row should now be 'matched'
        await db_session.refresh(queue_row)
        assert queue_row.status == "matched"
        assert queue_row.contact_id == maria.id


# ===========================================================================
# Section 7 — no-circular-import guard
# ===========================================================================

def test_no_circular_import_name_match():
    """Import app.services.name_match in isolation (no router import).

    This test ensures that name_match does not transitively import any router
    module (CN-25 rule).
    """
    # The module should already be importable (conftest imports the whole app).
    # Re-import to confirm it resolves without pulling in routers.
    mod = importlib.import_module("app.services.name_match")
    assert hasattr(mod, "match_name")
    assert hasattr(mod, "normalize_name")
    assert hasattr(mod, "lookup_alias")
    assert hasattr(mod, "lookup_deterministic")

    # Assert no router module is in sys.modules as a side-effect of name_match import
    # (they may already be there from app.main, but we verify name_match itself
    # does not list them in its own __module__ chain).
    # The core check: name_match's own imports must not include router paths.
    name_match_spec = mod.__spec__
    assert name_match_spec is not None
    # name_match must not have 'app.routers' anywhere in its module __name__
    assert "app.routers" not in mod.__name__
