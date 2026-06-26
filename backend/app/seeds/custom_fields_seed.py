"""Idempotent seed data for the six church custom-field groups.

Called from the S02 Alembic migration upgrade() as the final step.
Takes a *synchronous* SQLAlchemy Connection from ``op.get_bind()``.

Uses raw SQL + ON CONFLICT DO NOTHING so re-running upgrade (or calling
this function directly) never duplicates rows.

Datetime values are Python-side to avoid SQL NOW() incompatibility between
Postgres and SQLite (the CI test path).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa


def _utc_now() -> datetime:
    """Naive UTC timestamp — matches the app's utc_now() convention."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_church_custom_fields(conn: Any) -> None:  # conn: sqlalchemy.engine.Connection
    """Insert the six canonical church field groups and their fields.

    Idempotent: ON CONFLICT DO NOTHING on both tables means re-running
    this function leaves existing rows untouched (preserves admin edits).

    Groups are keyed by (entity, name); fields by (group_id, name).
    """
    now = _utc_now()
    now_str = now.isoformat()

    # ------------------------------------------------------------------
    # Placeholder help text for stub option lists
    # ------------------------------------------------------------------
    STUB_HELP = "Pending owner confirmation — update option list in admin UI"

    # ------------------------------------------------------------------
    # Group definitions: (name, label, weight)
    # ------------------------------------------------------------------
    groups = [
        ("constituent_info", "Constituent Info", 10),
        ("new_friend_info", "New Friend Info", 20),
        ("community_info", "Community Info", 30),
        ("leader_info", "Leader Info", 40),
        ("followup_info", "Followup Info", 50),
        ("church_info", "Church Info", 60),
    ]

    # ------------------------------------------------------------------
    # Insert groups
    # ------------------------------------------------------------------
    for name, label, weight in groups:
        conn.execute(
            sa.text(
                """
                INSERT INTO custom_field_group
                    (name, label, entity, weight, is_active, created_at, updated_at)
                VALUES
                    (:name, :label, 'contact', :weight, :is_active, :now, :now)
                ON CONFLICT (entity, name) DO NOTHING
                """
            ),
            {"name": name, "label": label, "weight": weight, "is_active": True, "now": now_str},
        )

    # ------------------------------------------------------------------
    # Resolve group ids (needed to insert fields)
    # ------------------------------------------------------------------
    result = conn.execute(
        sa.text(
            "SELECT id, name FROM custom_field_group WHERE entity = 'contact'"
        )
    )
    group_id_by_name: dict[str, int] = {row.name: row.id for row in result}

    # ------------------------------------------------------------------
    # Field definitions per group.
    # Each entry: (name, label, data_type, options_list, is_required,
    #              is_multi, weight, help_text)
    # ------------------------------------------------------------------
    pepsol_options = json.dumps([
        {"value": "pre_encounter", "label": "Pre-Encounter"},
        {"value": "encounter", "label": "Encounter"},
        {"value": "post_encounter", "label": "Post-Encounter"},
    ])
    how_heard_options = json.dumps([
        {"value": "friend", "label": "Friend/Family"},
        {"value": "social_media", "label": "Social Media"},
        {"value": "other", "label": "Other"},
    ])
    community_options = json.dumps([
        {"value": "community_a", "label": "Community A"},
        {"value": "community_b", "label": "Community B"},
        {"value": "community_c", "label": "Community C"},
    ])
    ministry_options = json.dumps([
        {"value": "worship", "label": "Worship"},
        {"value": "media", "label": "Media"},
        {"value": "ushering", "label": "Ushering"},
    ])
    followup_status_options = json.dumps([
        {"value": "pending", "label": "Pending"},
        {"value": "in_progress", "label": "In Progress"},
        {"value": "completed", "label": "Completed"},
    ])
    membership_class_options = json.dumps([
        {"value": "class_101", "label": "101 — Membership"},
        {"value": "class_201", "label": "201 — Maturity"},
        {"value": "class_301", "label": "301 — Ministry"},
    ])
    empty_options = json.dumps([])

    fields_by_group: dict[str, list[tuple]] = {
        "constituent_info": [
            # (name, label, data_type, options_json, is_required, is_multi, weight, help_text)
            ("barangay", "Barangay", "text", empty_options, 0, 0, 10, None),
            ("pepsol", "PEPSOL Pathway", "select", pepsol_options, 0, 0, 20, STUB_HELP),
            ("facebook_name", "Facebook Name", "text", empty_options, 0, 0, 30, None),
        ],
        "new_friend_info": [
            ("invited_by", "Invited By", "contact_reference", empty_options, 0, 0, 10, None),
            ("consolidated_by", "Consolidated By", "contact_reference", empty_options, 0, 0, 20, None),
            ("first_visit_date", "First Visit Date", "date", empty_options, 0, 0, 30, None),
            ("how_heard", "How Did You Hear About Us?", "select", how_heard_options, 0, 0, 40, STUB_HELP),
        ],
        "community_info": [
            ("community", "Community", "multiselect", community_options, 0, 1, 10, STUB_HELP),
            ("community_leader", "Community Leader", "contact_reference", empty_options, 0, 0, 20, None),
            ("community_add_date", "Date Added to Community", "date", empty_options, 0, 0, 30, None),
        ],
        "leader_info": [
            ("ministry", "Ministry", "multiselect", ministry_options, 0, 1, 10, STUB_HELP),
            ("ministry_leader", "Ministry Leader", "contact_reference", empty_options, 0, 0, 20, None),
            ("network_leader", "Network Leader", "contact_reference", empty_options, 0, 0, 30, None),
            ("lifegroup_leader", "Lifegroup Leader", "contact_reference", empty_options, 0, 0, 40, None),
        ],
        "followup_info": [
            ("followup_listing", "Followup Notes", "textarea", empty_options, 0, 0, 10, None),
            ("followup_status", "Followup Status", "select", followup_status_options, 0, 0, 20, STUB_HELP),
        ],
        "church_info": [
            ("membership_class", "Membership Class", "select", membership_class_options, 0, 0, 10, STUB_HELP),
            ("water_baptized", "Water Baptized?", "checkbox", empty_options, 0, 0, 20, None),
            ("spirit_baptized", "Spirit Baptized?", "checkbox", empty_options, 0, 0, 30, None),
            ("date_joined", "Date Joined", "date", empty_options, 0, 0, 40, None),
        ],
    }

    # ------------------------------------------------------------------
    # Insert fields
    # ------------------------------------------------------------------
    for group_name, field_list in fields_by_group.items():
        gid = group_id_by_name.get(group_name)
        if gid is None:
            # Should never happen — group insert above must have succeeded
            raise RuntimeError(
                f"seed_church_custom_fields: group '{group_name}' not found after insert"
            )
        for (
            fname,
            flabel,
            ftype,
            foptions,
            fis_required,
            fis_multi,
            fweight,
            fhelp,
        ) in field_list:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO custom_field_def
                        (group_id, name, label, data_type, options,
                         is_required, is_multi, weight, is_active,
                         help_text, created_at, updated_at)
                    VALUES
                        (:group_id, :name, :label, :data_type, :options,
                         :is_required, :is_multi, :weight, :is_active,
                         :help_text, :now, :now)
                    ON CONFLICT (group_id, name) DO NOTHING
                    """
                ),
                {
                    "group_id": gid,
                    "name": fname,
                    "label": flabel,
                    "data_type": ftype,
                    "options": foptions,
                    "is_required": bool(fis_required),
                    "is_multi": bool(fis_multi),
                    "weight": fweight,
                    "is_active": True,
                    "help_text": fhelp,
                    "now": now_str,
                },
            )
