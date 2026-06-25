"""Streaming CSV + constant-memory XLSX export service.

Ownership: S05-F06.

Public API
----------
_contact_columns(db)        -> list[tuple[str, str]]
_participant_columns()      -> list[tuple[str, str]]
stream_contacts_csv(db, params)       -> AsyncIterator[bytes]
stream_participants_csv(db, params)   -> AsyncIterator[bytes]
build_contacts_xlsx(db, params)       -> tuple[str, int]   (path, row_count)
build_participants_xlsx(db, params)   -> tuple[str, int]

Memory contract
---------------
- NEVER calls .all() on large sets.
- Uses db.stream(stmt.execution_options(yield_per=1000)) + async for partition
  in result.partitions(1000) so at most 1 000 ORM rows live in memory at once.
- XLSX uses xlsxwriter Workbook(path, {'constant_memory': True, 'in_memory': False})
  which flushes each row to disk immediately.

Column specs (§4)
-----------------
Contacts — 13 core + 7 derived snapshot + one per active CustomFieldDef ordered
            by (group_id, weight).
Participants — 13 cols joining Participant -> Contact (LEFT) -> Event (INNER).

Filter params dict keys (§4.4)
-------------------------------
  contact_type, contact_subtype, tier, is_active, is_regular, is_connected,
  include_deleted, audience_mode, audience_ids, group_id, saved_search_id,
  event_id, date_from, date_to, source, status
"""
from __future__ import annotations

import csv
import io
import tempfile
from datetime import date, datetime
from typing import Any, AsyncIterator

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, CustomFieldDef, CustomFieldGroup, Event, Participant

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_UTF8_BOM = b"\xef\xbb\xbf"

# ---------------------------------------------------------------------------
# Formula-injection neutralisation
# ---------------------------------------------------------------------------

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _sanitize_cell(value: str) -> str:
    """Prepend a single quote to any cell that starts with a formula prefix.

    Prevents CSV/XLSX formula injection (=, +, -, @, tab, CR) when exported
    files are opened in spreadsheet applications.  The leading quote is
    preserved literally in CSV and suppressed by spreadsheet apps in XLSX.
    """
    if value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def _render_cell(value: Any, data_type: str | None = None, is_multi: bool = False) -> str:
    """Render a custom_data value to a CSV-safe string.

    Rules:
    - multiselect / is_multi=True  -> '; '.join(items)
    - contact_reference            -> resolved by caller; this renders int/list[int] ids
                                      as comma-separated strings when resolver is absent.
    - scalar                       -> str(value) or ''
    - None / missing               -> ''
    """
    if value is None:
        return ""
    if data_type == "multiselect" or is_multi:
        if isinstance(value, list):
            return _sanitize_cell("; ".join(str(v) for v in value))
        return _sanitize_cell(str(value))
    return _sanitize_cell(str(value))


def _fmt_dt(value: Any) -> str:
    """ISO-8601 string from datetime/date, empty string otherwise."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _fmt_bool(value: Any) -> str:
    """'true'/'false'/'' for bool-ish snapshot columns."""
    if value is None:
        return ""
    return "true" if value else "false"


# ---------------------------------------------------------------------------
# Active CustomFieldDef loader
# ---------------------------------------------------------------------------


async def _load_active_defs(db: AsyncSession) -> list[CustomFieldDef]:
    """Return active contact CustomFieldDef rows ordered by (group_id, weight)."""
    result = await db.execute(
        select(CustomFieldDef)
        .join(CustomFieldGroup, CustomFieldDef.group_id == CustomFieldGroup.id)
        .where(
            CustomFieldGroup.entity == "contact",
            CustomFieldGroup.is_active.is_(True),
            CustomFieldDef.is_active.is_(True),
        )
        .order_by(CustomFieldDef.group_id, CustomFieldDef.weight, CustomFieldDef.id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# _contact_columns
# ---------------------------------------------------------------------------

_CORE_CONTACT_COLS: list[tuple[str, str]] = [
    ("id", "id"),
    ("external_id", "external_id"),
    ("contact_type", "contact_type"),
    ("contact_subtype", "contact_subtype"),
    ("first_name", "first_name"),
    ("last_name", "last_name"),
    ("suffix", "suffix"),
    ("gender", "gender"),
    ("birth_date", "birth_date"),
    ("phone", "phone"),
    ("email", "email"),
    ("street_address", "street_address"),
    ("created_at", "created_at"),
]

_DERIVED_CONTACT_COLS: list[tuple[str, str]] = [
    ("last_attended_at", "last_attended_at"),
    ("attendance_count", "attendance_count"),
    ("weeks_absent", "weeks_absent"),
    ("tier", "tier"),
    ("is_active", "is_active"),
    ("is_regular", "is_regular"),
    ("is_connected", "is_connected"),
]


async def _contact_columns(
    db: AsyncSession,
) -> list[tuple[str, str]]:
    """Return ordered list of (header, accessor) for the contacts export.

    13 core cols + 7 derived snapshot cols + one per active CustomFieldDef
    ordered by (group_id, weight).
    """
    defs = await _load_active_defs(db)
    custom_cols: list[tuple[str, str]] = [(d.label, d.name) for d in defs]
    return _CORE_CONTACT_COLS + _DERIVED_CONTACT_COLS + custom_cols


# ---------------------------------------------------------------------------
# _participant_columns
# ---------------------------------------------------------------------------

_PARTICIPANT_COLS: list[tuple[str, str]] = [
    ("participant_id", "participant_id"),
    ("contact_id", "contact_id"),
    ("first_name", "first_name"),
    ("last_name", "last_name"),
    ("contact_type", "contact_type"),
    ("event_id", "event_id"),
    ("event_title", "event_title"),
    ("occurrence_date", "occurrence_date"),
    ("start_at", "start_at"),
    ("event_type", "event_type"),
    ("status", "status"),
    ("source", "source"),
    ("created_at", "created_at"),
]


def _participant_columns() -> list[tuple[str, str]]:
    """Return ordered list of (header, accessor) for the participants export."""
    return list(_PARTICIPANT_COLS)


# ---------------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------------


def _audience_filter(
    stmt: Any,
    params: dict[str, Any],
) -> Any:
    """Apply audience / contact-level filters to a statement that selects Contact rows."""
    include_deleted: bool = bool(params.get("include_deleted", False))
    if not include_deleted:
        stmt = stmt.where(Contact.is_deleted.is_(False))

    contact_type = params.get("contact_type")
    if contact_type:
        stmt = stmt.where(Contact.contact_type == contact_type)

    contact_subtype = params.get("contact_subtype")
    if contact_subtype:
        stmt = stmt.where(Contact.contact_subtype == contact_subtype)

    tier = params.get("tier")
    if tier:
        stmt = stmt.where(Contact.tier == tier)

    is_active = params.get("is_active")
    if is_active is not None:
        stmt = stmt.where(Contact.is_active.is_(bool(is_active)))

    is_regular = params.get("is_regular")
    if is_regular is not None:
        stmt = stmt.where(Contact.is_regular.is_(bool(is_regular)))

    is_connected = params.get("is_connected")
    if is_connected is not None:
        stmt = stmt.where(Contact.is_connected.is_(bool(is_connected)))

    # audience_mode='ids' — filter by explicit contact id list
    audience_mode = params.get("audience_mode")
    if audience_mode == "ids":
        ids = params.get("audience_ids") or []
        if ids:
            stmt = stmt.where(Contact.id.in_(ids))

    return stmt


def _build_contact_query(params: dict[str, Any]) -> Any:
    """Return a SELECT * FROM contacts statement with filters applied."""
    stmt = select(Contact).order_by(Contact.last_name, Contact.first_name, Contact.id)
    stmt = _audience_filter(stmt, params)
    return stmt


def _build_participant_query(params: dict[str, Any]) -> Any:
    """Return a SELECT joining Participant->Contact(LEFT)->Event(INNER)."""
    stmt = (
        select(Participant, Contact, Event)
        .join(Event, Participant.event_id == Event.id)
        .join(Contact, Participant.contact_id == Contact.id, isouter=True)
        .order_by(Event.start_at.desc(), Participant.id)
    )

    # Event filters
    event_id = params.get("event_id")
    if event_id is not None:
        stmt = stmt.where(Participant.event_id == event_id)

    date_from = params.get("date_from")
    if date_from:
        stmt = stmt.where(
            or_(
                Event.occurrence_date >= date_from,
                Event.start_at >= date_from,
            )
        )

    date_to = params.get("date_to")
    if date_to:
        stmt = stmt.where(
            or_(
                Event.occurrence_date <= date_to,
                Event.start_at <= date_to,
            )
        )

    source = params.get("source")
    if source:
        stmt = stmt.where(Participant.source == source)

    status = params.get("status")
    if status:
        stmt = stmt.where(Participant.status == status)

    # Contact-level soft-delete guard (LEFT join means Contact may be NULL)
    include_deleted: bool = bool(params.get("include_deleted", False))
    if not include_deleted:
        stmt = stmt.where(
            or_(Contact.is_deleted.is_(False), Contact.id.is_(None))
        )

    return stmt


# ---------------------------------------------------------------------------
# contact_reference resolver (per partition, no N+1)
# ---------------------------------------------------------------------------


async def _resolve_contact_refs(
    db: AsyncSession,
    ref_ids: list[int],
) -> dict[int, str]:
    """Batch-load first+last name for a set of contact IDs.

    Returns dict {id: "First Last"}.
    """
    if not ref_ids:
        return {}
    result = await db.execute(
        select(Contact.id, Contact.first_name, Contact.last_name).where(
            Contact.id.in_(ref_ids)
        )
    )
    return {
        row.id: f"{row.first_name or ''} {row.last_name or ''}".strip()
        for row in result.all()
    }


def _collect_ref_ids_from_partition(
    rows: list[Contact],
    ref_def_names: list[str],
) -> list[int]:
    """Collect all contact_reference int/list[int] IDs from a partition."""
    ids: list[int] = []
    for contact in rows:
        custom = contact.custom_data or {}
        for name in ref_def_names:
            val = custom.get(name)
            if isinstance(val, int):
                ids.append(val)
            elif isinstance(val, list):
                ids.extend(v for v in val if isinstance(v, int))
    return ids


# ---------------------------------------------------------------------------
# Row renderers
# ---------------------------------------------------------------------------


def _render_contact_row(
    contact: Contact,
    defs: list[CustomFieldDef],
    ref_names: dict[int, str],
) -> list[str]:
    """Render a Contact ORM row to a list of string cells.

    ref_names: dict {contact_id -> "First Last"} resolved per partition.
    """
    custom = contact.custom_data or {}

    cells: list[str] = [
        str(contact.id),
        str(contact.external_id) if contact.external_id is not None else "",
        contact.contact_type or "",
        contact.contact_subtype or "",
        _sanitize_cell(contact.first_name or ""),
        _sanitize_cell(contact.last_name or ""),
        _sanitize_cell(contact.suffix or ""),
        contact.gender or "",
        _fmt_dt(contact.birth_date),
        contact.phone or "",
        _sanitize_cell(contact.email or ""),
        _sanitize_cell(contact.street_address or ""),
        _fmt_dt(contact.created_at),
        # derived snapshots (NULL pre-S23 -> '')
        _fmt_dt(contact.last_attended_at),
        str(contact.attendance_count) if contact.attendance_count is not None else "",
        str(contact.weeks_absent) if contact.weeks_absent is not None else "",
        contact.tier or "",
        _fmt_bool(contact.is_active),
        _fmt_bool(contact.is_regular),
        _fmt_bool(contact.is_connected),
    ]

    # Custom field columns
    for d in defs:
        val = custom.get(d.name)
        if d.data_type == "contact_reference":
            if val is None:
                cells.append("")
            elif isinstance(val, int):
                cells.append(_sanitize_cell(ref_names.get(val, str(val))))
            elif isinstance(val, list):
                parts = [ref_names.get(v, str(v)) for v in val if isinstance(v, int)]
                cells.append(_sanitize_cell("; ".join(parts)))
            else:
                cells.append(_sanitize_cell(str(val)))
        else:
            cells.append(_render_cell(val, d.data_type, d.is_multi))

    return cells


def _render_participant_row(
    participant: Participant,
    contact: Contact | None,
    event: Event,
) -> list[str]:
    """Render a Participant+Contact+Event tuple to string cells."""
    return [
        str(participant.id),
        str(participant.contact_id),
        _sanitize_cell(contact.first_name if contact else ""),
        _sanitize_cell(contact.last_name if contact else ""),
        contact.contact_type if contact else "",
        str(event.id),
        _sanitize_cell(event.title or ""),
        _fmt_dt(getattr(event, "occurrence_date", None)),
        _fmt_dt(event.start_at),
        getattr(event, "event_type", "") or "",
        participant.status or "",
        participant.source or "",
        _fmt_dt(participant.created_at),
    ]


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _rows_to_csv_bytes(rows: list[list[str]]) -> bytes:
    """Convert a list of string-rows to UTF-8 CSV bytes (no BOM — caller prepends)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# stream_contacts_csv
# ---------------------------------------------------------------------------


async def stream_contacts_csv(
    db: AsyncSession,
    params: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Yield UTF-8 BOM then chunked CSV rows for all matching contacts.

    Memory-bounded: at most 1 000 Contact ORM rows in memory at once.
    contact_reference fields are batch-resolved per partition (no N+1).
    """
    if params is None:
        params = {}

    defs = await _load_active_defs(db)
    ref_def_names = [d.name for d in defs if d.data_type == "contact_reference"]

    columns = _CORE_CONTACT_COLS + _DERIVED_CONTACT_COLS + [(d.label, d.name) for d in defs]
    headers = [h for h, _ in columns]

    stmt = _build_contact_query(params)
    stmt = stmt.execution_options(yield_per=1000)

    # First chunk: BOM + header row
    yield _UTF8_BOM + _rows_to_csv_bytes([headers])

    result = await db.stream(stmt)
    async for partition in result.scalars().partitions(1000):
        rows: list[Contact] = list(partition)

        # Resolve contact references for the whole partition in one query
        ref_ids = _collect_ref_ids_from_partition(rows, ref_def_names)
        ref_names = await _resolve_contact_refs(db, ref_ids)

        csv_rows = [_render_contact_row(row, defs, ref_names) for row in rows]
        yield _rows_to_csv_bytes(csv_rows)


# ---------------------------------------------------------------------------
# stream_participants_csv
# ---------------------------------------------------------------------------


async def stream_participants_csv(
    db: AsyncSession,
    params: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Yield UTF-8 BOM then chunked CSV rows for all matching participants.

    Memory-bounded: at most 1 000 Participant rows in memory at once.
    """
    if params is None:
        params = {}

    headers = [h for h, _ in _PARTICIPANT_COLS]
    stmt = _build_participant_query(params)
    stmt = stmt.execution_options(yield_per=1000)

    # First chunk: BOM + header row
    yield _UTF8_BOM + _rows_to_csv_bytes([headers])

    result = await db.stream(stmt)
    async for partition in result.partitions(1000):
        csv_rows = []
        for row in partition:
            participant: Participant = row[0]
            contact: Contact | None = row[1]
            event: Event = row[2]
            csv_rows.append(_render_participant_row(participant, contact, event))
        yield _rows_to_csv_bytes(csv_rows)


# ---------------------------------------------------------------------------
# build_contacts_xlsx
# ---------------------------------------------------------------------------


async def build_contacts_xlsx(
    db: AsyncSession,
    params: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Write a constant-memory XLSX for the contacts export.

    Returns (temp_file_path, row_count).
    row_count excludes the header row.

    Uses xlsxwriter Workbook with constant_memory=True so each row is flushed
    to disk immediately — safe for 33k+ rows without growing heap.
    """
    import xlsxwriter  # noqa: PLC0415

    if params is None:
        params = {}

    defs = await _load_active_defs(db)
    ref_def_names = [d.name for d in defs if d.data_type == "contact_reference"]
    columns = _CORE_CONTACT_COLS + _DERIVED_CONTACT_COLS + [(d.label, d.name) for d in defs]
    headers = [h for h, _ in columns]

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    import os
    os.close(fd)

    workbook = xlsxwriter.Workbook(
        path, {"constant_memory": True, "in_memory": False}
    )
    worksheet = workbook.add_worksheet("Contacts")

    # Write header row
    for col_idx, header in enumerate(headers):
        worksheet.write(0, col_idx, header)

    stmt = _build_contact_query(params)
    stmt = stmt.execution_options(yield_per=1000)

    row_num = 1  # 0 is header
    result = await db.stream(stmt)
    async for partition in result.scalars().partitions(1000):
        rows: list[Contact] = list(partition)

        ref_ids = _collect_ref_ids_from_partition(rows, ref_def_names)
        ref_names = await _resolve_contact_refs(db, ref_ids)

        for contact in rows:
            cells = _render_contact_row(contact, defs, ref_names)
            for col_idx, cell in enumerate(cells):
                worksheet.write(row_num, col_idx, cell)
            row_num += 1

    workbook.close()
    return path, row_num - 1  # subtract header row


# ---------------------------------------------------------------------------
# build_participants_xlsx
# ---------------------------------------------------------------------------


async def build_participants_xlsx(
    db: AsyncSession,
    params: dict[str, Any] | None = None,
) -> tuple[str, int]:
    """Write a constant-memory XLSX for the participants export.

    Returns (temp_file_path, row_count).
    """
    import xlsxwriter  # noqa: PLC0415

    if params is None:
        params = {}

    headers = [h for h, _ in _PARTICIPANT_COLS]

    fd, path = tempfile.mkstemp(suffix=".xlsx")
    import os
    os.close(fd)

    workbook = xlsxwriter.Workbook(
        path, {"constant_memory": True, "in_memory": False}
    )
    worksheet = workbook.add_worksheet("Participants")

    for col_idx, header in enumerate(headers):
        worksheet.write(0, col_idx, header)

    stmt = _build_participant_query(params)
    stmt = stmt.execution_options(yield_per=1000)

    row_num = 1
    result = await db.stream(stmt)
    async for partition in result.partitions(1000):
        for row in partition:
            participant: Participant = row[0]
            contact: Contact | None = row[1]
            event: Event = row[2]
            cells = _render_participant_row(participant, contact, event)
            for col_idx, cell in enumerate(cells):
                worksheet.write(row_num, col_idx, cell)
            row_num += 1

    workbook.close()
    return path, row_num - 1
