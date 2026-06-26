"""Postgres-gated migration round-trip test for S02 (Dynamic Custom-Field Engine).

Skips entirely unless DATABASE_URL starts with 'postgresql' so the standard
SQLite-backed CI run is unaffected.  On a real Postgres instance this test:

  1. Runs ``alembic upgrade head`` TWICE (idempotency check — both passes must
     succeed without error or duplicate rows).
  2. Asserts the two new tables exist with the correct unique constraints and
     weight indexes, and that exactly 6 ``entity='contact'`` groups were seeded
     at weights 10..60.
  3. Runs ``alembic downgrade -1`` and asserts:
     - ``custom_field_def`` and ``custom_field_group`` are gone.
     - ``contacts.custom_data`` column still exists (S01-owned, C7).
     - ``audit_log`` table still exists (S01-owned, C3).

How to run locally::

    DATABASE_URL=postgresql+asyncpg://user:pw@localhost/testdb \\
    REDIS_URL=memory:// ENVIRONMENT=test \\
    pytest backend/tests/test_s02_migration_roundtrip.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# ---------------------------------------------------------------------------
# Skip guard — entire module skips on SQLite
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="Postgres-gated test: set DATABASE_URL=postgresql+... to run",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

S02_REVISION = "h1i2j3k4l5m6"
S01_REVISION = "g7h8i9j0k1l2"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alembic_cfg(db_url: str) -> Config:
    """Build an Alembic Config wired to *db_url*, resolving the script_location
    to an absolute path (alembic.ini uses a relative value)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _sync_url(url: str) -> str:
    """Convert asyncpg URL to psycopg2 for synchronous reflection helpers."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_engine():
    """Synchronous engine for schema inspection and seed queries."""
    sync_url = _sync_url(DATABASE_URL)
    engine = sa.create_engine(sync_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def run_migrations(pg_engine):
    """Upgrade to head (twice for idempotency), yield for test body, then
    downgrade and assert the downgrade invariants inline."""
    cfg = _make_alembic_cfg(DATABASE_URL)
    sync_url = _sync_url(DATABASE_URL)

    # First upgrade — creates tables + seeds
    command.upgrade(cfg, "head")

    # Second upgrade — must be a no-op (idempotency)
    command.upgrade(cfg, "head")

    yield  # all test functions in this module run here

    # -----------------------------------------------------------------------
    # Teardown: downgrade -1 and assert post-downgrade invariants.
    # Failures here show up as ERROR on the module fixture in pytest output.
    # -----------------------------------------------------------------------
    command.downgrade(cfg, "-1")

    teardown_engine = sa.create_engine(sync_url)
    try:
        insp = sa.inspect(teardown_engine)

        assert not insp.has_table("custom_field_def"), (
            "custom_field_def must be dropped after downgrade -1"
        )
        assert not insp.has_table("custom_field_group"), (
            "custom_field_group must be dropped after downgrade -1"
        )
        assert insp.has_table("audit_log"), (
            "audit_log must NOT be dropped by S02 downgrade (S01-owned, MASTER C3)"
        )
        contact_col_names = [c["name"] for c in insp.get_columns("contacts")]
        assert "custom_data" in contact_col_names, (
            "contacts.custom_data must NOT be dropped by S02 downgrade "
            "(S01-owned, MASTER C7)"
        )
    finally:
        teardown_engine.dispose()


# ---------------------------------------------------------------------------
# Tests — run AFTER both upgrades and BEFORE downgrade teardown
# ---------------------------------------------------------------------------


def test_custom_field_group_table_exists(pg_engine):
    insp = sa.inspect(pg_engine)
    assert insp.has_table("custom_field_group"), (
        "custom_field_group table must exist after upgrade"
    )


def test_custom_field_def_table_exists(pg_engine):
    insp = sa.inspect(pg_engine)
    assert insp.has_table("custom_field_def"), (
        "custom_field_def table must exist after upgrade"
    )


def test_unique_constraint_on_custom_field_group(pg_engine):
    insp = sa.inspect(pg_engine)
    uq_names = {
        uc["name"]
        for uc in insp.get_unique_constraints("custom_field_group")
    }
    assert "uq_custom_field_group_entity_name" in uq_names, (
        f"expected uq_custom_field_group_entity_name; found: {uq_names}"
    )


def test_unique_constraint_on_custom_field_def(pg_engine):
    insp = sa.inspect(pg_engine)
    uq_names = {
        uc["name"]
        for uc in insp.get_unique_constraints("custom_field_def")
    }
    assert "uq_custom_field_def_group_name" in uq_names, (
        f"expected uq_custom_field_def_group_name; found: {uq_names}"
    )


def test_weight_index_on_custom_field_group(pg_engine):
    insp = sa.inspect(pg_engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("custom_field_group")}
    assert "ix_cfg_entity_active_weight" in idx_names, (
        f"expected ix_cfg_entity_active_weight; found: {idx_names}"
    )


def test_weight_index_on_custom_field_def(pg_engine):
    insp = sa.inspect(pg_engine)
    idx_names = {ix["name"] for ix in insp.get_indexes("custom_field_def")}
    assert "ix_cfd_group_active_weight" in idx_names, (
        f"expected ix_cfd_group_active_weight; found: {idx_names}"
    )


def test_six_contact_groups_seeded_at_correct_weights(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT name, weight FROM custom_field_group "
                "WHERE entity = 'contact' ORDER BY weight"
            )
        ).fetchall()

    assert len(rows) == 6, (
        f"expected exactly 6 contact groups after seed; got {len(rows)}: {rows}"
    )

    weights = [r[1] for r in rows]
    assert weights == [10, 20, 30, 40, 50, 60], (
        f"expected weights 10..60 in order; got {weights}"
    )


def test_idempotent_seed_no_extra_groups(pg_engine):
    """Running upgrade head twice must not double-insert seed rows."""
    with pg_engine.connect() as conn:
        count = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM custom_field_group WHERE entity = 'contact'"
            )
        ).scalar()

    assert count == 6, (
        f"idempotency violated: expected 6 groups, found {count} after two upgrades"
    )
