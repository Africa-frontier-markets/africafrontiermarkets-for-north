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


@pytest.mark.asyncio
async def test_frontierpay_quote_chains_xof_to_xaf_through_usd(monkeypatch):
    calls = []

    class FakeKoraClient:
        def __init__(self, _settings):
            pass

        async def get_exchange_rate(self, *, amount, from_currency, to_currency, reference):
            calls.append((amount, from_currency, to_currency, reference))
            if (from_currency, to_currency) == ("XOF", "USD"):
                return {"rate": Decimal("0.0016"), "to_amount": Decimal("160.00"), "expiry_date": "x", "expiry_in_seconds": 20}
            return {"rate": Decimal("600"), "to_amount": Decimal("96000.00"), "expiry_date": "y", "expiry_in_seconds": 15}

    monkeypatch.setattr(gateway, "KoraClient", FakeKoraClient)
    result = await gateway.get_frontierpay_kora_quote(
        amount=Decimal("100000"), source_currency="XOF", beneficiary_currency="XAF", reference="xof-xaf"
    )

    assert result["rate"] == Decimal("0.96000000")
    assert [(item[1], item[2]) for item in calls] == [("XOF", "USD"), ("USD", "XAF")]
    assert [leg["rate"] for leg in result["legs"]] == [Decimal("0.0016"), Decimal("600")]


@pytest.mark.asyncio
async def test_frontierpay_quote_chains_xaf_to_xof_through_usd(monkeypatch):
    calls = []

    class FakeKoraClient:
        def __init__(self, _settings):
            pass

        async def get_exchange_rate(self, *, amount, from_currency, to_currency, reference):
            calls.append((amount, from_currency, to_currency, reference))
            if (from_currency, to_currency) == ("XAF", "USD"):
                return {"rate": Decimal("0.0016"), "to_amount": Decimal("160.00"), "expiry_date": "x", "expiry_in_seconds": 20}
            return {"rate": Decimal("625"), "to_amount": Decimal("100000.00"), "expiry_date": "y", "expiry_in_seconds": 15}

    monkeypatch.setattr(gateway, "KoraClient", FakeKoraClient)
    result = await gateway.get_frontierpay_kora_quote(
        amount=Decimal("100000"), source_currency="XAF", beneficiary_currency="XOF", reference="xaf-xof"
    )

    assert result["rate"] == Decimal("1.00000000")
    assert [(item[1], item[2]) for item in calls] == [("XAF", "USD"), ("USD", "XOF")]
    assert [leg["rate"] for leg in result["legs"]] == [Decimal("0.0016"), Decimal("625")]
