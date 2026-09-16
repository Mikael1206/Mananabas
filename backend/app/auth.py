"""
Google Sign-In + app-issued JWT.

Flow:
  1. Frontend gets a Google ID token via Google Identity Services.
  2. Frontend POSTs it to /api/auth/google (see app/main.py).
  3. Backend verifies the ID token against GOOGLE_CLIENT_ID, upserts a User,
     and issues its own short-lived JWT (create_access_token).
  4. Frontend sends that JWT as `Authorization: Bearer <token>` on every
     subsequent request; get_current_user() decodes it and loads the User.

We issue our own JWT instead of trusting the Google ID token directly on
every request: Google ID tokens expire in ~1 hour and re-verifying them
against Google on every API call would mean an outbound HTTP call per
request. A locally-verified HS256 JWT is cheaper and lets us control
session length (JWT_EXPIRE_MINUTES).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.models import User

_ALGORITHM = "HS256"
_google_request = google_requests.Request()


def verify_google_id_token(credential: str) -> dict:
    """Verify a Google credential (ID token or access token) and return claims
    (sub, email, name, picture). Raises ValueError on invalid credentials."""
    if not settings.google_client_id:
        raise ValueError("GOOGLE_CLIENT_ID is not configured on the server.")

    # 1. Try verifying as Google ID Token (JWT)
    try:
        return google_id_token.verify_oauth2_token(
            credential, _google_request, audience=settings.google_client_id
        )
    except Exception:
        pass

    # 2. Try verifying as Google Access Token via Google userinfo API
    try:
        import httpx

        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {credential}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if "sub" in data:
                return data
    except Exception:
        pass

    raise ValueError("Invalid Google credential or access token.")


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> int:
    """Return the user_id encoded in `token`. Raises jwt.PyJWTError on any
    invalid/expired token — callers should turn that into a 401."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    return int(payload["sub"])


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    return token


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    """FastAPI dependency: resolves the caller's User from the
    `Authorization: Bearer <token>` header, or raises 401."""
    token = _extract_bearer_token(authorization)
    try:
        user_id = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    return user
