"""Authentication utilities — JWT tokens, SSO exchange, FastAPI dependencies."""
import os
import time
import logging

import jwt
import httpx
from fastapi import Request, HTTPException

from database import get_db
from datetime import datetime

logger = logging.getLogger("ai_scientist")

JWT_SECRET = os.environ.get("JWT_SECRET", "aixiv_jwt_s3cr3t_k3y_ch4ng3_m3")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = 60 * 60 * 24 * 7  # 7 days

SSO_REDIRECT_URL = "https://comparegpt.io/sso-redirect"
# Use the CompareGPT-AIScientist backend (running locally on port 9252)
# for SSO token exchange — the same method that cias.comparegpt.io uses.
CIAS_BACKEND_VALIDATE = "http://127.0.0.1:9252/api/user/validate"
# cias.comparegpt.io is whitelisted with CompareGPT SSO
SSO_CALLBACK_URL = os.environ.get("SSO_CALLBACK_URL", "https://cias.comparegpt.io/sso/callback")


def create_jwt(user_id: str, user_name: str = "", role: str = "user") -> str:
    """Create a JWT token for a user."""
    payload = {
        "sub": user_id,
        "name": user_name,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict | None:
    """Verify and decode a JWT token. Returns payload dict or None."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.warning("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT: {e}")
        return None


async def exchange_sso_token(sso_token: str) -> dict | None:
    """Exchange an SSO token using the CompareGPT-AIScientist backend at port 9252.

    This is the same method cias.comparegpt.io uses: call POST /api/user/validate
    with {"sso_token": token}, and the backend handles auth.comparegpt.io validation.

    Returns user info dict with keys: user_id, user_name, api_key, credit, token, role
    or None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                CIAS_BACKEND_VALIDATE,
                json={"sso_token": sso_token},
                headers={"Content-Type": "application/json"},
            )
            logger.info(f"CIAS validate response: {resp.status_code}")
            if resp.status_code == 200:
                body = resp.json()
                # CIAS backend returns:
                # { success: true, access_token: "...", user: {
                #     user_info: {user_id, user_name, role},
                #     balance: {credit, token},
                #     api_key: "..."
                # }}
                user = body.get("user") or {}
                user_info = user.get("user_info") or {}
                balance = user.get("balance") or {}
                return {
                    "user_id": str(user_info.get("user_id", "")),
                    "user_name": user_info.get("user_name", ""),
                    "api_key": user.get("api_key", ""),
                    "credit": balance.get("credit", 0),
                    "token": balance.get("token", 0),
                    "role": user_info.get("role", "user"),
                }
            else:
                logger.error(f"CIAS validate failed: {resp.status_code} {resp.text}")
                return None
    except Exception as e:
        logger.error(f"SSO exchange error: {e}")
        return None


def _get_user_from_db(user_id: str) -> dict | None:
    """Look up a user in the database."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def _extract_jwt_from_request(request: Request) -> str | None:
    """Extract JWT from cookie or Authorization header."""
    # Try cookie first
    token = request.cookies.get("aixiv_token")
    if token:
        return token
    # Try Authorization header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _build_user_dict(payload: dict, db_user: dict | None) -> dict:
    """Build the user context dict used by route handlers."""
    user = {
        "user_id": payload["sub"],
        "user_name": payload.get("name", ""),
        "role": payload.get("role", "user"),
    }
    if db_user:
        user["user_name"] = db_user.get("user_name") or user["user_name"]
        user["role"] = db_user.get("role") or user["role"]
        user["credit"] = db_user.get("credit", 0)
        user["token"] = db_user.get("token", 0)
        # Determine effective API key: custom overrides SSO
        if db_user.get("custom_api_key"):
            user["effective_api_key"] = db_user["custom_api_key"]
            user["effective_provider"] = db_user.get("custom_api_provider", "comparegpt")
        elif db_user.get("api_key"):
            user["effective_api_key"] = db_user["api_key"]
            user["effective_provider"] = "comparegpt"
        else:
            user["effective_api_key"] = None
            user["effective_provider"] = None
    else:
        user["effective_api_key"] = None
        user["effective_provider"] = None
    return user


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency — requires authentication.

    Returns user dict with effective_api_key and effective_provider.
    Raises 401 if unauthenticated.
    """
    token = _extract_jwt_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    db_user = _get_user_from_db(payload["sub"])
    return _build_user_dict(payload, db_user)


async def get_optional_user(request: Request) -> dict | None:
    """FastAPI dependency — returns user dict if authenticated, None otherwise."""
    token = _extract_jwt_from_request(request)
    if not token:
        return None

    payload = verify_jwt(token)
    if not payload:
        return None

    db_user = _get_user_from_db(payload["sub"])
    return _build_user_dict(payload, db_user)
