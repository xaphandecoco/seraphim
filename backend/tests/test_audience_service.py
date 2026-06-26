"""Tests for S05-F04: audience resolver service.

Covers all acceptance criteria:
  AC1  mode='all' returns base select (includes all non-deleted contacts)
  AC2  mode='all' with include_deleted=True omits soft-delete filter
  AC3  mode='ids' validates against real contacts, drops unknowns silently
  AC4  mode='ids' with empty list returns zero rows
  AC5  mode='ids' with only unknown ids returns zero rows
  AC6  mode='group' missing Group -> 404
  AC7  mode='group' static type joins GroupMember, respects soft-delete
  AC8  mode='saved_search' missing SavedSearch -> 404
  AC9  smart group / saved_search without S09 criteria compiler -> 400
  AC10 count_audience returns COUNT(*) over resolve_audience subquery
  AC11 mode='all' does not include deleted contacts by default

ISOLATION NOTE
--------------
Tests that rely on sys.modules patching + importlib.reload MUST NOT take the
db_session fixture (which uses SQLAlchemy's async engine).  Mixing module
reloads with the async engine causes SQLite I/O errors in fixture teardown due
to connection state corruption.  Those tests use AsyncMock(spec=AsyncSession)
exclusively.
"""

from __future__ import annotations

import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import make_contacts


# ---------------------------------------------------------------------------
# Helper: build a minimal AudienceSelector namespace
# ---------------------------------------------------------------------------


def audience(mode: str, **kw) -> SimpleNamespace:
    defaults = {
        "include_deleted": False,
        "ids": None,
        "group_id": None,
        "group_type": None,
        "saved_search_id": None,
    }
    defaults.update(kw)
    defaults["mode"] = mode
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Helper: patch app.models with fake attrs, reload aud_mod, restore on exit
# ---------------------------------------------------------------------------


class _ModelPatch:
    """Context manager that temporarily injects extra attributes into
    app.models (without breaking the existing module) and reloads the
    audience service so it picks them up.  Restores everything on exit."""

    def __init__(self, **extra_attrs):
        self._extra_attrs = extra_attrs
        self._original_module = None

    def __enter__(self):
        self._original_module = sys.modules.get("app.models")
        fake = types.ModuleType("app.models")
        if self._original_module:
            for attr in dir(self._original_module):
                try:
                    setattr(fake, attr, getattr(self._original_module, attr))
                except Exception:
                    pass
        for k, v in self._extra_attrs.items():
            setattr(fake, k, v)
        sys.modules["app.models"] = fake
        from app.services import audience as aud_mod
        importlib.reload(aud_mod)
        return aud_mod

    def __exit__(self, *_):
        if self._original_module is not None:
            sys.modules["app.models"] = self._original_module
        from app.services import audience as aud_mod
        importlib.reload(aud_mod)


class _CCPatch:
    """Context manager that temporarily removes criteria_compiler from
    sys.modules and reloads the audience service."""

    def __init__(self):
        self._original_cc = None

    def __enter__(self):
        self._original_cc = sys.modules.pop("app.services.criteria_compiler", None)
        from app.services import audience as aud_mod
        importlib.reload(aud_mod)
        return aud_mod

    def __exit__(self, *_):
        if self._original_cc is not None:
            sys.modules["app.services.criteria_compiler"] = self._original_cc
        from app.services import audience as aud_mod
        importlib.reload(aud_mod)


# ---------------------------------------------------------------------------
# AC1 / AC11 — mode='all' returns only non-deleted contacts by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_all_returns_non_deleted_contacts(db_session):
    """AC1 + AC11: mode='all' returns only non-deleted contacts by default."""
    live = await make_contacts(db_session, 3)
    deleted = await make_contacts(db_session, 2, is_deleted=True)
    await db_session.commit()

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(db_session, audience("all"))
    result = await db_session.execute(stmt)
    ids = {row[0] for row in result.fetchall()}

    live_ids = {c.id for c in live}
    deleted_ids = {c.id for c in deleted}

    assert live_ids.issubset(ids), "all live contacts should be returned"
    assert ids.isdisjoint(deleted_ids), "deleted contacts must not appear"


# ---------------------------------------------------------------------------
# AC2 — mode='all' with include_deleted=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_all_include_deleted(db_session):
    """AC2: include_deleted=True lifts the soft-delete guard."""
    live = await make_contacts(db_session, 2)
    deleted = await make_contacts(db_session, 1, is_deleted=True)
    await db_session.commit()

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(db_session, audience("all", include_deleted=True))
    result = await db_session.execute(stmt)
    ids = {row[0] for row in result.fetchall()}

    for c in live + deleted:
        assert c.id in ids, f"contact {c.id} should be present when include_deleted=True"


# ---------------------------------------------------------------------------
# AC3 — mode='ids' drops unknowns silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_ids_drops_unknowns_silently(db_session):
    """AC3: ids mode validates against real contacts; unknown ids are dropped."""
    contacts = await make_contacts(db_session, 3)
    await db_session.commit()

    real_ids = [c.id for c in contacts]
    fake_ids = [99998, 99999]

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(
        db_session, audience("ids", ids=real_ids + fake_ids)
    )
    result = await db_session.execute(stmt)
    returned = {row[0] for row in result.fetchall()}

    assert returned == set(real_ids), "unknowns must be dropped silently"
    assert not returned.intersection(fake_ids), "fake ids must not appear"


# ---------------------------------------------------------------------------
# AC4 — mode='ids' with empty list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_ids_empty_list_returns_zero_rows(db_session):
    """AC4: empty ids list returns zero rows."""
    await make_contacts(db_session, 2)
    await db_session.commit()

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(db_session, audience("ids", ids=[]))
    result = await db_session.execute(stmt)
    assert result.fetchall() == [], "empty ids list must yield zero rows"


# ---------------------------------------------------------------------------
# AC5 — mode='ids' with only unknowns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_ids_only_unknowns_returns_zero_rows(db_session):
    """AC5: when all ids are unknown, result is empty."""
    await make_contacts(db_session, 2)
    await db_session.commit()

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(db_session, audience("ids", ids=[99997, 99998]))
    result = await db_session.execute(stmt)
    assert result.fetchall() == []


# ---------------------------------------------------------------------------
# AC3 supplement — deleted contacts excluded from ids mode by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_ids_excludes_deleted_by_default(db_session):
    """Deleted contacts are excluded even if their id is explicitly in the list."""
    live = await make_contacts(db_session, 1)
    deleted = await make_contacts(db_session, 1, is_deleted=True)
    await db_session.commit()

    from app.services.audience import resolve_audience

    stmt = await resolve_audience(
        db_session,
        audience("ids", ids=[live[0].id, deleted[0].id]),
    )
    result = await db_session.execute(stmt)
    ids = {row[0] for row in result.fetchall()}

    assert live[0].id in ids
    assert deleted[0].id not in ids


# ---------------------------------------------------------------------------
# AC6 — mode='group' missing Group -> 404
# (No db_session: uses db_mock only — avoids module-reload engine corruption)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_group_missing_group_raises_404():
    """AC6: db.get(Group, id) returning None must raise HTTPException(404)."""
    from fastapi import HTTPException

    class _FakeGroup:
        pass

    class _FakeGroupMember:
        pass

    db_mock = AsyncMock(spec=AsyncSession)
    db_mock.get = AsyncMock(return_value=None)

    with _ModelPatch(Group=_FakeGroup, GroupMember=_FakeGroupMember) as aud_mod:
        with pytest.raises(HTTPException) as exc_info:
            await aud_mod.resolve_audience(
                db_mock, audience("group", group_id=9999)
            )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# AC7 — mode='group' static type builds correct SQL (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_group_static_query_structure():
    """AC7: static group mode generates a SELECT with GroupMember join + soft-delete.

    Verifies the SQL structure emitted by resolve_audience for
    mode='group', group_type='static' without executing against real tables.

    The service code does:
        select(Contact.id)
            .join(GroupMember, GroupMember.contact_id == Contact.id)
            .where(GroupMember.group_id == group_id)
            .where(Contact.is_deleted == False)

    We need _GroupMember to be a valid SQLAlchemy join target. We use a
    lightweight ORM-mapped class via sqlalchemy.orm.registry (separate
    metadata, not Base) so it does NOT appear in Base.metadata.create_all.
    """
    from sqlalchemy import Column, Integer as SAInt, MetaData, String as SAStr
    from sqlalchemy.dialects import sqlite as sa_sqlite
    from sqlalchemy.orm import registry as sa_registry

    # Use an isolated metadata — does NOT touch Base.metadata
    stub_meta = MetaData()
    mapper_reg = sa_registry(metadata=stub_meta)

    @mapper_reg.mapped
    class _Group:
        __tablename__ = "groups_s09_stub_v2"
        id = Column(SAInt, primary_key=True)
        group_type = Column(SAStr(20))
        criteria = Column(SAStr)

    @mapper_reg.mapped
    class _GroupMember:
        __tablename__ = "group_members_s09_stub_v2"
        id = Column(SAInt, primary_key=True)
        group_id = Column(SAInt)
        contact_id = Column(SAInt)

    fake_group = SimpleNamespace(id=42, group_type="static", criteria=None)

    db_mock = AsyncMock(spec=AsyncSession)
    db_mock.get = AsyncMock(return_value=fake_group)

    with _ModelPatch(Group=_Group, GroupMember=_GroupMember) as aud_mod:
        stmt = await aud_mod.resolve_audience(
            db_mock, audience("group", group_id=42, group_type="static")
        )

    compiled = str(stmt.compile(
        dialect=sa_sqlite.dialect(),
        compile_kwargs={"literal_binds": True},
    ))

    assert "group_members_s09_stub_v2" in compiled, (
        "static group SELECT must JOIN the group_members table"
    )
    assert "is_deleted" in compiled, (
        "static group SELECT must filter Contact.is_deleted"
    )
    assert "42" in compiled, "group_id=42 filter must appear in compiled SQL"


# ---------------------------------------------------------------------------
# AC8 — mode='saved_search' missing SavedSearch -> 404 (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_saved_search_missing_raises_404():
    """AC8: db.get(SavedSearch, id) returning None -> HTTPException(404)."""
    from fastapi import HTTPException

    class _FakeSavedSearch:
        pass

    db_mock = AsyncMock(spec=AsyncSession)
    db_mock.get = AsyncMock(return_value=None)

    with _ModelPatch(SavedSearch=_FakeSavedSearch) as aud_mod:
        with pytest.raises(HTTPException) as exc_info:
            await aud_mod.resolve_audience(
                db_mock, audience("saved_search", saved_search_id=7777)
            )

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# AC9 — smart group without criteria compiler -> 400 (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smart_group_without_criteria_compiler_raises_400():
    """AC9: smart group raises 400 when criteria_compiler (S09) is not installed."""
    from fastapi import HTTPException

    class _FakeGroup:
        def __init__(self):
            self.id = 1
            self.group_type = "smart"
            self.criteria = {"field": "tier", "op": "eq", "value": "Tier1"}

    class _FakeGroupMember:
        pass

    db_mock = AsyncMock(spec=AsyncSession)
    db_mock.get = AsyncMock(return_value=_FakeGroup())

    with _ModelPatch(Group=_FakeGroup, GroupMember=_FakeGroupMember):
        with _CCPatch() as aud_mod:
            with pytest.raises(HTTPException) as exc_info:
                await aud_mod.resolve_audience(
                    db_mock, audience("group", group_id=1, group_type="smart")
                )

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert "S09" in detail or "criteria" in detail.lower()


# ---------------------------------------------------------------------------
# AC9 — saved_search without criteria compiler -> 400 (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saved_search_without_criteria_compiler_raises_400():
    """AC9: saved_search mode raises 400 when criteria_compiler (S09) missing."""
    from fastapi import HTTPException

    class _FakeSavedSearch:
        def __init__(self):
            self.id = 99
            self.criteria = {"field": "first_name", "op": "contains", "value": "Jo"}

    db_mock = AsyncMock(spec=AsyncSession)
    db_mock.get = AsyncMock(return_value=_FakeSavedSearch())

    with _ModelPatch(SavedSearch=_FakeSavedSearch):
        with _CCPatch() as aud_mod:
            with pytest.raises(HTTPException) as exc_info:
                await aud_mod.resolve_audience(
                    db_mock, audience("saved_search", saved_search_id=99)
                )

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert "S09" in detail or "criteria" in detail.lower()


# ---------------------------------------------------------------------------
# AC10 — count_audience returns COUNT(*) over subquery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_audience_mode_all(db_session):
    """AC10: count_audience returns correct integer count."""
    contacts = await make_contacts(db_session, 4)
    _deleted = await make_contacts(db_session, 2, is_deleted=True)
    await db_session.commit()

    from app.services.audience import count_audience

    count = await count_audience(db_session, audience("all"))
    assert count == len(contacts), f"expected {len(contacts)}, got {count}"


@pytest.mark.asyncio
async def test_count_audience_mode_ids(db_session):
    """AC10: count_audience works for ids mode."""
    contacts = await make_contacts(db_session, 5)
    await db_session.commit()

    from app.services.audience import count_audience

    selected = contacts[:3]
    count = await count_audience(
        db_session, audience("ids", ids=[c.id for c in selected])
    )
    assert count == 3


@pytest.mark.asyncio
async def test_count_audience_mode_ids_drops_unknowns(db_session):
    """AC10 + AC3: count properly excludes unknown ids."""
    contacts = await make_contacts(db_session, 2)
    await db_session.commit()

    from app.services.audience import count_audience

    count = await count_audience(
        db_session,
        audience("ids", ids=[c.id for c in contacts] + [99999]),
    )
    assert count == 2


# ---------------------------------------------------------------------------
# AC9 (_compile_criteria) — ImportError -> HTTPException(400) with "S09" ref
# (synchronous: no db_session, no async engine interaction)
# ---------------------------------------------------------------------------


def test_compile_criteria_importerror_raises_400():
    """AC9: _compile_criteria converts ImportError to HTTPException(400)."""
    from fastapi import HTTPException

    with _CCPatch() as aud_mod:
        with pytest.raises(HTTPException) as exc_info:
            aud_mod._compile_criteria({"some": "criteria"})

    assert exc_info.value.status_code == 400
    assert "S09" in exc_info.value.detail


# ---------------------------------------------------------------------------
# Unknown mode -> 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_mode_raises_400(db_session):
    """Unknown mode string raises HTTPException(400)."""
    from fastapi import HTTPException
    from app.services.audience import resolve_audience

    with pytest.raises(HTTPException) as exc_info:
        await resolve_audience(db_session, audience("bogus_mode"))

    assert exc_info.value.status_code == 400
    assert "bogus_mode" in exc_info.value.detail


# ---------------------------------------------------------------------------
# mode='group' missing group_id -> 400 (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_group_missing_group_id_raises_400():
    """mode='group' without group_id raises HTTPException(400)."""
    from fastapi import HTTPException

    class _FakeGroup:
        pass

    class _FakeGroupMember:
        pass

    db_mock = AsyncMock(spec=AsyncSession)

    with _ModelPatch(Group=_FakeGroup, GroupMember=_FakeGroupMember) as aud_mod:
        with pytest.raises(HTTPException) as exc_info:
            await aud_mod.resolve_audience(
                db_mock, audience("group")  # group_id=None
            )

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# mode='saved_search' missing saved_search_id -> 400 (no db_session)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_saved_search_missing_id_raises_400():
    """mode='saved_search' without saved_search_id raises HTTPException(400)."""
    from fastapi import HTTPException

    class _FakeSavedSearch:
        pass

    db_mock = AsyncMock(spec=AsyncSession)

    with _ModelPatch(SavedSearch=_FakeSavedSearch) as aud_mod:
        with pytest.raises(HTTPException) as exc_info:
            await aud_mod.resolve_audience(
                db_mock, audience("saved_search")  # saved_search_id=None
            )

    assert exc_info.value.status_code == 400
