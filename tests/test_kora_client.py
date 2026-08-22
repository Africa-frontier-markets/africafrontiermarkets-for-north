import os

os.environ["SECRET_KEY"] = "s" * 48
os.environ["DATABASE_URL"] = "postgresql://user:pass@localhost/db"
os.environ["REDIS_URL"] = "redis://localhost:6379"

import pytest
import httpx
from decimal import Decimal

from config.config import Settings
from payment_hub.kora_client import KoraClient, KoraClientError


class FakeAsyncClient:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.request_headers = None
        self.request_json = None
        self.request_url = None

    async def post(self, url: str, headers: dict[str, str], json: dict):
        self.request_headers = headers
        self.request_json = json
        self.request_url = url
        self.response.request = httpx.Request("POST", url)
        return self.response

    async def get(self, url: str, headers: dict[str, str]):
        self.request_headers = headers
        self.response.request = httpx.Request("GET", url)
        return self.response


@pytest.fixture
def settings():
    return Settings(
        secret_key="s" * 48,
        database_url="postgresql://user:pass@localhost/db",
        redis_url="redis://localhost:6379",
        kora_secret_key="sk_live_test-only",
        environment="production",
    )


@pytest.mark.asyncio
async def test_balance_uses_server_side_secret_and_is_read_only(settings):
    response = httpx.Response(200, json={"status": True, "message": "success", "data": {"NGN": {"available_balance": 10}}})
    client = FakeAsyncClient(response)

    result = await KoraClient(settings, client).get_balance()

    assert result["read_only"] is True
    assert result["balances"]["NGN"]["available_balance"] == 10
    assert client.request_headers["Authorization"] == "Bearer sk_live_test-only"
    assert "sk_live_test-only" not in str(result)


@pytest.mark.asyncio
async def test_exchange_rate_uses_read_only_conversion_endpoint(settings):
    response = httpx.Response(200, json={"status": True, "data": {"from_currency": "XOF", "to_currency": "USD", "from_amount": 100000, "to_amount": 166.67, "rate": 0.0016667, "reference": "ref-1", "expiry_in_seconds": 25, "expiry_date": "2026-08-22T15:00:00Z"}})
    client = FakeAsyncClient(response)

    result = await KoraClient(settings, client).get_exchange_rate(
        amount=Decimal("100000"), from_currency="xof", to_currency="usd", reference="ref-1"
    )

    assert result["rate"] == Decimal("0.0016667")
    assert result["to_amount"] == Decimal("166.67")
    assert client.request_url.endswith("/merchant/api/v1/conversions/rates")
    assert client.request_json["from_currency"] == "XOF"
    assert client.request_headers["Authorization"] == "Bearer sk_live_test-only"


@pytest.mark.asyncio
async def test_exchange_rate_rejects_unsuccessful_response(settings):
    client = FakeAsyncClient(httpx.Response(200, json={"status": False, "data": None}))

    with pytest.raises(KoraClientError, match="not successful"):
        await KoraClient(settings, client).get_exchange_rate(
            amount=Decimal("100000"), from_currency="XOF", to_currency="USD", reference="ref-1"
        )


@pytest.mark.asyncio
async def test_balance_rejects_missing_secret(settings):
    settings.kora_secret_key = None
    client = FakeAsyncClient(httpx.Response(200, json={"status": True, "data": {}}))

    with pytest.raises(KoraClientError, match="not configured"):
        await KoraClient(settings, client).get_balance()


@pytest.mark.asyncio
async def test_balance_rejects_unsuccessful_kora_response(settings):
    client = FakeAsyncClient(httpx.Response(200, json={"status": False, "message": "unauthorized", "data": None}))

    with pytest.raises(KoraClientError, match="not successful"):
        await KoraClient(settings, client).get_balance()
