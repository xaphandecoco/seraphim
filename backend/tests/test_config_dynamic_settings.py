"""Unit tests for the DynamicSettings config layer that the sprint relies on.

Covers:
  - B-S3 CompreFace dual-key getters (config.py get_compreface_detect_api_key /
    get_compreface_recognize_api_key) and the legacy single-key getter.
  - The ComprefaceClient `or`-fallback precedence at the *config* boundary
    (service key present vs blank → which value the client ends up using).
  - B-S2 / Self-Review Issue 1: get_int / get_float must NOT raise when an admin
    stores a non-numeric value (e.g. sse_heartbeat_seconds = "auto"); they must
    return the supplied default. This guards the SSE endpoint from crashing on
    every connection.

These are pure unit tests against the singleton's in-memory `_settings` dict.
We snapshot and restore `_settings` around each test so we never leak state into
the rest of the suite (conftest pre-seeds jwt_secret / setup_complete there).
"""
import pytest

from app.config import DynamicSettings, dynamic_settings


@pytest.fixture
def ds():
    """Yield the DynamicSettings singleton with an isolated, restored _settings dict."""
    original = dict(DynamicSettings._settings)
    try:
        yield dynamic_settings
    finally:
        DynamicSettings._settings = original


# ---------------------------------------------------------------------------
# B-S3 — dual-key getters (config.py)
# ---------------------------------------------------------------------------


def test_get_compreface_detect_api_key_returns_stored_value(ds):
    ds._settings["compreface_detect_api_key"] = "detect-uuid"
    assert ds.get_compreface_detect_api_key() == "detect-uuid"


def test_get_compreface_recognize_api_key_returns_stored_value(ds):
    ds._settings["compreface_recognize_api_key"] = "recognize-uuid"
    assert ds.get_compreface_recognize_api_key() == "recognize-uuid"


def test_dual_key_getters_default_to_empty_string_when_absent(ds):
    """Empty string (not None) is the documented default so the `or`-fallback works."""
    ds._settings.pop("compreface_detect_api_key", None)
    ds._settings.pop("compreface_recognize_api_key", None)
    assert ds.get_compreface_detect_api_key() == ""
    assert ds.get_compreface_recognize_api_key() == ""


def test_legacy_get_compreface_api_key_still_present(ds):
    """Back-compat: the legacy single-key getter must remain for the fallback chain."""
    ds._settings["compreface_api_key"] = "legacy-uuid"
    assert ds.get_compreface_api_key() == "legacy-uuid"


# ---------------------------------------------------------------------------
# B-S3 — the `or`-fallback precedence expressed at the config boundary
# (mirrors ComprefaceClient.__init__: service_key or legacy_key)
# ---------------------------------------------------------------------------


def test_fallback_precedence_service_key_wins_when_present(ds):
    ds._settings["compreface_api_key"] = "legacy-uuid"
    ds._settings["compreface_detect_api_key"] = "detect-uuid"
    effective = ds.get_compreface_detect_api_key() or ds.get_compreface_api_key()
    assert effective == "detect-uuid"


def test_fallback_precedence_legacy_used_when_service_key_blank(ds):
    ds._settings["compreface_api_key"] = "legacy-uuid"
    ds._settings["compreface_detect_api_key"] = ""  # blank → fall through
    effective = ds.get_compreface_detect_api_key() or ds.get_compreface_api_key()
    assert effective == "legacy-uuid"


def test_fallback_precedence_both_blank_yields_empty(ds):
    ds._settings["compreface_api_key"] = ""
    ds._settings["compreface_recognize_api_key"] = ""
    effective = ds.get_compreface_recognize_api_key() or ds.get_compreface_api_key()
    assert effective == ""


# ---------------------------------------------------------------------------
# B-S2 — get_int / get_float robustness (SSE heartbeat default lookup)
# ---------------------------------------------------------------------------


def test_get_int_returns_value_when_numeric(ds):
    ds._settings["sse_heartbeat_seconds"] = "15"
    assert ds.get_int("sse_heartbeat_seconds", 99) == 15


def test_get_int_returns_default_for_non_numeric_string(ds):
    """A bad admin value must NOT raise — the SSE generator calls this per connection."""
    ds._settings["sse_heartbeat_seconds"] = "auto"
    assert ds.get_int("sse_heartbeat_seconds", 15) == 15


def test_get_int_returns_default_when_missing(ds):
    ds._settings.pop("sse_heartbeat_seconds", None)
    assert ds.get_int("sse_heartbeat_seconds", 15) == 15


def test_get_int_returns_default_when_none(ds):
    ds._settings["sse_heartbeat_seconds"] = None
    assert ds.get_int("sse_heartbeat_seconds", 15) == 15


def test_get_int_accepts_real_int_value(ds):
    ds._settings["sse_heartbeat_seconds"] = 30
    assert ds.get_int("sse_heartbeat_seconds", 15) == 30


def test_get_float_returns_default_for_non_numeric_string(ds):
    ds._settings["similarity_threshold_high"] = "not-a-float"
    assert ds.get_float("similarity_threshold_high", 0.98) == 0.98


def test_get_float_parses_numeric_string(ds):
    ds._settings["similarity_threshold_high"] = "0.97"
    assert ds.get_float("similarity_threshold_high", 0.98) == 0.97


def test_get_int_default_is_six_x_margin_for_cloudflare():
    """Documentation guard: the SSE endpoint uses a 15s default heartbeat, which is a
    6x safety margin under Cloudflare's 100s idle-origin timeout (Story S2 / Q1).

    This asserts the *default* the endpoint asks for is <= 15s (so the heartbeat is
    frequent enough). It reads the literal the production code passes.
    """
    import inspect
    import app.main as main_mod

    src = inspect.getsource(main_mod.task_feed)
    assert 'get_int("sse_heartbeat_seconds"' in src, (
        "SSE endpoint must look up sse_heartbeat_seconds"
    )
    # Extract the default argument and assert the 6x-margin invariant.
    import re
    m = re.search(r'get_int\(\s*"sse_heartbeat_seconds"\s*,\s*(\d+)\s*\)', src)
    assert m, "could not find the heartbeat default literal in task_feed source"
    default_seconds = int(m.group(1))
    assert default_seconds <= 15, (
        f"heartbeat default {default_seconds}s gives <6x margin under Cloudflare's "
        "100s idle timeout"
    )
    assert default_seconds > 0, "a 0s default would busy-loop emitting keepalives"
