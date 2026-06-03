"""Supplementary CompreFace dual-key tests (Story S3 / ML-S3).

`test_compreface.py` already asserts the per-method `x-api-key` routing and two
fallback cases. This file adds the gaps:

  - Construction-level attribute wiring (detect_api_key / recognize_api_key) so a
    regression in __init__ is caught directly, not only via an HTTP call.
  - The remaining single-key fallback methods (add_subject / add_example /
    list_subjects / delete_subject) — the prior file only covered detect/recognize.
  - A *mixed* config (only one service key set) — the other path must fall back.
  - Regression for Self-Review Issue 2: add_subject must NOT pass an explicit
    `Content-Type: application/json` header (httpx sets it from json=). A stray
    header is the documented defect that was fixed.
  - Regression: detect() and recognize() must hit the correct CompreFace URL paths.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.compreface import ComprefaceClient


def _make_client(detect_key: str, recognize_key: str, legacy_key: str) -> ComprefaceClient:
    with patch("app.services.compreface.dynamic_settings") as mock_settings:
        mock_settings.get_compreface_url.return_value = "http://compreface:8000"
        mock_settings.get_compreface_api_key.return_value = legacy_key
        mock_settings.get_compreface_detect_api_key.return_value = detect_key
        mock_settings.get_compreface_recognize_api_key.return_value = recognize_key
        mock_settings.get_similarity_threshold_high.return_value = 0.98
        mock_settings.get_similarity_threshold_medium.return_value = 0.91
        return ComprefaceClient()


# ---------------------------------------------------------------------------
# Construction-level wiring
# ---------------------------------------------------------------------------


def test_init_wires_distinct_service_keys():
    c = _make_client("detect-k", "recognize-k", "legacy-k")
    assert c.detect_api_key == "detect-k"
    assert c.recognize_api_key == "recognize-k"


def test_init_falls_back_to_legacy_when_both_service_keys_blank():
    c = _make_client("", "", "legacy-k")
    assert c.detect_api_key == "legacy-k"
    assert c.recognize_api_key == "legacy-k"


def test_init_mixed_config_only_detect_set_recognize_falls_back():
    """Operator set only the Detection key; Recognition must fall back to legacy."""
    c = _make_client("detect-k", "", "legacy-k")
    assert c.detect_api_key == "detect-k"
    assert c.recognize_api_key == "legacy-k"


def test_init_mixed_config_only_recognize_set_detect_falls_back():
    c = _make_client("", "recognize-k", "legacy-k")
    assert c.detect_api_key == "legacy-k"
    assert c.recognize_api_key == "recognize-k"


def test_init_no_keys_at_all_yields_empty_strings():
    c = _make_client("", "", "")
    assert c.detect_api_key == ""
    assert c.recognize_api_key == ""


# ---------------------------------------------------------------------------
# Single-key fallback — remaining methods (add_subject / add_example /
# list_subjects / delete_subject)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_key_fallback_add_subject_uses_legacy_key():
    c = _make_client("", "", "legacy-k")
    resp = MagicMock(status_code=201)
    c._client.post = AsyncMock(return_value=resp)
    await c.add_subject("member:1")
    headers = c._client.post.call_args.kwargs.get("headers", {})
    assert headers.get("x-api-key") == "legacy-k"


@pytest.mark.asyncio
async def test_single_key_fallback_add_example_uses_legacy_key():
    c = _make_client("", "", "legacy-k")
    resp = MagicMock(status_code=201)
    c._client.post = AsyncMock(return_value=resp)
    await c.add_example("member:1", b"img")
    headers = c._client.post.call_args.kwargs.get("headers", {})
    assert headers.get("x-api-key") == "legacy-k"


@pytest.mark.asyncio
async def test_single_key_fallback_list_subjects_uses_legacy_key():
    c = _make_client("", "", "legacy-k")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"subjects": []}
    c._client.get = AsyncMock(return_value=resp)
    await c.list_subjects()
    headers = c._client.get.call_args.kwargs.get("headers", {})
    assert headers.get("x-api-key") == "legacy-k"


@pytest.mark.asyncio
async def test_single_key_fallback_delete_subject_uses_legacy_key():
    c = _make_client("", "", "legacy-k")
    resp = MagicMock(status_code=200)
    c._client.delete = AsyncMock(return_value=resp)
    await c.delete_subject("member:1")
    headers = c._client.delete.call_args.kwargs.get("headers", {})
    assert headers.get("x-api-key") == "legacy-k"


# ---------------------------------------------------------------------------
# Regression — Self-Review Issue 2: no redundant Content-Type on add_subject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_subject_does_not_send_explicit_content_type_header():
    """httpx sets Content-Type from json=; an explicit header is the fixed defect."""
    c = _make_client("d", "r", "legacy")
    resp = MagicMock(status_code=201)
    c._client.post = AsyncMock(return_value=resp)
    await c.add_subject("member:1")
    headers = c._client.post.call_args.kwargs.get("headers", {})
    assert "Content-Type" not in headers, (
        "add_subject must rely on httpx's automatic Content-Type from json=, "
        f"but an explicit header was sent: {headers}"
    )
    # And it must still send the body via json= (so httpx sets the header itself).
    assert c._client.post.call_args.kwargs.get("json") == {"subject": "member:1"}


# ---------------------------------------------------------------------------
# Regression — correct CompreFace endpoint paths per service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_hits_detection_endpoint_path():
    c = _make_client("d", "r", "legacy")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"result": []}
    c._client.post = AsyncMock(return_value=resp)
    await c.detect(b"img")
    url = c._client.post.call_args.args[0]
    assert url.endswith("/api/v1/detection/detect")


@pytest.mark.asyncio
async def test_recognize_hits_recognition_endpoint_path():
    c = _make_client("d", "r", "legacy")
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"result": []}
    c._client.post = AsyncMock(return_value=resp)
    await c.recognize(b"img")
    url = c._client.post.call_args.args[0]
    assert url.endswith("/api/v1/recognition/recognize")
