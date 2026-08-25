"""Guarded Kora sandbox refund end-to-end test for Cameroon Mobile Money.

The runner is sandbox-only: it requires KORA_SANDBOX=true and a sk_test_ key.
It initiates one XAF pay-in, polls its status, then initiates one full refund
only after the pay-in is successful. It never accepts a live key and never
writes AFM ledger rows directly; reconciliation must arrive through the AFM
webhook endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("SECRET_KEY", "local-test-secret-012345678901234567890123456789012345678901234567")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from config.config import Settings
from payment_hub.kora_client import KoraClient, KoraClientError

SUCCESS_STATUSES = {"success", "successful", "completed", "settled"}


def settings_from_env() -> Settings:
    secret = os.getenv("KORA_SECRET_KEY", "")
    if os.getenv("KORA_SANDBOX", "").lower() != "true":
        raise RuntimeError("Refusing to run: KORA_SANDBOX=true is required")
    if not secret.startswith("sk_test_"):
        raise RuntimeError("Refusing to run: KORA_SECRET_KEY must be a Kora test key")
    return Settings(
        secret_key=os.getenv("SECRET_KEY", "local-test-secret-012345678901234567890123456789012345678901234567"),
        database_url=os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        environment="development",
        kora_secret_key=secret,
        kora_api_base_url=os.getenv("KORA_API_BASE_URL", "https://api.korapay.com/merchant/api/v1"),
    )


def status_of(data: dict) -> str:
    return str(data.get("status") or data.get("payment_status") or "").lower()


async def poll_charge(client: KoraClient, reference: str, attempts: int, delay: float) -> dict:
    latest: dict = {}
    for attempt in range(1, attempts + 1):
        try:
            latest = await client.verify_charge(reference=reference)
        except KoraClientError as exc:
            print(json.dumps({"charge_poll": attempt, "status": "unavailable", "retryable_error": str(exc)}, default=str))
            if attempt < attempts:
                await asyncio.sleep(delay)
            continue
        status = status_of(latest)
        print(json.dumps({"charge_poll": attempt, "status": status}, default=str))
        if status in SUCCESS_STATUSES or status in {"failed", "cancelled", "canceled", "expired"}:
            return latest
        if attempt < attempts:
            await asyncio.sleep(delay)
    return latest


async def poll_refund(client: KoraClient, reference: str, attempts: int, delay: float) -> dict:
    latest: dict = {}
    for attempt in range(1, attempts + 1):
        try:
            latest = await client.get_refund(refund_reference=reference)
        except KoraClientError as exc:
            print(json.dumps({"refund_poll": attempt, "status": "unavailable", "retryable_error": str(exc)}, default=str))
            if attempt < attempts:
                await asyncio.sleep(delay)
            continue
        status = status_of(latest)
        print(json.dumps({"refund_poll": attempt, "status": status}, default=str))
        if status in SUCCESS_STATUSES or status in {"failed", "cancelled", "canceled", "expired"}:
            return latest
        if attempt < attempts:
            await asyncio.sleep(delay)
    return latest


async def run() -> int:
    settings = settings_from_env()
    client = KoraClient(settings)
    amount = Decimal(os.getenv("KORA_TEST_AMOUNT", "100000"))
    currency = os.getenv("KORA_TEST_CURRENCY", "XAF").upper()
    phone = os.getenv("KORA_TEST_PHONE", "237655123456")
    email = os.getenv("KORA_TEST_EMAIL", "sandbox@example.com")
    webhook_url = os.getenv("AFM_WEBHOOK_URL", "https://africafrontiermarkets.com/webhooks/kora")
    corridor = os.getenv("KORA_TEST_CORRIDOR", "CM-CI").upper()
    # STK_PROMPT requires the wallet owner to authorize on the handset. Keep
    # polling long enough for that manual sandbox action; never transmit a PIN.
    poll_attempts = int(os.getenv("KORA_REFUND_POLL_ATTEMPTS", "60"))
    poll_delay = float(os.getenv("KORA_REFUND_POLL_DELAY_SECONDS", "10"))
    started_at = time.monotonic()
    base = f"afm-refund-sbx-{os.urandom(8).hex()}"
    payin_reference = f"{base}-payin"
    refund_reference = f"{base}-refund"

    quote = await client.get_exchange_rate(
        amount=amount, from_currency=currency, to_currency="USD", reference=f"{base}-quote"
    )
    print(json.dumps({"quote_rate": str(quote["rate"]), "quote_expiry": quote.get("expiry_date")}, default=str))

    payin = await client.initiate_mobile_money_charge(
        amount=amount,
        currency=currency,
        reference=payin_reference,
        phone_number=phone,
        customer_email=email,
        notification_url=webhook_url,
        metadata={"afm_test": "refund_e2e", "execution_mode": "sandbox", "corridor": corridor},
    )
    payin_reference_from_api = str(payin.get("transaction_reference") or payin.get("payment_reference") or payin_reference)
    auth_model = str(payin.get("auth_model") or "").upper()
    print(json.dumps({"payin_status": status_of(payin), "payin_reference": payin_reference_from_api, "auth_model": auth_model}, default=str))
    if auth_model == "STK_PROMPT":
        print(json.dumps({
            "manual_authorization_required": True,
            "instruction": "Authorize the STK prompt on the sandbox handset; the PIN is never sent by AFM.",
            "poll_window_seconds": round(poll_attempts * poll_delay, 1),
        }))

    if str(payin.get("auth_model", "")).upper() == "OTP":
        token = os.getenv("KORA_TEST_OTP", "")
        if not token:
            raise RuntimeError("KORA_TEST_OTP is required when Kora returns OTP")
        payin = await client.authorize_mobile_money(reference=payin_reference_from_api, token=token)
        print(json.dumps({"authorization_status": status_of(payin)}, default=str))

    final_payin = await poll_charge(client, payin_reference_from_api, poll_attempts, poll_delay)
    final_payin_status = status_of(final_payin)
    if final_payin_status not in SUCCESS_STATUSES:
        print(json.dumps({"refund_skipped": True, "reason": "pay-in did not reach a refundable success status", "payin_status": final_payin_status}, default=str))
        return 2

    refund = await client.initiate_refund(
        payment_reference=payin_reference_from_api,
        refund_reference=refund_reference,
        amount=amount,
        reason="AFM sandbox end-to-end refund validation",
        webhook_url=webhook_url,
    )
    refund_reference_from_api = str(refund.get("refund_reference") or refund.get("reference") or refund_reference)
    print(json.dumps({"refund_status": status_of(refund), "refund_reference": refund_reference_from_api}, default=str))

    final_refund = await poll_refund(client, refund_reference_from_api, poll_attempts, poll_delay)
    final_refund_status = status_of(final_refund)
    print(json.dumps({"final_payin_status": final_payin_status, "final_refund_status": final_refund_status, "webhook_url": webhook_url, "elapsed_seconds": round(time.monotonic() - started_at, 3)}, default=str))
    return 0 if final_refund_status in SUCCESS_STATUSES else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the guarded Cameroon XAF Kora refund sandbox flow")
    parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run()))
    except (RuntimeError, KoraClientError) as exc:
        print(f"sandbox refund refused/failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
