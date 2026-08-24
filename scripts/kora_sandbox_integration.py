"""Run a guarded Kora sandbox integration test.

This script requires KORA_SANDBOX=true and a test key. It never accepts a live
key and never calls authorize/settlement unless --execute is explicitly set.
Use only test phone/account fixtures provided by Kora.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The settings module instantiates a cached default at import time.
os.environ.setdefault("SECRET_KEY", "local-test-secret-012345678901234567890123456789012345678901234567")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from config.config import Settings
from payment_hub.kora_client import KoraClient, KoraClientError


def settings_from_env() -> Settings:
    secret = os.getenv("KORA_SECRET_KEY", "")
    if os.getenv("KORA_SANDBOX", "").lower() != "true":
        raise RuntimeError("Refusing to run: KORA_SANDBOX=true is required")
    if not secret or not secret.startswith("sk_test_"):
        raise RuntimeError("Refusing to run: KORA_SECRET_KEY must be a Kora test key")
    return Settings(
        secret_key=os.getenv("SECRET_KEY", "local-test-secret-012345678901234567890123456789012345678901234567"),
        database_url=os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379"),
        environment="development",
        kora_secret_key=secret,
        kora_api_base_url=os.getenv("KORA_API_BASE_URL", "https://api.korapay.com/merchant/api/v1"),
    )


async def run(execute: bool) -> int:
    settings = settings_from_env()
    client = KoraClient(settings)
    reference = f"afm-sbx-{os.urandom(8).hex()}"
    fixtures = {
        "amount": Decimal(os.getenv("KORA_TEST_AMOUNT", "100000")),
        "currency": os.getenv("KORA_TEST_CURRENCY", "XOF").upper(),
        "payout_currency": os.getenv("KORA_TEST_PAYOUT_CURRENCY", os.getenv("KORA_TEST_CURRENCY", "XOF")).upper(),
        "phone": os.getenv("KORA_TEST_PHONE", ""),
        "email": os.getenv("KORA_TEST_EMAIL", "sandbox@example.invalid"),
        "mobile_operator": os.getenv("KORA_TEST_MOBILE_OPERATOR", ""),
        "mobile_number": os.getenv("KORA_TEST_MOBILE_NUMBER", ""),
        "bank_code": os.getenv("KORA_TEST_BANK_CODE", ""),
        "account_number": os.getenv("KORA_TEST_ACCOUNT_NUMBER", ""),
    }
    if not fixtures["phone"] or not fixtures["mobile_operator"] or not fixtures["mobile_number"]:
        raise RuntimeError("Sandbox phone, mobile operator and mobile number fixtures are required")

    print(f"quote: requesting read-only {fixtures['currency']}/USD rate")
    quote = await client.get_exchange_rate(
        amount=fixtures["amount"], from_currency=fixtures["currency"], to_currency="USD", reference=f"{reference}-quote"
    )
    print(json.dumps({"rate": str(quote["rate"]), "expiry_date": quote.get("expiry_date")}, default=str))

    print("payin: initiating Mobile Money charge")
    payin = await client.initiate_mobile_money_charge(
        amount=fixtures["amount"], currency=fixtures["currency"], reference=f"{reference}-payin",
        phone_number=fixtures["phone"], customer_email=fixtures["email"],
        metadata={"afm_test": "true", "execution_mode": "sandbox"},
    )
    print(json.dumps({"status": payin.get("status"), "auth_model": payin.get("auth_model"), "reference": payin.get("transaction_reference")}, default=str))

    if execute and payin.get("auth_model") == "OTP":
        token = os.getenv("KORA_TEST_OTP", "")
        if not token:
            raise RuntimeError("KORA_TEST_OTP is required only for an OTP sandbox response")
        authorized = await client.authorize_mobile_money(reference=str(payin.get("transaction_reference")), token=token)
        print(json.dumps({"authorization_status": authorized.get("status")}, default=str))

    print("payout: initiating Mobile Money disbursement")
    payout_mobile = await client.create_payout(
        reference=f"{reference}-payout-mobile", amount=Decimal(os.getenv("KORA_TEST_PAYOUT_AMOUNT", "1000")), currency=fixtures["payout_currency"],
        customer_email=fixtures["email"], mobile_money_operator=fixtures["mobile_operator"],
        mobile_number=fixtures["mobile_number"],
    )
    print(json.dumps({"status": payout_mobile.get("status"), "reference": payout_mobile.get("reference")}, default=str))

    if fixtures["bank_code"] and fixtures["account_number"]:
        print("payout: initiating bank disbursement")
        payout_bank = await client.create_payout(
            reference=f"{reference}-payout-bank", amount=Decimal(os.getenv("KORA_TEST_BANK_AMOUNT", "1000")), currency=os.getenv("KORA_TEST_BANK_CURRENCY", "NGN").upper(),
            customer_email=fixtures["email"], bank_code=fixtures["bank_code"],
            account_number=fixtures["account_number"], bank_country=os.getenv("KORA_TEST_BANK_COUNTRY", "NG"),
        )
        print(json.dumps({"status": payout_bank.get("status"), "reference": payout_bank.get("reference")}, default=str))
    else:
        print("payout-bank: skipped; bank fixtures not supplied")
    print("sandbox integration completed; reconcile all references from Kora webhooks before marking settled")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Allow OTP authorization when Kora returns OTP")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args.execute)))
    except (RuntimeError, KoraClientError) as exc:
        print(f"sandbox integration refused/failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
