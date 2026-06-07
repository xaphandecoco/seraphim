"""nginx access-log token-scrubbing regression fence (QA).

Tokens travel in the query string on two paths:
  - EventSource SSE:  /api/tasks/feed?_t=<JWT>
  - <img> face URLs:  /api/storage/<path>?_t=<JWT>

The `scrubbed` log_format logs $uri (path only, never $args), so the token is
never written to the access log. This test pins that security property on the
**token-bearing** location blocks so it cannot silently regress (e.g. someone
deleting the per-location `access_log ... scrubbed;` line, which would start
logging ?_t=<JWT> in plaintext).

It also records the L6 state explicitly: the sprint's L6 item would add the
`scrubbed` format to the parent `/api/` and `/` blocks as defense-in-depth.
That parent-block scrubbing is asserted as an *advisory* (xfail) so this file
documents the intended end-state and will flip green the moment L6 lands —
without failing the suite while L6 is outstanding.

The file lives in the backend pytest suite (like test_deploy_artifacts.py) so a
single `pytest tests/ -q` reports nginx-config status alongside code tests.
"""
import re
from pathlib import Path

import pytest


def _find_nginx_conf() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "frontend" / "nginx.conf"
        if candidate.exists():
            return candidate
    # Fall back to repo-root/frontend/nginx.conf relative to backend/tests/<file>.
    return here.parents[2] / "frontend" / "nginx.conf"


NGINX_CONF = _find_nginx_conf()


def _require_conf():
    if not NGINX_CONF.exists():
        pytest.skip(f"nginx.conf not found at {NGINX_CONF}")


def _location_body(text: str, header_regex: str) -> str:
    """Return the body of the first `location <...> { ... }` block whose header
    line matches header_regex. Brace-balanced extraction (the blocks are flat —
    no nested braces inside these location bodies)."""
    m = re.search(header_regex + r"\s*\{", text)
    assert m, f"no location block matching {header_regex!r}"
    start = m.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[start : i - 1]


# ---------------------------------------------------------------------------
# The scrubbed log_format must exist and omit the query string
# ---------------------------------------------------------------------------


def test_scrubbed_log_format_defined_and_omits_args():
    _require_conf()
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "log_format scrubbed" in text, "the 'scrubbed' log_format must be defined"
    # The format must log $uri (path, no query) and must NOT reference $args/$query_string
    # or $request (which embeds the full path+query incl. ?_t=<JWT>).
    fmt_block = text.split("log_format scrubbed", 1)[1].split(";", 1)[0]
    assert "$uri" in fmt_block, "scrubbed format must log $uri (path only)"
    assert "$args" not in fmt_block, "scrubbed format must NOT log $args (would leak ?_t=)"
    assert "$query_string" not in fmt_block, "scrubbed format must NOT log $query_string"
    # Must NOT log the full $request (path+query) or $request_uri (path+query).
    # NOTE: $request_method (the bare verb) is fine — match the variable precisely so
    # we don't false-positive on $request_method.
    leaky_vars = re.findall(r"\$request(?:_uri)?\b", fmt_block)
    # $request_method ends in '_method', so \b after 'request' won't match it.
    assert not leaky_vars, (
        f"scrubbed format must NOT log $request/$request_uri (embeds query): {leaky_vars}"
    )


# ---------------------------------------------------------------------------
# Token-bearing blocks MUST scrub (current security guarantee — hard gate)
# ---------------------------------------------------------------------------


def test_sse_feed_block_uses_scrubbed_log():
    _require_conf()
    text = NGINX_CONF.read_text(encoding="utf-8")
    body = _location_body(text, r"location\s*=\s*/api/tasks/feed")
    assert re.search(r"access_log\s+\S+\s+scrubbed\s*;", body), (
        "/api/tasks/feed serves ?_t=<JWT> via EventSource — it MUST scrub the access log"
    )


def test_storage_block_uses_scrubbed_log():
    _require_conf()
    text = NGINX_CONF.read_text(encoding="utf-8")
    body = _location_body(text, r"location\s+/api/storage/")
    assert re.search(r"access_log\s+\S+\s+scrubbed\s*;", body), (
        "/api/storage/ serves ?_t=<JWT> via <img> tags — it MUST scrub the access log"
    )


# ---------------------------------------------------------------------------
# L6 — parent-block scrubbing (defense-in-depth). Advisory until implemented.
# ---------------------------------------------------------------------------


def test_l6_api_parent_block_scrubs_advisory():
    """L6 AC: add `access_log ... scrubbed;` to the parent /api/ block.
    EXPECTED to xfail until L6 lands (nginx.conf not modified this round)."""
    _require_conf()
    text = NGINX_CONF.read_text(encoding="utf-8")
    body = _location_body(text, r"location\s+/api/")
    if not re.search(r"access_log\s+\S+\s+scrubbed\s*;", body):
        pytest.xfail("L6 not yet applied: parent /api/ block has no scrubbed access_log")
    assert True


def test_l6_root_block_scrubs_advisory():
    """L6 AC: add `access_log ... scrubbed;` to the catch-all / block.
    EXPECTED to xfail until L6 lands."""
    _require_conf()
    text = NGINX_CONF.read_text(encoding="utf-8")
    body = _location_body(text, r"location\s+/\s")
    if not re.search(r"access_log\s+\S+\s+scrubbed\s*;", body):
        pytest.xfail("L6 not yet applied: catch-all / block has no scrubbed access_log")
    assert True
