"""Tests for backend/app/utils/db_helpers.py.

Validates the has_table() helper against the bare test-DB schema built via
Base.metadata.create_all() inside the db_session fixture.

mirrors style of backend/tests/test_audit_service.py.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_has_table_existing(db_session: AsyncSession):
    """contacts is a registered model — has_table must return True."""
    from app.utils.db_helpers import has_table

    result = await has_table(db_session, "contacts")
    assert result is True, (
        "has_table('contacts') should return True after create_all"
    )


@pytest.mark.asyncio
async def test_has_table_missing(db_session: AsyncSession):
    """A table that has never been defined must return False."""
    from app.utils.db_helpers import has_table

    result = await has_table(db_session, "definitely_not_a_real_table")
    assert result is False, (
        "has_table('definitely_not_a_real_table') should return False; "
        "that table name does not exist in the schema"
    )
