import os

os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/afm")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from api_gateway.auth import IdentityProfileRequest, OtpVerifyRequest, _normalize_phone, _otp_digest
from api_gateway.whatsapp import normalize_whatsapp_number


def test_otp_digest_is_deterministic_but_not_plaintext():
    digest = _otp_digest("signup", "user@example.com", "123456")
    assert len(digest) == 64
    assert digest != "123456"
    assert digest == _otp_digest("signup", "user@example.com", "123456")
    assert digest != _otp_digest("signup", "user@example.com", "123457")


def test_otp_schema_requires_six_digits():
    request = OtpVerifyRequest(
        email="user@example.com",
        code="123456",
        full_name="A User",
        whatsapp_phone="+237698054497",
        mobile_money_phone="+23768361360",
        country="CM",
        date_of_birth="1990-01-01",
        identity_consent=True,
    )
    assert request.code == "123456"


def test_identity_profile_contains_no_document_fields():
    request = IdentityProfileRequest(
        full_name="A User",
        whatsapp_phone="+237 698 054 497",
        mobile_money_phone="+237 683 613 60",
        country="CM",
        date_of_birth="1990-01-01",
        identity_consent=True,
    )
    assert request.model_dump().keys() == {
        "full_name", "whatsapp_phone", "mobile_money_phone", "country", "date_of_birth", "identity_consent"
    }
    assert _normalize_phone(request.whatsapp_phone) == "+237698054497"
    assert _normalize_phone(request.mobile_money_phone) == "+23768361360"
    assert normalize_whatsapp_number(request.whatsapp_phone) == "237698054497"
    assert normalize_whatsapp_number(request.whatsapp_phone) != _normalize_phone(request.mobile_money_phone)
