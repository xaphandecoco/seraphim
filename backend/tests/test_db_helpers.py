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
    """job_runs is not defined as a model — has_table must return False."""
    from app.utils.db_helpers import has_table

    result = await has_table(db_session, "job_runs")
    assert result is False, (
        "has_table('job_runs') should return False; job_runs is not in the schema"
    )
