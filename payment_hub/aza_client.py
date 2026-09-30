"""AZA Finance / TransferZero sandbox client.

Only the calculate endpoint is used by public FrontierPay previews. Transaction
creation is explicitly gated by AZA_ALLOW_TRANSACTION_WRITES and is never
implicitly enabled by a missing credential.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx

from config.config import Settings

logger = logging.getLogger(__name__)


class AzaClientError(RuntimeError):
    """Raised when AZA rejects or cannot answer a request."""


AZA_CORRIDORS: dict[str, dict[str, str]] = {
    "cameroon-ivory-coast": {"source": "XAF", "destination": "XOF", "payout_type": "XOF::Mobile", "source_country": "CM", "destination_country": "CI", "mobile_provider": "orange"},
    "ivory-coast-cameroon": {"source": "XOF", "destination": "XAF", "payout_type": "XAF::Mobile", "source_country": "CI", "destination_country": "CM", "mobile_provider": "orange"},
    "ci-ghana": {"source": "XOF", "destination": "GHS", "payout_type": "GHS::Mobile", "source_country": "CI", "destination_country": "GH", "mobile_provider": "mtn"},
    "ci-nigeria": {"source": "XOF", "destination": "NGN", "payout_type": "NGN::Bank", "source_country": "CI", "destination_country": "NG", "mobile_provider": ""},
    "cameroon-nigeria": {"source": "XAF", "destination": "NGN", "payout_type": "NGN::Bank", "source_country": "CM", "destination_country": "NG", "mobile_provider": ""},
    "benin-nigeria": {"source": "XOF", "destination": "NGN", "payout_type": "NGN::Bank", "source_country": "BJ", "destination_country": "NG", "mobile_provider": ""},
}


class AzaClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.settings.aza_api_key and self.settings.aza_api_secret)

    def _headers(self, method: str, url: str, body: bytes) -> dict[str, str]:
        nonce = str(uuid.uuid4())
        body_hash = hashlib.sha512(body).hexdigest()
        signing_string = f"{nonce}&{method.upper()}&{url}&{body_hash}"
        signature = hmac.new(
            self.settings.aza_api_secret.encode(), signing_string.encode(), hashlib.sha512
        ).hexdigest()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization-Key": self.settings.aza_api_key,
            "Authorization-Nonce": nonce,
            "Authorization-Signature": signature,
        }

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            logger.warning("AZA request skipped: credentials not configured")
            raise AzaClientError("AZA sandbox credentials are not configured")
        body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode()
        url = f"{self.settings.aza_api_base_url.rstrip('/')}/{path.lstrip('/')}"
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        try:
            response = await client.request(method.upper(), url, headers=self._headers(method, url, body), content=body)
            data: Any = response.json()
            if response.is_error:
                detail = data.get("message") if isinstance(data, dict) else response.text[:200]
                logger.warning("AZA request rejected: method=%s path=%s status=%s detail=%s", method.upper(), path, response.status_code, detail or "none")
                raise AzaClientError(f"AZA request failed ({response.status_code}): {detail or 'request rejected'}")
            if not isinstance(data, dict):
                raise AzaClientError("AZA response is not a JSON object")
            return data
        except httpx.HTTPError as exc:
            raise AzaClientError("AZA request failed (transport)") from exc
        except ValueError as exc:
            raise AzaClientError("AZA response is not valid JSON") from exc
        finally:
            if owns_client:
                await client.aclose()

    @staticmethod
    def build_sandbox_transaction(*, corridor: str, amount: Decimal, reference: str) -> dict[str, Any]:
        config = AZA_CORRIDORS.get(corridor)
        if config is None:
            raise AzaClientError(f"AZA corridor is not configured: {corridor}")
        source_country = config["source_country"]
        destination_country = config["destination_country"]
        destination_currency = config["destination"]
        sender_phone = {"CM": "+237670000000", "CI": "+2250700000000", "BJ": "+229970000000"}[source_country]
        sender = {
            "type": "person",
            "country": source_country,
            "phone_country": source_country,
            "phone_number": sender_phone[4:],
            "email": "sandbox@africafrontiermarkets.com",
            "first_name": "AFM",
            "last_name": "Sandbox",
            "city": "Test City",
            "street": "1 Sandbox Street",
            "postal_code": "00000",
            "birth_date": "1980-01-01",
            "ip": "127.0.0.1",
            "documents": [],
            "external_id": f"afm-sandbox-sender-{source_country.lower()}",
        }
        details: dict[str, Any] = {"first_name": "AFM", "last_name": "Sandbox"}
        if config["payout_type"] == "NGN::Bank":
            details.update({"bank_code": "058", "bank_account": "12345678900", "street": "1 Sandbox Street"})
        else:
            phone = {"CI": "+2250700000000", "CM": "+237670000000", "GH": "+233302123400"}[destination_country]
            details.update({"mobile_provider": config["mobile_provider"], "phone_number": phone, "country": destination_country})
        return {
            "transaction": {
                "input_currency": config["source"],
                "sender": sender,
                "recipients": [{
                    "requested_amount": float(amount),
                    "requested_currency": destination_currency,
                    "type": "person",
                    "payout_method": {"type": config["payout_type"], "details": details},
                }],
                "external_id": reference,
                "metadata": {"afm_corridor": corridor, "simulation_only": True},
            }
        }

    async def calculate_corridor(self, *, corridor: str, amount: Decimal, reference: str) -> dict[str, Any]:
        payload = self.build_sandbox_transaction(corridor=corridor, amount=amount, reference=reference)
        return await self._request("POST", "/transactions/calculate", payload)

    async def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.aza_allow_transaction_writes:
            raise AzaClientError("AZA transaction writes are disabled")
        return await self._request("POST", "/transactions", payload)

    async def payout_transaction(self, transaction_id: str) -> dict[str, Any]:
        if not self.settings.aza_allow_transaction_writes:
            raise AzaClientError("AZA transaction writes are disabled")
        return await self._request("POST", f"/transactions/{transaction_id}/payout", {})
