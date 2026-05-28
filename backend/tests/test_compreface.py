import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.compreface import ComprefaceClient, RecognitionResult, _map_tier


@pytest.fixture
def client():
    with patch("app.services.compreface.legacy_settings") as mock_settings:
        mock_settings.COMPREFACE_URL = "http://compreface:8000"
        mock_settings.COMPREFACE_API_KEY = "test-api-key"
        c = ComprefaceClient()
        yield c


# ============================================================================
# _map_tier unit tests
# ============================================================================


def test_map_tier_high_confidence_returns_100():
    assert _map_tier(0.99) == "100"
    assert _map_tier(0.98) == "100"


def test_map_tier_medium_confidence_returns_91_99():
    assert _map_tier(0.95) == "91-99"
    assert _map_tier(0.91) == "91-99"


def test_map_tier_low_confidence_returns_below90():
    assert _map_tier(0.90) == "below90"
    assert _map_tier(0.50) == "below90"


def test_map_tier_none_returns_unknown():
    assert _map_tier(None) == "unknown"


# ============================================================================
# recognize() tests
# ============================================================================


@pytest.mark.asyncio
async def test_recognize_high_confidence_returns_match(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": [
            {
                "box": {"x_min": 10, "y_min": 20, "x_max": 110, "y_max": 120},
                "subjects": [{"subject": "member:42", "similarity": 0.99}],
            }
        ]
    }
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.recognize(b"fake_image")

    assert result.subject_id == "member:42"
    assert result.similarity_score == 0.99
    assert result.tier == "100"
    assert result.box == {"x": 10, "y": 20, "w": 100, "h": 100}


@pytest.mark.asyncio
async def test_recognize_medium_confidence_returns_91_99_tier(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": [
            {
                "box": {"x_min": 0, "y_min": 0, "x_max": 50, "y_max": 50},
                "subjects": [{"subject": "member:7", "similarity": 0.95}],
            }
        ]
    }
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.recognize(b"fake_image")

    assert result.subject_id == "member:7"
    assert result.similarity_score == 0.95
    assert result.tier == "91-99"


@pytest.mark.asyncio
async def test_recognize_low_confidence_returns_below90_tier(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": [
            {
                "box": {"x_min": 0, "y_min": 0, "x_max": 50, "y_max": 50},
                "subjects": [{"subject": "member:3", "similarity": 0.85}],
            }
        ]
    }
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.recognize(b"fake_image")

    assert result.subject_id == "member:3"
    assert result.similarity_score == 0.85
    assert result.tier == "below90"


@pytest.mark.asyncio
async def test_recognize_no_match_returns_unknown_tier_with_box(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": [
            {
                "box": {"x_min": 5, "y_min": 5, "x_max": 55, "y_max": 55},
                "subjects": [],
            }
        ]
    }
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.recognize(b"fake_image")

    assert result.subject_id is None
    assert result.similarity_score is None
    assert result.tier == "unknown"
    assert result.box == {"x": 5, "y": 5, "w": 50, "h": 50}


@pytest.mark.asyncio
async def test_recognize_empty_response_returns_unknown_no_box(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}
    client._client.post = AsyncMock(return_value=mock_response)

    result = await client.recognize(b"fake_image")

    assert result.subject_id is None
    assert result.similarity_score is None
    assert result.tier == "unknown"
    assert result.box is None


# ============================================================================
# detect() tests
# ============================================================================


@pytest.mark.asyncio
async def test_detect_successful_returns_faces(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "result": [
            {
                "box": {"x_min": 10, "y_min": 20, "x_max": 110, "y_max": 120},
                "probability": 0.95,
            },
            {
                "box": {"x_min": 200, "y_min": 200, "x_max": 300, "y_max": 300},
                "probability": 0.88,
            },
        ]
    }
    client._client.post = AsyncMock(return_value=mock_response)

    faces = await client.detect(b"fake_image")

    assert len(faces) == 2
    assert faces[0] == {"x": 10, "y": 20, "w": 100, "h": 100, "probability": 0.95}
    assert faces[1] == {"x": 200, "y": 200, "w": 100, "h": 100, "probability": 0.88}


@pytest.mark.asyncio
async def test_detect_no_faces_returns_empty_list(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}
    client._client.post = AsyncMock(return_value=mock_response)

    faces = await client.detect(b"fake_image")

    assert faces == []


@pytest.mark.asyncio
async def test_detect_error_response_raises_http_status_error(client):
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server Error",
        request=MagicMock(),
        response=MagicMock(),
    )
    client._client.post = AsyncMock(return_value=mock_response)

    with pytest.raises(httpx.HTTPStatusError):
        await client.detect(b"fake_image")


# ============================================================================
# Retry behavior tests
# ============================================================================


@pytest.mark.asyncio
async def test_recognize_timeout_on_first_call_retries_then_succeeds(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}

    client._client.post = AsyncMock(
        side_effect=[httpx.TimeoutException("Timeout"), mock_response]
    )

    result = await client.recognize(b"fake_image")

    assert result.tier == "unknown"
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_detect_timeout_exhaustion_raises_after_three_attempts(client):
    client._client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

    with pytest.raises(httpx.TimeoutException):
        await client.detect(b"fake_image")

    assert client._client.post.call_count == 3


@pytest.mark.asyncio
async def test_recognize_connect_error_triggers_retry_then_succeeds(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"result": []}

    client._client.post = AsyncMock(
        side_effect=[httpx.ConnectError("Connection failed"), mock_response]
    )

    result = await client.recognize(b"fake_image")

    assert result.tier == "unknown"
    assert client._client.post.call_count == 2


@pytest.mark.asyncio
async def test_detect_connect_error_exhaustion_raises_after_three_attempts(client):
    client._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection failed"))

    with pytest.raises(httpx.ConnectError):
        await client.detect(b"fake_image")

    assert client._client.post.call_count == 3
