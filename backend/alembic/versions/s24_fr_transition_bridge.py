"""s24_fr_transition_bridge

S24 — Face Recognition Transition Bridge.

Remaps compreface_subjects.contact_id from legacy CiviCRM contact ids to new
contacts.id values (via contacts.external_id), and detections.event_id from
legacy CiviCRM event ids to new events.id values (via events.external_id).
Remaps detections.matched_name from member:<old_external_id> to
member:<new_contact_id>.

Drops the _legacy_civicrm_contact_id and _legacy_civicrm_event_id stash
columns after remapping is complete.

Creates biometric_consent table (idempotency-guarded).

Logs unmatched subjects to job_runs if the table and detail column exist,
else falls back to logger.warning.

IRREVERSIBLE for data: downgrade() re-adds empty stash columns and drops
biometric_consent; the remapped contact/event id values cannot be
automatically restored.

Revision ID: s24a1b2c3d4e5
Revises: eed28c4ef46a
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from alembic import op

logger = logging.getLogger("alembic.s24_fr_transition_bridge")

# Cross-dialect JSON: JSONB (with .astext support) on Postgres, plain JSON on
# SQLite (test path).  Use with_variant so the column type is portable.
# postgresql.JSONB(astext_type=sa.Text()) enables the .astext operator used by
# T07 pending_review_count queries.
_JSONB = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()), "postgresql"
)

revision: str = "s24a1b2c3d4e5"
down_revision: Union[str, None] = "eed28c4ef46a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 0: Abort guard — contacts.external_id must not be all-NULL
    # if there are subjects with stash values to remap.
    # ------------------------------------------------------------------
    cs_cols = [c["name"] for c in insp.get_columns("compreface_subjects")]
    has_stash_contact = "_legacy_civicrm_contact_id" in cs_cols

    if has_stash_contact:
        stash_count_row = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM compreface_subjects"
                " WHERE _legacy_civicrm_contact_id IS NOT NULL"
            )
        ).scalar()
        stash_count = stash_count_row or 0

        if stash_count > 0:
            ext_id_count_row = bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM contacts WHERE external_id IS NOT NULL"
                )
            ).scalar()
            ext_id_count = ext_id_count_row or 0

            if ext_id_count == 0:
                raise RuntimeError(
                    "S24 abort: contacts.external_id all NULL; run S06 import first"
                )

    # ------------------------------------------------------------------
    # Step 1: Remap compreface_subjects.contact_id via contacts.external_id
    # ------------------------------------------------------------------
    if has_stash_contact:
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(
                "UPDATE compreface_subjects AS cs"
                " SET contact_id = c.id"
                " FROM contacts c"
                " WHERE c.external_id = cs._legacy_civicrm_contact_id"
            ))
        else:
            # SQLite: correlated subquery
            bind.execute(sa.text(
                "UPDATE compreface_subjects"
                " SET contact_id = ("
                "   SELECT c.id FROM contacts c"
                "   WHERE c.external_id = compreface_subjects._legacy_civicrm_contact_id"
                " )"
                " WHERE _legacy_civicrm_contact_id IS NOT NULL"
            ))

        # Log unmatched subjects (contact_id still NULL after remap attempt,
        # but had a stash value set)
        unmatched_rows = bind.execute(sa.text(
            "SELECT id, subject_name, _legacy_civicrm_contact_id"
            " FROM compreface_subjects"
            " WHERE _legacy_civicrm_contact_id IS NOT NULL"
            "   AND contact_id IS NULL"
        )).fetchall()

        if unmatched_rows:
            has_job_runs = "job_runs" in existing_tables
            if has_job_runs:
                jr_cols = [c["name"] for c in insp.get_columns("job_runs")]
                has_detail_col = "detail" in jr_cols
            else:
                has_detail_col = False

            for row in unmatched_rows:
                msg = (
                    f"S24: unmatched compreface_subject id={row[0]}"
                    f" name={row[1]!r}"
                    f" legacy_contact_id={row[2]}"
                )
                if has_job_runs and has_detail_col:
                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    try:
                        bind.execute(sa.text(
                            "INSERT INTO job_runs (job_name, status, detail, started_at)"
                            " VALUES ('s24_fr_transition_bridge', 'warning', :detail, :ts)"
                        ), {"detail": msg, "ts": now_str})
                    except Exception:
                        logger.warning(msg)
                else:
                    logger.warning(msg)

    # ------------------------------------------------------------------
    # Step 2: Remap detections.event_id via events.external_id
    # ------------------------------------------------------------------
    det_cols = [c["name"] for c in insp.get_columns("detections")]
    has_stash_event = "_legacy_civicrm_event_id" in det_cols

    if has_stash_event:
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text(
                "UPDATE detections AS d"
                " SET event_id = e.id"
                " FROM events e"
                " WHERE e.external_id = d._legacy_civicrm_event_id"
            ))
        else:
            # SQLite: correlated subquery
            bind.execute(sa.text(
                "UPDATE detections"
                " SET event_id = ("
                "   SELECT e.id FROM events e"
                "   WHERE e.external_id = detections._legacy_civicrm_event_id"
                " )"
                " WHERE _legacy_civicrm_event_id IS NOT NULL"
            ))

    # ------------------------------------------------------------------
    # Step 3: Remap detections.matched_name from member:<old_ext_id>
    # to member:<new_contact_id>
    #
    # Only rows matching 'member:<integer>' are touched.
    # Strategy: build a join between detections and contacts where the
    # suffix of matched_name equals contacts.external_id (cast to text),
    # then update to member:<contacts.id>.
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text(
            "UPDATE detections d"
            " SET matched_name = 'member:' || c.id::text"
            " FROM contacts c"
            " WHERE d.matched_name LIKE 'member:%'"
            "   AND d.matched_name ~ '^member:[0-9]+$'"
            "   AND SUBSTRING(d.matched_name FROM 8)::integer = c.external_id"
        ))
    else:
        # SQLite: no regex, use a join-driven update via a subquery map.
        # CAST(SUBSTR(matched_name, 8) AS INTEGER) extracts the numeric suffix.
        # 'member:' is 7 characters so SUBSTR starts at position 8.
        bind.execute(sa.text(
            "UPDATE detections"
            " SET matched_name = 'member:' || ("
            "   SELECT c.id FROM contacts c"
            "   WHERE c.external_id = CAST(SUBSTR(detections.matched_name, 8) AS INTEGER)"
            " )"
            " WHERE matched_name LIKE 'member:%'"
            "   AND CAST(SUBSTR(matched_name, 8) AS INTEGER) IS NOT NULL"
            "   AND EXISTS ("
            "     SELECT 1 FROM contacts c2"
            "     WHERE c2.external_id = CAST(SUBSTR(detections.matched_name, 8) AS INTEGER)"
            "   )"
        ))

    # ------------------------------------------------------------------
    # Step 4: Drop stash columns (guarded by column presence)
    # Use batch_alter_table(recreate='always') for SQLite-safe column drop.
    # ------------------------------------------------------------------
    cs_cols_now = [c["name"] for c in insp.get_columns("compreface_subjects")]
    if "_legacy_civicrm_contact_id" in cs_cols_now:
        with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
            batch.drop_column("_legacy_civicrm_contact_id")

    det_cols_now = [c["name"] for c in insp.get_columns("detections")]
    if "_legacy_civicrm_event_id" in det_cols_now:
        with op.batch_alter_table("detections", recreate="always") as batch:
            batch.drop_column("_legacy_civicrm_event_id")

    # ------------------------------------------------------------------
    # Step 5: Create biometric_consent table (idempotency-guarded)
    # ------------------------------------------------------------------
    # Re-inspect after potential batch operations above (SQLite recreates the table)
    insp5 = sa_inspect(bind)
    existing_tables_now = insp5.get_table_names()

    if "biometric_consent" not in existing_tables_now:
        op.create_table(
            "biometric_consent",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "contact_id",
                sa.Integer(),
                sa.ForeignKey("contacts.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "consent_given",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("consented_at", sa.DateTime(), nullable=True),
            sa.Column("basis_note", sa.Text(), nullable=True),
            sa.Column(
                "recorded_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("(datetime('now'))") if bind.dialect.name != "postgresql" else sa.text("now()"),
            ),
        )
        op.create_index(
            "ix_biometric_consent_contact_id",
            "biometric_consent",
            ["contact_id"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade is IRREVERSIBLE for data.

    The contact/event id remapping cannot be automatically restored since the
    original _legacy_civicrm_* values are dropped.  This downgrade re-adds
    empty stash columns (NULL for all rows) and drops biometric_consent.
    """
    bind = op.get_bind()
    insp = sa_inspect(bind)
    existing_tables = insp.get_table_names()

    # ------------------------------------------------------------------
    # Step 5 reversal: Drop biometric_consent
    # ------------------------------------------------------------------
    if "biometric_consent" in existing_tables:
        existing_bc_indexes = {
            idx["name"] for idx in insp.get_indexes("biometric_consent")
        }
        if "ix_biometric_consent_contact_id" in existing_bc_indexes:
            op.drop_index(
                "ix_biometric_consent_contact_id",
                table_name="biometric_consent",
            )
        op.drop_table("biometric_consent")

    # ------------------------------------------------------------------
    # Step 4 reversal: Re-add empty stash columns (data NOT restored —
    # see docstring above).
    # ------------------------------------------------------------------
    insp2 = sa_inspect(bind)
    cs_cols = [c["name"] for c in insp2.get_columns("compreface_subjects")]
    if "_legacy_civicrm_contact_id" not in cs_cols:
        with op.batch_alter_table("compreface_subjects", recreate="always") as batch:
            batch.add_column(
                sa.Column("_legacy_civicrm_contact_id", sa.Integer(), nullable=True)
            )

    det_cols = [c["name"] for c in insp2.get_columns("detections")]
    if "_legacy_civicrm_event_id" not in det_cols:
        with op.batch_alter_table("detections", recreate="always") as batch:
            batch.add_column(
                sa.Column("_legacy_civicrm_event_id", sa.Integer(), nullable=True)
            )
