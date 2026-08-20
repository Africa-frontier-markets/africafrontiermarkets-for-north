"""Minimal, read-only Kora client used by authenticated AFM operations."""

from __future__ import annotations

from typing import Any

import httpx

from config.config import Settings


class KoraClientError(RuntimeError):
    """Raised when Kora cannot provide a valid response."""


class KoraClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.kora_secret_key)

    async def get_balance(self) -> dict[str, Any]:
        """Retrieve Kora balances without initiating a payment or payout."""
        if not self.settings.kora_secret_key:
            raise KoraClientError("Kora secret key is not configured")

        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

        try:
            response = await client.get(
                "https://api.korapay.com/merchant/api/v1/balances",
                headers={
                    "Authorization": f"Bearer {self.settings.kora_secret_key}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") is not True:
                raise KoraClientError("Kora balance request was not successful")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise KoraClientError("Kora balance response is malformed")
            return {
                "balances": data,
                "source": "payment-operations",
                "read_only": True,
            }
        except httpx.HTTPError as exc:
            raise KoraClientError("Kora balance request failed") from exc
        except ValueError as exc:
            raise KoraClientError("Kora balance response is not valid JSON") from exc
        finally:
            if owns_client:
                await client.aclose()


async def get_kora_balance(settings: Settings) -> dict[str, Any]:
    return await KoraClient(settings).get_balance()
