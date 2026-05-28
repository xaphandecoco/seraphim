import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_no_setup(client: AsyncClient):
    """Health check should work even before setup."""
    resp = await client.get("/health")
    # May return 200 or 503 depending on setup state
    assert resp.status_code in (200, 503)
