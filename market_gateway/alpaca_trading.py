"""Read-only Alpaca Trading API adapter for AFM market and portfolio reads.

This adapter uses the Trading API key pair directly. It intentionally allows
only GET requests: submitting, replacing, cancelling, closing or funding an
order through this client is outside the current transition scope.
"""
from __future__ import annotations

from typing import Any

import httpx

from config.config import settings


class AlpacaTradingClient:
    """Minimal, read-only client for Alpaca Trading and Market Data APIs."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        market_data_base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.alpaca_trading_api_key
        self.api_secret = api_secret or settings.alpaca_trading_api_secret
        self.base_url = (base_url or settings.alpaca_trading_base_url).rstrip("/")
        self.market_data_base_url = (
            market_data_base_url or settings.alpaca_trading_market_data_base_url
        ).rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise RuntimeError("Alpaca Trading API credentials are not configured")
        return {
            "APCA-API-KEY-ID": str(self.api_key),
            "APCA-API-SECRET-KEY": str(self.api_secret),
        }

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() != "GET":
            raise RuntimeError("Trading API adapter is read-only during the AFM transition")
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
            response = await client.request("GET", path, headers=headers, **kwargs)
            response.raise_for_status()
            return None if response.status_code == 204 or not response.content else response.json()

    async def market_data_request(self, method: str, path: str, **kwargs: Any) -> Any:
        if method.upper() != "GET":
            raise RuntimeError("Trading API adapter is read-only during the AFM transition")
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        async with httpx.AsyncClient(base_url=self.market_data_base_url, timeout=30) as client:
            response = await client.request("GET", path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()

    async def get_account(self) -> Any:
        return await self.request("GET", "/v2/account")

    async def list_assets(self, **params: Any) -> Any:
        return await self.request("GET", "/v2/assets", params=params)

    async def get_positions(self) -> Any:
        return await self.request("GET", "/v2/positions")

    async def list_activities(self, **params: Any) -> Any:
        return await self.request("GET", "/v2/account/activities", params=params)

    async def get_stock_bars(self, symbols: list[str], **params: Any) -> Any:
        return await self.market_data_request(
            "GET", "/v2/stocks/bars", params={"symbols": ",".join(symbols), **params}
        )

    async def get_crypto_bars(self, symbols: list[str], **params: Any) -> Any:
        return await self.market_data_request(
            "GET", "/v1beta3/crypto/us/bars", params={"symbols": ",".join(symbols), **params}
        )


alpaca_trading_client = AlpacaTradingClient()
