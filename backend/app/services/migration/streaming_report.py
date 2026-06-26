"""Streaming CSV report generator for migration import batches.

Ownership: S06 migration ETL (T05).

Public API
----------
stream_batch_csv(batch_id, session)   -> AsyncIterator[str]

Memory contract
---------------
- Uses db.stream(stmt.execution_options(yield_per=1000)) +
  async for partition in result.scalars().partitions(1000) so at most
  1 000 ImportRowResult ORM rows live in memory at once.
- Never calls .all() on the full result set.
- Yields one CSV header line then one line per ImportRowResult ordered by
  row_number.

Circular-import rule (CN-25): this module must NOT import from any router module.
"""

from __future__ import annotations

import csv
import io
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportRowResult

# CSV column order for the batch report
_HEADERS = [
    "row_number",
    "external_id",
    "outcome",
    "entity_id",
    "message",
]


def _row_to_csv_line(row: ImportRowResult) -> str:
    """Render one ImportRowResult into a CSV line (no trailing newline)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            row.row_number,
            row.external_id if row.external_id is not None else "",
            row.outcome,
            row.entity_id if row.entity_id is not None else "",
            row.message if row.message is not None else "",
        ]
    )
    return buf.getvalue()


def _header_line() -> str:
    """Return the CSV header line."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_HEADERS)
    return buf.getvalue()


async def stream_batch_csv(
    batch_id: int,
    session: AsyncSession,
) -> AsyncIterator[str]:
    """Yield a CSV header line then one line per ImportRowResult for batch_id.

    Rows are ordered by row_number.  Memory is bounded to at most 1 000 ORM
    rows at a time via db.stream + partitions(1000).

    Yields
    ------
    str
        UTF-8 text lines (each includes the trailing newline written by csv).
    """
    yield _header_line()

    stmt = (
        select(ImportRowResult)
        .where(ImportRowResult.batch_id == batch_id)
        .order_by(ImportRowResult.row_number)
        .execution_options(yield_per=1000)
    )

    result = await session.stream(stmt)
    async for partition in result.scalars().partitions(1000):
        for row in partition:
            yield _row_to_csv_line(row)
