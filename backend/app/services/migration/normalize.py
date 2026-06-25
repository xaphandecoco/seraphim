"""Pure sync normalization helpers for the CiviCRM migration pipeline.

No DB calls, no SQLAlchemy imports — safe to import from any context.

Public API
----------
normalize_option(value, aliases) -> str
split_multi(value, delimiter=',') -> list[str]
coerce(value, data_type) -> object
name_key(first, last) -> str

Supported data_type values (§4.2):
    string, int, float, bool, checkbox, date, datetime,
    multiselect, email
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Truthy values for bool / checkbox fields
# ---------------------------------------------------------------------------
_BOOL_TRUTHY: frozenset[str] = frozenset(
    {"1", "true", "yes", "y", "x", "checked"}
)

# ---------------------------------------------------------------------------
# Date format patterns tried in order for coerce(..., 'date')
# ---------------------------------------------------------------------------
_DATE_FORMATS: list[str] = [
    "%m/%d/%Y",          # M/D/YYYY  (CiviCRM export default)
    "%Y-%m-%d",          # ISO date
    "%Y-%m-%d %H:%M:%S", # ISO datetime stored in a date column
]


def normalize_option(value: str, aliases: dict[str, str]) -> str:
    """Return the canonical form of *value* by consulting *aliases*.

    Lookup is case-insensitive and strips surrounding whitespace.
    If no alias matches, the stripped original is returned unchanged.

    Parameters
    ----------
    value:
        Raw string value from the spreadsheet cell.
    aliases:
        Mapping of lower-cased alias strings to canonical values.
        Example: {"attended": "attended", "yes": "attended", "1": "attended"}
    """
    stripped = value.strip()
    return aliases.get(stripped.lower(), stripped)


def split_multi(value: str, delimiter: str = ",") -> list[str]:
    """Split *value* on *delimiter* and return a list of non-empty stripped tokens.

    Parameters
    ----------
    value:
        Raw multi-value string, e.g. ``"Music, Arts, Sports"``.
    delimiter:
        Token separator.  Defaults to comma.

    Returns
    -------
    list[str]
        Ordered list of stripped, non-empty tokens.  Returns ``[]`` for a
        blank or whitespace-only input.
    """
    if not value or not value.strip():
        return []
    return [token.strip() for token in value.split(delimiter) if token.strip()]


def coerce(value: Any, data_type: str) -> Any:
    """Convert *value* to the Python type indicated by *data_type*.

    Supported data_type strings
    ---------------------------
    ``string``      Strip and return as str (or None if empty).
    ``int``         Cast to int.
    ``float``       Cast to float.
    ``bool``        Truthy set: '1','true','yes','y','x','checked' (case-insensitive).
    ``checkbox``    Alias for bool.
    ``date``        Parse M/D/YYYY, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, or ISO 8601.
                    Returns :class:`datetime.date`.  Raises :exc:`ValueError` on
                    unknown format.
    ``datetime``    Parse ISO 8601 or YYYY-MM-DD HH:MM:SS.
                    Returns :class:`datetime.datetime`.
    ``multiselect`` Split on comma; return list[str].
    ``email``       Strip and lower-case.

    Parameters
    ----------
    value:
        Raw cell value.  May be ``None`` or already the correct Python type
        (e.g. openpyxl can return a Python ``datetime`` for date cells).
    data_type:
        One of the supported strings above.

    Returns
    -------
    Converted value, or ``None`` when *value* is ``None`` / empty string
    (except for bool/checkbox which always return a bool).

    Raises
    ------
    ValueError
        For ``date``/``datetime`` when the string cannot be parsed.
    """
    # --- already None ---
    if value is None:
        if data_type in ("bool", "checkbox"):
            return False
        if data_type == "multiselect":
            return []
        return None

    # --- openpyxl may yield native Python objects from typed cells ---
    if data_type == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        s = str(value).strip()
        if not s:
            return None
        return _parse_date(s)

    if data_type == "datetime":
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        s = str(value).strip()
        if not s:
            return None
        return _parse_datetime(s)

    if data_type in ("bool", "checkbox"):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in _BOOL_TRUTHY

    # --- string-based conversions ---
    raw = str(value).strip()
    if not raw:
        if data_type == "multiselect":
            return []
        return None

    if data_type == "string":
        return raw

    if data_type == "int":
        return int(float(raw))  # handles "42.0" from Excel numeric cells

    if data_type == "float":
        return float(raw)

    if data_type == "multiselect":
        return split_multi(raw)

    if data_type == "email":
        return raw.lower()

    # Unknown data_type — return raw string with a warning-safe fallback
    return raw


def name_key(first: str, last: str) -> str:
    """Return a normalised lookup key combining *first* and *last* names.

    Both components are lower-cased, stripped, and joined with a single
    space.  Leading/trailing whitespace and internal runs of whitespace are
    collapsed to a single space.

    Parameters
    ----------
    first:
        Given name (may be empty).
    last:
        Family name (may be empty).

    Returns
    -------
    str
        Normalised ``"<first> <last>"`` key, stripped of surrounding
        whitespace.
    """
    combined = f"{first} {last}"
    return re.sub(r"\s+", " ", combined).strip().lower()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> date:
    """Try each format in ``_DATE_FORMATS``; raise ValueError on failure."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Try ISO 8601 with timezone suffix (e.g. "2024-01-15T00:00:00Z")
    try:
        return datetime.fromisoformat(s.rstrip("Z").split("+")[0]).date()
    except ValueError:
        pass
    raise ValueError(
        f"Cannot parse date {s!r}. "
        f"Supported formats: M/D/YYYY, YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, ISO 8601."
    )


def _parse_datetime(s: str) -> datetime:
    """Parse a datetime string; raise ValueError on failure."""
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.rstrip("Z").split("+")[0])
    except ValueError:
        pass
    raise ValueError(
        f"Cannot parse datetime {s!r}. "
        f"Supported formats: YYYY-MM-DD HH:MM:SS, YYYY-MM-DD, ISO 8601."
    )
