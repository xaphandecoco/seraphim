"""/storage/{path} serving + path-containment tests (QA gap-closing).

test_storage_auth.py exhaustively covers the AUTH gate (Bearer/query reject
refresh, cookie permissive, denylist parity), but every case hits a missing
file → 401/404. The contract §6 SUCCESS path (200 FileResponse with the right
media type) and the §6 `403 Access denied` path-traversal containment branch
had no coverage. Those are security/correctness boundaries on a biometric
face-image endpoint, so they are pinned here.

We write real fixture files into the route's actual resolved storage root
(``app.routers.storage._STORAGE_ROOT``) and clean them up afterward, so the test
exercises the real FileResponse + the real ``requested.relative_to(root)``
containment check.
"""
from pathlib import Path

import pytest
from httpx import AsyncClient

from conftest import make_token
from app.routers import storage as storage_router


@pytest.fixture
def storage_file(request):
    """Create a file under the real storage root; remove it (and empty parents) after.

    Param: (relative_path, bytes). Returns the relative path string to request as
    /storage/<relative_path>.
    """
    rel, content = request.param
    root: Path = storage_router._STORAGE_ROOT
    target = (root / rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    try:
        yield rel
    finally:
        try:
            target.unlink()
        except OSError:
            pass
        # Tidy any dirs we created under the root (best-effort, never touch root).
        parent = target.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()  # only removes if empty
            except OSError:
                break
            parent = parent.parent


def _bearer(admin_user):
    tok = make_token(admin_user.id, admin_user.email, admin_user.role, "Admin")
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# §6 success — 200 FileResponse with correct media type
# ---------------------------------------------------------------------------

# 1x1 JPEG and PNG magic-byte prefixes are enough; the route keys media type off
# the file SUFFIX, not the content, so any bytes suffice.
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_BIN = b"\x00\x01\x02\x03"


@pytest.mark.parametrize(
    "storage_file, expected_type",
    [
        (("face_001.jpg", _JPEG), "image/jpeg"),
        (("face_002.jpeg", _JPEG), "image/jpeg"),
        (("snapshot.png", _PNG), "image/png"),
        (("blob.dat", _BIN), "application/octet-stream"),
        (("nested/dir/face.jpg", _JPEG), "image/jpeg"),
    ],
    indirect=["storage_file"],
)
async def test_serves_existing_file_with_media_type(
    client: AsyncClient, admin_user, storage_file, expected_type
):
    resp = await client.get(f"/storage/{storage_file}", headers=_bearer(admin_user))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].split(";")[0] == expected_type


@pytest.mark.parametrize("storage_file", [(("served.jpg", _JPEG))], indirect=True)
async def test_served_bytes_match_written_bytes(client: AsyncClient, admin_user, storage_file):
    resp = await client.get(f"/storage/{storage_file}", headers=_bearer(admin_user))
    assert resp.status_code == 200
    assert resp.content == _JPEG


# ---------------------------------------------------------------------------
# §6 error — 403 Access denied on path traversal (containment)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        "../secret.txt",
        "../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",  # encoded — Starlette decodes before the route
        "subdir/../../outside.txt",
    ],
)
async def test_path_traversal_is_denied(client: AsyncClient, admin_user, evil):
    """A path escaping the storage root must yield 403 (or 404 if the decoded path
    still resolves inside root but is absent) — never a 200 disclosing a file."""
    resp = await client.get(f"/storage/{evil}", headers=_bearer(admin_user))
    assert resp.status_code in (403, 404), (
        f"path traversal {evil!r} must NOT succeed; got {resp.status_code}"
    )
    assert resp.status_code != 200


async def test_auth_passes_but_missing_file_is_404(client: AsyncClient, admin_user):
    resp = await client.get("/storage/definitely_absent.jpg", headers=_bearer(admin_user))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "File not found"


# ---------------------------------------------------------------------------
# Auth precedence: traversal check happens only AFTER auth (no creds → 401 first)
# ---------------------------------------------------------------------------


async def test_encoded_traversal_without_auth_is_401(client: AsyncClient):
    """Auth runs before path containment. An *encoded* traversal (%2f/%2e not
    collapsed by the client/ASGI layer) reaches the route and hits the auth gate
    first → 401 (never 403/200, which would leak containment/file info)."""
    resp = await client.get("/storage/..%2f..%2fetc%2fpasswd")
    assert resp.status_code == 401


async def test_literal_dot_traversal_does_not_disclose_a_file(client: AsyncClient, admin_user):
    """A literal ``../`` path is normalized away by the client/ASGI URL layer to a
    path outside /storage/* (so it 404s as an unmatched route) — the key guarantee
    is simply that it never returns 200 with file contents, with OR without auth."""
    unauth = await client.get("/storage/../../etc/passwd")
    assert unauth.status_code != 200
    authed = await client.get("/storage/../../etc/passwd", headers=_bearer(admin_user))
    assert authed.status_code != 200
