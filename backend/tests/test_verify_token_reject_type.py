"""Unit tests for verify_token(..., reject_type=...) — M2 rename (QA gap-closing).

The HTTP-level lockdown tests (test_token_type_lockdown.py, test_storage_auth.py)
exercise the renamed kwarg end-to-end, but the pure function contract was never
unit-tested.  These tests pin the semantics so the rename can't silently regress:

  - reject_type=None  -> any valid token decodes (access OR refresh).
  - reject_type="refresh" -> a refresh token (type="refresh") decodes to None,
    while an access token (no `type` claim) still decodes normally.
  - reject_type matching an arbitrary custom type is honored generically.
  - Invalid signature / expired / malformed tokens always decode to None,
    independent of reject_type.
  - Regression fence: verify_token must NOT accept the legacy `require_type`
    kwarg any more (M2 AC: param renamed, no stragglers), and the public
    signature must expose `reject_type` as a keyword-only parameter.

Maps to AC (story M2):
  "verify_token keyword param renamed require_type -> reject_type ... with
   accurate docstring (rejects the named token type)."
"""
import inspect
from datetime import timedelta

import jwt
import pytest

from conftest import TEST_JWT_SECRET
from app.utils.auth import create_access_token, create_refresh_token, verify_token


# ---------------------------------------------------------------------------
# reject_type=None — permissive (cookie path uses this)
# ---------------------------------------------------------------------------


def test_reject_type_none_accepts_access_token():
    tok = create_access_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=5),
    )
    payload = verify_token(tok, TEST_JWT_SECRET)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["role"] == "admin"
    # access tokens carry no type claim
    assert "type" not in payload


def test_reject_type_none_accepts_refresh_token():
    """With no reject_type, a refresh token is a perfectly valid decode (cookie path)."""
    tok = create_refresh_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(days=7),
    )
    payload = verify_token(tok, TEST_JWT_SECRET)
    assert payload is not None
    assert payload["type"] == "refresh"
    assert "jti" in payload  # refresh tokens carry a jti for the denylist


# ---------------------------------------------------------------------------
# reject_type="refresh" — Bearer / query-param lockdown (S1/Design B)
# ---------------------------------------------------------------------------


def test_reject_type_refresh_blocks_refresh_token():
    tok = create_refresh_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(days=7),
    )
    assert verify_token(tok, TEST_JWT_SECRET, reject_type="refresh") is None


def test_reject_type_refresh_still_allows_access_token():
    """The lockdown must NOT block ordinary access tokens (they have no type claim)."""
    tok = create_access_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "volunteer"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=5),
    )
    payload = verify_token(tok, TEST_JWT_SECRET, reject_type="refresh")
    assert payload is not None
    assert payload["role"] == "volunteer"


def test_reject_type_is_generic_for_any_type_claim():
    """reject_type rejects whatever string it is given, not just 'refresh'."""
    tok = jwt.encode(
        {"sub": "1", "type": "magic-link"}, TEST_JWT_SECRET, algorithm="HS256"
    )
    # Rejected when reject_type matches
    assert verify_token(tok, TEST_JWT_SECRET, reject_type="magic-link") is None
    # Accepted when reject_type does not match
    payload = verify_token(tok, TEST_JWT_SECRET, reject_type="refresh")
    assert payload is not None
    assert payload["type"] == "magic-link"


# ---------------------------------------------------------------------------
# Invalid tokens always fail, regardless of reject_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reject", [None, "refresh"])
def test_wrong_secret_returns_none(reject):
    tok = create_access_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=5),
    )
    assert verify_token(tok, "a-totally-different-secret-3456789012", reject_type=reject) is None


@pytest.mark.parametrize("reject", [None, "refresh"])
def test_expired_token_returns_none(reject):
    tok = create_access_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(seconds=-1),  # already expired
    )
    assert verify_token(tok, TEST_JWT_SECRET, reject_type=reject) is None


@pytest.mark.parametrize("reject", [None, "refresh"])
def test_malformed_token_returns_none(reject):
    assert verify_token("not.a.valid.jwt", TEST_JWT_SECRET, reject_type=reject) is None


# ---------------------------------------------------------------------------
# Regression fence — the old kwarg name must be gone (M2 AC)
# ---------------------------------------------------------------------------


def test_signature_exposes_reject_type_keyword_only():
    sig = inspect.signature(verify_token)
    assert "reject_type" in sig.parameters, "verify_token must expose reject_type"
    param = sig.parameters["reject_type"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        "reject_type must remain keyword-only (defensive against positional misuse)"
    )


def test_legacy_require_type_kwarg_is_rejected():
    """Passing the OLD kwarg name must raise TypeError — proves the rename is complete
    and no caller can accidentally rely on the removed parameter."""
    tok = create_access_token(
        {"sub": "1", "email": "a@lightnc.org", "role": "admin"},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=5),
    )
    with pytest.raises(TypeError):
        verify_token(tok, TEST_JWT_SECRET, require_type="refresh")  # type: ignore[call-arg]
