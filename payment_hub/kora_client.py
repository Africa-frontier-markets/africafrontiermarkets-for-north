"""Minimal, read-only Kora client used by authenticated AFM operations."""

from __future__ import annotations

from decimal import Decimal
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

    async def get_exchange_rate(
        self,
        *,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        reference: str,
    ) -> dict[str, Any]:
        """Fetch a read-only Kora conversion quote for one currency pair."""
        if not self.settings.kora_secret_key:
            raise KoraClientError("Kora secret key is not configured")
        payload = {
            "amount": float(amount),
            "from_currency": from_currency.upper(),
            "to_currency": to_currency.upper(),
            "reference": reference,
        }
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        try:
            response = await client.post(
                "https://api.korapay.com/merchant/api/v1/conversions/rates",
                headers={
                    "Authorization": f"Bearer {self.settings.kora_secret_key}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            if body.get("status") is not True or not isinstance(body.get("data"), dict):
                raise KoraClientError("Kora exchange rate request was not successful")
            data = body["data"]
            try:
                rate = Decimal(str(data["rate"]))
                to_amount = Decimal(str(data["to_amount"]))
            except (KeyError, ValueError, ArithmeticError) as exc:
                raise KoraClientError("Kora exchange rate response is malformed") from exc
            if rate <= 0 or to_amount < 0:
                raise KoraClientError("Kora exchange rate response is invalid")
            return {
                "from_currency": str(data.get("from_currency", from_currency)).upper(),
                "to_currency": str(data.get("to_currency", to_currency)).upper(),
                "from_amount": Decimal(str(data.get("from_amount", amount))),
                "to_amount": to_amount,
                "rate": rate,
                "reference": str(data.get("reference", reference)),
                "expiry_in_seconds": data.get("expiry_in_seconds"),
                "expiry_date": data.get("expiry_date"),
            }
        except httpx.HTTPError as exc:
            raise KoraClientError("Kora exchange rate request failed") from exc
        except ValueError as exc:
            raise KoraClientError("Kora exchange rate response is not valid JSON") from exc
        finally:
            if owns_client:
                await client.aclose()

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
