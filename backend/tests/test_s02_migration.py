"""test_s02_migration.py — QA acceptance criteria for task s02-migration.

Validates all six acceptance criteria defined in the sprint contract:

  AC1: Migration file creates custom_field_group and custom_field_def with all
       documented columns, uq_custom_field_group_entity_name +
       uq_custom_field_def_group_name unique constraints, and
       ix_cfg_entity_active_weight + ix_cfd_group_active_weight indexes.

  AC2: Seed is idempotent — ON CONFLICT DO NOTHING, no duplicate rows.
       (seed-level tests are in test_custom_fields_seed.py; this file checks
       migration-level idempotency via inspector guards in the upgrade() body.)

  AC3: downgrade() drops custom_field_def THEN custom_field_group, and does
       NOT execute any drop on contacts.custom_data or audit_log.

  AC4: After seed, exactly six groups exist at entity='contact' with weights
       10/20/30/40/50/60.  (Covered in test_custom_fields_seed.py; regression-
       guard here checks seed names/weights via sync SQLite.)

  AC5: Migration does NOT add event_series/event_type/session_time/is_active
       to events (those are S04 columns).

  AC6: SQLite test suite builds schema via Base.metadata.create_all (models
       drive SQLite; migration is Postgres-path only) and test_migrations.py
       passes.  (Covered by the overall test run; this file adds explicit
       create_all smoke tests for the new tables.)
"""
from __future__ import annotations

import ast
import pathlib

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
S02_MIGRATION = (
    BACKEND_DIR / "alembic" / "versions" / "h1i2j3k4l5m6_add_custom_field_engine.py"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migration_text() -> str:
    return S02_MIGRATION.read_text(encoding="utf-8")


def _downgrade_body() -> str:
    """Return only the text of the downgrade() function."""
    text = _migration_text()
    idx = text.find("def downgrade(")
    assert idx != -1, "downgrade() function not found in migration"
    return text[idx:]


# ---------------------------------------------------------------------------
# AC1a — Migration file exists and has correct revision chain
# ---------------------------------------------------------------------------


def test_s02_migration_file_exists():
    assert S02_MIGRATION.exists(), (
        f"S02 migration file {S02_MIGRATION} does not exist"
    )


def test_s02_revision_id():
    text = _migration_text()
    assert 'revision: str = "h1i2j3k4l5m6"' in text or "revision = 'h1i2j3k4l5m6'" in text or 'revision: str = "h1i2j3k4l5m6"' in text, (
        "S02 migration must have revision = 'h1i2j3k4l5m6'"
    )


def test_s02_down_revision_is_s01():
    text = _migration_text()
    assert '"g7h8i9j0k1l2"' in text or "'g7h8i9j0k1l2'" in text, (
        "S02 migration down_revision must be 'g7h8i9j0k1l2' (S01)"
    )


# ---------------------------------------------------------------------------
# AC1b — custom_field_group: all columns present in migration
# ---------------------------------------------------------------------------


def test_migration_custom_field_group_has_all_columns():
    text = _migration_text()
    required_columns = ["id", "name", "label", "entity", "weight", "is_active", "created_at", "updated_at"]
    missing = [c for c in required_columns if f'"{c}"' not in text and f"'{c}'" not in text]
    assert not missing, (
        f"S02 migration custom_field_group is missing columns: {missing}"
    )


def test_migration_custom_field_group_unique_constraint():
    text = _migration_text()
    assert "uq_custom_field_group_entity_name" in text, (
        "S02 migration must create UniqueConstraint 'uq_custom_field_group_entity_name'"
    )


def test_migration_custom_field_group_index():
    text = _migration_text()
    assert "ix_cfg_entity_active_weight" in text, (
        "S02 migration must create index 'ix_cfg_entity_active_weight'"
    )


# ---------------------------------------------------------------------------
# AC1c — custom_field_def: all columns, constraints, indexes present
# ---------------------------------------------------------------------------


def test_migration_custom_field_def_has_all_columns():
    text = _migration_text()
    required_columns = [
        "id", "group_id", "name", "label", "data_type", "options",
        "is_required", "is_multi", "weight", "is_active", "help_text",
        "created_at", "updated_at",
    ]
    missing = [c for c in required_columns if f'"{c}"' not in text and f"'{c}'" not in text]
    assert not missing, (
        f"S02 migration custom_field_def is missing columns: {missing}"
    )


def test_migration_custom_field_def_unique_constraint():
    text = _migration_text()
    assert "uq_custom_field_def_group_name" in text, (
        "S02 migration must create UniqueConstraint 'uq_custom_field_def_group_name'"
    )


def test_migration_custom_field_def_index():
    text = _migration_text()
    assert "ix_cfd_group_active_weight" in text, (
        "S02 migration must create index 'ix_cfd_group_active_weight'"
    )


def test_migration_custom_field_def_fk_on_delete_cascade():
    text = _migration_text()
    assert "ondelete=" in text and "CASCADE" in text, (
        "S02 migration custom_field_def.group_id FK must have ondelete='CASCADE'"
    )


# ---------------------------------------------------------------------------
# AC2 — Idempotency: upgrade() contains inspector guards and seed call
# ---------------------------------------------------------------------------


def test_migration_upgrade_has_inspector_guard_for_contacts_custom_data():
    """upgrade() uses sa_inspect to guard contacts.custom_data add — idempotent."""
    text = _migration_text()
    # The upgrade has an if 'custom_data' not in cols: guard
    assert "custom_data" in text and "if" in text, (
        "upgrade() must guard the contacts.custom_data column add with an inspector check"
    )
    assert "sa_inspect" in text or "insp" in text, (
        "upgrade() must use SQLAlchemy inspector for idempotency guard"
    )


def test_migration_upgrade_has_inspector_guard_for_audit_log():
    """upgrade() uses sa_inspect to guard audit_log creation — idempotent."""
    text = _migration_text()
    assert "audit_log" in text and "existing_tables" in text, (
        "upgrade() must guard audit_log creation with inspector + existing_tables check"
    )


def test_migration_upgrade_calls_seed():
    """upgrade() must call seed_church_custom_fields as the last step."""
    text = _migration_text()
    assert "seed_church_custom_fields" in text, (
        "upgrade() must import and call seed_church_custom_fields"
    )
    # Seed must be AFTER table creation (appear after create_table calls)
    create_pos = text.rfind("op.create_table")
    seed_pos = text.find("seed_church_custom_fields(bind)")
    assert seed_pos > create_pos, (
        "seed_church_custom_fields(bind) must be called AFTER all create_table calls "
        f"(create_table at pos {create_pos}, seed at pos {seed_pos})"
    )


# ---------------------------------------------------------------------------
# AC3 — Asymmetric downgrade: only S02-owned tables are dropped
# ---------------------------------------------------------------------------


def test_downgrade_drops_custom_field_def():
    body = _downgrade_body()
    assert "custom_field_def" in body, (
        "downgrade() must drop custom_field_def"
    )
    assert 'drop_table("custom_field_def")' in body or "drop_table('custom_field_def')" in body, (
        "downgrade() must call op.drop_table('custom_field_def')"
    )


def test_downgrade_drops_custom_field_group():
    body = _downgrade_body()
    assert "custom_field_group" in body, (
        "downgrade() must drop custom_field_group"
    )
    assert 'drop_table("custom_field_group")' in body or "drop_table('custom_field_group')" in body, (
        "downgrade() must call op.drop_table('custom_field_group')"
    )


def test_downgrade_drops_def_before_group():
    """custom_field_def must be dropped before custom_field_group (FK order)."""
    body = _downgrade_body()
    def_pos = body.find("custom_field_def")
    cfg_pos = body.find("custom_field_group")
    assert def_pos < cfg_pos, (
        f"downgrade() must drop custom_field_def BEFORE custom_field_group "
        f"(def at pos {def_pos}, group at pos {cfg_pos}); FK constraint requires child-first drop"
    )


def test_downgrade_does_not_drop_contacts_custom_data():
    """contacts.custom_data is owned by S01; S02 downgrade must NOT touch it."""
    body = _downgrade_body()
    # The docstring may mention custom_data, but no op.drop_column call should appear
    assert "drop_column" not in body or "custom_data" not in body.replace(
        "# contacts.custom_data is NOT dropped", ""
    ).replace(
        "contacts.custom_data is NOT dropped", ""
    ).split("drop_column")[1] if "drop_column" in body else True, (
        "downgrade() must NOT call op.drop_column for contacts.custom_data "
        "(that column is owned by S01 / MASTER C7)"
    )
    # Stronger: no op.drop_column call at all in the downgrade
    assert "op.drop_column" not in body, (
        "downgrade() must NOT contain op.drop_column — S02 only owns two tables; "
        "contacts.custom_data is S01-owned and must not be dropped here"
    )


def test_downgrade_does_not_drop_audit_log():
    """audit_log is owned by S01; S02 downgrade must NOT call drop_table on it."""
    body = _downgrade_body()
    # Strip the docstring comment which mentions audit_log
    # Only check for executable drop_table("audit_log") calls
    # The docstring will contain 'audit_log' but no op.drop_table call
    assert 'drop_table("audit_log")' not in body and "drop_table('audit_log')" not in body, (
        "downgrade() must NOT drop audit_log — that table is owned by S01 / MASTER C3"
    )


def test_downgrade_indexes_dropped_before_tables():
    """Index drops must precede table drops in downgrade (Postgres FK enforcement)."""
    body = _downgrade_body()
    # ix_cfd_group_active_weight should be dropped before custom_field_def table
    cfd_idx_pos = body.find("ix_cfd_group_active_weight")
    cfd_tbl_pos = body.find('drop_table("custom_field_def")') or body.find("drop_table('custom_field_def')")
    if cfd_idx_pos != -1 and cfd_tbl_pos != -1:
        assert cfd_idx_pos < cfd_tbl_pos, (
            "ix_cfd_group_active_weight must be dropped before the custom_field_def table"
        )


# ---------------------------------------------------------------------------
# AC4 — Six groups with correct weights (migration-level SQLite smoke test)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_sqlite_engine():
    """Build in-memory SQLite DB with schema + seed applied synchronously."""
    import app.models  # noqa: F401
    from app.database import Base
    from app.seeds.custom_fields_seed import seed_church_custom_fields

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        seed_church_custom_fields(conn)

    yield engine
    engine.dispose()


def test_seed_produces_six_contact_groups(seeded_sqlite_engine):
    with seeded_sqlite_engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT COUNT(*) FROM custom_field_group WHERE entity = 'contact'")
        )
        count = result.scalar()
    assert count == 6, (
        f"After seed, expected exactly 6 groups at entity='contact'; got {count}"
    )


def test_seed_produces_weights_10_to_60(seeded_sqlite_engine):
    with seeded_sqlite_engine.connect() as conn:
        result = conn.execute(
            sa.text(
                "SELECT weight FROM custom_field_group WHERE entity = 'contact' ORDER BY weight"
            )
        )
        weights = [row[0] for row in result]
    assert weights == [10, 20, 30, 40, 50, 60], (
        f"Expected weights [10,20,30,40,50,60] for contact groups; got {weights}"
    )


def test_seed_idempotent_no_duplicates(seeded_sqlite_engine):
    """Running seed a second time must not produce duplicate groups."""
    from app.seeds.custom_fields_seed import seed_church_custom_fields

    with seeded_sqlite_engine.begin() as conn:
        seed_church_custom_fields(conn)  # second call

    with seeded_sqlite_engine.connect() as conn:
        result = conn.execute(
            sa.text("SELECT COUNT(*) FROM custom_field_group WHERE entity = 'contact'")
        )
        count = result.scalar()
    assert count == 6, (
        f"After two seed calls, expected 6 groups (idempotent); got {count}. "
        "Check that ON CONFLICT DO NOTHING is used on the INSERT."
    )


# ---------------------------------------------------------------------------
# AC5 — Migration does NOT add S04 columns to events
# ---------------------------------------------------------------------------


S04_COLUMNS = ["event_series", "event_type", "session_time", "occurrence_date", "is_active"]


@pytest.mark.parametrize("col_name", S04_COLUMNS)
def test_s02_migration_does_not_touch_s04_column(col_name):
    """S02 migration must not add any S04-owned event column."""
    text = _migration_text()
    # Find all op.add_column / create_table calls that include the column name
    # — we allow the column name to appear in comments but not in op calls
    import re
    # Strip comments
    lines = text.split("\n")
    non_comment_lines = "\n".join(
        line for line in lines if not line.strip().startswith("#")
    )
    # Check for op.add_column calls on 'events' with this column name
    add_col_pattern = rf'op\.add_column\(["\']events["\'].*?{col_name}'
    assert not re.search(add_col_pattern, non_comment_lines, re.DOTALL), (
        f"S02 migration must NOT add column '{col_name}' to events; that belongs to S04"
    )


def test_s02_migration_does_not_create_event_series_table():
    """S04 owns event_series; S02 must not create it."""
    text = _migration_text()
    assert 'create_table("event_series"' not in text and "create_table('event_series'" not in text, (
        "S02 migration must NOT create event_series table (S04 owns it)"
    )


def test_events_model_has_no_s04_columns():
    """Base model for Event must not have S04 columns added in this sprint.

    AC5 explicitly lists is_active as an S04 column for events (not to be
    confused with is_active on custom_field_group/custom_field_def which are
    S02-owned).
    """
    from app.models import Event

    col_names = {c.name for c in Event.__table__.columns}
    s04_found = {c for c in ["event_type", "session_time", "occurrence_date", "event_series_id", "is_active"] if c in col_names}
    assert not s04_found, (
        f"Event model must not have S04 columns yet; found: {s04_found}"
    )


# ---------------------------------------------------------------------------
# AC6 — SQLite test path: Base.metadata.create_all builds both new tables
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    import app.models  # noqa: F401
    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def fresh_inspector(fresh_sqlite_engine):
    return inspect(fresh_sqlite_engine)


def test_create_all_builds_custom_field_group_table(fresh_inspector):
    assert fresh_inspector.has_table("custom_field_group"), (
        "Base.metadata.create_all must create custom_field_group (SQLite test path)"
    )


def test_create_all_builds_custom_field_def_table(fresh_inspector):
    assert fresh_inspector.has_table("custom_field_def"), (
        "Base.metadata.create_all must create custom_field_def (SQLite test path)"
    )


def test_create_all_custom_field_group_has_required_columns(fresh_inspector):
    cols = {c["name"] for c in fresh_inspector.get_columns("custom_field_group")}
    required = {"id", "name", "label", "entity", "weight", "is_active", "created_at", "updated_at"}
    missing = required - cols
    assert not missing, (
        f"custom_field_group via create_all is missing columns: {missing}"
    )


def test_create_all_custom_field_def_has_required_columns(fresh_inspector):
    cols = {c["name"] for c in fresh_inspector.get_columns("custom_field_def")}
    required = {
        "id", "group_id", "name", "label", "data_type", "options",
        "is_required", "is_multi", "weight", "is_active", "help_text",
        "created_at", "updated_at",
    }
    missing = required - cols
    assert not missing, (
        f"custom_field_def via create_all is missing columns: {missing}"
    )


def test_create_all_unique_constraint_cfg_in_schema(fresh_inspector):
    """uq_custom_field_group_entity_name must appear in SQLite schema."""
    unique_constraints = fresh_inspector.get_unique_constraints("custom_field_group")
    names = {uc["name"] for uc in unique_constraints}
    assert "uq_custom_field_group_entity_name" in names, (
        f"custom_field_group must have unique constraint 'uq_custom_field_group_entity_name'; "
        f"found: {names}"
    )


def test_create_all_unique_constraint_cfd_in_schema(fresh_inspector):
    """uq_custom_field_def_group_name must appear in SQLite schema."""
    unique_constraints = fresh_inspector.get_unique_constraints("custom_field_def")
    names = {uc["name"] for uc in unique_constraints}
    assert "uq_custom_field_def_group_name" in names, (
        f"custom_field_def must have unique constraint 'uq_custom_field_def_group_name'; "
        f"found: {names}"
    )
