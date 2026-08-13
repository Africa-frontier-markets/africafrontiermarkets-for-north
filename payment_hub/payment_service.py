"""Payment service with Redis-backed idempotency lock. V45+ Production Correction."""

import hashlib
from decimal import Decimal
from datetime import datetime, timezone

import redis.asyncio as redis

from config.config import get_settings
from config.exceptions import PaymentError, ConflictError, CurrencyNotSupportedError
from config.security import verify_webhook_signature
from config.telemetry import payments_processed
from payment_hub.models import Transaction, PaymentStatus, PSPType
from payment_hub.psp_router import psp_router
from event_bus.event_schema import BaseEvent, EventType
from event_bus.redis_producer import event_producer

settings = get_settings()


class PaymentService:
    SUPPORTED_CURRENCIES = {
        "XOF", "XAF", "NGN", "KES", "GHS", "ZAR",
        "USD", "EUR", "GBP", "CAD", "AUD",
    }

    def __init__(self):
        self._redis = None

    async def _get_redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.redis_url,
                max_connections=settings.redis_pool_size,
                decode_responses=True,
            )
        return self._redis

    def _generate_lock_id(self, user_id: str, amount: str, currency: str, timestamp: str) -> str:
        """V45: SHA256 stable lock ID for idempotency."""
        key = f"{user_id}:{amount}:{currency}:{timestamp}"
        return hashlib.sha256(key.encode()).hexdigest()

    async def _acquire_lock(self, lock_id: str, ttl_seconds: int = 300) -> bool:
        """Production Correction: Redis distributed lock instead of in-memory set()."""
        redis_client = await self._get_redis()
        # SET NX EX: set if not exists, with expiry
        acquired = await redis_client.set(f"payment_lock:{lock_id}", "1", nx=True, ex=ttl_seconds)
        return acquired is not None

    async def _release_lock(self, lock_id: str) -> None:
        redis_client = await self._get_redis()
        await redis_client.delete(f"payment_lock:{lock_id}")
"""
AFM Payment Service — REAL DB persistence + REAL HTTP calls to PSPs
"""

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx

from config.config import get_settings
from config.database import AsyncSessionLocal
from config.exceptions import (
    PaymentError, ConflictError, CurrencyNotSupportedError,
    ValidationError, PSPAPIError, NotFoundError,
)
from config.security import hash_idempotency_key
from config.logging_config import configure_logging
from common.fx import FX_RATES_TO_USD, convert_usd_bounds
from payment_hub.models import Transaction, PaymentStatus, PSPType
from payment_hub.psp_router import psp_router

logger = configure_logging()


class PaymentService:
    """
    AFM Payment Service — Production Implementation

    Features:
    - REAL DB persistence (session.add() + commit())
    - REAL HTTP calls to PSP APIs (Kora, Fincra, Flutterwave)
    - Idempotency with SHA256 keys (header-aware, not date-truncated)
    - FX-aware fee calculation (converts min/max bounds to local currency)
    - Orphan transaction cleanup on unexpected errors
    """

    SUPPORTED_CURRENCIES = {
        "XOF", "XAF", "NGN", "KES", "GHS", "ZAR",
        "USD", "EUR", "GBP",
    }

    # FX rates and fee-bound conversion now live in common/fx.py — this
    # used to be a second, independent copy that could (and did) drift
    # from revenue_engine's version. See common/fx.py docstring.

    def __init__(self):
        self._pending_locks: set[str] = set()
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    # Default dedup window when the caller doesn't supply X-Idempotency-Key.
    # A genuine network retry of the same request typically happens within
    # seconds; anything past this window is treated as a new, intentional
    # payment. Tunable, but must exist — see bug note below.
    DEFAULT_IDEMPOTENCY_WINDOW_SECONDS = 300

    def _generate_idempotency_key(
        self,
        user_id: str,
        amount: str,
        currency: str,
        client_key: Optional[str] = None,
        method: str = "",
        phone_number: Optional[str] = None,
    ) -> str:
        """
        Generate stable idempotency key.

        If client provides X-Idempotency-Key header, use it (scoped per
        user, so two different users can't collide on the same client key
        against the global unique DB constraint).

        Otherwise, derive a deterministic key from user_id + amount +
        currency + method + phone_number + a coarse time bucket.

        🔴 BUG FIXED: the previous implementation appended
        `datetime.now(...).isoformat(timespec="microseconds")` and a random
        uuid4 suffix to the auto-generated key. That made every
        auto-generated key unique *by construction*, so a client retrying
        an identical request after a timeout (the #1 reason idempotency
        keys exist) always produced a brand-new key — resulting in a
        duplicate charge instead of being deduplicated. Clients that care
        about strict idempotency should still pass X-Idempotency-Key
        explicitly; this fallback just makes the "no header" default
        behave sanely instead of providing zero protection.
        """
        if client_key:
            # Client-provided key — scope by user + hash to normalize length
            return hashlib.sha256(f"client:{user_id}:{client_key}".encode()).hexdigest()

        time_bucket = int(
            datetime.now(timezone.utc).timestamp() // self.DEFAULT_IDEMPOTENCY_WINDOW_SECONDS
        )
        key = f"{user_id}:{amount}:{currency}:{method}:{phone_number or ''}:{time_bucket}"
        return hashlib.sha256(key.encode()).hexdigest()

    def _convert_fee_bounds(self, currency: str) -> tuple[Decimal, Decimal]:
        """
        Convert USD fee bounds to local currency (delegates to common.fx,
        the single source of truth also used by revenue_engine).
        """
        settings = get_settings()
        return convert_usd_bounds(
            settings.afm_min_fee_usd, settings.afm_max_fee_usd, currency, logger=logger
        )

    def _calculate_afm_fee(self, amount: Decimal, currency: str) -> tuple[Decimal, Decimal]:
        """
        Calculate AFM commission with FX-aware bounds.

        Returns (fee_amount, net_amount) in the transaction currency.
        """
        settings = get_settings()

        # Commission in local currency
        fee = (amount * Decimal(str(settings.afm_commission_bps)) / Decimal("10000"))

        # Apply FX-aware min/max bounds
        min_fee, max_fee = self._convert_fee_bounds(currency)
        fee = max(fee, min_fee)
        fee = min(fee, max_fee)

        fee = fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        net = amount - fee

        logger.info(
            "Fee calculated",
            amount=str(amount),
            currency=currency,
            fee=str(fee),
            net=str(net),
            min_bound=str(min_fee),
            max_bound=str(max_fee),
        )

        return fee, net

    async def process_payment(
        self,
        user_id: str,
        amount: Decimal,
        currency: str,
        method: str = "mobile_money",
        region: str = "west_africa",
        phone_number: Optional[str] = None,
        metadata: Optional[Dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Transaction:
        """
        Process a payment end-to-end with orphan cleanup.
        """

        # 1. Validate
        currency = currency.upper()
        if currency not in self.SUPPORTED_CURRENCIES:
            raise CurrencyNotSupportedError(f"Currency {currency} not supported")

        if amount <= 0:
            raise PaymentError("Amount must be greater than 0")

        timestamp = datetime.now(timezone.utc).isoformat()
        idempotency_key = self._generate_lock_id(
            str(user_id),
            str(amount),
            currency,
            timestamp[:10],
        )

        # Production Correction: Redis distributed lock (multi-pod safe)
        lock_acquired = await self._acquire_lock(idempotency_key, ttl_seconds=300)
        if not lock_acquired:
            raise ConflictError("Duplicate payment detected (already processing)")

        try:
            # Production Correction: Use PSP router instead of hardcoded Kora
            selected_psp = psp_router.select_psp(currency, method=method, region=region)

            transaction = Transaction(
                idempotency_key=idempotency_key,
                user_id=user_id,
                psp=selected_psp,
                amount=amount,
                currency=currency,
                status=PaymentStatus.PENDING,
                metadata=metadata or {},
            )

            event = BaseEvent(
                event_type=EventType.PAYMENT_INITIATED,
                payload={
                    "transaction_id": str(transaction.id),
                    "amount": str(amount),
                    "currency": currency,
                    "psp": selected_psp.value,
                    "user_id": str(user_id),
                    "status": "pending",
                },
            )
            await event_producer.publish(event)

            payments_processed.labels(
                psp=selected_psp.value,
                status="pending",
                currency=currency,
            ).inc()

            return transaction

        finally:
            await self._release_lock(idempotency_key)

    async def verify_webhook(self, psp: str, payload: bytes, signature: str) -> bool:
        secrets_map = {
            "kora": settings.kora_webhook_secret,
            "fincra": settings.fincra_webhook_secret,
        }
        secret = secrets_map.get(psp)
        if not secret:
            return False
        return verify_webhook_signature(payload, signature, secret)

    async def close(self):
        if self._redis:
            await self._redis.close()
            raise ValidationError("Amount must be greater than 0")

        if phone_number is None and method == "mobile_money":
            raise ValidationError("phone_number required for mobile_money")

        # 2. Idempotency key (header-aware, deterministic time-bucket fallback)
        idempotency_key = self._generate_idempotency_key(
            str(user_id), str(amount), currency,
            client_key=idempotency_key, method=method, phone_number=phone_number,
        )

        if idempotency_key in self._pending_locks:
            raise ConflictError("Duplicate payment detected")

        self._pending_locks.add(idempotency_key)

        transaction: Optional[Transaction] = None

        try:
            # 3. Select PSP
            psp = psp_router.select_psp(currency, method=method, region=region, amount=str(amount))

            # 4. Calculate fee with FX-aware bounds
            fee_amount, net_amount = self._calculate_afm_fee(amount, currency)

            # 5. Create DB record — REAL PERSISTENCE
            async with AsyncSessionLocal() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(Transaction).where(Transaction.idempotency_key == idempotency_key)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    raise ConflictError(f"Transaction with idempotency key already exists: {existing.id}")

                transaction = Transaction(
                    idempotency_key=idempotency_key,
                    user_id=user_id,
                    psp=psp,
                    amount=amount,
                    currency=currency,
                    fee_amount=fee_amount,
                    fee_currency=currency,  # Fee in same currency as transaction
                    net_amount=net_amount,
                    status=PaymentStatus.PENDING,
                    txn_metadata=metadata or {},
                )
                session.add(transaction)
                await session.commit()
                await session.refresh(transaction)

                logger.info(
                    "Transaction created in DB",
                    transaction_id=str(transaction.id),
                    idempotency_key=idempotency_key,
                )

            # 6. Call PSP API — with orphan protection
            try:
                psp_response = await self._call_psp_api(
                    psp=psp,
                    transaction=transaction,
                    phone_number=phone_number,
                    method=method,
                )
            except Exception as psp_error:
                # 🟡 FIX: Mark transaction as FAILED on PSP error (no orphan)
                logger.error("PSP call failed, marking transaction FAILED", error=str(psp_error))
                async with AsyncSessionLocal() as session:
                    transaction.status = PaymentStatus.FAILED
                    transaction.error_message = f"PSP error: {psp_error}"
                    await session.merge(transaction)
                    await session.commit()
                raise PSPAPIError(f"Payment provider error: {psp_error}")

            # 7. Update DB with PSP result
            async with AsyncSessionLocal() as session:
                transaction.psp_transaction_id = psp_response.get("psp_transaction_id")
                transaction.psp_response = psp_response

                if psp_response.get("success"):
                    transaction.status = PaymentStatus.COMPLETED
                    transaction.settled_at = datetime.now(timezone.utc)
                else:
                    transaction.status = PaymentStatus.FAILED
                    transaction.error_message = psp_response.get("error", "PSP processing failed")

                await session.merge(transaction)
                await session.commit()

                logger.info(
                    "Transaction finalized",
                    transaction_id=str(transaction.id),
                    status=transaction.status.value,
                )

                return transaction

        finally:
            self._pending_locks.discard(idempotency_key)

    async def _call_psp_api(
        self,
        psp: PSPType,
        transaction: Transaction,
        phone_number: Optional[str],
        method: str,
    ) -> Dict[str, Any]:
        """REAL HTTP call to PSP API."""
        settings = get_settings()
        client = await self._get_http_client()

        try:
            if psp in (PSPType.MTN_MOMO, PSPType.ORANGE_MONEY):
                # 🔴 FIX: these are mobile-money *rails*, not PSPs in their
                # own right — they must go through an actual aggregator
                # (Kora or Flutterwave). The old code always called
                # _call_kora() here regardless of which aggregator's
                # credentials were actually configured. psp_router only
                # checked `kora_api_key OR flutterwave_public_key` for
                # these two, so if only Flutterwave creds were set,
                # _call_kora() would see no kora_secret_key and silently
                # return a "simulated success" — i.e. a fake COMPLETED
                # transaction in production with no money actually moved.
                if settings.kora_secret_key:
                    return await self._call_kora(client, transaction, phone_number, settings)
                elif settings.flutterwave_secret_key:
                    return await self._call_flutterwave(client, transaction, phone_number, settings)
                elif settings.is_development:
                    return await self._call_kora(client, transaction, phone_number, settings)
                else:
                    raise PSPAPIError(
                        f"No aggregator credentials configured for {psp.value} in production"
                    )
            elif psp == PSPType.KORA:
                return await self._call_kora(client, transaction, phone_number, settings)
            elif psp == PSPType.FINCRA:
                return await self._call_fincra(client, transaction, settings)
            elif psp == PSPType.FLUTTERWAVE:
                return await self._call_flutterwave(client, transaction, phone_number, settings)
            elif psp == PSPType.STRIPE:
                return await self._call_stripe(client, transaction, settings)
            else:
                raise PSPAPIError(f"Unsupported PSP: {psp}")

        except httpx.HTTPError as e:
            logger.error("PSP HTTP error", psp=psp.value, error=str(e))
            return {"success": False, "error": f"HTTP error: {e}"}
        except Exception as e:
            logger.error("PSP API error", psp=psp.value, error=str(e))
            raise  # Re-raise to be caught by orphan cleanup

    async def _call_kora(self, client, transaction, phone_number, settings):
        """Call Kora API."""
        if settings.is_development:
            logger.warning("Kora simulated")
            return {
                "success": True,
                "psp_transaction_id": f"kora_sim_{hashlib.sha256(transaction.idempotency_key.encode()).hexdigest()[:16]}",
                "status": "success",
            }
        if not settings.kora_secret_key:
            # 🔴 FIX: never silently fake a successful payment outside of
            # development. Previously `not kora_secret_key` alone triggered
            # simulation even in production — a misconfiguration (missing
            # env var) would mark real transactions COMPLETED with no
            # money ever moved.
            raise PSPAPIError("Kora secret key not configured in production")

        url = "https://api.korapay.com/merchant/api/v1/charges/mobile-money"
        payload = {
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "reference": transaction.idempotency_key,
            "customer": {
                "email": transaction.txn_metadata.get("email", "customer@afm.com"),
            },
            "mobile_money": {
                "phone": phone_number,
                "provider": self._detect_provider(phone_number, transaction.currency),
            } if phone_number else None,
        }
        headers = {"Authorization": f"Bearer {settings.kora_secret_key}", "Content-Type": "application/json"}

        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        return {
            "success": data.get("status") == "success",
            "psp_transaction_id": data.get("data", {}).get("reference"),
            "raw_response": data,
        }

    async def _call_fincra(self, client, transaction, settings):
        """Call Fincra API."""
        if settings.is_development:
            logger.warning("Fincra simulated")
            return {
                "success": True,
                "psp_transaction_id": f"fincra_sim_{hashlib.sha256(transaction.idempotency_key.encode()).hexdigest()[:16]}",
            }
        if not settings.fincra_secret_key:
            raise PSPAPIError("Fincra secret key not configured in production")

        url = "https://api.fincra.com/disbursements/payouts"
        payload = {
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "reference": transaction.idempotency_key,
        }
        headers = {"api-key": settings.fincra_secret_key, "Content-Type": "application/json"}

        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        return {
            "success": data.get("status") == "success",
            "psp_transaction_id": data.get("data", {}).get("reference"),
            "raw_response": data,
        }

    async def _call_flutterwave(self, client, transaction, phone_number, settings):
        """Call Flutterwave API."""
        if settings.is_development:
            logger.warning("Flutterwave simulated")
            return {
                "success": True,
                "psp_transaction_id": f"fw_sim_{hashlib.sha256(transaction.idempotency_key.encode()).hexdigest()[:16]}",
            }
        if not settings.flutterwave_secret_key:
            raise PSPAPIError("Flutterwave secret key not configured in production")

        url = "https://api.flutterwave.com/v3/charges?type=mobile_money_"
        payload = {
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "tx_ref": transaction.idempotency_key,
            "phone_number": phone_number,
        }
        headers = {"Authorization": f"Bearer {settings.flutterwave_secret_key}", "Content-Type": "application/json"}

        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        return {
            "success": data.get("status") == "success",
            "psp_transaction_id": data.get("data", {}).get("id"),
            "raw_response": data,
        }

    async def _call_stripe(self, client, transaction, settings):
        """Call Stripe API."""
        if settings.is_development:
            logger.warning("Stripe simulated")
            return {
                "success": True,
                "psp_transaction_id": f"stripe_sim_{hashlib.sha256(transaction.idempotency_key.encode()).hexdigest()[:16]}",
                "status": "requires_confirmation",
            }
        if not settings.stripe_secret_key:
            raise PSPAPIError("Stripe secret key not configured in production")

        url = "https://api.stripe.com/v1/payment_intents"
        payload = {
            "amount": int(transaction.amount * 100),
            "currency": transaction.currency.lower(),
            "metadata[afm_reference]": transaction.idempotency_key,
        }
        headers = {"Authorization": f"Bearer {settings.stripe_secret_key}"}

        response = await client.post(url, data=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        return {
            "success": data.get("status") in ("succeeded", "requires_confirmation"),
            "psp_transaction_id": data.get("id"),
            "client_secret": data.get("client_secret"),
            "raw_response": data,
        }

    def _detect_provider(self, phone_number: str, currency: str) -> str:
        """Detect mobile money provider from phone number prefix."""
        if currency == "XOF":
            if phone_number.startswith(("+223", "223")): return "orange_money"
            elif phone_number.startswith(("+225", "225")): return "mtn"
            elif phone_number.startswith(("+221", "221")): return "wave"
        elif currency == "XAF":
            if phone_number.startswith(("+237", "237")): return "mtn"
        elif currency == "NGN": return "mtn"
        elif currency == "KES": return "mpesa"
        elif currency == "GHS": return "mtn"
        return "mtn"

    async def get_transaction(self, transaction_id) -> Transaction:
        """Get transaction by ID (accepts str or uuid.UUID)."""
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Transaction).where(Transaction.id == transaction_id)
            )
            transaction = result.scalar_one_or_none()
            if not transaction:
                raise NotFoundError(f"Transaction {transaction_id} not found")
            return transaction

    async def verify_webhook(self, psp: str, payload: bytes, signature: str) -> bool:
        from config.security import verify_webhook_signature
        settings = get_settings()
        secrets_map = {"kora": settings.kora_webhook_secret, "fincra": settings.fincra_webhook_secret}
        secret = secrets_map.get(psp)
        if not secret: return False
        return verify_webhook_signature(payload, signature, secret)

    async def close(self):
        if self._http_client:
            await self._http_client.aclose()


payment_service = PaymentService()
