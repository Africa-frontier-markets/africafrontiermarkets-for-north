"""Nexus Payment Gateway sandbox client for AFM.

The adapter defaults to read-only operations. Cash-in/cash-out writes require
an explicit allow flag and an idempotency key. Secrets are never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx

from config.config import Settings

logger = logging.getLogger(__name__)


class NexusClientError(RuntimeError):
    """Raised when Nexus rejects a request or cannot answer."""


class NexusClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.nexus_secret_key)

    @property
    def writes_enabled(self) -> bool:
        return bool(self.settings.nexus_allow_transaction_writes)

    def _url(self, path: str) -> str:
        return f"{self.settings.nexus_api_base_url.rstrip('/')}/{path.lstrip('/')}"

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        if not self.configured:
            raise NexusClientError("Nexus sandbox secret is not configured")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if idempotency_key:
            headers["X-IDEMPOTENCY-KEY"] = idempotency_key
        url = self._url(path)
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        try:
            response = await client.request(
                method.upper(), url, auth=(self.settings.nexus_secret_key, ""), headers=headers, content=body
            )
            try:
                data: Any = response.json()
            except ValueError:
                data = {"message": response.text[:300]}
            if response.is_error:
                code = data.get("code") if isinstance(data, dict) else None
                message = data.get("message") if isinstance(data, dict) else response.text[:200]
                logger.warning("Nexus request rejected: method=%s path=%s status=%s code=%s", method.upper(), path, response.status_code, code or "none")
                raise NexusClientError(f"Nexus request failed ({response.status_code}) [{code or 'unknown'}]: {message or 'request rejected'}")
            return data
        except httpx.HTTPError as exc:
            raise NexusClientError("Nexus request failed (transport)") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def list_payment_methods(self) -> Any:
        return await self._request("GET", "/payment-methods")

    async def get_payment_method(self, payment_method_id: str) -> Any:
        return await self._request("GET", f"/payment-methods/{payment_method_id}")

    async def create_mobile_money_payment_method(
        self, *, phone_number: str, country_iso: str, provider: str
    ) -> Any:
        payload = {
            "type": "MOBILE_MONEY",
            "mobileMoneyDetails": {
                "phoneNumber": phone_number,
                "countryIso": country_iso,
                "mobileMoneyProvider": provider,
            },
        }
        return await self._request("POST", "/payment-methods", payload)

    async def create_cash_out(
        self,
        *,
        amount: int,
        currency_code: str,
        platform_code: str,
        external_transaction_id: str,
        payment_type: str,
        source_payment_method_id: str,
        destination_payment_method_id: str,
        idempotency_key: str | None = None,
        confirm: bool = False,
    ) -> Any:
        if not self.writes_enabled:
            raise NexusClientError("Nexus transaction writes are disabled")
        if not idempotency_key:
            raise NexusClientError("Nexus cash-out requires an idempotency key")
        payload = {
            "amount": amount,
            "currencyCode": currency_code,
            "platformCode": platform_code,
            "externalTransactionId": external_transaction_id,
            "paymentType": payment_type,
            "sourcePaymentMethodId": source_payment_method_id,
            "destinationPaymentMethodId": destination_payment_method_id,
            "confirm": confirm,
        }
        return await self._request("POST", "/transaction-intents/cash-out", payload, idempotency_key=idempotency_key)

    async def get_transaction_intent(self, transaction_id: str) -> Any:
        return await self._request("GET", f"/transaction-intents/{transaction_id}")

    @staticmethod
    def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
        expected = hmac.new(secret.encode(), payload, hashlib.sha512).hexdigest()
        supplied = signature.strip()
        if supplied.lower().startswith("sha512="):
            supplied = supplied.split("=", 1)[1]
        return hmac.compare_digest(expected, supplied)


def new_idempotency_key() -> str:
    return str(uuid.uuid4())
