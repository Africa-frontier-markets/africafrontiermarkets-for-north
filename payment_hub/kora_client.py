"""Kora API client for read-only quotes and controlled payment operations."""

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

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.settings.kora_secret_key:
            raise KoraClientError("Kora secret key is not configured")
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
        try:
            base_url = self.settings.kora_api_base_url.rstrip("/")
            url = f"{base_url}/{path.lstrip('/')}"
            headers = {
                "Authorization": f"Bearer {self.settings.kora_secret_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if method.upper() == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.post(url, headers=headers, **kwargs)
            response.raise_for_status()
            body = response.json()
            if body.get("status") is not True or not isinstance(body.get("data"), dict):
                detail = str(body.get("message") or "request rejected")
                raise KoraClientError(f"Kora request was not successful: {detail}")
            return body["data"]
        except httpx.HTTPStatusError as exc:
            detail = "http error"
            try:
                payload = exc.response.json()
                detail = str(payload.get("message") or payload.get("error") or detail)
            except (ValueError, TypeError):
                detail = exc.response.text[:160] or detail
            raise KoraClientError(f"Kora API request failed ({exc.response.status_code}): {detail}") from exc
        except httpx.HTTPError as exc:
            raise KoraClientError("Kora API request failed (transport)") from exc
        except ValueError as exc:
            raise KoraClientError("Kora API response is not valid JSON") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_exchange_rate(
        self,
        *,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        reference: str,
    ) -> dict[str, Any]:
        """Fetch a Kora conversion quote without moving funds."""
        data = await self._request(
            "POST",
            "conversions/rates",
            json={
                "amount": float(amount),
                "from_currency": from_currency.upper(),
                "to_currency": to_currency.upper(),
                "reference": reference,
            },
        )
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

    async def initiate_mobile_money_charge(
        self,
        *,
        amount: Decimal,
        currency: str,
        reference: str,
        phone_number: str,
        customer_email: str,
        customer_name: str | None = None,
        notification_url: str | None = None,
        redirect_url: str | None = None,
        description: str | None = None,
        merchant_bears_cost: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Start the Kora Mobile Money pay-in flow.

        Kora returns an auth_model of OTP, STK_PROMPT or REDIRECT. This method
        only initiates the charge; it never collects a wallet PIN.
        """
        if not phone_number or not customer_email:
            raise KoraClientError("Mobile Money phone number and customer email are required")
        payload: dict[str, Any] = {
            "amount": float(amount),
            "currency": currency.upper(),
            "reference": reference,
            "customer": {"email": customer_email, "name": customer_name or "AFM customer"},
            "mobile_money": {"number": phone_number},
            "merchant_bears_cost": merchant_bears_cost,
        }
        for key, value in (
            ("notification_url", notification_url),
            ("redirect_url", redirect_url),
            ("description", description),
        ):
            if value:
                payload[key] = value
        if metadata:
            payload["metadata"] = metadata
        return await self._request("POST", "charges/mobile-money", json=payload)

    async def authorize_mobile_money(self, *, reference: str, token: str) -> dict[str, Any]:
        """Authorize an OTP Mobile Money charge; the wallet PIN stays with the telco."""
        if not token:
            raise KoraClientError("OTP token is required")
        return await self._request(
            "POST",
            "charges/mobile-money/authorize",
            json={"reference": reference, "token": token},
        )

    async def verify_charge(self, *, reference: str) -> dict[str, Any]:
        """Verify the final state of a Kora charge."""
        return await self._request("GET", f"charges/{reference}")

    async def resolve_mobile_money_account(
        self, *, operator_code: str, phone_number: str, currency: str
    ) -> dict[str, Any]:
        """Resolve a Mobile Money beneficiary before payout where required."""
        return await self._request(
            "POST",
            "misc/mobile-money/resolve",
            json={
                "mobileMoneyCode": operator_code,
                "phoneNumber": phone_number,
                "currency": currency.upper(),
            },
        )

    async def create_payout(
        self,
        *,
        reference: str,
        amount: Decimal,
        currency: str,
        customer_email: str,
        customer_name: str | None = None,
        narration: str | None = None,
        mobile_money_operator: str | None = None,
        mobile_number: str | None = None,
        bank_country: str | None = None,
        bank_name: str | None = None,
        bank_code: str | None = None,
        account_number: str | None = None,
    ) -> dict[str, Any]:
        """Create a Kora payout to Mobile Money or a bank account."""
        if len(reference) < 5 or not customer_email:
            raise KoraClientError("Payout reference and customer email are required")
        if mobile_money_operator or mobile_number:
            if not (mobile_money_operator and mobile_number):
                raise KoraClientError("Mobile Money operator and number are both required")
            destination: dict[str, Any] = {
                "type": "mobile_money",
                "amount": float(amount),
                "currency": currency.upper(),
                "mobile_money": {
                    "operator": mobile_money_operator,
                    "mobile_number": mobile_number,
                },
            }
        else:
            if not (account_number and (bank_code or bank_name)):
                raise KoraClientError("Bank code/name and account number are required")
            destination = {
                "type": "bank_account",
                "amount": float(amount),
                "currency": currency.upper(),
                "bank_account": {
                    "account": account_number,
                    **({"bank": bank_code} if bank_code else {}),
                    **({"bank_name": bank_name} if bank_name else {}),
                },
            }
            if bank_country:
                destination["bank_country"] = bank_country.upper()
        destination["customer"] = {
            "email": customer_email,
            "name": customer_name or "AFM beneficiary",
        }
        if narration:
            destination["narration"] = narration
        return await self._request(
            "POST",
            "transactions/disburse",
            json={"reference": reference, "destination": destination},
        )

    async def initiate_refund(
        self,
        *,
        payment_reference: str,
        refund_reference: str,
        amount: Decimal | None = None,
        reason: str | None = None,
        webhook_url: str | None = None,
        completion_status: str | None = None,
        status_reason: str | None = None,
    ) -> dict[str, Any]:
        """Initiate a full or partial refund for a successful Kora pay-in.

        ``completion_status`` and ``status_reason`` are intentionally limited to
        sandbox callers by the API route; they are never sent by production
        business logic.
        """
        if not payment_reference or len(refund_reference) < 5 or len(refund_reference) > 50:
            raise KoraClientError("Payment and refund references are required")
        payload: dict[str, Any] = {
            "payment_reference": payment_reference,
            "reference": refund_reference,
        }
        if amount is not None:
            if amount <= 0:
                raise KoraClientError("Refund amount must be positive")
            payload["amount"] = float(amount)
        for key, value in (
            ("reason", reason),
            ("webhook_url", webhook_url),
            ("completion_status", completion_status),
            ("status_reason", status_reason),
        ):
            if value:
                payload[key] = value
        return await self._request("POST", "refunds/initiate", json=payload)

    async def get_refund(self, *, refund_reference: str) -> dict[str, Any]:
        """Retrieve the current status of one Kora refund."""
        if not refund_reference:
            raise KoraClientError("Refund reference is required")
        return await self._request("GET", f"refunds/{refund_reference}")

    async def get_balance(self) -> dict[str, Any]:
        """Retrieve Kora balances without initiating a payment or payout."""
        data = await self._request("GET", "balances")
        return {"balances": data, "source": "payment-operations", "read_only": True}


async def get_kora_balance(settings: Settings) -> dict[str, Any]:
    return await KoraClient(settings).get_balance()
