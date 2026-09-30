from decimal import Decimal

import pytest

pytestmark = pytest.mark.filterwarnings("ignore")

import api_gateway.main as gateway
from api_gateway.main import PaymentSimulationRequest, public_frontierpay_simulate
from payment_hub.aza_client import AzaClientError


@pytest.mark.asyncio
async def test_public_frontierpay_simulation_is_non_financial(monkeypatch):
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"),
        source_currency="XOF",
        beneficiary_currency="NGN",
        corridor="ci-nigeria",
        kaybic_fee=Decimal("1000"),
        aza_payin_fee=Decimal("3000"),
        aza_payout_fee=Decimal("2000"),
        afm_fee=Decimal("2000"),
        fx_rate=Decimal("1.5"),
        direction="payout",
    )

    async def fake_aza_quote(**kwargs):
        assert kwargs["corridor"] == "ci-nigeria"
        return {"rate": Decimal("1.5"), "output_amount": Decimal("138000"), "expiry_date": "2026-08-22T15:00:25Z", "rate_source": "aza_sandbox_calculate"}

    monkeypatch.setattr(gateway, "get_frontierpay_aza_quote", fake_aza_quote)
    result = await public_frontierpay_simulate(payload)

    assert result["simulation_only"] is True
    assert result["execution_mode"] == "public_preview"
    assert result["funds_movement"] is False
    assert result["ledger_write"] is False
    assert result["platform_fees"] == "8000.00"
    assert "fee_breakdown" not in result
    assert result["net_source_amount"] == "92000.00"
    assert result["net_destination_amount"] == "138000.00"
    assert result["rate_source"] == "aza_sandbox_calculate"
    assert result["rate_expiry"] == "2026-08-22T15:00:25Z"


@pytest.mark.asyncio
async def test_public_frontierpay_fails_closed_when_aza_quote_unavailable(monkeypatch):
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"), source_currency="XOF", beneficiary_currency="NGN",
        corridor="ci-nigeria", kaybic_fee=Decimal("1000"),
        aza_payin_fee=Decimal("3000"), aza_payout_fee=Decimal("2000"),
        afm_fee=Decimal("2000"), direction="payout",
    )

    async def unavailable(**kwargs):
        raise AzaClientError("unavailable")

    monkeypatch.setattr(gateway, "get_frontierpay_aza_quote", unavailable)
    with pytest.raises(Exception) as exc:
        await public_frontierpay_simulate(payload)
    assert getattr(exc.value, "status_code", None) == 503


@pytest.mark.asyncio
async def _legacy_test_frontierpay_quote_chains_xof_to_xaf_through_usd(monkeypatch):
    calls = []

    class FakeAzaClient:
        def __init__(self, _settings):
            pass

        async def get_exchange_rate(self, *, amount, from_currency, to_currency, reference):
            calls.append((amount, from_currency, to_currency, reference))
            if (from_currency, to_currency) == ("XOF", "USD"):
                return {"rate": Decimal("0.0016"), "to_amount": Decimal("160.00"), "expiry_date": "x", "expiry_in_seconds": 20}
            return {"rate": Decimal("600"), "to_amount": Decimal("96000.00"), "expiry_date": "y", "expiry_in_seconds": 15}

    monkeypatch.setattr(gateway, "KoraClient", FakeAzaClient)
    result = await gateway.get_frontierpay_aza_quote(
        amount=Decimal("100000"), source_currency="XOF", beneficiary_currency="XAF", reference="xof-xaf"
    )

    assert result["rate"] == Decimal("0.96000000")
    assert [(item[1], item[2]) for item in calls] == [("XOF", "USD"), ("USD", "XAF")]
    assert [leg["rate"] for leg in result["legs"]] == [Decimal("0.0016"), Decimal("600")]


@pytest.mark.asyncio
async def _legacy_test_frontierpay_quote_chains_xaf_to_xof_through_usd(monkeypatch):
    calls = []

    class FakeAzaClient:
        def __init__(self, _settings):
            pass

        async def get_exchange_rate(self, *, amount, from_currency, to_currency, reference):
            calls.append((amount, from_currency, to_currency, reference))
            if (from_currency, to_currency) == ("XAF", "USD"):
                return {"rate": Decimal("0.0016"), "to_amount": Decimal("160.00"), "expiry_date": "x", "expiry_in_seconds": 20}
            return {"rate": Decimal("625"), "to_amount": Decimal("100000.00"), "expiry_date": "y", "expiry_in_seconds": 15}

    monkeypatch.setattr(gateway, "KoraClient", FakeAzaClient)
    result = await gateway.get_frontierpay_aza_quote(
        amount=Decimal("100000"), source_currency="XAF", beneficiary_currency="XOF", reference="xaf-xof"
    )

    assert result["rate"] == Decimal("1.00000000")
    assert [(item[1], item[2]) for item in calls] == [("XAF", "USD"), ("USD", "XOF")]
    assert [leg["rate"] for leg in result["legs"]] == [Decimal("0.0016"), Decimal("625")]


@pytest.mark.parametrize(
    ("corridor", "source_currency", "beneficiary_currency"),
    [
        ("cameroon-ivory-coast", "XAF", "XOF"),
        ("ivory-coast-cameroon", "XOF", "XAF"),
    ],
)
def test_cameroon_ivory_coast_corridor_contract(corridor, source_currency, beneficiary_currency):
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"),
        source_currency=source_currency,
        beneficiary_currency=beneficiary_currency,
        corridor=corridor,
    )
    assert payload.corridor == corridor
    assert payload.source_currency == source_currency
    assert payload.beneficiary_currency == beneficiary_currency
