"""Fuzzy header-to-target suggestion engine for the import wizard (S10).

Public API
----------
suggest_mappings(headers, entity, db) -> dict[str, dict]
targets_catalog(entity, db)           -> list[dict]

suggest_mappings returns::

    {
        "<header>": {
            "target":     "<field_name>" | "custom:<name>" | "ignore",
            "data_type":  "<data_type_string>",
            "confidence": 0.0 – 1.0,
        },
        ...
    }

targets_catalog returns a list of valid target dicts::

    [
        {"target": "first_name",    "label": "First Name",     "data_type": "text",   "is_custom": False},
        {"target": "custom:pepsol", "label": "PEPSOL Pathway", "data_type": "select", "is_custom": True},
        {"target": "ignore",        "label": "Ignore Column",  "data_type": "text",   "is_custom": False},
    ]

No SQL queries in suggest_mappings beyond get_active_schema (S02 service).
Circular-import rule (CN-25): must NOT import from any router module.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.custom_fields import get_active_schema


# ---------------------------------------------------------------------------
# Core field definitions per entity  (wizard target format)
# ---------------------------------------------------------------------------

_CONTACT_CORE: dict[str, dict[str, str]] = {
    "external_id":    {"label": "External ID",    "data_type": "integer"},
    "first_name":     {"label": "First Name",     "data_type": "text"},
    "last_name":      {"label": "Last Name",      "data_type": "text"},
    "nickname":       {"label": "Nickname",        "data_type": "text"},
    "suffix":         {"label": "Suffix",          "data_type": "text"},
    "gender":         {"label": "Gender",          "data_type": "text"},
    "birth_date":     {"label": "Birth Date",      "data_type": "date"},
    "phone":          {"label": "Phone",           "data_type": "text"},
    "email":          {"label": "Email",           "data_type": "email"},
    "street_address": {"label": "Street Address",  "data_type": "text"},
    "contact_type":   {"label": "Contact Type",    "data_type": "text"},
    "contact_subtype":{"label": "Contact Subtype", "data_type": "text"},
}

_EVENT_CORE: dict[str, dict[str, str]] = {
    "external_id":     {"label": "External ID",     "data_type": "integer"},
    "title":           {"label": "Title",            "data_type": "text"},
    "event_type":      {"label": "Event Type",       "data_type": "text"},
    "session_time":    {"label": "Session Time",     "data_type": "text"},
    "occurrence_date": {"label": "Occurrence Date",  "data_type": "date"},
    "start_at":        {"label": "Start At",         "data_type": "datetime"},
    "end_at":          {"label": "End At",           "data_type": "datetime"},
    "location":        {"label": "Location",         "data_type": "text"},
}

_PARTICIPANT_CORE: dict[str, dict[str, str]] = {
    "contact_ref": {"label": "Contact Reference", "data_type": "text"},
    "event_ref":   {"label": "Event Reference",   "data_type": "text"},
    "status":      {"label": "Attendance Status", "data_type": "text"},
    "role":        {"label": "Role",              "data_type": "text"},
}

_ENTITY_SINGULAR: dict[str, str] = {
    "contacts":     "contact",
    "events":       "event",
    "participants": "participant",
}


def _core_map(entity: str) -> dict[str, dict[str, str]]:
    singular = _ENTITY_SINGULAR.get(entity, entity)
    if singular == "contact":
        return _CONTACT_CORE
    if singular == "event":
        return _EVENT_CORE
    if singular == "participant":
        return _PARTICIPANT_CORE
    return {}


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------


def _nk(s: str) -> str:
    """Normalise a string for fuzzy comparison: lowercase + alphanumeric only."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def targets_catalog(
    entity: str,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Return all valid import targets for *entity*.

    Includes core fields, active custom fields (prefixed ``custom:``), and the
    special ``ignore`` sentinel.
    """
    catalog: list[dict[str, Any]] = []

    core = _core_map(entity)
    for field_name, meta in core.items():
        catalog.append({
            "target":    field_name,
            "label":     meta["label"],
            "data_type": meta["data_type"],
            "is_custom": False,
        })

    # Active custom field defs for the singular entity
    singular = _ENTITY_SINGULAR.get(entity, entity)
    schema_entries = await get_active_schema(db, singular)
    for entry in schema_entries:
        for cfd in entry["defs"]:
            catalog.append({
                "target":    f"custom:{cfd.name}",
                "label":     cfd.label or cfd.name,
                "data_type": cfd.data_type or "text",
                "is_custom": True,
            })

    catalog.append({
        "target":    "ignore",
        "label":     "Ignore Column",
        "data_type": "text",
        "is_custom": False,
    })

    return catalog


async def suggest_mappings(
    headers: list[str],
    entity: str,
    db: AsyncSession,
) -> dict[str, dict[str, Any]]:
    """Suggest the best import target for each *header*.

    Algorithm
    ---------
    1. Build a lookup table of normalised_key → (target, data_type) from core
       fields and active custom field defs.
    2. For each header, normalise it and compare against the table.
       - Exact normalised match → confidence 1.0.
       - SequenceMatcher ratio ≥ 0.75 → confidence = ratio.
       - Otherwise → target='ignore', confidence=0.0.

    Returns
    -------
    dict
        ``{header: {target, data_type, confidence}}``.
    """
    # Build normalised → (target, data_type) map
    target_lookup: dict[str, tuple[str, str]] = {}

    core = _core_map(entity)
    for field_name, meta in core.items():
        nk = _nk(field_name)
        if nk not in target_lookup:
            target_lookup[nk] = (field_name, meta["data_type"])
        # Also index by label key
        label_nk = _nk(meta["label"])
        if label_nk not in target_lookup:
            target_lookup[label_nk] = (field_name, meta["data_type"])

    singular = _ENTITY_SINGULAR.get(entity, entity)
    schema_entries = await get_active_schema(db, singular)
    for entry in schema_entries:
        for cfd in entry["defs"]:
            nk = _nk(cfd.name)
            wizard_target = f"custom:{cfd.name}"
            data_type = cfd.data_type or "text"
            if nk not in target_lookup:
                target_lookup[nk] = (wizard_target, data_type)
            if cfd.label:
                label_nk = _nk(cfd.label)
                if label_nk not in target_lookup:
                    target_lookup[label_nk] = (wizard_target, data_type)

    result: dict[str, dict[str, Any]] = {}
    all_nks = list(target_lookup.keys())

    for header in headers:
        header_nk = _nk(header)

        # 1. Exact match
        if header_nk in target_lookup:
            target, data_type = target_lookup[header_nk]
            result[header] = {"target": target, "data_type": data_type, "confidence": 1.0}
            continue

        # 2. Fuzzy match
        best_ratio = 0.0
        best_target: str = "ignore"
        best_dtype: str = "text"
        for candidate_nk in all_nks:
            if not candidate_nk:
                continue
            ratio = difflib.SequenceMatcher(None, header_nk, candidate_nk).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_target, best_dtype = target_lookup[candidate_nk]

        if best_ratio >= 0.75:
            result[header] = {
                "target":     best_target,
                "data_type":  best_dtype,
                "confidence": round(best_ratio, 3),
            }
        else:
            result[header] = {"target": "ignore", "data_type": "text", "confidence": 0.0}

    return result
