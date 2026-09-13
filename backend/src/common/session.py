"""HMAC-signed session cookie management.

Provides signed session cookie creation, validation, and lifecycle
management to prevent client-side session tampering.
"""

import base64
import hashlib
import hmac
import os
import secrets
from functools import lru_cache

from fastapi import Request, Response

from backend.src.common.paths import tmp_dir
from backend.src.common.secrets import get_or_create_secret

SESSION_COOKIE_NAME = "bi_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 180
SESSION_SECRET_FILE = os.path.join(str(tmp_dir()), "secrets", "session.key")


@lru_cache(maxsize=1)
def _get_session_secret() -> bytes:
    """Load or generate secret key used for session signing."""
    return get_or_create_secret(SESSION_SECRET_FILE, env_var="BLOOMINDEX_SESSION_SECRET")


def _sign_session_id(session_id: str) -> str:
    """Compute an unpadded URL-safe base64 HMAC-SHA256 signature."""
    digest = hmac.new(_get_session_secret(), session_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _encode_session_cookie(session_id: str) -> str:
    """Format session identifier and signature into a cookie value."""
    return f"{session_id}.{_sign_session_id(session_id)}"


def _decode_session_cookie(cookie_value: str | None) -> str | None:
    """Validate and extract the session identifier from a signed cookie.

    Uses constant-time comparison to prevent timing side-channel
    attacks on signatures. Returns None if invalid or tampered.
    """
    if not cookie_value or "." not in cookie_value:
        return None

    session_id, signature = cookie_value.rsplit(".", 1)
    if not session_id or not signature:
        return None

    expected = _sign_session_id(session_id)
    # Mitigate timing attacks with constant-time equality check.
    if not hmac.compare_digest(expected, signature):
        return None
    return session_id


def get_session_id(request: Request) -> str | None:
    """Extract and verify session identifier from incoming request."""
    return _decode_session_cookie(request.cookies.get(SESSION_COOKIE_NAME))


def attach_session_cookie(response: Response, request: Request, session_id: str) -> None:
    """Attach signed session cookie with secure browser attributes."""
    # Scope cookie securely and restrict script access via HttpOnly.
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_encode_session_cookie(session_id),
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def get_or_set_session_id(request: Request, response: Response) -> str:
    """Return valid session ID or issue a new signed cookie."""
    session_id = get_session_id(request)
    if session_id:
        return session_id

    session_id = f"sess_{secrets.token_urlsafe(24)}"
    attach_session_cookie(response, request, session_id)
    return session_id
