#!/usr/bin/env python3
"""Project Seraphim - production smoke test.

Runs a set of read-only (and optionally one write) checks against a RUNNING
Seraphim stack over HTTP. Intended as the automated half of the go-live gate
documented in docs/PRODUCTION_RUNBOOK.md.

It uses only the Python standard library, falling back from `httpx` to
`urllib.request` if httpx is not importable, so it can be dropped onto the
Unraid host (or any box that can reach the stack) and run with bare `python3`.

Usage
-----
  # Health + auth + read-only checks against a local dev stack:
  python3 scripts/smoke_test.py \
      --base-url http://localhost:8000 \
      --email admin@example.com --password 'YourPassw0rd!'

  # Against the production frontend proxy (nginx/Caddy strips /api):
  python3 scripts/smoke_test.py \
      --base-url https://attendance.example.org/api \
      --email admin@example.org --password 'YourPassw0rd!'

  # Include the optional write check (uploads a synthetic face image):
  python3 scripts/smoke_test.py --write \
      --base-url http://localhost:8000 \
      --email admin@example.com --password 'YourPassw0rd!'

  # Everything can also come from the environment:
  BASE_URL=http://localhost:8000 SMOKE_EMAIL=admin@example.com \
      SMOKE_PASSWORD='YourPassw0rd!' python3 scripts/smoke_test.py

Environment variables (CLI flags take precedence):
  BASE_URL        Base URL of the API (default: http://localhost:8000)
  SMOKE_EMAIL     Admin email for login
  SMOKE_PASSWORD  Admin password for login

Exit status:
  0  all checks passed (warnings allowed)
  1  one or more checks failed, or login was required but did not succeed

Notes:
  * --base-url should point at the FastAPI app. Routers are registered WITHOUT
    an /api prefix, but the nginx/Caddy production proxy expects /api/... and
    strips it - so when hitting the public site, include the trailing /api.
  * `--help` prints this usage and exits 0 without touching the network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional, Tuple

# ---------------------------------------------------------------------------
# HTTP backend: prefer httpx, fall back to urllib. Both are wrapped in a tiny
# uniform interface that returns (status_code, parsed_json_or_None, raw_text).
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import-time branch
    import httpx  # type: ignore

    _HAVE_HTTPX = True
except Exception:  # pragma: no cover - import-time branch
    httpx = None  # type: ignore
    _HAVE_HTTPX = False

import urllib.error
import urllib.request

# Per-request timeout in seconds. Overridden by --timeout in main().
_TIMEOUT: float = 15.0


class HttpError(Exception):
    """Raised for transport-level failures (connection refused, DNS, timeout)."""


def _parse_body(raw: bytes, content_type: str) -> Tuple[Optional[Any], str]:
    text = ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = "<undecodable body>"
    parsed: Optional[Any] = None
    if "json" in (content_type or "").lower() or text[:1] in ("{", "["):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
    return parsed, text


def http_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json_body: Optional[dict] = None,
    multipart: Optional[Tuple[str, str, bytes, str]] = None,
    timeout: Optional[float] = None,
) -> Tuple[int, Optional[Any], str]:
    """Perform an HTTP request and return (status, parsed_json|None, raw_text).

    `multipart` is (field_name, filename, content_bytes, content_type) for a
    single-file upload. Transport errors raise HttpError.
    """
    if timeout is None:
        timeout = _TIMEOUT
    headers = dict(headers or {})

    if _HAVE_HTTPX:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                kwargs: dict[str, Any] = {"headers": headers}
                if json_body is not None:
                    kwargs["json"] = json_body
                if multipart is not None:
                    field, filename, content, ctype = multipart
                    kwargs["files"] = {field: (filename, content, ctype)}
                resp = client.request(method, url, **kwargs)
                ctype = resp.headers.get("content-type", "")
                parsed, text = _parse_body(resp.content, ctype)
                return resp.status_code, parsed, text
        except httpx.HTTPError as exc:  # connect/read/timeout etc.
            raise HttpError(str(exc)) from exc

    # --- urllib fallback ---------------------------------------------------
    data: Optional[bytes] = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif multipart is not None:
        field, filename, content, ctype = multipart
        boundary = "----seraphimsmoke7c3a9f1e"
        crlf = b"\r\n"
        body = b"".join(
            [
                ("--" + boundary).encode() + crlf,
                (
                    f'Content-Disposition: form-data; name="{field}"; '
                    f'filename="{filename}"'
                ).encode()
                + crlf,
                f"Content-Type: {ctype}".encode() + crlf + crlf,
                content,
                crlf,
                ("--" + boundary + "--").encode() + crlf,
            ]
        )
        data = body
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            raw = resp.read()
            ctype = resp.headers.get("content-type", "")
            parsed, text = _parse_body(raw, ctype)
            return resp.getcode(), parsed, text
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        ctype = exc.headers.get("content-type", "") if exc.headers else ""
        parsed, text = _parse_body(raw, ctype)
        return exc.code, parsed, text
    except urllib.error.URLError as exc:
        raise HttpError(str(getattr(exc, "reason", exc))) from exc
    except Exception as exc:  # socket timeout, etc.
        raise HttpError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Result tracking + pretty printing
# ---------------------------------------------------------------------------


def _stdout_supports_unicode() -> bool:
    """True if stdout can encode the check-mark glyphs (UTF-8 etc.)."""
    enc = getattr(sys.stdout, "encoding", None) or ""
    try:
        "✓✗".encode(enc)
        return True
    except Exception:
        return False


# Try to make stdout UTF-8 so the nicer glyphs work on hosts that default to a
# legacy code page (e.g. Windows cp1252). Falls back to ASCII markers if not.
try:  # pragma: no cover - environment dependent
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

if _stdout_supports_unicode():
    PASS = "✓"
    FAIL = "✗"
    WARN = "!"
else:  # ASCII-only console
    PASS = "[PASS]"
    FAIL = "[FAIL]"
    WARN = "[WARN]"


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.warned = 0

    def ok(self, msg: str) -> None:
        self.passed += 1
        print(f"  {PASS} {msg}")

    def bad(self, msg: str) -> None:
        self.failed += 1
        print(f"  {FAIL} {msg}")

    def warn(self, msg: str) -> None:
        self.warned += 1
        print(f"  {WARN} {msg}")

    @property
    def all_passed(self) -> bool:
        return self.failed == 0


def group(title: str) -> None:
    print(f"\n=== {title} ===")


def _short(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _detail(parsed: Optional[Any], raw: str) -> str:
    """Best-effort extraction of an error detail for messages."""
    if isinstance(parsed, dict):
        for key in ("detail", "message", "error"):
            if key in parsed and parsed[key]:
                return _short(str(parsed[key]))
    return _short(raw)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_health(base: str, res: Results) -> None:
    group("Health")
    try:
        status, parsed, raw = http_request("GET", f"{base}/health")
    except HttpError as exc:
        res.bad(f"GET /health - connection failed: {_short(str(exc))}")
        res.warn("Is the stack up and is --base-url correct? (proxy may need /api)")
        return

    if status != 200:
        res.bad(f"GET /health returned HTTP {status}: {_detail(parsed, raw)}")
        return
    if not isinstance(parsed, dict):
        res.bad("GET /health returned 200 but body was not JSON")
        return

    res.ok(f"GET /health -> 200 (status={parsed.get('status')!r})")
    for comp in ("postgres", "redis", "compreface"):
        if comp not in parsed:
            res.warn(f"/health missing component flag '{comp}'")
            continue
        val = parsed.get(comp)
        if val is True:
            res.ok(f"component {comp}: up")
        else:
            # Compreface is external; report but don't hard-fail the whole run
            # on it (the overall summary still passes if only this warns).
            level = res.bad if comp in ("postgres",) else res.warn
            level(f"component {comp}: down/false")


def do_login(base: str, email: str, password: str, res: Results) -> Optional[str]:
    group("Authentication")
    if not email or not password:
        res.bad("No credentials provided (set --email/--password or SMOKE_EMAIL/SMOKE_PASSWORD)")
        return None
    try:
        status, parsed, raw = http_request(
            "POST",
            f"{base}/auth/login",
            json_body={"email": email, "password": password},
        )
    except HttpError as exc:
        res.bad(f"POST /auth/login - connection failed: {_short(str(exc))}")
        return None

    if status == 401:
        res.bad("POST /auth/login -> 401 (invalid email or password)")
        return None
    if status == 429:
        res.bad("POST /auth/login -> 429 (rate limited; wait a minute and retry)")
        return None
    if status != 200:
        res.bad(f"POST /auth/login -> HTTP {status}: {_detail(parsed, raw)}")
        return None
    if not isinstance(parsed, dict) or not parsed.get("access_token"):
        res.bad("POST /auth/login -> 200 but no access_token in response")
        return None

    res.ok(f"POST /auth/login -> 200 (token_type={parsed.get('token_type')!r})")
    return str(parsed["access_token"])


def check_active_event(base: str, headers: dict, res: Results) -> None:
    group("Active event")
    try:
        status, parsed, raw = http_request(
            "GET", f"{base}/events/active-event-id", headers=headers
        )
    except HttpError as exc:
        res.bad(f"GET /events/active-event-id - connection failed: {_short(str(exc))}")
        return
    if status != 200 or not isinstance(parsed, dict):
        res.bad(f"GET /events/active-event-id -> HTTP {status}: {_detail(parsed, raw)}")
        return
    active = parsed.get("active_event_id")
    if active is None:
        res.warn(
            "No active event set - tier-100 detections will NOT be logged to "
            "attendance. Set one via POST /events/set-active before go-live."
        )
    else:
        res.ok(f"Active event is set (active_event_id={active})")


def _check_list_endpoint(
    base: str,
    path: str,
    headers: dict,
    res: Results,
    *,
    shape: str,
    items_key: Optional[str] = None,
) -> None:
    """Generic read-only check. `shape` is 'list' or 'object'."""
    try:
        status, parsed, raw = http_request("GET", f"{base}{path}", headers=headers)
    except HttpError as exc:
        res.bad(f"GET {path} - connection failed: {_short(str(exc))}")
        return
    if status == 403:
        res.bad(f"GET {path} -> 403 (the smoke account lacks admin rights for this endpoint)")
        return
    if status != 200:
        res.bad(f"GET {path} -> HTTP {status}: {_detail(parsed, raw)}")
        return

    if shape == "list":
        if isinstance(parsed, list):
            res.ok(f"GET {path} -> 200 (list, {len(parsed)} item(s))")
        else:
            res.bad(f"GET {path} -> 200 but body was not a JSON array")
    else:  # object
        if not isinstance(parsed, dict):
            res.bad(f"GET {path} -> 200 but body was not a JSON object")
            return
        if items_key is not None and items_key not in parsed:
            res.bad(f"GET {path} -> 200 but missing '{items_key}' key")
            return
        extra = ""
        if items_key and isinstance(parsed.get(items_key), list):
            extra = f", {len(parsed[items_key])} item(s)"
        res.ok(f"GET {path} -> 200 (object{extra})")


def check_readonly(base: str, headers: dict, res: Results) -> None:
    group("Read-only endpoints")
    # /members -> JSON list (limit clamped server-side); request a small page.
    _check_list_endpoint(base, "/members?limit=5", headers, res, shape="list")
    # /tasks -> PaginatedTaskResponse object with an `items` array.
    _check_list_endpoint(
        base, "/tasks?page_size=5", headers, res, shape="object", items_key="items"
    )
    # /attendance -> JSON list of records.
    _check_list_endpoint(base, "/attendance?limit=5", headers, res, shape="list")
    # /health/queue -> object exposing safe_mode + saturation flags.
    group("Queue health")
    try:
        status, parsed, raw = http_request(
            "GET", f"{base}/health/queue", headers=headers
        )
    except HttpError as exc:
        res.bad(f"GET /health/queue - connection failed: {_short(str(exc))}")
        return
    if status != 200 or not isinstance(parsed, dict):
        res.bad(f"GET /health/queue -> HTTP {status}: {_detail(parsed, raw)}")
        return
    missing = [k for k in ("pending_count", "safe_mode", "saturated") if k not in parsed]
    if missing:
        res.bad(f"GET /health/queue -> 200 but missing keys: {', '.join(missing)}")
        return
    res.ok(
        f"GET /health/queue -> 200 (pending={parsed.get('pending_count')}, "
        f"safe_mode={parsed.get('safe_mode')}, saturated={parsed.get('saturated')})"
    )
    if parsed.get("safe_mode"):
        res.warn("Safe mode is ON - recognition is paused; no new tasks will be created.")
    if parsed.get("saturated"):
        res.warn("Queue is saturated - camera ingestion is paused until it drains.")


# A tiny valid baseline JPEG (64x64, high-variance checkerboard) used when
# numpy/cv2 are unavailable. Stored as base64 so it cannot suffer odd-length
# hex issues. CompreFace will likely find no face in it - that is fine: the
# write check asserts on response *shape*, not on detections.
_FALLBACK_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkz"
    "ODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/wAALCABAAEABAREA/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1Fh"
    "ByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZ"
    "WmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/AO/rwCvf68Ar3+vAK9/r"
    "wCivf68Ar3+vAK9/rwCvf6K8Ar3+vAK9/rwCvf68Aor3+vAK9/rwCvf68Ar3+ivAK9/rwCvf68Ar"
    "3+vAKK9/rwCvf68Ar3+vAK9/orwCvf68Ar3+vAK9/rwCivf68Ar3+vAK9/rwCvf6/9k="
)


def _fallback_jpeg() -> bytes:
    import base64

    return base64.b64decode(_FALLBACK_JPEG_B64)


def _build_test_image() -> Tuple[bytes, str]:
    """Return (jpeg_bytes, how) for the write check.

    Prefer a high-variance checkerboard rendered with numpy + cv2; otherwise
    fall back to a small prebuilt JPEG byte string.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        size = 256
        tile = 16
        board = np.zeros((size, size, 3), dtype="uint8")
        for y in range(0, size, tile):
            for x in range(0, size, tile):
                if ((x // tile) + (y // tile)) % 2 == 0:
                    board[y : y + tile, x : x + tile] = (255, 255, 255)
        # Add a coloured stripe so it is not a pure 2-colour image.
        board[:, : size // 3, 0] = 200
        ok, buf = cv2.imencode(".jpg", board, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            return buf.tobytes(), "numpy+cv2 checkerboard"
    except Exception:
        pass
    return _fallback_jpeg(), "prebuilt fallback JPEG"


def check_write(base: str, headers: dict, res: Results) -> None:
    group("Write check (--write): POST /uploads/faces")
    image, how = _build_test_image()
    res.ok(f"Built synthetic image ({how}, {len(image)} bytes)")
    try:
        status, parsed, raw = http_request(
            "POST",
            f"{base}/uploads/faces",
            headers=headers,
            multipart=("file", "smoke.jpg", image, "image/jpeg"),
        )
    except HttpError as exc:
        res.bad(f"POST /uploads/faces - connection failed: {_short(str(exc))}")
        return

    if status == 403:
        res.bad("POST /uploads/faces -> 403 (write check requires an ADMIN account)")
        return
    if status == 502:
        res.warn(
            "POST /uploads/faces -> 502 (CompreFace unreachable). Endpoint and auth "
            "are wired; fix CompreFace URL/key, then re-run --write."
        )
        return
    if status != 200:
        res.bad(f"POST /uploads/faces -> HTTP {status}: {_detail(parsed, raw)}")
        return
    if not isinstance(parsed, dict):
        res.bad("POST /uploads/faces -> 200 but body was not JSON")
        return

    expected = {
        "faces_detected",
        "quality_passed",
        "tasks_created",
        "auto_logged",
        "skipped",
        "deduplicated",
    }
    missing = expected - set(parsed.keys())
    if missing:
        res.bad(f"POST /uploads/faces -> 200 but missing keys: {', '.join(sorted(missing))}")
        return
    res.ok(
        "POST /uploads/faces -> 200 with expected shape "
        f"(faces_detected={parsed.get('faces_detected')}, "
        f"tasks_created={parsed.get('tasks_created')}, "
        f"auto_logged={parsed.get('auto_logged')})"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smoke_test.py",
        description="Automated smoke test for a running Project Seraphim stack.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/smoke_test.py --base-url http://localhost:8000 \\\n"
            "      --email admin@example.com --password 'YourPassw0rd!'\n\n"
            "  python3 scripts/smoke_test.py --write \\\n"
            "      --base-url https://attendance.example.org/api \\\n"
            "      --email admin@example.org --password 'YourPassw0rd!'\n"
        ),
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL", "http://localhost:8000"),
        help="Base URL of the API (default: %(default)s; behind nginx/Caddy add /api).",
    )
    p.add_argument(
        "--email",
        default=os.environ.get("SMOKE_EMAIL"),
        help="Admin email for login (or SMOKE_EMAIL).",
    )
    p.add_argument(
        "--password",
        default=os.environ.get("SMOKE_PASSWORD"),
        help="Admin password for login (or SMOKE_PASSWORD).",
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="Run the optional write check (uploads a synthetic face image).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    return p


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    global _TIMEOUT
    _TIMEOUT = args.timeout

    base = args.base_url.rstrip("/")

    print("Project Seraphim - smoke test")
    print(f"Base URL : {base}")
    print(f"HTTP lib : {'httpx' if _HAVE_HTTPX else 'urllib (stdlib fallback)'}")
    print(f"Write    : {'ENABLED' if args.write else 'disabled (use --write to enable)'}")

    res = Results()

    # 1) Health (no auth required).
    check_health(base, res)

    # 2) Login -> bearer token for everything else.
    token = do_login(base, args.email or "", args.password or "", res)

    if token:
        headers = {"Authorization": f"Bearer {token}"}
        # 3) Active event (warn-only if unset).
        check_active_event(base, headers, res)
        # 4) Read-only endpoints.
        check_readonly(base, headers, res)
        # 5) Optional write check.
        if args.write:
            check_write(base, headers, res)
        else:
            group("Write check")
            res.warn("Skipped (pass --write to exercise POST /uploads/faces).")
    else:
        group("Authenticated checks")
        res.bad("Skipped - login did not yield a token (see Authentication above).")

    # Summary.
    print("\n" + "=" * 48)
    print(
        f"Summary: {res.passed} passed, {res.failed} failed, {res.warned} warning(s)"
    )
    if res.all_passed:
        print(f"{PASS} OVERALL: PASS")
        return 0
    print(f"{FAIL} OVERALL: FAIL")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
