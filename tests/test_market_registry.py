import pytest
from datetime import datetime, timezone

from market_gateway.market_registry import market_registry
from market_gateway.models import Market


def test_market_registry_zoneinfo_weekend():
    """V45: ZoneInfo + weekend closed."""
    market = Market(
        code="BRVM",
        name="Bourse Regionale des Valeurs Mobilieres",
        country="CI",
        timezone="Africa/Abidjan",
        currency="XOF",
        open_time="09:00",
        close_time="15:30",
        weekend_days="6,7",
    )
    market_registry.register(market)

    # Saturday = closed
    saturday = datetime(2024, 1, 6, 10, 0, tzinfo=timezone.utc)
    assert market_registry.is_open("BRVM", saturday) == False

    # Sunday = closed
    sunday = datetime(2024, 1, 7, 10, 0, tzinfo=timezone.utc)
    assert market_registry.is_open("BRVM", sunday) == False

    # Monday 10:00 = open
    monday = datetime(2024, 1, 8, 10, 0, tzinfo=timezone.utc)
    assert market_registry.is_open("BRVM", monday) == True

    # After hours = closed
    evening = datetime(2024, 1, 8, 20, 0, tzinfo=timezone.utc)
    assert market_registry.is_open("BRVM", evening) == False
