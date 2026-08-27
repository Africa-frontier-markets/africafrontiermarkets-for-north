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

import asyncio
import hashlib
import hmac
import json
import secrets
import smtplib
import uuid
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

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
    get_current_user_id,
    hash_password,
    verify_password,
)
from payment_hub.models import User, UserOtpChallenge
from api_gateway.whatsapp import send_authentication_code

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, min_length=2, max_length=2, description="ISO 3166-1 alpha-2")


class OtpRequest(BaseModel):
    email: EmailStr
    identity_consent: bool = Field(..., description="Consent to share the minimum identity attributes with AFM")


class OtpVerifyRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    full_name: str = Field(..., min_length=2, max_length=255)
    whatsapp_phone: str | None = Field(default=None, min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")
    mobile_money_phone: str | None = Field(default=None, min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")
    country: str = Field(..., min_length=2, max_length=2)
    date_of_birth: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    identity_consent: bool = Field(..., description="Consent to share the minimum identity attributes with AFM")


class PhoneOtpRequest(BaseModel):
    whatsapp_phone: str = Field(..., min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")


class PhoneOtpVerifyRequest(BaseModel):
    whatsapp_phone: str = Field(..., min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class IdentityProfileRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=255)
    whatsapp_phone: str = Field(..., min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")
    mobile_money_phone: str | None = Field(default=None, min_length=8, max_length=20, pattern=r"^\+?[0-9][0-9 .-]{7,18}$")
    country: str = Field(..., min_length=2, max_length=2)
    date_of_birth: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    identity_consent: bool = Field(..., description="Consent to share the minimum identity attributes with AFM")


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
    date_of_birth: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    identity_consent: bool = False


def is_valid_mobile_exchange_secret(presented: str | None, configured: str | None) -> bool:
    return bool(presented and configured and hmac.compare_digest(presented, configured))


def _issue_tokens(user_id: uuid.UUID) -> TokenResponse:
    sub = str(user_id)
    return TokenResponse(
        access_token=create_access_token({"sub": sub}),
        refresh_token=create_refresh_token({"sub": sub}),
        user_id=sub,
    )


def _normalize_phone(phone: str) -> str:
    return "+" + "".join(ch for ch in phone if ch.isdigit()).lstrip("+")


def _otp_digest(purpose: str, target: str, code: str) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    return hmac.new(secret, f"{purpose}:{target}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def _send_otp_sync(to_email: str, code: str) -> None:
    settings = get_settings()
    if not all((settings.smtp_host, settings.smtp_username, settings.smtp_password, settings.smtp_from_email)):
        raise RuntimeError("Email OTP delivery is not configured")
    message = EmailMessage()
    message["Subject"] = "Votre code de vérification AFM"
    message["From"] = settings.smtp_from_email
    message["To"] = to_email
    message.set_content(
        "Votre code de vérification Africa Frontier Markets est valable 10 minutes. "
        "Ne le partagez avec personne. Si vous n’êtes pas à l’origine de cette demande, ignorez cet e-mail."
        f"\\n\\nCode : {code}"
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


async def _send_otp(to_email: str, code: str) -> None:
    await asyncio.to_thread(_send_otp_sync, to_email, code)


def _send_sms_sync(to_phone: str, code: str) -> None:
    settings = get_settings()
    if not all((settings.sms_api_url, settings.sms_api_token, settings.sms_sender)):
        raise RuntimeError("Phone OTP delivery is not configured")
    body = json.dumps({
        "to": to_phone,
        "from": settings.sms_sender,
        "message": f"Votre code AFM est {code}. Il expire dans {settings.otp_expire_minutes} minutes.",
    }).encode("utf-8")
    req = urlrequest.Request(
        settings.sms_api_url,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.sms_api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError("Phone OTP provider rejected the request")


async def _send_sms(to_phone: str, code: str) -> None:
    await asyncio.to_thread(_send_sms_sync, to_phone, code)


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


@router.post("/otp/request")
async def request_signup_otp(payload: OtpRequest, db: AsyncSession = Depends(get_db)):
    if not payload.identity_consent:
        raise HTTPException(status_code=400, detail="Identity sharing consent is required")
    email = str(payload.email).strip().lower()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    recent = await db.execute(
        select(UserOtpChallenge).where(
            UserOtpChallenge.email == email,
            UserOtpChallenge.purpose == "signup",
            UserOtpChallenge.created_at >= cutoff,
        )
    )
    if recent.scalars().first() is not None:
        raise HTTPException(status_code=429, detail="Please wait before requesting another code")

    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = UserOtpChallenge(
        email=email,
        purpose="signup",
        code_digest=_otp_digest("signup", email, code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=get_settings().otp_expire_minutes),
    )
    db.add(challenge)
    await db.commit()
    try:
        await _send_otp(email, code)
    except Exception as exc:
        challenge.consumed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=503, detail="Email verification is temporarily unavailable") from exc

    response = {"status": "sent", "expires_in_seconds": get_settings().otp_expire_minutes * 60}
    if get_settings().otp_expose_dev_code and not get_settings().is_production:
        response["dev_code"] = code
    return response


@router.post("/otp/verify", response_model=TokenResponse)
async def verify_signup_otp(payload: OtpVerifyRequest, db: AsyncSession = Depends(get_db)):
    email = str(payload.email).strip().lower()
    result = await db.execute(
        select(UserOtpChallenge)
        .where(
            UserOtpChallenge.email == email,
            UserOtpChallenge.purpose == "signup",
            UserOtpChallenge.consumed_at.is_(None),
        )
        .order_by(UserOtpChallenge.created_at.desc())
    )
    challenge = result.scalars().first()
    now = datetime.now(timezone.utc)
    if challenge is None or challenge.expires_at <= now:
        raise HTTPException(status_code=400, detail="Code expired or invalid")
    if challenge.attempts >= get_settings().otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many verification attempts")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_digest, _otp_digest("signup", email, payload.code)):
        await db.commit()
        raise HTTPException(status_code=400, detail="Code expired or invalid")

    if not payload.identity_consent:
        await db.commit()
        raise HTTPException(status_code=400, detail="Identity sharing consent is required")
    challenge.consumed_at = now
    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    normalized_whatsapp = _normalize_phone(payload.whatsapp_phone) if payload.whatsapp_phone else None
    normalized_mobile_money = _normalize_phone(payload.mobile_money_phone) if payload.mobile_money_phone else None
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=payload.full_name.strip(),
            phone=normalized_whatsapp,
            whatsapp_phone=normalized_whatsapp,
            mobile_money_phone=normalized_mobile_money,
            country=payload.country.upper(),
            date_of_birth=payload.date_of_birth,
            email_verified_at=now,
            identity_consent_at=now,
            is_active="1",
            kyc_status="pending",
        )
        db.add(user)
    elif user.is_active != "1":
        raise AuthenticationError("Account is disabled")
    else:
        user.full_name = payload.full_name.strip()
        user.phone = normalized_whatsapp
        user.whatsapp_phone = normalized_whatsapp
        user.mobile_money_phone = normalized_mobile_money
        user.country = payload.country.upper()
        user.date_of_birth = payload.date_of_birth
        user.email_verified_at = now
        user.identity_consent_at = now
        user.kyc_status = "pending"
    await db.commit()
    await db.refresh(user)
    return _issue_tokens(user.id)


@router.post("/phone-otp/request")
async def request_phone_otp(
    payload: PhoneOtpRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    phone = _normalize_phone(payload.whatsapp_phone)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.email_verified_at:
        raise AuthenticationError("Email verification is required")
    if _normalize_phone(user.whatsapp_phone or user.phone or "") != phone:
        raise HTTPException(status_code=422, detail="WhatsApp number does not match the signup profile")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
    recent = await db.execute(select(UserOtpChallenge).where(
        UserOtpChallenge.email == user.email,
        UserOtpChallenge.phone == phone,
        UserOtpChallenge.purpose == "phone_verify",
        UserOtpChallenge.created_at >= cutoff,
    ))
    if recent.scalars().first() is not None:
        raise HTTPException(status_code=429, detail="Please wait before requesting another code")
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = UserOtpChallenge(
        email=user.email,
        phone=phone,
        purpose="phone_verify",
        code_digest=_otp_digest("phone_verify", phone, code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=get_settings().otp_expire_minutes),
    )
    db.add(challenge)
    await db.commit()
    try:
        await send_authentication_code(phone, code)
    except Exception as exc:
        challenge.consumed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=503, detail="Phone verification is temporarily unavailable") from exc
    response = {"status": "sent", "expires_in_seconds": get_settings().otp_expire_minutes * 60}
    if get_settings().otp_expose_dev_code and not get_settings().is_production:
        response["dev_code"] = code
    return response


@router.post("/phone-otp/verify")
async def verify_phone_otp(
    payload: PhoneOtpVerifyRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    phone = _normalize_phone(payload.whatsapp_phone)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or _normalize_phone(user.whatsapp_phone or user.phone or "") != phone:
        raise HTTPException(status_code=422, detail="WhatsApp number does not match the signup profile")
    result = await db.execute(select(UserOtpChallenge).where(
        UserOtpChallenge.email == user.email,
        UserOtpChallenge.phone == phone,
        UserOtpChallenge.purpose == "phone_verify",
        UserOtpChallenge.consumed_at.is_(None),
    ).order_by(UserOtpChallenge.created_at.desc()))
    challenge = result.scalars().first()
    now = datetime.now(timezone.utc)
    if challenge is None or challenge.expires_at <= now:
        raise HTTPException(status_code=400, detail="Code expired or invalid")
    if challenge.attempts >= get_settings().otp_max_attempts:
        raise HTTPException(status_code=429, detail="Too many verification attempts")
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_digest, _otp_digest("phone_verify", phone, payload.code)):
        await db.commit()
        raise HTTPException(status_code=400, detail="Code expired or invalid")
    challenge.consumed_at = now
    user.phone_verified_at = now
    user.kyc_status = "verified" if user.email_verified_at else "pending"
    await db.commit()
    return {"status": "verified", "identity_status": user.kyc_status, "whatsapp_phone_verified": True, "mobile_money_phone_verified": False}


@router.get("/me/kyc")
async def get_kyc(user_id: uuid.UUID = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("User not found")
    return {
        "status": "verified" if user.email_verified_at and user.phone_verified_at else "pending",
        "documents_collected": False,
        "email_verified": bool(user.email_verified_at),
        "phone_verified": bool(user.phone_verified_at),
    }


@router.post("/me/kyc", status_code=202)
async def submit_kyc(
    payload: IdentityProfileRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    if not payload.identity_consent:
        raise HTTPException(status_code=400, detail="Identity sharing consent is required")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise AuthenticationError("User not found")
    user.full_name = payload.full_name
    if payload.whatsapp_phone:
        user.whatsapp_phone = _normalize_phone(payload.whatsapp_phone)
        user.phone = user.whatsapp_phone
    if payload.mobile_money_phone:
        user.mobile_money_phone = _normalize_phone(payload.mobile_money_phone)
    user.country = payload.country.upper()
    user.date_of_birth = payload.date_of_birth
    user.identity_consent_at = datetime.now(timezone.utc)
    user.kyc_status = "verified" if user.email_verified_at and user.phone_verified_at else "pending"
    await db.commit()
    return {
        "status": user.kyc_status,
        "documents_collected": False,
        "message": "Identity profile saved; phone OTP verification remains required before transfers",
    }


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
                date_of_birth=payload.date_of_birth,
                email_verified_at=datetime.now(timezone.utc),
                identity_consent_at=datetime.now(timezone.utc) if payload.identity_consent else None,
                is_active="1",
            )
            db.add(user)
        else:
            user.oauth_subject = payload.oauth_subject
            if payload.full_name:
                user.full_name = payload.full_name
            if payload.date_of_birth:
                user.date_of_birth = payload.date_of_birth
            user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
            if payload.identity_consent:
                user.identity_consent_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

    if user.is_active != "1":
        raise AuthenticationError("Account is disabled")
    if not user.email_verified_at:
        user.email_verified_at = datetime.now(timezone.utc)
        await db.commit()

    return AccessTokenResponse(access_token=create_access_token({"sub": str(user.id)}))
