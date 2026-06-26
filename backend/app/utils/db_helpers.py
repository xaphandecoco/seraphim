"""Database utility helpers for Project Seraphim.

Dialect-agnostic helpers that work with both SQLite (test path) and Postgres
(production) without change.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def has_table(db: AsyncSession, name: str) -> bool:
    """Return True if a table named *name* exists in the current database.

    Uses the SQLAlchemy Inspector via the underlying sync connection so the
    check is dialect-agnostic (SQLite and Postgres).  Returns False — without
    raising — when the table is absent.

    Args:
        db:   An active AsyncSession bound to the target database.
        name: The table name to look up (case-sensitive per the dialect).

    Returns:
        True if the table exists, False otherwise.
    """
    conn = await db.connection()
    return await conn.run_sync(
        lambda sync_conn: sa.inspect(sync_conn).has_table(name)
    )
