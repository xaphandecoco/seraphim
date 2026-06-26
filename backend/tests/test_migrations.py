"""Migration / schema invariant tests for Story S3 (D-S3).

D-S3 states: 'No schema migration needed: CompreFace keys live in the
admin_settings table (key/value rows), not columns. Confirm Alembic head stays
f3a4b5c6d7e8.'

These tests guard that contract:
  - The Alembic revision graph has exactly ONE head and it is f3a4b5c6d7e8.
  - The revision chain is linear and connected back to the initial migration
    (no orphan/broken down_revision).
  - admin_settings remains a key/value table (key PK + JSON value) and has NO
    compreface_* columns — i.e. nobody quietly added a column migration.
  - Base.metadata (what the test suite's create_all uses) builds an admin_settings
    table that can store arbitrary key/value rows, so new CompreFace key rows need
    no DDL.
  - task_actions still has the dual-approval unique index defined by the head
    migration (regression guard — head migration content unchanged).

ScriptDirectory is read straight from alembic.ini so we don't depend on a DB.
"""
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

EXPECTED_HEAD = "s11a1b2c3d4e5"  # updated by S11 (dedupe rule set; chains s11a1b2c3d4e5 -> s10a1b2c3d4e5 -> s09a1b2c3d4e5)
INITIAL_REV = "74e9ab60ea7e"


@pytest.fixture(scope="module")
def script_dir() -> ScriptDirectory:
    cfg = Config(str(ALEMBIC_INI))
    # alembic.ini uses a relative script_location; resolve it against the backend dir.
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg)


# ---------------------------------------------------------------------------
# Head / chain invariants
# ---------------------------------------------------------------------------


def test_single_head(script_dir):
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected exactly one Alembic head, got {heads}"


def test_head_is_expected_revision(script_dir):
    assert script_dir.get_current_head() == EXPECTED_HEAD, (
        f"Alembic head changed (S3 expects it to stay {EXPECTED_HEAD}). "
        "If a column migration was genuinely required, update this test AND base "
        "the migration on down_revision='f3a4b5c6d7e8'."
    )


def test_revision_chain_is_connected_back_to_initial(script_dir):
    """Walk down_revisions from head; we must reach the initial migration with no gaps."""
    seen = []
    rev = script_dir.get_revision(EXPECTED_HEAD)
    while rev is not None:
        seen.append(rev.revision)
        down = rev.down_revision
        if down is None:
            break
        # down_revision may be a tuple for merges; this project is linear → str.
        assert isinstance(down, str), f"unexpected merge point at {rev.revision}"
        rev = script_dir.get_revision(down)
    assert INITIAL_REV in seen, (
        f"revision chain from head did not reach the initial migration {INITIAL_REV}; "
        f"walked: {seen}"
    )


def test_head_down_revision_is_s10(script_dir):
    """Regression: the S11 head (dedupe rule set) builds on the S10 import-wizard migration."""
    head = script_dir.get_revision(EXPECTED_HEAD)
    assert head.down_revision == "s10a1b2c3d4e5"


# ---------------------------------------------------------------------------
# admin_settings stays a key/value table — no compreface_* columns
# ---------------------------------------------------------------------------


def test_admin_settings_is_key_value_table():
    from app.models import AdminSetting

    cols = set(AdminSetting.__table__.columns.keys())
    assert "key" in cols and "value" in cols
    # The keys live as ROWS keyed by `key`, not as dedicated columns.
    assert not any(c.startswith("compreface") for c in cols), (
        "admin_settings must NOT have compreface_* columns — keys are key/value rows "
        f"(found: {sorted(cols)})"
    )


def test_admin_settings_key_is_primary_key():
    from app.models import AdminSetting

    pk_cols = [c.name for c in AdminSetting.__table__.primary_key.columns]
    assert pk_cols == ["key"]


@pytest.mark.asyncio
async def test_metadata_create_all_supports_compreface_key_rows(db_session):
    """The schema the test suite builds (create_all) can store the three new key rows
    without any DDL change — proving D-S3's 'no migration needed' claim end to end."""
    from app.models import AdminSetting
    from sqlalchemy import select

    for k in (
        "compreface_api_key",
        "compreface_detect_api_key",
        "compreface_recognize_api_key",
    ):
        db_session.add(
            AdminSetting(key=k, value={"value": f"{k}-val"}, category="general", sensitive=True)
        )
    await db_session.commit()

    rows = (await db_session.execute(select(AdminSetting))).scalars().all()
    by_key = {r.key: r.value.get("value") for r in rows}
    assert by_key["compreface_detect_api_key"] == "compreface_detect_api_key-val"
    assert by_key["compreface_recognize_api_key"] == "compreface_recognize_api_key-val"


# ---------------------------------------------------------------------------
# Head migration content regression (dual-approval unique index)
# ---------------------------------------------------------------------------


def test_head_migration_defines_dual_approval_unique_index():
    """The f3a4b5c6d7e8 migration creates the partial unique index that closes the
    dual-approval race. Guard that its content wasn't accidentally gutted."""
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "f3a4b5c6d7e8_add_task_action_approval_unique.py"
    )
    text = mig.read_text(encoding="utf-8")
    assert "uq_task_action_approval" in text
    assert "task_actions" in text
    assert "unique=True" in text


def test_s01_head_migration_creates_contacts_and_events():
    """The g7h8i9j0k1l2 migration creates contacts/events/participants/audit_log.
    Guard that its content wasn't accidentally gutted."""
    mig = (
        BACKEND_DIR
        / "alembic"
        / "versions"
        / "g7h8i9j0k1l2_schema_inversion_civicrm_excision.py"
    )
    assert mig.exists(), "S01 migration file must exist"
    text = mig.read_text(encoding="utf-8")
    assert "contacts" in text
    assert "events" in text
    assert "participants" in text
    assert "audit_log" in text
    assert "down_revision" in text and "f3a4b5c6d7e8" in text
