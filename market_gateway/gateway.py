"""
AFM Market Gateway — façade broker-agnostique.

Un seul broker réel est branché (Alpaca — actions US). La façade existe
pour que trading_engine ne dépende jamais directement d'alpaca_client :
si un second broker (ex: pour une classe d'actifs ou une région
différente) est ajouté plus tard, il s'enregistre ici sans que
trading_engine change. Ce n'est pas une abstraction spéculative pour du
code qui n'existe pas — c'est la même logique que payment_hub/psp_router.py
(un seul point de routage, plusieurs PSP), appliquée au trading.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from config.exceptions import BrokerUnavailableError, MarketClosedError
from market_gateway.alpaca_client import alpaca_client


@dataclass
class Quote:
    symbol: str
    bid: Decimal
    ask: Decimal
    mid: Decimal
    broker: str
    as_of: datetime


@dataclass
class BrokerOrderResult:
    broker: str
    broker_order_id: str
    status: str  # tel que renvoyé par le broker (ex: "accepted", "filled", "rejected")
    filled_qty: Decimal
    filled_avg_price: Decimal | None
    raw: dict


# Un seul marché couvert pour l'instant : actions US via Alpaca. La clé est
# volontairement le "marché" et pas juste "us_equity" en dur pour que
# _resolve_broker reste le point d'extension unique.
SUPPORTED_MARKETS = {"us_equity"}


class MarketGateway:
    def _resolve_broker(self, market: str) -> str:
        if market not in SUPPORTED_MARKETS:
            raise BrokerUnavailableError(f"No broker configured for market '{market}'")
        return "alpaca"

    async def is_market_open(self, market: str = "us_equity") -> bool:
        self._resolve_broker(market)
        clock = await alpaca_client.get_clock()
        return bool(clock.get("is_open"))

    async def get_quote(self, symbol: str, market: str = "us_equity") -> Quote:
        self._resolve_broker(market)
        data = await alpaca_client.get_latest_quote(symbol)
        raw_quote = data.get("quote", {})
        bid = Decimal(str(raw_quote.get("bp", 0)))
        ask = Decimal(str(raw_quote.get("ap", 0)))
        if bid <= 0 or ask <= 0:
            raise BrokerUnavailableError(f"Alpaca returned no tradable quote for {symbol}")
        mid = (bid + ask) / 2
        return Quote(symbol=symbol.upper(), bid=bid, ask=ask, mid=mid, broker="alpaca", as_of=datetime.now(timezone.utc))

    async def submit_order(
        self, symbol: str, qty: Decimal, side: Literal["buy", "sell"],
        market: str = "us_equity", client_order_id: str | None = None,
    ) -> BrokerOrderResult:
        self._resolve_broker(market)
        if not await self.is_market_open(market):
            raise MarketClosedError(f"Market '{market}' is currently closed")

        response = await alpaca_client.submit_order(
            symbol=symbol, qty=qty, side=side, order_type="market", client_order_id=client_order_id,
        )
        filled_qty = Decimal(str(response.get("filled_qty") or "0"))
        filled_avg_price = Decimal(str(response["filled_avg_price"])) if response.get("filled_avg_price") else None
        return BrokerOrderResult(
            broker="alpaca",
            broker_order_id=response["id"],
            status=response.get("status", "unknown"),
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            raw=response,
        )

    async def get_order_status(self, broker_order_id: str, market: str = "us_equity") -> BrokerOrderResult:
        self._resolve_broker(market)
        response = await alpaca_client.get_order(broker_order_id)
        filled_qty = Decimal(str(response.get("filled_qty") or "0"))
        filled_avg_price = Decimal(str(response["filled_avg_price"])) if response.get("filled_avg_price") else None
        return BrokerOrderResult(
            broker="alpaca",
            broker_order_id=response["id"],
            status=response.get("status", "unknown"),
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            raw=response,
        )


market_gateway = MarketGateway()
