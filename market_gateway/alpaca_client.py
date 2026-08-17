"""
AFM Market Gateway — Alpaca client.

Client HTTP réel contre l'API Alpaca (paper trading par défaut —
`alpaca_paper=True` dans config, comme documenté ailleurs dans ce projet).
Premier module de tout ce paquet qui parle effectivement à Alpaca : avant
ce soir, seules les clés de config existaient (config/config.py), rien ne
les utilisait. Pas de simulation cachée ici — si les identifiants sont
absents ou invalides, les appels échouent avec BrokerUnavailableError,
exactement comme le ferait un vrai appel réseau raté ; il n'y a pas de
fallback silencieux vers des données inventées (contrairement au fallback
PSP de payment_service, qui lui est un choix assumé et documenté pour le
paiement — ici, inventer un prix ou un fill serait dangereux, pas pratique).
"""

from decimal import Decimal
from typing import Any, Literal, Optional

import httpx

from config.config import get_settings
from config.exceptions import BrokerUnavailableError, TradeExecutionError, MarketClosedError
from config.logging_config import configure_logging

logger = configure_logging()

PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"
DATA_BASE_URL = "https://data.alpaca.markets"


class AlpacaClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._trading_client: Optional[httpx.AsyncClient] = None
        self._data_client: Optional[httpx.AsyncClient] = None

    def _has_credentials(self) -> bool:
        return bool(self._settings.alpaca_api_key and self._settings.alpaca_secret_key)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key or "",
            "APCA-API-SECRET-KEY": self._settings.alpaca_secret_key or "",
        }

    def _trading_base_url(self) -> str:
        return PAPER_BASE_URL if self._settings.alpaca_paper else LIVE_BASE_URL

    async def _get_trading_client(self) -> httpx.AsyncClient:
        if self._trading_client is None:
            self._trading_client = httpx.AsyncClient(
                base_url=self._trading_base_url(), headers=self._headers(), timeout=10.0,
            )
        return self._trading_client

    async def _get_data_client(self) -> httpx.AsyncClient:
        if self._data_client is None:
            self._data_client = httpx.AsyncClient(
                base_url=DATA_BASE_URL, headers=self._headers(), timeout=10.0,
            )
        return self._data_client

    async def close(self) -> None:
        if self._trading_client:
            await self._trading_client.aclose()
        if self._data_client:
            await self._data_client.aclose()

    async def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        if not self._has_credentials():
            raise BrokerUnavailableError("Alpaca credentials not configured")
        client = await self._get_data_client()
        try:
            resp = await client.get(f"/v2/stocks/{symbol.upper()}/quotes/latest")
        except httpx.RequestError as exc:
            raise BrokerUnavailableError(f"Alpaca market data unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise TradeExecutionError(f"Unknown symbol: {symbol}")
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"Alpaca quote request failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_account(self) -> dict[str, Any]:
        if not self._has_credentials():
            raise BrokerUnavailableError("Alpaca credentials not configured")
        client = await self._get_trading_client()
        try:
            resp = await client.get("/v2/account")
        except httpx.RequestError as exc:
            raise BrokerUnavailableError(f"Alpaca trading API unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"Alpaca account request failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def get_clock(self) -> dict[str, Any]:
        if not self._has_credentials():
            raise BrokerUnavailableError("Alpaca credentials not configured")
        client = await self._get_trading_client()
        try:
            resp = await client.get("/v2/clock")
        except httpx.RequestError as exc:
            raise BrokerUnavailableError(f"Alpaca trading API unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"Alpaca clock request failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()

    async def submit_order(
        self, symbol: str, qty: Decimal, side: Literal["buy", "sell"],
        order_type: Literal["market", "limit"] = "market",
        limit_price: Optional[Decimal] = None,
        client_order_id: Optional[str] = None,
        time_in_force: Literal["day", "gtc", "ioc"] = "day",
    ) -> dict[str, Any]:
        if not self._has_credentials():
            raise BrokerUnavailableError("Alpaca credentials not configured")

        payload: dict[str, Any] = {
            "symbol": symbol.upper(),
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if order_type == "limit":
            if limit_price is None:
                raise TradeExecutionError("limit_price is required for limit orders")
            payload["limit_price"] = str(limit_price)

        client = await self._get_trading_client()
        try:
            resp = await client.post("/v2/orders", json=payload)
        except httpx.RequestError as exc:
            raise BrokerUnavailableError(f"Alpaca order submission unreachable: {exc}") from exc

        if resp.status_code == 403:
            raise TradeExecutionError(f"Alpaca rejected order (buying power / risk check): {resp.text[:300]}")
        if resp.status_code == 422:
            raise TradeExecutionError(f"Alpaca rejected order (invalid input): {resp.text[:300]}")
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"Alpaca order request failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    async def get_order(self, broker_order_id: str) -> dict[str, Any]:
        if not self._has_credentials():
            raise BrokerUnavailableError("Alpaca credentials not configured")
        client = await self._get_trading_client()
        try:
            resp = await client.get(f"/v2/orders/{broker_order_id}")
        except httpx.RequestError as exc:
            raise BrokerUnavailableError(f"Alpaca order status unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise TradeExecutionError(f"Unknown broker order id: {broker_order_id}")
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"Alpaca order status request failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()


alpaca_client = AlpacaClient()
