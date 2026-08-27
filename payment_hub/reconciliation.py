"""Durable reconciliation for Kora pay-ins that remain in processing."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config.config import get_settings
from payment_hub.kora_client import KoraClient, KoraClientError
from payment_hub.models import KoraPaymentReconciliation, PaymentStatus, Transaction
from payment_hub.support_ai import analyze_incident, enrich_with_external_llm

SUCCESS_STATUSES = {"success", "successful", "completed", "settled"}
FAILURE_STATUSES = {"failed", "cancelled", "canceled", "reversed"}
PENDING_STATUSES = {"", "pending", "processing", "requires_authorization"}
logger = logging.getLogger(__name__)


def _normalized_phone(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _provider_phone_matches(transaction: Transaction, response: object) -> bool:
    expected = _normalized_phone(transaction.mobile_money_phone)
    if not expected or not isinstance(response, dict):
        return False
    candidates: list[object] = []
    for key in ("phone_number", "mobile_number", "phone", "number"):
        if key in response:
            candidates.append(response[key])
    mobile_money = response.get("mobile_money")
    if isinstance(mobile_money, dict):
        candidates.extend(mobile_money.get(key) for key in ("number", "mobile_number", "phone_number"))
    return any(_normalized_phone(candidate) == expected for candidate in candidates if candidate)


def normalize_kora_status(raw: object) -> str:
    value = str(raw or "").strip().lower()
    if value in SUCCESS_STATUSES:
        return "completed"
    if value in FAILURE_STATUSES:
        return "failed"
    return "processing"


def status_of(data: dict[str, Any]) -> str:
    return str(data.get("status") or data.get("payment_status") or "").strip().lower()


def next_backoff(attempt: int) -> int:
    if attempt >= 6:
        return 300
    return min(300, 15 * (2 ** min(max(attempt - 1, 0), 4)))


async def enqueue_reconciliation(
    db: AsyncSession,
    transaction: Transaction,
    data: dict[str, Any],
) -> KoraPaymentReconciliation:
    payment_reference = str(data.get("payment_reference") or transaction.psp_transaction_id or "")
    transaction_reference = str(data.get("transaction_reference") or "")
    result = await db.execute(
        select(KoraPaymentReconciliation).where(
            KoraPaymentReconciliation.transaction_id == transaction.id
        )
    )
    task = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if task is None:
        task = KoraPaymentReconciliation(
            transaction_id=transaction.id,
            payment_reference=payment_reference,
            transaction_reference=transaction_reference or None,
            provider_status=status_of(data),
            state="scheduled",
            next_attempt_at=now + timedelta(seconds=15),
            expires_at=now + timedelta(hours=24),
        )
        db.add(task)
    else:
        if payment_reference:
            task.payment_reference = payment_reference
        if transaction_reference:
            task.transaction_reference = transaction_reference
        task.provider_status = status_of(data)
        if task.state not in {"completed", "failed"}:
            task.state = "scheduled"
            task.next_attempt_at = min(task.next_attempt_at or now, now + timedelta(seconds=15))
    transaction.txn_metadata = {
        **(transaction.txn_metadata or {}),
        "reconciliation_status": "scheduled",
        "provider_status": status_of(data),
    }
    return task


async def reconcile_due_once(
    session_factory: async_sessionmaker[AsyncSession],
    client_factory: Callable[[], KoraClient] | None = None,
) -> bool:
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        result = await db.execute(
            select(KoraPaymentReconciliation)
            .where(
                or_(
                    and_(
                        KoraPaymentReconciliation.state == "scheduled",
                        KoraPaymentReconciliation.next_attempt_at <= now,
                    ),
                    and_(
                        KoraPaymentReconciliation.state == "processing",
                        KoraPaymentReconciliation.last_checked_at <= now - timedelta(minutes=5),
                    ),
                )
            )
            .order_by(KoraPaymentReconciliation.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return False
        task.state = "processing"
        task.attempt_count += 1
        task.last_checked_at = now
        task.locked_at = now
        task.locked_by = f"afm-reconciliation-{os.getpid()}"
        task.updated_at = now
        await db.commit()
        task_id = task.id
        reference = task.payment_reference or task.transaction_reference

    client = client_factory() if client_factory else KoraClient(get_settings())
    try:
        response = await client.verify_charge(reference=reference)
        raw_status = status_of(response)
        normalized = normalize_kora_status(raw_status)
        error = None
    except KoraClientError as exc:
        response = {}
        raw_status = ""
        normalized = "processing"
        error = str(exc)[:500]

    async with session_factory() as db:
        result = await db.execute(
            select(KoraPaymentReconciliation).where(KoraPaymentReconciliation.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task is None:
            return True
        transaction = await db.get(Transaction, task.transaction_id)
        now = datetime.now(timezone.utc)
        task.provider_status = raw_status or task.provider_status
        task.last_error = error
        task.locked_at = None
        task.locked_by = None
        task.updated_at = now
        if transaction is not None:
            transaction.psp_response = {
                **(transaction.psp_response or {}),
                "reconciliation": response or {"error": error},
                "provider_status": raw_status or task.provider_status,
            }
            if _provider_phone_matches(transaction, response):
                transaction.mobile_money_owner_verified_at = now
            if normalized == "completed" and transaction.status not in {
                PaymentStatus.COMPLETED,
                PaymentStatus.REFUNDED,
            }:
                transaction.status = PaymentStatus.COMPLETED
                transaction.settled_at = now
            elif normalized == "failed" and transaction.status not in {
                PaymentStatus.COMPLETED,
                PaymentStatus.REFUNDED,
            }:
                transaction.status = PaymentStatus.FAILED
                transaction.error_message = raw_status or error or "Kora payment failed"
        if normalized in {"completed", "failed"}:
            task.state = normalized
            task.next_attempt_at = None
        elif task.expires_at and now >= task.expires_at:
            task.state = "expired"
            task.next_attempt_at = None
        else:
            task.state = "scheduled"
            task.next_attempt_at = now + timedelta(seconds=next_backoff(task.attempt_count))
        await db.commit()
    return True


async def reconciliation_worker(
    stop_event: asyncio.Event,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    while not stop_event.is_set():
        try:
            processed = await reconcile_due_once(session_factory)
            if not processed:
                await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            continue
        except Exception as exc:
            decision = await enrich_with_external_llm(
                analyze_incident(
                    str(exc),
                    {"component": "reconciliation_worker", "provider": "kora"},
                )
            )
            # The support layer may diagnose and propose a safe action, but it
            # never mutates payment state or executes a provider operation here.
            logger.error(
                "Corrective support diagnosis",
                extra={
                    "incident_key": decision.incident_key,
                    "category": decision.category,
                    "proposed_action": decision.proposed_action,
                    "requires_human_approval": decision.requires_human_approval,
                },
            )
            await asyncio.sleep(5)
