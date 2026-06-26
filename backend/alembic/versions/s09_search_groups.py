"""s09_search_groups

S09 — Advanced Search, Saved Searches & Smart Groups.

Creates three new tables that power the S09 search and grouping features:
  - saved_searches: user-owned saved query presets for any entity type.
  - groups: named static or smart (criteria-driven) groups of contacts.
  - group_members: join table binding contacts to static or smart groups.

FK creation order (avoiding forward-reference issues):
  saved_searches → users
  groups → users
  group_members → groups, contacts, users

All operations are idempotency-guarded via sa.inspect() consistent with the
s24/s08/s16 migration patterns.

JSON columns use _JSONB (JSON().with_variant(JSONB)) so the SQLite test path
works identically to the PostgreSQL production path.

Revision ID: s09a1b2c3d4e5
Revises: s08a1b2c3d4e5
Create Date: 2026-06-26 00:00:00.000000
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

logger = logging.getLogger("alembic.s09_search_groups")

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path).  Consistent with s24/s08 _JSONB convention.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "s09a1b2c3d4e5"
down_revision: Union[str, None] = "s08a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)

    # Dialect-branched server default for now() — mirrors s08/s24 pattern.
    if bind.dialect.name == "postgresql":
        now_default = sa.text("now()")
    else:
        now_default = sa.text("(datetime('now'))")

    # ------------------------------------------------------------------
    # Table 1: saved_searches
    # owner_id FK → users.id ON DELETE CASCADE (not null)
    # Unique: (owner_id, name)
    # Index: ix_saved_searches_owner_id
    # ------------------------------------------------------------------
    existing_tables = insp.get_table_names()

    if "saved_searches" not in existing_tables:
        op.create_table(
            "saved_searches",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column(
                "entity",
                sa.String(20),
                nullable=False,
                server_default=sa.text("'contact'"),
            ),
            sa.Column("criteria", _JSONB, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
            sa.ForeignKeyConstraint(
                ["owner_id"],
                ["users.id"],
                name="fk_saved_searches_owner_id_users",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "owner_id", "name",
                name="uq_saved_searches_owner_name",
            ),
        )
        op.create_index(
            "ix_saved_searches_owner_id",
            "saved_searches",
            ["owner_id"],
        )
        logger.info("S09: created saved_searches")
    else:
        logger.info("S09: saved_searches already exists — skipping")

    # ------------------------------------------------------------------
    # Table 2: groups
    # owner_id FK → users.id ON DELETE SET NULL (nullable)
    # group_type: 'smart' | 'static'  (no DB CHECK — app-layer enforced)
    # criteria: JSONB nullable (NULL for static groups)
    # Unique: (name, entity)
    # ------------------------------------------------------------------
    # Re-inspect so that the guard sees saved_searches if just created.
    insp2 = sa_inspect(bind)
    existing_tables2 = insp2.get_table_names()

    if "groups" not in existing_tables2:
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column(
                "entity",
                sa.String(20),
                nullable=False,
                server_default=sa.text("'contact'"),
            ),
            sa.Column("group_type", sa.String(20), nullable=False),
            sa.Column("criteria", _JSONB, nullable=True),
            sa.Column("owner_id", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
            sa.ForeignKeyConstraint(
                ["owner_id"],
                ["users.id"],
                name="fk_groups_owner_id_users",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "name", "entity",
                name="uq_groups_name_entity",
            ),
        )
        logger.info("S09: created groups")
    else:
        logger.info("S09: groups already exists — skipping")

    # ------------------------------------------------------------------
    # Table 3: group_members
    # group_id FK → groups.id ON DELETE CASCADE (not null)
    # contact_id FK → contacts.id ON DELETE CASCADE (not null)
    # added_by_id FK → users.id ON DELETE SET NULL (nullable)
    # Unique: (group_id, contact_id)
    # Indexes: ix_group_members_group_id, ix_group_members_contact_id
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    existing_tables3 = insp3.get_table_names()

    if "group_members" not in existing_tables3:
        op.create_table(
            "group_members",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("contact_id", sa.Integer(), nullable=False),
            sa.Column("added_by_id", sa.Integer(), nullable=True),
            sa.Column(
                "added_at",
                sa.DateTime(),
                nullable=False,
                server_default=now_default,
            ),
            sa.ForeignKeyConstraint(
                ["group_id"],
                ["groups.id"],
                name="fk_group_members_group_id_groups",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["contact_id"],
                ["contacts.id"],
                name="fk_group_members_contact_id_contacts",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["added_by_id"],
                ["users.id"],
                name="fk_group_members_added_by_id_users",
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "group_id", "contact_id",
                name="uq_group_members_group_contact",
            ),
        )
        op.create_index(
            "ix_group_members_group_id",
            "group_members",
            ["group_id"],
        )
        op.create_index(
            "ix_group_members_contact_id",
            "group_members",
            ["contact_id"],
        )
        logger.info("S09: created group_members")
    else:
        logger.info("S09: group_members already exists — skipping")


def downgrade() -> None:
    """Drop the three S09 tables in reverse FK dependency order.

    Drops: group_members → groups → saved_searches
    Each drop is guarded by an inspector table-existence check.
    """
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Drop group_members first (references both groups and contacts).
    # ------------------------------------------------------------------
    insp = sa_inspect(bind)
    existing = insp.get_table_names()

    if "group_members" in existing:
        gm_indexes = {idx["name"] for idx in insp.get_indexes("group_members")}
        if "ix_group_members_contact_id" in gm_indexes:
            op.drop_index("ix_group_members_contact_id", table_name="group_members")
        if "ix_group_members_group_id" in gm_indexes:
            op.drop_index("ix_group_members_group_id", table_name="group_members")
        op.drop_table("group_members")

    # ------------------------------------------------------------------
    # Drop groups next (referenced by group_members).
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    existing2 = insp2.get_table_names()
    if "groups" in existing2:
        op.drop_table("groups")

    # ------------------------------------------------------------------
    # Drop saved_searches last.
    # ------------------------------------------------------------------
    insp3 = sa_inspect(bind)
    existing3 = insp3.get_table_names()
    if "saved_searches" in existing3:
        ss_indexes = {idx["name"] for idx in insp3.get_indexes("saved_searches")}
        if "ix_saved_searches_owner_id" in ss_indexes:
            op.drop_index("ix_saved_searches_owner_id", table_name="saved_searches")
        op.drop_table("saved_searches")
