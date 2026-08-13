import pytest
from decimal import Decimal

from easymarkets.copy_trading_service import copy_trading_service
from config.exceptions import SelfFollowError, InvalidAllocationError


@pytest.mark.asyncio
async def test_self_follow_blocked():
    """V45: Cannot copy-trade yourself."""
    with pytest.raises(SelfFollowError):
        await copy_trading_service.start_copy_trading("user_1", "user_1", 50)


@pytest.mark.asyncio
async def test_allocation_positive():
    """V45: Allocation must be > 0."""
    with pytest.raises(InvalidAllocationError):
        await copy_trading_service.start_copy_trading("user_1", "user_2", 0)

    with pytest.raises(InvalidAllocationError):
        await copy_trading_service.start_copy_trading("user_1", "user_2", -10)


@pytest.mark.asyncio
async def test_allocation_max_100():
    """V45: Allocation cannot exceed 100%."""
    with pytest.raises(InvalidAllocationError):
        await copy_trading_service.start_copy_trading("user_1", "user_2", 101)
