"""
AFM Auth Router — register / login / refresh.

🔴 FIX (production blocker): the payment endpoints require a real bearer
JWT (see config/security.get_current_user_id), and the only way to obtain
one was POST /dev/token — which is deliberately disabled outside
`environment=development`. That meant the API was completely unusable
once deployed with ENVIRONMENT=production: there was no way for anyone to
get a token. This router closes that gap with a minimal, real
email/password auth flow backed by the existing `users` table.
"""

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import get_db
from config.config import get_settings
from config.exceptions import AuthenticationError, ConflictError, ValidationError
from config.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from payment_hub.models import User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user_id: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MobileOAuthExchangeRequest(BaseModel):
    oauth_subject: str = Field(..., min_length=1, max_length=128)
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)


def is_valid_mobile_exchange_secret(presented: str | None, configured: str | None) -> bool:
    return bool(presented and configured and hmac.compare_digest(presented, configured))


def _issue_tokens(user_id: uuid.UUID) -> TokenResponse:
    sub = str(user_id)
    return TokenResponse(
        access_token=create_access_token({"sub": sub}),
        refresh_token=create_refresh_token({"sub": sub}),
        user_id=sub,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == payload.email))
    if existing.scalar_one_or_none():
        raise ConflictError("An account with this email already exists")

    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        phone=payload.phone,
        country=payload.country.upper() if payload.country else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return _issue_tokens(user.id)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()

    # Constant-shape response whether the email exists or not, to avoid
    # leaking which emails are registered.
    if not user or not user.hashed_password or not verify_password(payload.password, user.hashed_password):
        raise AuthenticationError("Invalid email or password")

    if user.is_active != "1":
        raise AuthenticationError("Account is disabled")

    return _issue_tokens(user.id)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(payload: RefreshRequest):
    claims = decode_token(payload.refresh_token)
    if claims.get("type") != "refresh":
        raise ValidationError("Provided token is not a refresh token")

    sub = claims.get("sub")
    if not sub:
        raise AuthenticationError("Token missing subject claim")

    return AccessTokenResponse(access_token=create_access_token({"sub": sub}))


@router.post("/oauth/mobile/exchange", response_model=AccessTokenResponse)
async def exchange_mobile_oauth(
    payload: MobileOAuthExchangeRequest,
    db: AsyncSession = Depends(get_db),
    exchange_secret: str | None = Header(default=None, alias="X-AFM-Mobile-Exchange-Secret"),
):
    """Issue a short-lived AFM JWT for a Manus OAuth identity validated by the mobile server."""
    configured_secret = get_settings().mobile_oauth_exchange_secret
    if not configured_secret:
        raise HTTPException(status_code=503, detail="Mobile OAuth exchange is not configured")
    if not is_valid_mobile_exchange_secret(exchange_secret, configured_secret):
        raise AuthenticationError("Invalid mobile OAuth exchange credentials")

    result = await db.execute(select(User).where(User.oauth_subject == payload.oauth_subject))
    user = result.scalar_one_or_none()
    if user is None:
        existing_email = await db.execute(select(User).where(User.email == payload.email))
        user = existing_email.scalar_one_or_none()
        if user is not None and user.oauth_subject not in (None, payload.oauth_subject):
            raise ConflictError("Email is already linked to another OAuth identity")
        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=payload.email,
                oauth_subject=payload.oauth_subject,
                full_name=payload.full_name,
                is_active="1",
            )
            db.add(user)
        else:
            user.oauth_subject = payload.oauth_subject
            if payload.full_name:
                user.full_name = payload.full_name
        await db.commit()
        await db.refresh(user)

    if user.is_active != "1":
        raise AuthenticationError("Account is disabled")

    return AccessTokenResponse(access_token=create_access_token({"sub": str(user.id)}))
