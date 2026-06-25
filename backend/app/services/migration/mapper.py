"""Row-mapping functions for the CiviCRM migration pipeline.

Public API
----------
BatchFatalError(Exception)
MappingError                         -- dataclass for per-row errors
ContactMapped                        -- dataclass for a mapped contact row
EventMapped                          -- dataclass for a mapped event row
ParticipantRow                       -- dataclass for a mapped participant row

async map_contact_row(row, column_map, options) -> ContactMapped | MappingError
async map_event_row(row, column_map, options)   -> EventMapped   | MappingError
async map_participant_row(row, column_map, options) -> ParticipantRow | MappingError

Design rules
------------
- No DB calls.
- No SQLAlchemy imports.
- Row-level problems return MappingError (do NOT raise).
- BatchFatalError is raised only for structural / preflight issues (e.g. an
  unknown target field detected during the pre-flight column-map validation
  in runner.py — the detection logic lives here; the raise lives in runner).
- Raw payload stored as json.dumps(row) truncated to 2048 bytes.
- Participant source is always 'migration' (CN-07).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional, Union

from app.services.migration.normalize import coerce, normalize_option


# ---------------------------------------------------------------------------
# Sentinel exception — raised by runner.py preflight on structural failures
# ---------------------------------------------------------------------------

class BatchFatalError(Exception):
    """Raised when the import batch cannot proceed at all.

    Examples: an unknown target field in column_map, a required header
    missing from the sheet, or an unsupported file format.

    This exception is NOT raised inside map_*_row functions.  Those functions
    return a MappingError for per-row problems.  BatchFatalError is raised by
    the preflight stage in runner.py after it has called
    ``validate_column_map()`` below.
    """


# ---------------------------------------------------------------------------
# Per-row error container
# ---------------------------------------------------------------------------

@dataclass
class MappingError:
    """Describes a problem with a single spreadsheet row.

    Attributes
    ----------
    row_index:
        0-based index of the data row (excluding the header row).
    field:
        The target field name that caused the error, or ``"__row__"`` for
        row-level structural errors.
    message:
        Human-readable description of the problem.
    raw_payload:
        JSON-serialised snapshot of the original row dict, truncated to
        2048 bytes.
    """
    row_index: int
    field: str
    message: str
    raw_payload: str


# ---------------------------------------------------------------------------
# Mapped output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ContactMapped:
    """Normalised contact record ready for upsert.

    Attributes mirror the Contact ORM model columns.  All optional fields
    default to None.  ``links`` carries any non-contact-column values that
    should be stored separately (e.g. group memberships, relationships).
    """
    external_id: Optional[int]
    first_name: str
    last_name: str
    contact_type: str
    contact_subtype: Optional[str]
    nickname: Optional[str]
    suffix: Optional[str]
    gender: Optional[str]
    birth_date: Optional[date]
    phone: Optional[str]
    email: Optional[str]
    street_address: Optional[str]
    custom_data: dict[str, Any]
    links: dict[str, Any]          # non-contact fields (groups, relationships …)
    raw_payload: str               # json.dumps(original_row)[:2048]


@dataclass
class EventMapped:
    """Normalised event record ready for upsert."""
    external_id: Optional[int]
    title: str
    event_type: str
    session_time: Optional[str]
    occurrence_date: Optional[date]
    start_at: Optional[datetime]
    end_at: Optional[datetime]
    location: Optional[str]
    links: dict[str, Any]
    raw_payload: str


@dataclass
class ParticipantRow:
    """Normalised participant/attendance record.

    ``contact_ref`` is the raw identifier used to resolve the contact in
    the links phase (runner.py).  ``event_ref`` similarly.
    """
    contact_ref: str             # raw value — resolved to contact_id by runner
    event_ref: str               # raw value — resolved to event_id by runner
    status: str                  # attended | no_show | registered | cancelled
    role: Optional[str]
    source: str                  # always 'migration' per CN-07
    links: dict[str, Any]
    raw_payload: str


# ---------------------------------------------------------------------------
# Known target field sets (used by validate_column_map)
# ---------------------------------------------------------------------------

_CONTACT_FIELDS: frozenset[str] = frozenset(
    {
        "external_id", "first_name", "last_name", "contact_type",
        "contact_subtype", "nickname", "suffix", "gender", "birth_date",
        "phone", "email", "street_address", "custom_data",
    }
)

_EVENT_FIELDS: frozenset[str] = frozenset(
    {
        "external_id", "title", "event_type", "session_time",
        "occurrence_date", "start_at", "end_at", "location",
    }
)

_PARTICIPANT_FIELDS: frozenset[str] = frozenset(
    {
        "contact_ref", "event_ref", "status", "role",
    }
)

# Fields that are always routed to ``links`` regardless of target declaration
_LINK_PREFIXES: tuple[str, ...] = ("group:", "relationship:", "tag:", "link:")


# ---------------------------------------------------------------------------
# Column-map validation (preflight — raises BatchFatalError)
# ---------------------------------------------------------------------------

def validate_column_map(
    column_map: dict[str, dict[str, Any]],
    entity: str,
) -> None:
    """Validate that every target field in *column_map* is known.

    Parameters
    ----------
    column_map:
        Mapping of ``{source_header: {target, data_type, ...}}``.
    entity:
        One of ``'contact'``, ``'event'``, ``'participant'``.

    Raises
    ------
    BatchFatalError
        If any target field is not in the known field set for *entity*.
        The caller (runner.py preflight) is responsible for catching this
        and aborting the batch.
    """
    known: frozenset[str]
    if entity == "contact":
        known = _CONTACT_FIELDS
    elif entity == "event":
        known = _EVENT_FIELDS
    elif entity == "participant":
        known = _PARTICIPANT_FIELDS
    else:
        raise BatchFatalError(f"Unknown entity type: {entity!r}")

    unknown: list[str] = []
    for header, spec in column_map.items():
        target: str = spec.get("target", "")
        if not target:
            continue
        # Link-prefixed targets are always valid
        if any(target.startswith(p) for p in _LINK_PREFIXES):
            continue
        # custom_data.* targets are always valid
        if target.startswith("custom_data."):
            continue
        if target not in known:
            unknown.append(f"{header!r} -> {target!r}")

    if unknown:
        raise BatchFatalError(
            f"Unknown target field(s) in {entity} column_map: "
            + ", ".join(unknown)
        )


# ---------------------------------------------------------------------------
# Participant status normalisation
# ---------------------------------------------------------------------------

_STATUS_ALIASES: dict[str, str] = {
    # attended
    "attended": "attended",
    "present": "attended",
    "yes": "attended",
    "1": "attended",
    "true": "attended",
    # no_show
    "no_show": "no_show",
    "no-show": "no_show",
    "noshow": "no_show",
    "absent": "no_show",
    "no": "no_show",
    "0": "no_show",
    "false": "no_show",
    # registered
    "registered": "registered",
    "register": "registered",
    "rsvp": "registered",
    "invited": "registered",
    # cancelled
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "cancel": "cancelled",
    "withdrawn": "cancelled",
}


def _normalise_status(raw: Optional[str], default: str) -> str:
    """Return a canonical participant status string."""
    if not raw:
        return default
    return _STATUS_ALIASES.get(raw.strip().lower(), default)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _raw_payload(row: dict[str, Any]) -> str:
    """Return a JSON snapshot of *row*, truncated to 2048 bytes."""
    try:
        serialised = json.dumps(row, default=str)
    except (TypeError, ValueError):
        serialised = json.dumps({k: str(v) for k, v in row.items()})
    return serialised[:2048]


def _apply_column_map(
    row: dict[str, Any],
    column_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply *column_map* to *row*, returning (mapped_fields, links).

    Parameters
    ----------
    row:
        Raw spreadsheet row dict ``{header: value}``.
    column_map:
        ``{source_header: {target, data_type, aliases?, ...}}``

    Returns
    -------
    (mapped_fields, links)
        ``mapped_fields`` maps target field names to coerced values.
        ``links`` collects all link-prefixed or unknown-target fields.
    """
    mapped: dict[str, Any] = {}
    links: dict[str, Any] = {}

    for header, spec in column_map.items():
        raw_value = row.get(header)
        target: str = spec.get("target", "")
        data_type: str = spec.get("data_type", "string")
        aliases: dict[str, str] = spec.get("aliases", {})

        # Coerce
        try:
            coerced = coerce(raw_value, data_type)
        except (ValueError, TypeError) as exc:
            # Caller will wrap this in a MappingError
            raise _CoercionError(field=target or header, message=str(exc)) from exc

        # Normalise option if aliases provided
        if aliases and isinstance(coerced, str):
            coerced = normalize_option(coerced, aliases)
        elif aliases and isinstance(coerced, list):
            coerced = [normalize_option(item, aliases) for item in coerced]

        # Route to links dict for link-prefixed targets
        if any(target.startswith(p) for p in _LINK_PREFIXES):
            links[target] = coerced
            continue

        if not target:
            links[header] = coerced
            continue

        # custom_data.* -> nested into custom_data
        if target.startswith("custom_data."):
            sub_key = target[len("custom_data."):]
            mapped.setdefault("custom_data", {})[sub_key] = coerced
            continue

        mapped[target] = coerced

    return mapped, links


class _CoercionError(Exception):
    """Internal: carries field + message for MappingError construction."""
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(message)


# ---------------------------------------------------------------------------
# Public async mapping functions
# ---------------------------------------------------------------------------

async def map_contact_row(
    row: dict[str, Any],
    column_map: dict[str, dict[str, Any]],
    options: dict[str, Any],
    row_index: int = 0,
) -> Union[ContactMapped, MappingError]:
    """Map a single spreadsheet row to a :class:`ContactMapped`.

    Parameters
    ----------
    row:
        Raw row dict from :func:`reader.read_rows`.
    column_map:
        Column mapping specification for contact fields.
    options:
        Batch-level options dict (currently unused for contacts but accepted
        for API consistency).
    row_index:
        0-based data row index (for error reporting).

    Returns
    -------
    ContactMapped | MappingError
        Returns :class:`MappingError` for per-row validation failures.
        Does NOT raise.
    """
    payload = _raw_payload(row)

    try:
        mapped, links = _apply_column_map(row, column_map)
    except _CoercionError as exc:
        return MappingError(
            row_index=row_index,
            field=exc.field,
            message=exc.message,
            raw_payload=payload,
        )

    # Require first_name + last_name (or derive from a combined name field)
    first_name: Optional[str] = mapped.get("first_name") or None
    last_name: Optional[str] = mapped.get("last_name") or None

    if not first_name or not last_name:
        return MappingError(
            row_index=row_index,
            field="first_name" if not first_name else "last_name",
            message="first_name and last_name are required for contact rows.",
            raw_payload=payload,
        )

    # external_id: coerce to int if present
    external_id_raw = mapped.get("external_id")
    external_id: Optional[int] = None
    if external_id_raw is not None:
        try:
            external_id = int(external_id_raw)
        except (ValueError, TypeError):
            return MappingError(
                row_index=row_index,
                field="external_id",
                message=f"external_id must be an integer, got {external_id_raw!r}.",
                raw_payload=payload,
            )

    # birth_date: ensure it is a date object (coerce handles string parsing)
    birth_date_raw = mapped.get("birth_date")
    birth_date: Optional[date] = None
    if birth_date_raw is not None:
        if isinstance(birth_date_raw, datetime):
            birth_date = birth_date_raw.date()
        elif isinstance(birth_date_raw, date):
            birth_date = birth_date_raw
        else:
            return MappingError(
                row_index=row_index,
                field="birth_date",
                message=f"Cannot resolve birth_date from {birth_date_raw!r}.",
                raw_payload=payload,
            )

    return ContactMapped(
        external_id=external_id,
        first_name=str(first_name).strip(),
        last_name=str(last_name).strip(),
        contact_type=str(mapped.get("contact_type") or "individual").strip().lower(),
        contact_subtype=mapped.get("contact_subtype"),
        nickname=mapped.get("nickname"),
        suffix=mapped.get("suffix"),
        gender=mapped.get("gender"),
        birth_date=birth_date,
        phone=mapped.get("phone"),
        email=mapped.get("email"),
        street_address=mapped.get("street_address"),
        custom_data=mapped.get("custom_data", {}),
        links=links,
        raw_payload=payload,
    )


async def map_event_row(
    row: dict[str, Any],
    column_map: dict[str, dict[str, Any]],
    options: dict[str, Any],
    row_index: int = 0,
) -> Union[EventMapped, MappingError]:
    """Map a single spreadsheet row to an :class:`EventMapped`.

    Parameters
    ----------
    row:
        Raw row dict from :func:`reader.read_rows`.
    column_map:
        Column mapping specification for event fields.
    options:
        Batch-level options dict.
    row_index:
        0-based data row index (for error reporting).

    Returns
    -------
    EventMapped | MappingError
    """
    payload = _raw_payload(row)

    try:
        mapped, links = _apply_column_map(row, column_map)
    except _CoercionError as exc:
        return MappingError(
            row_index=row_index,
            field=exc.field,
            message=exc.message,
            raw_payload=payload,
        )

    title: Optional[str] = mapped.get("title") or None
    if not title:
        return MappingError(
            row_index=row_index,
            field="title",
            message="title is required for event rows.",
            raw_payload=payload,
        )

    external_id_raw = mapped.get("external_id")
    external_id: Optional[int] = None
    if external_id_raw is not None:
        try:
            external_id = int(external_id_raw)
        except (ValueError, TypeError):
            return MappingError(
                row_index=row_index,
                field="external_id",
                message=f"external_id must be an integer, got {external_id_raw!r}.",
                raw_payload=payload,
            )

    occurrence_date_raw = mapped.get("occurrence_date")
    occurrence_date: Optional[date] = None
    if occurrence_date_raw is not None:
        if isinstance(occurrence_date_raw, datetime):
            occurrence_date = occurrence_date_raw.date()
        elif isinstance(occurrence_date_raw, date):
            occurrence_date = occurrence_date_raw

    start_at_raw = mapped.get("start_at")
    start_at: Optional[datetime] = None
    if isinstance(start_at_raw, datetime):
        start_at = start_at_raw
    elif isinstance(start_at_raw, date):
        start_at = datetime(start_at_raw.year, start_at_raw.month, start_at_raw.day)

    end_at_raw = mapped.get("end_at")
    end_at: Optional[datetime] = None
    if isinstance(end_at_raw, datetime):
        end_at = end_at_raw
    elif isinstance(end_at_raw, date):
        end_at = datetime(end_at_raw.year, end_at_raw.month, end_at_raw.day)

    return EventMapped(
        external_id=external_id,
        title=str(title).strip(),
        event_type=str(mapped.get("event_type") or "Event").strip(),
        session_time=mapped.get("session_time"),
        occurrence_date=occurrence_date,
        start_at=start_at,
        end_at=end_at,
        location=mapped.get("location"),
        links=links,
        raw_payload=payload,
    )


async def map_participant_row(
    row: dict[str, Any],
    column_map: dict[str, dict[str, Any]],
    options: dict[str, Any],
    row_index: int = 0,
) -> Union[ParticipantRow, MappingError]:
    """Map a single spreadsheet row to a :class:`ParticipantRow`.

    Parameters
    ----------
    row:
        Raw row dict from :func:`reader.read_rows`.
    column_map:
        Column mapping specification for participant fields.
    options:
        Batch-level options dict.  Must contain
        ``participant_default_status`` (fallback when status cell is blank
        or unrecognised).
    row_index:
        0-based data row index (for error reporting).

    Returns
    -------
    ParticipantRow | MappingError
    """
    payload = _raw_payload(row)

    try:
        mapped, links = _apply_column_map(row, column_map)
    except _CoercionError as exc:
        return MappingError(
            row_index=row_index,
            field=exc.field,
            message=exc.message,
            raw_payload=payload,
        )

    contact_ref: Optional[str] = mapped.get("contact_ref") or None
    if not contact_ref:
        return MappingError(
            row_index=row_index,
            field="contact_ref",
            message="contact_ref is required for participant rows.",
            raw_payload=payload,
        )

    event_ref: Optional[str] = mapped.get("event_ref") or None
    if not event_ref:
        return MappingError(
            row_index=row_index,
            field="event_ref",
            message="event_ref is required for participant rows.",
            raw_payload=payload,
        )

    default_status: str = options.get("participant_default_status", "attended")
    raw_status: Optional[str] = mapped.get("status")
    status: str = _normalise_status(
        str(raw_status) if raw_status is not None else None,
        default=default_status,
    )

    return ParticipantRow(
        contact_ref=str(contact_ref).strip(),
        event_ref=str(event_ref).strip(),
        status=status,
        role=mapped.get("role"),
        source="migration",   # CN-07: always 'migration' for this pipeline
        links=links,
        raw_payload=payload,
    )
