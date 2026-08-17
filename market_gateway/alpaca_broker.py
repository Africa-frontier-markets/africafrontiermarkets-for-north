"""Alpaca Broker API client.

Implements the OAuth2 client-credentials flow required by the Alpaca
Broker API: the Client ID / Client Secret pair generated in the Alpaca
Broker Dashboard is exchanged for a short-lived access token, which is
then used as a Bearer token on subsequent Broker API calls.

Environment variables (see .env.example):
    ALPACA_API_KEY          -> Broker Client ID
    ALPACA_SECRET_KEY       -> Broker Client Secret
    ALPACA_BROKER_BASE_URL  -> https://broker-api.sandbox.alpaca.markets
    ALPACA_AUTH_URL         -> https://authx.sandbox.alpaca.markets/v1/oauth2/token
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from config.config import settings


class AlpacaBrokerClient:
    """Minimal async client for the Alpaca Broker API."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        auth_url: str | None = None,
    ) -> None:
        self.client_id = client_id or settings.alpaca_api_key
        self.client_secret = client_secret or settings.alpaca_secret_key
        self.base_url = (base_url or settings.alpaca_broker_base_url).rstrip("/")
        self.auth_url = auth_url or settings.alpaca_auth_url
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def _fetch_token(self) -> str:
        if not self.configured:
            raise RuntimeError("Alpaca Broker credentials are not configured")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                self.auth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            payload = resp.json()
        self._access_token = payload["access_token"]
        # Refresh 60s before actual expiry
        self._token_expires_at = time.time() + int(payload.get("expires_in", 300)) - 60
        return self._access_token

    async def token(self) -> str:
        if self._access_token is None or time.time() >= self._token_expires_at:
            return await self._fetch_token()
        return self._access_token

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        token = await self.token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
            resp.raise_for_status()
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()

    # -- Convenience wrappers -------------------------------------------------
    async def list_accounts(self, **params: Any) -> Any:
        return await self.request("GET", "/v1/accounts", params=params)

    async def create_account(self, payload: dict[str, Any]) -> Any:
        return await self.request("POST", "/v1/accounts", json=payload)

    async def get_account(self, account_id: str) -> Any:
        return await self.request("GET", f"/v1/accounts/{account_id}")

    async def list_assets(self, **params: Any) -> Any:
        return await self.request("GET", "/v1/assets", params=params)

    async def get_account_positions(self, account_id: str) -> Any:
        return await self.request("GET", f"/v1/trading/accounts/{account_id}/positions")

    async def get_account_balance(self, account_id: str) -> Any:
        return await self.request("GET", f"/v1/accounts/{account_id}")

    async def list_account_activities(self, account_id: str, **params: Any) -> Any:
        """Return real account activities for one linked Broker API account."""
        return await self.request(
            "GET",
            "/v1/accounts/activities",
            params={"account_id": account_id, **params},
        )

    async def create_order(self, account_id: str, order: dict[str, Any]) -> Any:
        return await self.request("POST", f"/v1/trading/accounts/{account_id}/orders", json=order)

    async def list_orders(self, account_id: str, **params: Any) -> Any:
        return await self.request("GET", f"/v1/trading/accounts/{account_id}/orders", params=params)


# Shared singleton
alpaca_broker_client = AlpacaBrokerClient()
