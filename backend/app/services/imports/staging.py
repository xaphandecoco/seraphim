"""Multi-format reader for the CSV/XLSX import wizard (S10).

Public API
----------
read_header_and_sample(path, fmt, sheet=None) -> dict
iter_csv_rows(path, encoding, delimiter)      -> Iterator[dict]

read_header_and_sample returns::

    {
        "headers":     list[str],           # trimmed column names
        "sample_rows": list[dict],          # up to 10 data rows
        "sheets":      list[str] | None,    # XLSX only; None for CSV
        "total_rows":  int,
        "encoding":    str | None,          # CSV only
        "delimiter":   str | None,          # CSV only
    }

Raises ValueError when:
- CSV cannot be decoded with any encoding in the ladder.
- Data row count exceeds MAX_DATA_ROWS (50 000).

No DB calls.  No SQLAlchemy imports.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Iterator, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1252")
MAX_DATA_ROWS: int = 50_000
_SAMPLE_SIZE: int = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_encoding(raw: bytes) -> tuple[str, str]:
    """Try each encoding in order; return (encoding, decoded_text).

    A decoding is rejected if it produces the U+FFFD replacement character,
    which indicates the bytes are not valid for that codec.

    Raises ValueError if no encoding produces clean text.
    """
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            if "�" not in text:
                return enc, text
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError("Could not decode file; re-save as UTF-8")


def _sniff_delimiter(sample_text: str) -> str:
    """Detect CSV delimiter from a sample of text; fallback to comma."""
    try:
        dialect = csv.Sniffer().sniff(sample_text[:4096], delimiters=",;\t")
        return dialect.delimiter
    except csv.Error:
        return ","


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_header_and_sample(
    path: str,
    fmt: str,
    sheet: Optional[str] = None,
) -> dict[str, Any]:
    """Read headers, up to 10 sample rows, and metadata from a staged file.

    Parameters
    ----------
    path:
        Absolute path to the staged file.
    fmt:
        ``'csv'`` or ``'xlsx'``.
    sheet:
        Sheet name (XLSX only). Uses the active sheet when ``None``.

    Returns
    -------
    dict
        Keys: headers, sample_rows, sheets, total_rows, encoding, delimiter.

    Raises
    ------
    ValueError
        Undecodable CSV or data row count > MAX_DATA_ROWS.
    """
    if fmt == "csv":
        return _read_csv(path)
    if fmt == "xlsx":
        return _read_xlsx(path, sheet)
    raise ValueError(f"Unsupported format: {fmt!r}. Use 'csv' or 'xlsx'.")


def _read_csv(path: str) -> dict[str, Any]:
    with open(path, "rb") as fh:
        raw = fh.read(65536)  # 64 KB for encoding detection

    encoding, sample_text = _detect_encoding(raw)
    delimiter = _sniff_delimiter(sample_text)

    headers: list[str] = []
    sample_rows: list[dict] = []
    total_rows = 0

    with open(path, encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        raw_headers = reader.fieldnames or []
        # Trim and strip U+FEFF BOM which can survive double-BOM encoded files.
        headers = [
            h.strip().strip("﻿")
            for h in raw_headers
            if h and h.strip().strip("﻿")
        ]

        for row in reader:
            # Skip completely blank rows; exclude the DictReader restkey (None)
            # which holds excess columns as a list and is not a real field.
            if all(v is None or str(v).strip() == "" for k, v in row.items() if k is not None):
                continue
            total_rows += 1
            if total_rows > MAX_DATA_ROWS:
                raise ValueError(
                    f"File has more than {MAX_DATA_ROWS:,} data rows; "
                    "reduce the file size or split it into batches."
                )
            if len(sample_rows) < _SAMPLE_SIZE:
                sample_rows.append({
                    h: (row.get(h, "").strip() if row.get(h) is not None else None) or None
                    for h in headers
                })

    return {
        "headers": headers,
        "sample_rows": sample_rows,
        "sheets": None,
        "total_rows": total_rows,
        "encoding": encoding,
        "delimiter": delimiter,
    }


def _read_xlsx(path: str, sheet: Optional[str] = None) -> dict[str, Any]:
    import openpyxl  # deferred — not always installed in every test env

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet_names: list[str] = list(wb.sheetnames)

        if sheet and sheet in sheet_names:
            ws = wb[sheet]
        else:
            ws = wb.active

        headers: list[str] = []
        # (original_position, trimmed_name) pairs for non-empty header columns
        header_positions: list[tuple[int, str]] = []
        sample_rows: list[dict] = []
        total_rows = 0
        first_data_row = True

        for raw_row in ws.iter_rows(values_only=True):
            # Skip blank rows
            if all(cell is None or str(cell).strip() == "" for cell in raw_row):
                continue

            if first_data_row:
                # Parse headers from first non-blank row
                for i, cell in enumerate(raw_row):
                    val = str(cell).strip() if cell is not None else ""
                    if val:
                        header_positions.append((i, val))
                        headers.append(val)
                first_data_row = False
                continue

            total_rows += 1
            if total_rows > MAX_DATA_ROWS:
                raise ValueError(
                    f"File has more than {MAX_DATA_ROWS:,} data rows; "
                    "reduce the file size or split it into batches."
                )

            if len(sample_rows) < _SAMPLE_SIZE:
                row_dict: dict[str, Any] = {}
                for col_pos, col_name in header_positions:
                    val = raw_row[col_pos] if col_pos < len(raw_row) else None
                    # Normalise: strip strings, keep None for empty
                    if isinstance(val, str):
                        val = val.strip() or None
                    row_dict[col_name] = val
                sample_rows.append(row_dict)

        return {
            "headers": headers,
            "sample_rows": sample_rows,
            "sheets": sheet_names,
            "total_rows": total_rows,
            "encoding": None,
            "delimiter": None,
        }
    finally:
        wb.close()


def iter_csv_rows(
    path: str,
    encoding: str,
    delimiter: str,
) -> Iterator[dict]:
    """Stdlib generator yielding one dict per non-blank CSV data row.

    Empty cells are yielded as empty strings (same as csv.DictReader).
    Used in the wizard run/preview path; XLSX uses S06 reader.read_rows.
    Caller is responsible for commit/flush cycles.
    """
    with open(path, encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        for row in reader:
            # Skip entirely blank rows; exclude the DictReader restkey (None)
            # which holds excess columns as a list and is not a real field.
            if all(v is None or str(v).strip() == "" for k, v in row.items() if k is not None):
                continue
            yield dict(row)
