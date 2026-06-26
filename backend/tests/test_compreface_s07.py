"""QA tests for s07-compreface-client patch.

Verifies:
  1. add_example returns the image_id UUID string from the response body on success.
  2. add_example falls back to imageId key when image_id is absent.
  3. add_example returns None on non-200/201 HTTP status.
  4. add_example returns None when both image_id and imageId are absent from body.
  5. delete_example method exists and is callable.
  6. delete_example returns True on HTTP 200.
  7. delete_example returns True on HTTP 204.
  8. delete_example returns False on non-200/204 HTTP status.
  9. delete_example uses recognize_api_key in the x-api-key header.
 10. delete_example hits the correct CompreFace URL path (with image_id embedded).
 11. delete_example has no retry decorator (best-effort; only one HTTP call on failure).
 12. add_example return type is Optional[str] (type annotation verifiable at import time).
"""
import inspect
import pytest
from typing import Optional, get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.compreface import ComprefaceClient


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with patch("app.services.compreface.dynamic_settings") as mock_settings:
        mock_settings.get_compreface_url.return_value = "http://compreface:8000"
        mock_settings.get_compreface_api_key.return_value = "legacy-api-key"
        mock_settings.get_compreface_detect_api_key.return_value = "detect-api-key"
        mock_settings.get_compreface_recognize_api_key.return_value = "recognize-api-key"
        mock_settings.get_similarity_threshold_high.return_value = 0.98
        mock_settings.get_similarity_threshold_medium.return_value = 0.91
        c = ComprefaceClient()
        yield c


# ---------------------------------------------------------------------------
# 1. add_example returns image_id from response body on 200/201 success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_example_returns_image_id_on_200(client):
    """add_example must parse the JSON body and return the image_id UUID string."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"image_id": "uuid-abc-123", "subject": "member:1"}
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:1", b"fake_image")

    assert result == "uuid-abc-123", (
        f"Expected 'uuid-abc-123', got {result!r}"
    )


@pytest.mark.asyncio
async def test_add_example_returns_image_id_on_201(client):
    """add_example must also accept 201 Created and return the image_id."""
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"image_id": "uuid-xyz-999", "subject": "member:2"}
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:2", b"fake_image")

    assert result == "uuid-xyz-999", (
        f"Expected 'uuid-xyz-999', got {result!r}"
    )


# ---------------------------------------------------------------------------
# 2. add_example falls back to imageId (camelCase) when image_id absent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_example_falls_back_to_imageId_key(client):
    """Some CompreFace builds return imageId (camelCase); add_example must handle it."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"imageId": "uuid-camel-456", "subject": "member:3"}
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:3", b"fake_image")

    assert result == "uuid-camel-456", (
        f"Expected 'uuid-camel-456' from imageId fallback, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 3. add_example returns None on non-200/201 HTTP status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_example_returns_none_on_400(client):
    """HTTP 400 Bad Request → add_example must return None."""
    resp = MagicMock(status_code=400)
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:1", b"fake_image")

    assert result is None, f"Expected None on HTTP 400, got {result!r}"


@pytest.mark.asyncio
async def test_add_example_returns_none_on_500(client):
    """HTTP 500 → add_example must return None."""
    resp = MagicMock(status_code=500)
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:1", b"fake_image")

    assert result is None, f"Expected None on HTTP 500, got {result!r}"


@pytest.mark.asyncio
async def test_add_example_returns_none_on_404(client):
    """HTTP 404 → add_example must return None."""
    resp = MagicMock(status_code=404)
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:1", b"fake_image")

    assert result is None, f"Expected None on HTTP 404, got {result!r}"


# ---------------------------------------------------------------------------
# 4. add_example returns None when body lacks both image_id and imageId
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_example_returns_none_when_body_has_no_image_id(client):
    """If the 200 response body lacks image_id and imageId, return None."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"subject": "member:1"}  # no image_id key
    client._client.post = AsyncMock(return_value=resp)

    result = await client.add_example("member:1", b"fake_image")

    assert result is None, (
        f"Expected None when image_id absent from body, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 5–7. delete_example exists, returns True on 200 and 204
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_example_returns_true_on_200(client):
    """delete_example must return True when CompreFace responds 200."""
    resp = MagicMock(status_code=200)
    client._client.delete = AsyncMock(return_value=resp)

    result = await client.delete_example("uuid-abc-123")

    assert result is True, f"Expected True on HTTP 200, got {result!r}"


@pytest.mark.asyncio
async def test_delete_example_returns_true_on_204(client):
    """delete_example must return True when CompreFace responds 204 No Content."""
    resp = MagicMock(status_code=204)
    client._client.delete = AsyncMock(return_value=resp)

    result = await client.delete_example("uuid-abc-123")

    assert result is True, f"Expected True on HTTP 204, got {result!r}"


# ---------------------------------------------------------------------------
# 8. delete_example returns False on non-200/204 status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_example_returns_false_on_404(client):
    """delete_example must return False when the face UUID is not found (404)."""
    resp = MagicMock(status_code=404)
    client._client.delete = AsyncMock(return_value=resp)

    result = await client.delete_example("nonexistent-uuid")

    assert result is False, f"Expected False on HTTP 404, got {result!r}"


@pytest.mark.asyncio
async def test_delete_example_returns_false_on_500(client):
    """delete_example must return False on server error (500)."""
    resp = MagicMock(status_code=500)
    client._client.delete = AsyncMock(return_value=resp)

    result = await client.delete_example("uuid-abc-123")

    assert result is False, f"Expected False on HTTP 500, got {result!r}"


# ---------------------------------------------------------------------------
# 9. delete_example uses recognize_api_key header
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_example_sends_recognition_service_key(client):
    """delete_example must send x-api-key: recognize_api_key."""
    resp = MagicMock(status_code=200)
    client._client.delete = AsyncMock(return_value=resp)

    await client.delete_example("uuid-abc-123")

    call_kwargs = client._client.delete.call_args
    sent_headers = call_kwargs.kwargs.get("headers", {})
    assert sent_headers.get("x-api-key") == "recognize-api-key", (
        "delete_example must use recognize_api_key (Recognition Service), got: "
        f"{sent_headers.get('x-api-key')}"
    )


# ---------------------------------------------------------------------------
# 10. delete_example hits the correct URL path (/api/v1/recognition/faces/{id})
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_example_hits_correct_url_path(client):
    """delete_example must call DELETE /api/v1/recognition/faces/{image_id}."""
    resp = MagicMock(status_code=200)
    client._client.delete = AsyncMock(return_value=resp)
    image_id = "uuid-face-789"

    await client.delete_example(image_id)

    url = client._client.delete.call_args.args[0]
    expected_suffix = f"/api/v1/recognition/faces/{image_id}"
    assert url.endswith(expected_suffix), (
        f"Expected URL ending in '{expected_suffix}', got: {url!r}"
    )


# ---------------------------------------------------------------------------
# 11. delete_example has no retry decorator (best-effort; one HTTP call on error)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_example_no_retry_on_connect_error(client):
    """delete_example must NOT retry on ConnectError — it is best-effort."""
    import httpx

    client._client.delete = AsyncMock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(httpx.ConnectError):
        await client.delete_example("uuid-abc-123")

    # Only 1 attempt — no tenacity retry wrapping
    assert client._client.delete.call_count == 1, (
        f"delete_example must not retry; expected 1 call, got {client._client.delete.call_count}"
    )


@pytest.mark.asyncio
async def test_delete_example_no_retry_on_timeout(client):
    """delete_example must NOT retry on TimeoutException — it is best-effort."""
    import httpx

    client._client.delete = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    with pytest.raises(httpx.TimeoutException):
        await client.delete_example("uuid-abc-123")

    assert client._client.delete.call_count == 1, (
        f"delete_example must not retry; expected 1 call, got {client._client.delete.call_count}"
    )


# ---------------------------------------------------------------------------
# 12. add_example return type annotation is Optional[str]
# ---------------------------------------------------------------------------

def test_add_example_return_annotation_is_optional_str():
    """The return type annotation of add_example must be Optional[str]."""
    hints = get_type_hints(ComprefaceClient.add_example)
    actual = hints.get("return")
    assert actual == Optional[str], (
        f"add_example return annotation must be Optional[str], got: {actual!r}"
    )


def test_delete_example_return_annotation_is_bool():
    """The return type annotation of delete_example must be bool."""
    hints = get_type_hints(ComprefaceClient.delete_example)
    actual = hints.get("return")
    assert actual is bool, (
        f"delete_example return annotation must be bool, got: {actual!r}"
    )
