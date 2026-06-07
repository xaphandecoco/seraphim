import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.config import dynamic_settings, legacy_settings
from app.utils.auth import verify_token
from app.utils.token_denylist import is_jti_denied

router = APIRouter(tags=["storage"])

_STORAGE_ROOT = Path(os.environ.get("STORAGE_PATH", legacy_settings.STORAGE_PATH)).resolve()


async def _authenticate(request: Request) -> bool:
    """Authenticate via Bearer header, ?_t= query param, or HttpOnly refresh cookie.

    Bearer and ?_t= reject refresh tokens (require_type="refresh") — a stolen refresh
    cookie must not be replayable as an Authorization header or query param (S1/Design B).
    The HttpOnly refresh cookie path is intentionally permissive: browser <img> tags
    send cookies automatically (same-origin) and cannot set Authorization headers.

    The cookie path additionally honors the JTI denylist: a deny-listed (logged-out or
    rotated) refresh token is rejected here too, bringing /storage/* to revocation parity
    with /auth/refresh. Fails open under REDIS_URL=memory:// (is_jti_denied returns False).
    """
    secret = dynamic_settings.get_jwt_secret()
    if not secret:
        return False

    # 1. Bearer header — access tokens only (block type="refresh")
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        payload = verify_token(auth_header.split(" ", 1)[1], secret, require_type="refresh")
        if payload:
            return True

    # 2. ?_t= query param (EventSource / explicit token) — access tokens only
    token_param = request.query_params.get("_t")
    if token_param:
        payload = verify_token(token_param, secret, require_type="refresh")
        if payload:
            return True

    # 3. HttpOnly refresh cookie (browser <img> tags, same-origin) — any valid token,
    #    but a deny-listed (revoked/rotated) refresh JTI must not read files.
    refresh_tok = request.cookies.get("refresh_token")
    if refresh_tok:
        payload = verify_token(refresh_tok, secret)
        if payload:
            jti = payload.get("jti")
            if jti and await is_jti_denied(jti):
                return False
            return True

    return False


@router.get("/storage/{file_path:path}")
async def serve_storage_file(
    file_path: str,
    request: Request,
):
    """Serve face images with cookie-or-bearer authentication (supports browser <img> tags)."""
    if not await _authenticate(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Strict path containment — prevent path traversal
    requested = (_STORAGE_ROOT / file_path).resolve()
    try:
        requested.relative_to(_STORAGE_ROOT)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    suffix = requested.suffix.lower()
    media_type = (
        "image/jpeg" if suffix in (".jpg", ".jpeg")
        else "image/png" if suffix == ".png"
        else "application/octet-stream"
    )
    return FileResponse(str(requested), media_type=media_type)
