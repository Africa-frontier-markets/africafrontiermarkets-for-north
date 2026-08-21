"""Smart broker routing by region, latency, and priority."""

import json
import random

from config.exceptions import BrokerUnavailableError
from market_gateway.models import Broker


class BrokerRouter:
    def __init__(self):
        self._brokers = []

    def register(self, broker: Broker) -> None:
        self._brokers.append(broker)

    def select_broker(self, market_code: str, order_type: str = "market", preferred_region: str | None = None) -> Broker:
        candidates = [
            b for b in self._brokers
            if b.is_active and self._supports_market(b, market_code) and self._supports_order_type(b, order_type)
        ]

        if not candidates:
            raise BrokerUnavailableError(f"No broker available for market {market_code}")

        if preferred_region:
            region_candidates = [b for b in candidates if self._matches_region(b, preferred_region)]
            if region_candidates:
                candidates = region_candidates

        weights = []
        for b in candidates:
            latency_weight = max(1.0, 1000.0 / max(b.latency_ms, 1.0))
            weights.append(b.priority * latency_weight)

        total = sum(weights)
        if total == 0:
            return random.choice(candidates)

        r = random.uniform(0, total)
        cumulative = 0
        for broker, weight in zip(candidates, weights):
            cumulative += weight
            if r <= cumulative:
                return broker

        return candidates[-1]

    def _matches_region(self, broker: Broker, preferred_region: str) -> bool:
        """Match a preferred region without confusing it with a market code.

        The current legacy Broker model has no dedicated region column. Until that
        additive schema change is introduced, use an optional `region` attribute
        when present and fall back to an explicit region token in the broker name
        or endpoint (e.g. ``Broker CI``). This keeps routing deterministic and
        avoids treating ``CI`` as a supported market.
        """
        wanted = preferred_region.strip().upper()
        declared = getattr(broker, "region", None)
        if declared and str(declared).upper() == wanted:
            return True
        name = str(getattr(broker, "name", "")).upper()
        endpoint = str(getattr(broker, "api_endpoint", "")).upper()
        return wanted in name.split() or f"-{wanted.lower()}" in endpoint.lower() or f"{wanted.lower()}." in endpoint.lower()

    def _supports_market(self, broker: Broker, market_code: str) -> bool:
        try:
            markets = json.loads(broker.supported_markets)
            return market_code in markets
        except (json.JSONDecodeError, TypeError):
            return False

    def _supports_order_type(self, broker: Broker, order_type: str) -> bool:
        try:
            types = json.loads(broker.supported_order_types)
            return order_type in types
        except (json.JSONDecodeError, TypeError):
            return order_type == "market"


broker_router = BrokerRouter()
