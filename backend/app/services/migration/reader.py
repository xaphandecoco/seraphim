"""Async XLSX row reader for the CiviCRM migration pipeline.

Public API
----------
read_rows(path: str) -> AsyncIterator[dict]

The synchronous openpyxl iteration runs inside ``asyncio.to_thread`` so that
the event loop is never blocked by file I/O or CPU-bound cell parsing.

Behaviour
---------
- First row is treated as headers.
- Fully-blank rows (all cells None or empty string after strip) are skipped.
- String cell values are stripped of leading/trailing whitespace.
- Empty strings (after strip) are normalised to ``None``.
- Non-string values (int, float, datetime …) are returned as-is from openpyxl.

No DB calls.  No SQLAlchemy imports.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional


def _iter_rows_sync(path: str) -> list[dict]:
    """Read the first worksheet and return a list of row dicts.

    Runs synchronously; intended to be called inside ``asyncio.to_thread``.
    openpyxl ``read_only=True`` keeps memory usage low for large files.
    """
    import openpyxl  # deferred — not available in all test environments

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)

        # --- header row ---
        try:
            raw_headers = next(rows_iter)
        except StopIteration:
            return []

        headers: list[Optional[str]] = [
            (str(h).strip() if h is not None else None) for h in raw_headers
        ]

        results: list[dict] = []
        for raw_row in rows_iter:
            row_dict: dict[str, object] = {}
            all_blank = True

            for header, cell_val in zip(headers, raw_row):
                if header is None:
                    # Skip columns with a blank header
                    continue

                # Normalise strings; leave other types (int, float, date …) as-is
                if isinstance(cell_val, str):
                    cell_val = cell_val.strip() or None

                if cell_val is not None:
                    all_blank = False

                row_dict[header] = cell_val

            if all_blank:
                continue

            results.append(row_dict)

        return results
    finally:
        wb.close()


async def read_rows(path: str) -> AsyncIterator[dict]:
    """Yield each data row from the XLSX file at *path* as a plain dict.

    Parameters
    ----------
    path:
        Absolute or relative filesystem path to the ``.xlsx`` file.

    Yields
    ------
    dict
        Mapping of ``{header: cell_value}`` for each non-blank data row.
        String values are stripped; empty strings become ``None``.
        Non-string cell values (int, float, :class:`datetime.datetime` …)
        are returned unchanged from openpyxl.

    Notes
    -----
    The entire file is read in a single ``asyncio.to_thread`` call to avoid
    repeated thread-pool round-trips per row.  Memory overhead is bounded by
    the worksheet size; for very large files the caller can process rows in
    streaming fashion using ``async for``.
    """
    rows: list[dict] = await asyncio.to_thread(_iter_rows_sync, path)
    for row in rows:
        yield row
