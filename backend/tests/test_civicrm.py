import json
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.civicrm import CiviCRMClient, _is_transient_error


@pytest.fixture
def client():
    with patch("app.services.civicrm.dynamic_settings") as mock_settings:
        mock_settings.get_civicrm_url.return_value = "http://civicrm.local"
        mock_settings.get_civicrm_api_key.return_value = "api-key"
        mock_settings.get_civicrm_site_key.return_value = "site-key"
        c = CiviCRMClient()
        yield c


# ============================================================================
# _is_transient_error unit tests
# ============================================================================


def test_is_transient_error_429_returns_true():
    response = MagicMock()
    response.status_code = 429
    exc = httpx.HTTPStatusError("Rate limited", request=MagicMock(), response=response)
    assert _is_transient_error(exc) is True


def test_is_transient_error_500_returns_true():
    response = MagicMock()
    response.status_code = 500
    exc = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=response)
    assert _is_transient_error(exc) is True


def test_is_transient_error_502_returns_true():
    response = MagicMock()
    response.status_code = 502
    exc = httpx.HTTPStatusError("Bad Gateway", request=MagicMock(), response=response)
    assert _is_transient_error(exc) is True


def test_is_transient_error_503_returns_true():
    response = MagicMock()
    response.status_code = 503
    exc = httpx.HTTPStatusError(
        "Service Unavailable", request=MagicMock(), response=response
    )
    assert _is_transient_error(exc) is True


def test_is_transient_error_400_returns_false():
    response = MagicMock()
    response.status_code = 400
    exc = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=response)
    assert _is_transient_error(exc) is False


def test_is_transient_error_404_returns_false():
    response = MagicMock()
    response.status_code = 404
    exc = httpx.HTTPStatusError("Not Found", request=MagicMock(), response=response)
    assert _is_transient_error(exc) is False


def test_is_transient_error_timeout_returns_true():
    assert _is_transient_error(httpx.TimeoutException("Timeout")) is True


def test_is_transient_error_connect_error_returns_true():
    assert _is_transient_error(httpx.ConnectError("Failed")) is True


def test_is_transient_error_generic_exception_returns_false():
    assert _is_transient_error(ValueError("Random")) is False


# ============================================================================
# sync_members() tests
# ============================================================================


@pytest.mark.asyncio
async def test_sync_members_success_returns_multiple_members(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_error": 0,
        "values": {
            "1": {
                "id": "1",
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "2": {
                "id": "2",
                "first_name": "Jane",
                "last_name": "Smith",
                "email": "jane@example.com",
            },
        },
    }
    client._client.post = AsyncMock(return_value=mock_response)

    members = await client.sync_members()

    assert len(members) == 2
    assert members[0]["first_name"] == "John"
    assert members[1]["first_name"] == "Jane"


@pytest.mark.asyncio
async def test_sync_members_empty_response_returns_empty_list(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 0, "values": {}}
    client._client.post = AsyncMock(return_value=mock_response)

    members = await client.sync_members()

    assert members == []


@pytest.mark.asyncio
async def test_sync_members_error_response_raises_runtime_error(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_error": 1,
        "error_message": "Invalid API key",
    }
    client._client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(RuntimeError, match="Invalid API key"):
        await client.sync_members()


# ============================================================================
# sync_events() tests
# ============================================================================


@pytest.mark.asyncio
async def test_sync_events_success_returns_events(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "is_error": 0,
        "values": {
            "10": {
                "id": "10",
                "title": "Sunday Service",
                "start_date": "2024-01-01 09:00:00",
            },
        },
    }
    client._client.post = AsyncMock(return_value=mock_response)

    events = await client.sync_events()

    assert len(events) == 1
    assert events[0]["title"] == "Sunday Service"


@pytest.mark.asyncio
async def test_sync_events_empty_returns_empty_list(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 0, "values": {}}
    client._client.post = AsyncMock(return_value=mock_response)

    events = await client.sync_events()

    assert events == []


@pytest.mark.asyncio
async def test_sync_events_error_response_raises_runtime_error(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 1, "error_message": "DB Error"}
    client._client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(RuntimeError, match="DB Error"):
        await client.sync_events()


# ============================================================================
# push_attendance() tests
# ============================================================================


@pytest.mark.asyncio
async def test_push_attendance_success_returns_true(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 0}
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.push_attendance(contact_id=42, event_id=10)

    assert result is True


@pytest.mark.asyncio
async def test_push_attendance_failed_push_raises_runtime_error(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 1, "error_message": "Duplicate"}
    client._client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(RuntimeError, match="Duplicate"):
        await client.push_attendance(contact_id=42, event_id=10)


@pytest.mark.asyncio
async def test_push_attendance_missing_contact_id_passes_through(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"is_error": 0}
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.push_attendance(contact_id=0, event_id=10)

    assert result is True
    data = client._client.post.call_args[1]["data"]
    params = json.loads(data["json"])
    assert params["contact_id"] == 0


# ============================================================================
# Retry behavior tests
# ============================================================================


@pytest.mark.asyncio
async def test_call_retry_on_429_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    error_response = MagicMock()
    error_response.status_code = 429

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Rate limited", request=MagicMock(), response=error_response
            ),
            success_response,
        ]
    )

    result = await client.sync_members()

    assert result == []
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_call_retry_on_500_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    error_response = MagicMock()
    error_response.status_code = 500

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            ),
            success_response,
        ]
    )

    result = await client.sync_events()

    assert result == []
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_call_retry_on_502_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    error_response = MagicMock()
    error_response.status_code = 502

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Bad Gateway", request=MagicMock(), response=error_response
            ),
            success_response,
        ]
    )

    result = await client.sync_members()

    assert result == []
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_call_retry_on_503_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    error_response = MagicMock()
    error_response.status_code = 503

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Service Unavailable", request=MagicMock(), response=error_response
            ),
            success_response,
        ]
    )

    result = await client.sync_events()

    assert result == []
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_call_no_retry_on_400_raises_immediately(client):
    error_response = MagicMock()
    error_response.status_code = 400

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=error_response
            ),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.sync_members()

    assert client._client.post.call_count == 1


@pytest.mark.asyncio
async def test_call_no_retry_on_404_raises_immediately(client):
    error_response = MagicMock()
    error_response.status_code = 404

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Not Found", request=MagicMock(), response=error_response
            ),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.sync_events()

    assert client._client.post.call_count == 1


@pytest.mark.asyncio
async def test_call_retry_exhaustion_on_500_raises(client):
    error_response = MagicMock()
    error_response.status_code = 500

    client._client.post = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            ),
            httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            ),
            httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            ),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.push_attendance(contact_id=1, event_id=1)

    assert client._client.post.call_count == 3


@pytest.mark.asyncio
async def test_call_retry_on_timeout_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    client._client.post = AsyncMock(
        side_effect=[httpx.TimeoutException("Timeout"), success_response]
    )

    result = await client.sync_members()

    assert result == []
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_call_retry_on_connect_error_then_succeeds(client):
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = {"is_error": 0, "values": {}}

    client._client.post = AsyncMock(
        side_effect=[httpx.ConnectError("Connection failed"), success_response]
    )

    result = await client.sync_events()

    assert result == []
    assert client._client.post.call_count == 2
