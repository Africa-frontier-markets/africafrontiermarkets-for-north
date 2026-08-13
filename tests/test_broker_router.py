import pytest
from decimal import Decimal

from market_gateway.broker_router import broker_router
from market_gateway.models import Broker


def test_broker_select_by_region():
    """Broker routing par region avec fallback."""
    broker1 = Broker(
        name="Broker CI",
        api_endpoint="https://broker-ci.com",
        supported_markets='["BRVM", "NGX"]',
        supported_order_types='["market", "limit"]',
        latency_ms=50.0,
        priority=1.0,
        is_active=True,
    )
    broker2 = Broker(
        name="Broker SA",
        api_endpoint="https://broker-sa.com",
        supported_markets='["JSE", "BRVM"]',
        supported_order_types='["market"]',
        latency_ms=200.0,
        priority=0.5,
        is_active=True,
    )
    broker_router.register(broker1)
    broker_router.register(broker2)

    # Selection par region preferee
    selected = broker_router.select_broker("BRVM", preferred_region="CI")
    assert selected.name == "Broker CI"

    # Fallback si region non trouvee
    selected = broker_router.select_broker("JSE")
    assert selected.name == "Broker SA"


def test_broker_unavailable_error():
    """Exception si aucun broker disponible."""
    from config.exceptions import BrokerUnavailableError
    with pytest.raises(BrokerUnavailableError):
        broker_router.select_broker("UNKNOWN")


def test_broker_inactive_excluded():
    """Broker inactif exclu de la selection."""
    inactive = Broker(
        name="Broker Inactive",
        api_endpoint="https://inactive.com",
        supported_markets='["BRVM"]',
        supported_order_types='["market"]',
        is_active=False,
    )
    broker_router.register(inactive)

    # Doit selectionner l'actif, pas l'inactif
    selected = broker_router.select_broker("BRVM")
    assert selected.is_active == True
