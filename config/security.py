"""Production security: JWT with short expiration, bcrypt, HMAC verification."""
"""
AFM Security — JWT, bcrypt, HMAC, API keys
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from config.config import get_settings
from config.exceptions import AuthenticationError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc), "type": "access"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc), "type": "refresh"})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise AuthenticationError("Invalid token")


def generate_api_key() -> str:
    return f"afm_live_{secrets.token_urlsafe(32)}"


def generate_api_secret() -> str:
    return secrets.token_urlsafe(48)


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def hash_idempotency_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# 🔴 FIX: real authentication dependency.
# api_gateway/main.py used to hardcode `user_id = "user_demo_001"`, which is
# not a valid UUID and does not correspond to any row in `users`, so every
# payment call failed against a real Postgres DB (invalid UUID / FK
# violation) and, worse, was not actually protected by auth at all.
# This dependency decodes the bearer JWT and returns the authenticated
# user's UUID, raising AuthenticationError if missing/invalid.
# ─────────────────────────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> uuid.UUID:
    if credentials is None:
        raise AuthenticationError("Missing bearer token")

    payload = decode_token(credentials.credentials)

    if payload.get("type") != "access":
        raise AuthenticationError("Invalid token type")

    sub = payload.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject claim")

    try:
        return uuid.UUID(str(sub))
    except ValueError:
        raise AuthenticationError("Token subject is not a valid user id")
