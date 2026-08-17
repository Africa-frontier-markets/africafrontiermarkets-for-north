"""
AFM Webhook Monitoring — Kora art. 4.1/5.4 ("Webhook Monitoring: Status,
Retries, Latency, Replay").

Scope volontaire : une entrée WebhookEvent qui ne résout à aucune
transaction connue (référence absente ou inconnue) ne peut être rattachée à
un marchand — elle n'est donc visible que pour un futur rôle
d'administration AFM. L'endpoint /api/v1/webhooks (non-admin) ne retourne
que les webhooks liés à une transaction du marchand appelant — cohérent
avec l'isolation déjà appliquée sur /transactions et /activity-logs, jamais
une vue globale non filtrée. La vue transverse existe séparément
(list_all_admin), réservée au rôle AFM_STAFF.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from payment_hub.models import WebhookEvent, Transaction


@dataclass
class WebhookMonitoringStats:
    total: int
    processed: int
    failed: int
    no_reference: int
    total_replays: int
    avg_latency_ms: Optional[float]


class WebhookMonitoringService:
    async def list_for_user(
        self,
        session: AsyncSession,
        user_id: UUID,
        status: Optional[str] = None,
        psp: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[WebhookEvent], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)

        base = (
            select(WebhookEvent)
            .join(Transaction, Transaction.id == WebhookEvent.transaction_id)
            .where(Transaction.user_id == user_id)
        )
        count_base = (
            select(func.count(WebhookEvent.id))
            .join(Transaction, Transaction.id == WebhookEvent.transaction_id)
            .where(Transaction.user_id == user_id)
        )

        if status:
            base = base.where(WebhookEvent.status == status)
            count_base = count_base.where(WebhookEvent.status == status)
        if psp:
            base = base.where(WebhookEvent.psp == psp)
            count_base = count_base.where(WebhookEvent.psp == psp)

        total = (await session.execute(count_base)).scalar_one()

        stmt = base.order_by(WebhookEvent.received_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = list((await session.execute(stmt)).scalars().all())

        return items, total

    async def compute_stats_for_user(self, session: AsyncSession, user_id: UUID) -> WebhookMonitoringStats:
        base_conditions = [Transaction.user_id == user_id]

        rows = (await session.execute(
            select(WebhookEvent.status, func.count(WebhookEvent.id))
            .join(Transaction, Transaction.id == WebhookEvent.transaction_id)
            .where(and_(*base_conditions))
            .group_by(WebhookEvent.status)
        )).all()
        by_status = {status: count for status, count in rows}

        total_replays = (await session.execute(
            select(func.coalesce(func.sum(WebhookEvent.replay_count), 0))
            .join(Transaction, Transaction.id == WebhookEvent.transaction_id)
            .where(and_(*base_conditions))
        )).scalar_one()

        avg_latency_row = (await session.execute(
            select(
                func.avg(
                    func.extract("epoch", WebhookEvent.processed_at - WebhookEvent.received_at) * 1000
                )
            )
            .join(Transaction, Transaction.id == WebhookEvent.transaction_id)
            .where(and_(*base_conditions, WebhookEvent.processed_at.isnot(None)))
        )).scalar_one()

        total = sum(by_status.values())

        return WebhookMonitoringStats(
            total=total,
            processed=by_status.get("processed", 0),
            failed=by_status.get("failed", 0),
            no_reference=by_status.get("no_reference", 0),
            total_replays=int(total_replays or 0),
            avg_latency_ms=float(avg_latency_row) if avg_latency_row is not None else None,
        )

    async def list_all_admin(
        self,
        session: AsyncSession,
        status: Optional[str] = None,
        psp: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[WebhookEvent], int]:
        """Vue transverse — réservée à AFM_STAFF (voir
        get_current_admin_user_id dans api_gateway/main.py). Distincte de
        list_for_user par construction pour qu'une erreur d'appel ne puisse
        jamais accidentellement exposer les webhooks d'un marchand à un
        autre — il n'existe aucun paramètre 'skip user filter' sur la
        méthode scopée, seulement cette méthode séparée, explicitement
        nommée _admin."""
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)

        conditions = []
        if status:
            conditions.append(WebhookEvent.status == status)
        if psp:
            conditions.append(WebhookEvent.psp == psp)

        count_stmt = select(func.count(WebhookEvent.id))
        stmt = select(WebhookEvent)
        if conditions:
            count_stmt = count_stmt.where(and_(*conditions))
            stmt = stmt.where(and_(*conditions))

        total = (await session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(WebhookEvent.received_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = list((await session.execute(stmt)).scalars().all())
        return items, total


webhook_monitoring_service = WebhookMonitoringService()
