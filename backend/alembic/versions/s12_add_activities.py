"""s12_add_activities

S12 — Activities: create activities table, and idempotent guards for
audit_log (S01-owned, defensive no-op once S01 lands) and outbox
(S17-owned, early producer created here if absent).

Revision ID: s12a1b2c3d4e5
Revises: s11a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "s12a1b2c3d4e5"
down_revision: Union[str, None] = "s11a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path). Consistent with s10/s11 _JSONB convention.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)


def upgrade() -> None:
    # 1. Create activities table (always — this migration owns it).
    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("activity_type", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("activity_date", sa.DateTime(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="scheduled"
        ),
        sa.Column(
            "priority", sa.String(10), nullable=False, server_default="normal"
        ),
        sa.Column(
            "assignee_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_contact_id",
            sa.Integer(),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_activities_assignee_status",
        "activities",
        ["assignee_user_id", "status"],
    )
    op.create_index(
        "ix_activities_target_contact_id", "activities", ["target_contact_id"]
    )
    op.create_index("ix_activities_due_date", "activities", ["due_date"])
    op.create_index("ix_activities_status", "activities", ["status"])

    bind = op.get_bind()
    insp = sa.inspect(bind)

    # 2. audit_log — DEFENSIVE-ONLY guard (per CN-03: table + AuditLog model
    #    owned by S01; this branch is a no-op once S01 has been applied).
    if not insp.has_table("audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "actor_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("entity", sa.String(50), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("before", _JSONB, nullable=True),
            sa.Column("after", _JSONB, nullable=True),
            sa.Column("at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_audit_log_entity", "audit_log", ["entity", "entity_id"]
        )

    # 3. outbox — idempotent guard (owned by S17; S12 is an early producer).
    if not insp.has_table("outbox"):
        op.create_table(
            "outbox",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_type", sa.String(100), nullable=False),
            sa.Column("payload", _JSONB, nullable=True),
            sa.Column(
                "status", sa.String(20), nullable=False, server_default="pending"
            ),
            sa.Column(
                "attempts", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("run_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_outbox_status_run_at", "outbox", ["status", "run_at"]
        )


def downgrade() -> None:
    # NOTE: audit_log and outbox are shared infrastructure (S01/S17).
    # They are NOT dropped here to avoid breaking sprints that were deployed
    # after S12. Drop them manually only if S12 was their sole creator and all
    # dependents are rolled back.
    op.drop_index("ix_activities_status", table_name="activities")
    op.drop_index("ix_activities_due_date", table_name="activities")
    op.drop_index(
        "ix_activities_target_contact_id", table_name="activities"
    )
    op.drop_index("ix_activities_assignee_status", table_name="activities")
    op.drop_table("activities")
