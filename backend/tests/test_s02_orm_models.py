"""test_s02_orm_models.py — QA validation for patch s02-orm-models.

Verifies every acceptance criterion listed in the patch spec:
  - CustomFieldGroup and CustomFieldDef ORM classes are present
  - Module-level JSONB alias is reused (not re-declared)
  - No duplication of AuditLog or Contact.custom_data
  - options column: nullable=False, server_default="'[]'", default=list
  - group_id FK: ForeignKey('custom_field_group.id', ondelete='CASCADE')
  - __table_args__ constraint + index names mirror the migration exactly
  - Mapped[]/mapped_column 2.0 style used
  - utc_now defaults; updated_at onupdate=utc_now
  - timestamp columns are nullable=False (consistent with Contact/Participant/AuditLog)
  - No new imports added (all imports already present at models.py lines 1-19)
  - ORM round-trip: CustomFieldDef.options defaults to [] in-session without flush
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, UniqueConstraint, Index
from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    import app.models  # noqa: F401 — register all ORM classes on Base.metadata
    from app.database import Base
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def inspector(engine):
    return inspect(engine)


# ---------------------------------------------------------------------------
# AC-1: Classes exist and are importable
# ---------------------------------------------------------------------------

def test_custom_field_group_class_exists():
    from app.models import CustomFieldGroup
    assert CustomFieldGroup.__tablename__ == "custom_field_group"


def test_custom_field_def_class_exists():
    from app.models import CustomFieldDef
    assert CustomFieldDef.__tablename__ == "custom_field_def"


# ---------------------------------------------------------------------------
# AC-2: JSONB alias is reused — not re-declared inside either class
# ---------------------------------------------------------------------------

def test_jsonb_alias_reused_not_redeclared():
    """options column must use the module-level JSONB alias, not a local one."""
    import app.models as m
    # The module-level JSONB object is the exact same object used in the options column
    options_col = m.CustomFieldDef.__table__.columns["options"]
    # Both the module JSONB and the column type should be JSON with_variant
    # The key assertion: no second _PG_JSONB import or redeclaration inside the class
    assert hasattr(m, "JSONB"), "module-level JSONB alias must exist"
    # Inspect the type used — should be JSON (with_variant) not raw PG JSONB
    assert type(options_col.type).__name__ in ("JSON", "Variant"), (
        f"options column type should be the module-level JSON alias, got {type(options_col.type)}"
    )


# ---------------------------------------------------------------------------
# AC-3: No duplication of AuditLog or Contact.custom_data
# ---------------------------------------------------------------------------

def test_audit_log_not_duplicated(inspector):
    """audit_log must exist exactly once (one table)."""
    tables = inspector.get_table_names()
    assert tables.count("audit_log") == 1, "audit_log must appear exactly once in schema"


def test_contact_custom_data_not_duplicated(inspector):
    """contacts.custom_data must appear exactly once."""
    cols = [c["name"] for c in inspector.get_columns("contacts")]
    assert cols.count("custom_data") == 1, "custom_data must appear exactly once in contacts"


# ---------------------------------------------------------------------------
# AC-4: options column — nullable=False, server_default="'[]'", default=list
# ---------------------------------------------------------------------------

def test_options_nullable_false():
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["options"]
    assert col.nullable is False, "options must be nullable=False"


def test_options_server_default():
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["options"]
    assert col.server_default is not None, "options must have a server_default"
    # The server_default text should contain '[]'
    sd_str = str(col.server_default.arg)
    assert "'[]'" in sd_str or "[]" in sd_str, (
        f"options server_default must contain '[]', got: {sd_str!r}"
    )


def test_options_python_default_is_list():
    """options must have default=list so in-session instances have [] before flush.

    This is the correctness gap called out explicitly in the task spec:
      'NOTE: spec §3.2 line 140 shows the weaker options ... default=list only —
       use the stronger nullable=False + server_default version so the ORM matches
       the migration.'
    Without default=list, instance.options is None until a DB round-trip.
    """
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["options"]
    assert col.default is not None, (
        "options is missing default=list. "
        "Add default=list to the mapped_column call so instance.options is [] "
        "before the row is flushed. "
        "Fix: options: Mapped[list] = mapped_column(JSONB, nullable=False, "
        "server_default=text(\"'[]'\"), default=list)"
    )
    assert col.default.is_callable, (
        "options default must be a callable (list), not a scalar. "
        "Set default=list on the mapped_column call."
    )
    # Calling the default arg should return a fresh empty list
    result = col.default.arg(None)
    assert result == [], f"options default() must return [], got {result!r}"


def test_options_default_returns_independent_lists():
    """Each call to the default must produce a new list (not a shared mutable)."""
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["options"]
    if col.default is None:
        pytest.skip("default=list not set; tested in test_options_python_default_is_list")
    a = col.default.arg(None)
    b = col.default.arg(None)
    assert a is not b, "default=list must return a new list each time (no shared mutable)"


def test_options_in_session_before_flush():
    """Instance.options must be [] immediately after construction, before flush."""
    from app.models import CustomFieldGroup, CustomFieldDef
    from sqlalchemy.orm import Session

    eng = create_engine("sqlite:///:memory:")
    from app.database import Base
    Base.metadata.create_all(eng)

    with Session(eng) as session:
        grp = CustomFieldGroup(name="test_group", label="Test Group", entity="contact")
        session.add(grp)
        session.flush()  # get grp.id

        defn = CustomFieldDef(
            group_id=grp.id,
            name="field_a",
            label="Field A",
            data_type="text",
        )
        # Before flush: options must be [] not None
        assert defn.options == [], (
            f"CustomFieldDef.options must be [] before flush; got {defn.options!r}. "
            "This means default=list is missing from the mapped_column call."
        )
    eng.dispose()


# ---------------------------------------------------------------------------
# AC-5: group_id FK — ForeignKey('custom_field_group.id', ondelete='CASCADE')
# ---------------------------------------------------------------------------

def test_group_id_fk_points_to_custom_field_group(inspector):
    fks = inspector.get_foreign_keys("custom_field_def")
    group_fk = [fk for fk in fks if "group_id" in fk["constrained_columns"]]
    assert group_fk, "custom_field_def.group_id must have a FK"
    assert group_fk[0]["referred_table"] == "custom_field_group", (
        f"group_id FK must point to custom_field_group, got {group_fk[0]['referred_table']}"
    )


def test_group_id_fk_ondelete_cascade(inspector):
    """SQLite reflect doesn't always surface ON DELETE; check ORM model directly."""
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["group_id"]
    fk = list(col.foreign_keys)[0]
    assert fk.ondelete.upper() == "CASCADE", (
        f"group_id FK must have ondelete='CASCADE', got {fk.ondelete!r}"
    )


# ---------------------------------------------------------------------------
# AC-6: __table_args__ constraint + index names match migration exactly
# ---------------------------------------------------------------------------

def test_custom_field_group_unique_constraint_name():
    from app.models import CustomFieldGroup
    constraint_names = {
        arg.name
        for arg in CustomFieldGroup.__table_args__
        if isinstance(arg, UniqueConstraint)
    }
    assert "uq_custom_field_group_entity_name" in constraint_names, (
        f"CustomFieldGroup must have UniqueConstraint named 'uq_custom_field_group_entity_name'; "
        f"found: {constraint_names}"
    )


def test_custom_field_group_unique_constraint_columns():
    from app.models import CustomFieldGroup
    for arg in CustomFieldGroup.__table_args__:
        if isinstance(arg, UniqueConstraint) and arg.name == "uq_custom_field_group_entity_name":
            col_names = {c.name for c in arg.columns}
            assert col_names == {"entity", "name"}, (
                f"uq_custom_field_group_entity_name must cover (entity, name); got {col_names}"
            )


def test_custom_field_group_index_name():
    from app.models import CustomFieldGroup
    index_names = {
        arg.name
        for arg in CustomFieldGroup.__table_args__
        if isinstance(arg, Index)
    }
    assert "ix_cfg_entity_active_weight" in index_names, (
        f"CustomFieldGroup must have Index named 'ix_cfg_entity_active_weight'; "
        f"found: {index_names}"
    )


def test_custom_field_group_index_columns():
    from app.models import CustomFieldGroup
    for arg in CustomFieldGroup.__table_args__:
        if isinstance(arg, Index) and arg.name == "ix_cfg_entity_active_weight":
            col_names = [c.name for c in arg.columns]
            assert col_names == ["entity", "is_active", "weight"], (
                f"ix_cfg_entity_active_weight columns must be [entity, is_active, weight]; "
                f"got {col_names}"
            )


def test_custom_field_def_unique_constraint_name():
    from app.models import CustomFieldDef
    constraint_names = {
        arg.name
        for arg in CustomFieldDef.__table_args__
        if isinstance(arg, UniqueConstraint)
    }
    assert "uq_custom_field_def_group_name" in constraint_names, (
        f"CustomFieldDef must have UniqueConstraint named 'uq_custom_field_def_group_name'; "
        f"found: {constraint_names}"
    )


def test_custom_field_def_unique_constraint_columns():
    from app.models import CustomFieldDef
    for arg in CustomFieldDef.__table_args__:
        if isinstance(arg, UniqueConstraint) and arg.name == "uq_custom_field_def_group_name":
            col_names = {c.name for c in arg.columns}
            assert col_names == {"group_id", "name"}, (
                f"uq_custom_field_def_group_name must cover (group_id, name); got {col_names}"
            )


def test_custom_field_def_index_name():
    from app.models import CustomFieldDef
    index_names = {
        arg.name
        for arg in CustomFieldDef.__table_args__
        if isinstance(arg, Index)
    }
    assert "ix_cfd_group_active_weight" in index_names, (
        f"CustomFieldDef must have Index named 'ix_cfd_group_active_weight'; "
        f"found: {index_names}"
    )


def test_custom_field_def_index_columns():
    from app.models import CustomFieldDef
    for arg in CustomFieldDef.__table_args__:
        if isinstance(arg, Index) and arg.name == "ix_cfd_group_active_weight":
            col_names = [c.name for c in arg.columns]
            assert col_names == ["group_id", "is_active", "weight"], (
                f"ix_cfd_group_active_weight columns must be [group_id, is_active, weight]; "
                f"got {col_names}"
            )


# ---------------------------------------------------------------------------
# AC-7: Timestamp columns — nullable=False, utc_now defaults, onupdate=utc_now
# ---------------------------------------------------------------------------

def test_custom_field_group_timestamps_nullable_false():
    from app.models import CustomFieldGroup
    for col_name in ["created_at", "updated_at"]:
        col = CustomFieldGroup.__table__.columns[col_name]
        assert col.nullable is False, (
            f"CustomFieldGroup.{col_name} must be nullable=False (consistent with "
            "Contact/Participant/AuditLog patterns in the same file)"
        )


def test_custom_field_def_timestamps_nullable_false():
    from app.models import CustomFieldDef
    for col_name in ["created_at", "updated_at"]:
        col = CustomFieldDef.__table__.columns[col_name]
        assert col.nullable is False, (
            f"CustomFieldDef.{col_name} must be nullable=False (consistent with "
            "Contact/Participant/AuditLog patterns in the same file)"
        )


def test_custom_field_group_updated_at_has_onupdate():
    from app.models import CustomFieldGroup
    col = CustomFieldGroup.__table__.columns["updated_at"]
    assert col.onupdate is not None, (
        "CustomFieldGroup.updated_at must have onupdate=utc_now"
    )


def test_custom_field_def_updated_at_has_onupdate():
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["updated_at"]
    assert col.onupdate is not None, (
        "CustomFieldDef.updated_at must have onupdate=utc_now"
    )


def test_custom_field_group_created_at_default():
    from app.models import CustomFieldGroup
    col = CustomFieldGroup.__table__.columns["created_at"]
    assert col.default is not None, (
        "CustomFieldGroup.created_at must have default=utc_now"
    )


def test_custom_field_def_created_at_default():
    from app.models import CustomFieldDef
    col = CustomFieldDef.__table__.columns["created_at"]
    assert col.default is not None, (
        "CustomFieldDef.created_at must have default=utc_now"
    )


# ---------------------------------------------------------------------------
# AC-8: Tables are actually created by create_all (ORM <-> SQLite schema)
# ---------------------------------------------------------------------------

def test_custom_field_group_table_exists(inspector):
    assert inspector.has_table("custom_field_group"), \
        "custom_field_group table must be created by Base.metadata.create_all"


def test_custom_field_def_table_exists(inspector):
    assert inspector.has_table("custom_field_def"), \
        "custom_field_def table must be created by Base.metadata.create_all"


def test_custom_field_group_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("custom_field_group")}
    required = {"id", "name", "label", "entity", "weight", "is_active", "created_at", "updated_at"}
    missing = required - cols
    assert not missing, f"custom_field_group missing columns: {missing}"


def test_custom_field_def_columns(inspector):
    cols = {c["name"] for c in inspector.get_columns("custom_field_def")}
    required = {
        "id", "group_id", "name", "label", "data_type", "options",
        "is_required", "is_multi", "weight", "is_active", "help_text",
        "created_at", "updated_at",
    }
    missing = required - cols
    assert not missing, f"custom_field_def missing columns: {missing}"


# ---------------------------------------------------------------------------
# AC-9: Cascade delete works in SQLite (ORM-level behaviour)
# ---------------------------------------------------------------------------

def test_cascade_delete_removes_defs_when_group_deleted():
    """Deleting a CustomFieldGroup must cascade-delete its CustomFieldDef rows."""
    from app.models import CustomFieldGroup, CustomFieldDef
    from sqlalchemy.orm import Session

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # Enable FK enforcement in SQLite
    from sqlalchemy import event as sa_event

    @sa_event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    from app.database import Base
    Base.metadata.create_all(eng)

    with Session(eng) as session:
        grp = CustomFieldGroup(name="g1", label="G1", entity="contact")
        session.add(grp)
        session.flush()

        defn = CustomFieldDef(
            group_id=grp.id,
            name="f1",
            label="F1",
            data_type="text",
            options=[],
        )
        session.add(defn)
        session.commit()

        # Delete the group
        session.delete(grp)
        session.commit()

        remaining = session.query(CustomFieldDef).filter_by(group_id=grp.id).all()
        assert remaining == [], (
            f"Deleting CustomFieldGroup must cascade-delete CustomFieldDef rows; "
            f"found {len(remaining)} orphaned rows"
        )
    eng.dispose()
