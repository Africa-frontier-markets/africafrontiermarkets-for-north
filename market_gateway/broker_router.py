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
            region_candidates = [b for b in candidates if preferred_region in b.supported_markets]
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
