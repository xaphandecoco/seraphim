"""s23_member_status_snapshot_guard

S23 — Member Status Snapshot Guard.

Ensures the 7 snapshot columns on contacts (originally added by S01) are
present, then creates 5 single-column indexes to support the S23 nightly
status-recompute query paths (tier, is_active, is_regular, is_connected,
last_attended_at).

Column additions are idempotency-guarded: on a properly-initialised DB all
7 columns already exist (S01 owns them) so every op.add_column call is a
no-op.  The guard prevents OperationalError on re-run.

Revision ID: a3b4c5d6e7f8
Revises: s24a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "s24a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 7 snapshot columns owned by S01 — (column_name, sa_type, nullable)
_SNAPSHOT_COLUMNS: list[tuple[str, sa.types.TypeEngine, bool]] = [
    ("last_attended_at", sa.DateTime(), True),
    ("attendance_count", sa.Integer(), True),
    ("weeks_absent", sa.Integer(), True),
    ("tier", sa.String(20), True),   # String(20) matches model + existing column
    ("is_active", sa.Boolean(), True),
    ("is_regular", sa.Boolean(), True),
    ("is_connected", sa.Boolean(), True),
]

# 5 single-column indexes — (index_name, column_name)
_SNAPSHOT_INDEXES: list[tuple[str, str]] = [
    ("ix_contacts_tier", "tier"),
    ("ix_contacts_is_active", "is_active"),
    ("ix_contacts_is_regular", "is_regular"),
    ("ix_contacts_is_connected", "is_connected"),
    ("ix_contacts_last_attended_at", "last_attended_at"),
]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # ------------------------------------------------------------------
    # Step 1: Ensure the 7 snapshot columns exist on contacts.
    # S01 already added them on every real DB; this guard is purely a
    # safety net that prevents an OperationalError on re-run or on a
    # fresh DB built directly from create_all (which the test suite uses).
    # ------------------------------------------------------------------
    cols = [c["name"] for c in insp.get_columns("contacts")]

    for col_name, col_type, nullable in _SNAPSHOT_COLUMNS:
        if col_name not in cols:
            op.add_column(
                "contacts",
                sa.Column(col_name, col_type, nullable=nullable),
            )

    # ------------------------------------------------------------------
    # Step 2: Create the 5 single-column indexes (guarded by name).
    # Plain indexes — no postgresql_ops so SQLite and Postgres both apply.
    # ------------------------------------------------------------------
    # Re-inspect after potential column additions above.
    existing_indexes = {idx["name"] for idx in insp.get_indexes("contacts")}

    for index_name, column_name in _SNAPSHOT_INDEXES:
        if index_name not in existing_indexes:
            op.create_index(index_name, "contacts", [column_name])


def downgrade() -> None:
    """Drop ONLY the 5 S23 indexes (guarded by existence).

    The 7 snapshot columns are owned by S01 and are intentionally NOT
    dropped here.  Only the indexes introduced by this migration are removed.
    """
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_indexes = {idx["name"] for idx in insp.get_indexes("contacts")}

    for index_name, _ in _SNAPSHOT_INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name="contacts")
