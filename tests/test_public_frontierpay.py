from decimal import Decimal

import pytest

import api_gateway.main as gateway
from api_gateway.main import PaymentSimulationRequest, public_frontierpay_simulate
from payment_hub.kora_client import KoraClientError


@pytest.mark.asyncio
async def test_public_frontierpay_simulation_is_non_financial(monkeypatch):
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"),
        source_currency="XOF",
        beneficiary_currency="NGN",
        corridor="ci-nigeria",
        kaybic_fee=Decimal("1000"),
        kora_payin_fee=Decimal("3000"),
        kora_payout_fee=Decimal("2000"),
        afm_fee=Decimal("2000"),
        fx_rate=Decimal("1.5"),
        direction="payout",
    )

    async def fake_kora_quote(**kwargs):
        assert kwargs["source_currency"] == "XOF"
        assert kwargs["beneficiary_currency"] == "NGN"
        return {"rate": Decimal("1.5"), "expiry_date": "2026-08-22T15:00:25Z", "expiry_in_seconds": 25, "legs": []}

    monkeypatch.setattr(gateway, "get_frontierpay_kora_quote", fake_kora_quote)
    result = await public_frontierpay_simulate(payload)

    assert result["simulation_only"] is True
    assert result["execution_mode"] == "public_preview"
    assert result["funds_movement"] is False
    assert result["ledger_write"] is False
    assert result["platform_fees"] == "8000.00"
    assert "fee_breakdown" not in result
    assert result["net_source_amount"] == "92000.00"
    assert result["net_destination_amount"] == "138000.00"
    assert result["rate_source"] == "live_corridor_quote"
    assert result["rate_expiry"] == "2026-08-22T15:00:25Z"


@pytest.mark.asyncio
async def test_public_frontierpay_fails_closed_when_kora_quote_unavailable(monkeypatch):
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"), source_currency="XOF", beneficiary_currency="NGN",
        corridor="ci-nigeria", kaybic_fee=Decimal("1000"),
        kora_payin_fee=Decimal("3000"), kora_payout_fee=Decimal("2000"),
        afm_fee=Decimal("2000"), direction="payout",
    )

    async def unavailable(**kwargs):
        raise KoraClientError("unavailable")

    monkeypatch.setattr(gateway, "get_frontierpay_kora_quote", unavailable)
    with pytest.raises(Exception) as exc:
        await public_frontierpay_simulate(payload)
    assert getattr(exc.value, "status_code", None) == 503
