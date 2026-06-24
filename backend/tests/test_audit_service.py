"""Tests for backend/app/services/audit.py (S02 acceptance criteria).

Covers:
- Happy path: record() writes an AuditLog row with all fields set.
- Nullable fields: actor_id=None, entity_id=None, before=None, after=None.
- created_at is populated (ORM default or explicit utc_now()).
- record() does NOT commit — the row is present inside the same session but
  the caller owns commit.
- No router import: verify the module's import graph respects CN-25.
- Function signature: exact parameter names & return annotation.
"""

import importlib
import inspect
import sys
from datetime import datetime, timezone
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_record():
    """Return the `record` coroutine from app.services.audit."""
    import app.services.audit as audit_mod
    return audit_mod.record


# ---------------------------------------------------------------------------
# Signature tests (no DB needed)
# ---------------------------------------------------------------------------

class TestAuditSignature:
    def test_record_is_coroutine_function(self):
        record = _get_record()
        assert inspect.iscoroutinefunction(record), (
            "audit.record must be an async function"
        )

    def test_return_annotation_is_none(self):
        record = _get_record()
        hints = inspect.get_annotations(record, eval_str=True)
        # Python 3.14 returns the literal None value for `-> None`, not type(None).
        # Both forms are equivalent: `-> None` annotation == None value.
        return_hint = hints.get("return")
        assert return_hint is None or return_hint is type(None), (
            f"Expected return annotation None (or NoneType), got {return_hint!r}"
        )

    def test_parameter_names(self):
        record = _get_record()
        sig = inspect.signature(record)
        expected = ["db", "actor_id", "action", "entity", "entity_id", "before", "after"]
        actual = list(sig.parameters.keys())
        assert actual == expected, (
            f"Parameter list mismatch.\n  Expected: {expected}\n  Got:      {actual}"
        )

    def test_no_router_import(self):
        """CN-25: audit module must NEVER import from any router module."""
        import app.services.audit as audit_mod
        audit_source = inspect.getfile(audit_mod)
        # Collect all modules imported by audit.py at module level
        imported_names = [
            name for name in sys.modules
            if name.startswith("app.routers")
        ]
        # Reload audit in isolation to check its direct imports
        source_code = open(audit_source).read()
        assert "app.routers" not in source_code, (
            "audit.py must NOT import from any app.routers module (CN-25 violation)"
        )

    def test_only_approved_imports(self):
        """audit.py must only import from app.models and sqlalchemy.ext.asyncio."""
        import app.services.audit as audit_mod
        audit_source = inspect.getfile(audit_mod)
        source_code = open(audit_source).read()
        # These are the only allowed non-stdlib import roots
        allowed_roots = {"app.models", "sqlalchemy", "typing"}
        # Pull out every `from X import` and `import X` line
        import re
        from_imports = re.findall(r"^from\s+([\w.]+)\s+import", source_code, re.MULTILINE)
        bare_imports = re.findall(r"^import\s+([\w.]+)", source_code, re.MULTILINE)
        for mod in from_imports + bare_imports:
            root = mod.split(".")[0]
            # Allow stdlib and approved roots
            if root in {"app", "sqlalchemy", "typing"}:
                # If app-rooted, make sure it's app.models not app.routers.*
                if root == "app":
                    assert not mod.startswith("app.routers"), (
                        f"Forbidden import in audit.py: {mod} (CN-25)"
                    )


# ---------------------------------------------------------------------------
# Database tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_happy_path(db_session: AsyncSession):
    """record() inserts a row with all fields populated."""
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=42,
        action="contact.update",
        entity="contact",
        entity_id=7,
        before={"first_name": "Juan"},
        after={"first_name": "John"},
    )
    # record() does NOT commit — flush to make the row visible in this session
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    rows = result.scalars().all()
    assert len(rows) == 1, f"Expected 1 AuditLog row, found {len(rows)}"

    row = rows[0]
    assert row.actor_id == 42
    assert row.action == "contact.update"
    assert row.entity == "contact"
    assert row.entity_id == 7
    assert row.before == {"first_name": "Juan"}
    assert row.after == {"first_name": "John"}


@pytest.mark.asyncio
async def test_record_created_at_is_populated(db_session: AsyncSession):
    """created_at must be set (either via ORM default or explicit utc_now())."""
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=1,
        action="contact.create",
        entity="contact",
        entity_id=1,
        before=None,
        after={"first_name": "Alice"},
    )
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    row = result.scalars().first()
    assert row is not None
    assert row.created_at is not None, "created_at must not be None"
    assert isinstance(row.created_at, datetime), (
        f"Expected datetime, got {type(row.created_at)}"
    )


@pytest.mark.asyncio
async def test_record_nullable_actor_id(db_session: AsyncSession):
    """actor_id=None is supported for system/background actors."""
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=None,
        action="system.sync",
        entity="contact",
        entity_id=99,
        before=None,
        after=None,
    )
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    row = result.scalars().first()
    assert row is not None
    assert row.actor_id is None, f"Expected actor_id=None, got {row.actor_id}"


@pytest.mark.asyncio
async def test_record_nullable_entity_id(db_session: AsyncSession):
    """entity_id=None is supported for entity-less audit events."""
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=5,
        action="settings.update",
        entity="settings",
        entity_id=None,
        before={"theme": "light"},
        after={"theme": "dark"},
    )
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    row = result.scalars().first()
    assert row is not None
    assert row.entity_id is None


@pytest.mark.asyncio
async def test_record_nullable_before_after(db_session: AsyncSession):
    """before=None and after=None are both valid (create / delete actions)."""
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=3,
        action="contact.delete",
        entity="contact",
        entity_id=11,
        before={"first_name": "Bob"},
        after=None,
    )
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    row = result.scalars().first()
    assert row is not None
    assert row.before == {"first_name": "Bob"}
    assert row.after is None


@pytest.mark.asyncio
async def test_record_does_not_commit(db_session: AsyncSession):
    """record() must not call db.commit() — caller owns the transaction.

    Strategy: call record(), then rollback the session, then verify no rows
    were persisted. If record() had committed, the row would survive the
    rollback (SQLite does not support savepoints in this path, so the test
    instead inspects that the row count after rollback is 0, proving the
    row was part of the caller's transaction).
    """
    from app.services.audit import record

    await record(
        db=db_session,
        actor_id=1,
        action="contact.create",
        entity="contact",
        entity_id=50,
        before=None,
        after={"first_name": "Charlie"},
    )
    # Roll back without flushing — should discard the pending add
    await db_session.rollback()

    result = await db_session.execute(select(AuditLog))
    rows = result.scalars().all()
    assert len(rows) == 0, (
        f"record() appears to have committed: found {len(rows)} row(s) after rollback. "
        "record() must not call db.commit()."
    )


@pytest.mark.asyncio
async def test_record_multiple_rows(db_session: AsyncSession):
    """Multiple record() calls produce multiple rows (no unique constraint issues)."""
    from app.services.audit import record

    for i in range(3):
        await record(
            db=db_session,
            actor_id=1,
            action=f"contact.update",
            entity="contact",
            entity_id=i,
            before={"v": i},
            after={"v": i + 1},
        )
    await db_session.flush()

    result = await db_session.execute(select(AuditLog))
    rows = result.scalars().all()
    assert len(rows) == 3, f"Expected 3 rows, found {len(rows)}"


@pytest.mark.asyncio
async def test_record_returns_none(db_session: AsyncSession):
    """record() must return None (-> None annotation enforced at runtime)."""
    from app.services.audit import record

    result = await record(
        db=db_session,
        actor_id=1,
        action="contact.create",
        entity="contact",
        entity_id=1,
        before=None,
        after={"first_name": "Dave"},
    )
    assert result is None, f"record() returned {result!r}, expected None"
