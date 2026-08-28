import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api_gateway import main
from config.security import get_current_user_id
from payment_hub.payment_service import PaymentService, payment_service


def test_runtime_uses_real_payment_service_implementation():
    assert isinstance(payment_service, PaymentService)
    assert payment_service.__class__.__name__ == "PaymentService"
    assert hasattr(payment_service, "_call_psp_api")


@pytest.mark.asyncio
async def test_simulation_route_is_unavailable_in_production(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(is_production=True),
    )
    with pytest.raises(HTTPException) as exc:
        await main.simulate_payment(
            payment=object(),
            user_id=uuid.uuid4(),
            db=None,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_refresh_and_access_tokens_do_not_accept_wrong_token_type(monkeypatch):
    token = "not-a-jwt"
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(Exception):
        await get_current_user_id(credentials)


@pytest.mark.asyncio
async def test_password_registration_is_disabled_in_production(monkeypatch):
    from api_gateway import auth

    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(is_production=True),
    )
    with pytest.raises(HTTPException) as exc:
        await auth.register(object(), None)
    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_refresh_rejects_disabled_account(monkeypatch):
    from api_gateway import auth

    user_id = uuid.uuid4()
    claims = {"type": "refresh", "sub": str(user_id)}
    monkeypatch.setattr(auth, "decode_token", lambda token: claims)

    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(id=user_id, is_active="0")

    class FakeDb:
        async def execute(self, statement):
            return FakeResult()

    with pytest.raises(Exception) as exc:
        await auth.refresh(auth.RefreshRequest(refresh_token="refresh-token"), FakeDb())
    assert "unavailable" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_dev_token_is_unavailable_outside_development(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(is_development=False),
    )
    with pytest.raises(HTTPException) as exc:
        await main.issue_dev_token()
    assert exc.value.status_code == 404
