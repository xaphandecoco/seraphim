"""QA: S04 migration content tests.

Validates every acceptance criterion for the S04 event series and event
columns migration (j4k5l6m7n8o9_s04_event_series_and_event_columns.py).

The Postgres-gated round-trip test is decorated with @pytest.mark.skipif
so it is collected as a skip on SQLite.  The static content tests run on
all platforms.
"""
from __future__ import annotations

import os
import pathlib

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_DIR
    / "alembic"
    / "versions"
    / "j4k5l6m7n8o9_s04_event_series_and_event_columns.py"
)


# ---------------------------------------------------------------------------
# Helper: detect Postgres
# ---------------------------------------------------------------------------


def is_postgres() -> bool:
    """Return True when DATABASE_URL points at a Postgres instance."""
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith("postgresql")


# ---------------------------------------------------------------------------
# Helper: migration text
# ---------------------------------------------------------------------------


def migration_text() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Static: file existence and revision chain
# ---------------------------------------------------------------------------


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"Migration file missing: {MIGRATION_PATH}"


def test_revision_id():
    text = migration_text()
    assert 'revision: str = "j4k5l6m7n8o9"' in text


def test_down_revision():
    text = migration_text()
    assert 'down_revision: Union[str, None] = "i3j4k5l6m7n8"' in text


# ---------------------------------------------------------------------------
# Static: upgrade ordering — event_series BEFORE events.recurring_series_id
# ---------------------------------------------------------------------------


def test_create_event_series_before_add_recurring_series_id():
    """op.create_table('event_series') must appear before
    op.add_column('events', ... 'recurring_series_id' ...)."""
    text = migration_text()
    # Operate on the upgrade section only to avoid matching docstring
    upgrade_section = text[text.index("def upgrade()"):]
    import re
    match = re.search(
        r"create_table\(\s*['\"]event_series['\"]", upgrade_section, re.DOTALL
    )
    assert match is not None, "create_table('event_series') not found in upgrade()"
    series_pos = match.start()

    # Find the add_column for recurring_series_id within upgrade section
    col_match = re.search(
        r"add_column\(.*?recurring_series_id", upgrade_section, re.DOTALL
    )
    assert col_match is not None, (
        "'recurring_series_id' add_column not found in upgrade()"
    )
    col_pos = col_match.start()

    assert series_pos < col_pos, (
        "create_table('event_series') must appear BEFORE the "
        "recurring_series_id column addition in upgrade()"
    )


# ---------------------------------------------------------------------------
# Static: FK ondelete='SET NULL' for recurring_series_id
# ---------------------------------------------------------------------------


def test_recurring_series_id_fk_has_set_null():
    text = migration_text()
    assert "ondelete='SET NULL'" in text or 'ondelete="SET NULL"' in text, (
        "FK for recurring_series_id must use ondelete='SET NULL'"
    )


# ---------------------------------------------------------------------------
# Static: is_active column with server_default=sa.true()
# ---------------------------------------------------------------------------


def test_is_active_column_with_server_default_true():
    text = migration_text()
    assert "server_default=sa.true()" in text, (
        "is_active column must use server_default=sa.true()"
    )


# ---------------------------------------------------------------------------
# Static: both indexes present in upgrade
# ---------------------------------------------------------------------------


def test_ix_events_event_type_created():
    text = migration_text()
    assert "ix_events_event_type" in text, (
        "Index ix_events_event_type must be created in the migration"
    )


def test_ix_events_occurrence_date_created():
    text = migration_text()
    assert "ix_events_occurrence_date" in text, (
        "Index ix_events_occurrence_date must be created in the migration"
    )


# ---------------------------------------------------------------------------
# Static: backfill UPDATE present
# ---------------------------------------------------------------------------


def test_backfill_occurrence_date_present():
    text = migration_text()
    assert (
        "UPDATE events SET occurrence_date = DATE(start_at) WHERE start_at IS NOT NULL"
        in text
    ), "Backfill UPDATE for occurrence_date must be present in the migration"


# ---------------------------------------------------------------------------
# Static: downgrade drops 6 events columns then drops event_series
# ---------------------------------------------------------------------------


def test_downgrade_drops_events_columns_before_event_series():
    """Downgrade must drop events columns before dropping event_series table."""
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]

    # At least one drop_column on events must appear
    drop_col_pos = downgrade_section.find("drop_column")
    assert drop_col_pos >= 0, "downgrade must drop events columns"

    # drop_table('event_series') must appear after drop_column calls
    import re as _re
    match = _re.search(
        r"drop_table\(\s*['\"]event_series['\"]", downgrade_section, _re.DOTALL
    )
    assert match is not None, "downgrade must drop_table('event_series')"
    drop_series_pos = match.start()

    assert drop_col_pos < drop_series_pos, (
        "downgrade must drop events columns BEFORE drop_table('event_series')"
    )


def test_downgrade_drops_all_six_events_columns():
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    expected_cols = {
        "is_active", "location", "recurring_series_id",
        "occurrence_date", "session_time", "event_type",
    }
    missing = [col for col in expected_cols if col not in downgrade_section]
    assert not missing, (
        f"downgrade must drop these events columns: {missing}"
    )


def test_downgrade_drops_both_indexes():
    text = migration_text()
    downgrade_section = text[text.index("def downgrade()"):]
    assert "ix_events_event_type" in downgrade_section, (
        "downgrade must drop ix_events_event_type"
    )
    assert "ix_events_occurrence_date" in downgrade_section, (
        "downgrade must drop ix_events_occurrence_date"
    )


# ---------------------------------------------------------------------------
# Static: inspector guards for idempotency
# ---------------------------------------------------------------------------


def test_inspector_guard_event_series_table():
    text = migration_text()
    assert "insp.get_table_names()" in text, (
        "Migration must use insp.get_table_names() to guard event_series creation"
    )
    assert "event_series" in text


def test_inspector_guard_events_columns():
    text = migration_text()
    assert "insp.get_columns(" in text, (
        "Migration must use insp.get_columns() to guard events column additions"
    )
    assert "event_type" in text


# ---------------------------------------------------------------------------
# Postgres-gated round-trip test
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not is_postgres(), reason="migration CI only")
def test_postgres_round_trip():
    """Run upgrade + downgrade and assert schema changes on Postgres.

    This test only executes when DATABASE_URL is a Postgres URL.
    On SQLite it is collected as a skip.
    """
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    db_url = os.environ.get("DATABASE_URL", "")
    alembic_ini = BACKEND_DIR / "alembic.ini"

    def _sync_url(url: str) -> str:
        return url.replace("postgresql+asyncpg://", "postgresql://", 1)

    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    sync_url = _sync_url(db_url)

    # Upgrade to head
    command.upgrade(cfg, "head")

    # Verify the schema after upgrade
    engine = sa.create_engine(sync_url)
    try:
        insp = sa.inspect(engine)

        assert insp.has_table("event_series"), (
            "event_series table must exist after upgrade"
        )

        event_series_cols = {c["name"] for c in insp.get_columns("event_series")}
        assert "cadence" in event_series_cols, "event_series.cadence must exist"
        assert "is_active" in event_series_cols, "event_series.is_active must exist"

        events_cols = {c["name"] for c in insp.get_columns("events")}
        for col in ("event_type", "session_time", "occurrence_date",
                    "recurring_series_id", "location", "is_active"):
            assert col in events_cols, f"events.{col} must exist after upgrade"

        events_idx = {idx["name"] for idx in insp.get_indexes("events")}
        assert "ix_events_event_type" in events_idx
        assert "ix_events_occurrence_date" in events_idx

        fks = insp.get_foreign_keys("events")
        series_fks = [
            fk for fk in fks if "recurring_series_id" in fk["constrained_columns"]
        ]
        assert series_fks, "events.recurring_series_id must have a FK"
        assert series_fks[0]["referred_table"] == "event_series"
        assert series_fks[0]["options"].get("ondelete", "").upper() == "SET NULL"

    finally:
        engine.dispose()

    # Downgrade -1 and verify rollback
    command.downgrade(cfg, "-1")

    engine2 = sa.create_engine(sync_url)
    try:
        insp2 = sa.inspect(engine2)

        assert not insp2.has_table("event_series"), (
            "event_series must be dropped after downgrade"
        )

        events_cols_after = {c["name"] for c in insp2.get_columns("events")}
        for col in ("event_type", "session_time", "occurrence_date",
                    "recurring_series_id", "location", "is_active"):
            assert col not in events_cols_after, (
                f"events.{col} must be gone after downgrade"
            )

    finally:
        engine2.dispose()
