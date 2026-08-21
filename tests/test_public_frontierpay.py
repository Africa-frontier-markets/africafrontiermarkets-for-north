from decimal import Decimal

import pytest

from api_gateway.main import PaymentSimulationRequest, public_frontierpay_simulate


@pytest.mark.asyncio
async def test_public_frontierpay_simulation_is_non_financial():
    payload = PaymentSimulationRequest(
        amount=Decimal("100000"),
        source_currency="XOF",
        beneficiary_currency="NGN",
        corridor="ci-nigeria",
        kaybic_fee=Decimal("1000"),
        kora_payin_fee=Decimal("3000"),
        kora_payout_fee=Decimal("2000"),
        afm_fee=Decimal("250"),
        fx_rate=Decimal("1.5"),
        direction="payout",
    )

    result = await public_frontierpay_simulate(payload)

    assert result["simulation_only"] is True
    assert result["execution_mode"] == "public_preview"
    assert result["funds_movement"] is False
    assert result["ledger_write"] is False
    assert result["fee_breakdown"]["total"] == "6250.00"
    assert result["net_source_amount"] == "93750.00"
    assert result["net_destination_amount"] == "140625.00"
