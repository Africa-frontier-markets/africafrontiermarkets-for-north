from types import SimpleNamespace

import httpx
import pytest

from payment_hub.nexus_client import NexusClient, NexusClientError


def settings(**overrides):
    values = {
        "nexus_secret_key": "test_secret_key",
        "nexus_api_base_url": "https://api.dev.neero.io/payment-gateway/api/v1",
        "nexus_allow_transaction_writes": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_nexus_uses_basic_auth_and_lists_methods():
    seen = {}

    async def handler(request: httpx.Request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await NexusClient(settings(), client).list_payment_methods()
    finally:
        await client.aclose()

    assert result == []
    assert seen["auth"].startswith("Basic ")


@pytest.mark.asyncio
async def test_cash_out_is_fail_closed_without_write_flag():
    with pytest.raises(NexusClientError, match="writes are disabled"):
        await NexusClient(settings()).create_cash_out(
            amount=500,
            currency_code="XAF",
            platform_code="pltf_test",
            external_transaction_id="afm-test-1",
            payment_type="MTN_MONEY_TRANSFER",
            source_payment_method_id="source",
            destination_payment_method_id="destination",
            idempotency_key="idem-1",
        )


@pytest.mark.asyncio
async def test_cash_out_requires_idempotency_key_when_enabled():
    with pytest.raises(NexusClientError, match="idempotency key"):
        await NexusClient(settings(nexus_allow_transaction_writes=True)).create_cash_out(
            amount=500,
            currency_code="XAF",
            platform_code="pltf_test",
            external_transaction_id="afm-test-1",
            payment_type="MTN_MONEY_TRANSFER",
            source_payment_method_id="source",
            destination_payment_method_id="destination",
        )


def test_nexus_webhook_signature_supports_sha512_prefix():
    payload = b'{"id":"evt-test","status":"SUCCESSFUL"}'
    import hashlib, hmac

    signature = hmac.new(b"secret", payload, hashlib.sha512).hexdigest()
    assert NexusClient.verify_webhook_signature(payload, "sha512=" + signature, "secret")
    assert not NexusClient.verify_webhook_signature(payload, "0" * 128, "secret")
