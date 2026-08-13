import pytest
from decimal import Decimal

from easymarkets.wallet_service import wallet_service
from config.exceptions import CurrencyNotSupportedError, ValidationError


@pytest.mark.asyncio
async def test_wallet_currency_whitelist():
    """V45: Unsupported currency rejected."""
    with pytest.raises(CurrencyNotSupportedError):
        await wallet_service.deposit("wallet_1", Decimal("100"), "XYZ", "ref_1")


@pytest.mark.asyncio
async def test_wallet_amount_positive():
    """V45: Amount <= 0 rejected."""
    with pytest.raises(ValidationError):
        await wallet_service.deposit("wallet_1", Decimal("0"), "XOF", "ref_1")

    with pytest.raises(ValidationError):
        await wallet_service.deposit("wallet_1", Decimal("-10"), "XOF", "ref_1")
