"""
AFM Merchant Monitoring & Activity Log services.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config.exceptions import NotFoundError, ValidationError, LedgerError
from config.logging_config import configure_logging
from merchant.models import (
    Merchant, MerchantStatus, KYCTier,
    ChargebackEvent, ChargebackStatus,
    ActivityLog, ActivityEventType,
)
from payment_hub.models import Transaction, PaymentStatus
from payment_hub.ledger_engine import ledger_engine
from payment_hub.transaction_query_service import ORCHESTRATION_RAIL_THRESHOLD

logger = configure_logging()

# Seuil contractuel Kora art. 7.11 — "Monitoring du taux de chargeback (<0.5%)".
# Gardé comme constante nommée plutôt qu'un nombre en dur dans le calcul, pour
# que le lien avec l'article du contrat reste visible dans le code.
KORA_MAX_CHARGEBACK_RATE = Decimal("0.005")  # 0.5%

# Fenêtre par défaut pour le calcul de volume/refund%/chargeback% — glissante,
# pas depuis la création du compte, pour refléter le risque récent plutôt
# qu'une moyenne diluée sur toute la durée de vie du marchand.
DEFAULT_MONITORING_WINDOW_DAYS = 30


class ActivityLogService:
    async def log(
        self,
        session: AsyncSession,
        event_type: ActivityEventType,
        description: str,
        merchant_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        extra_data: Optional[dict] = None,
    ) -> ActivityLog:
        """Ajoute une entrée au journal — ne commit PAS. L'entrée n'est
        persistée que si le caller commit sa propre session, ce qui la rend
        atomique avec l'opération qu'elle documente (ex: un paiement qui
        échoue à se committer ne laisse pas non plus une trace
        PAYMENT_INITIATED orpheline en base)."""
        entry = ActivityLog(
            merchant_id=merchant_id,
            user_id=user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            description=description,
            extra_data=extra_data or {},
        )
        session.add(entry)
        await session.flush()
        return entry

    async def list_for_merchant(
        self,
        session: AsyncSession,
        merchant_id: UUID,
        event_type: Optional[ActivityEventType] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ActivityLog], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)

        conditions = [ActivityLog.merchant_id == merchant_id]
        if event_type:
            conditions.append(ActivityLog.event_type == event_type)
        if date_from:
            conditions.append(ActivityLog.created_at >= date_from)
        if date_to:
            conditions.append(ActivityLog.created_at <= date_to)

        count_stmt = select(func.count(ActivityLog.id)).where(and_(*conditions))
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = (
            select(ActivityLog)
            .where(and_(*conditions))
            .order_by(ActivityLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list((await session.execute(stmt)).scalars().all())
        return items, total


@dataclass
class MerchantMonitoring:
    merchant_id: str
    status: str
    kyc_tier: str
    window_days: int
    transaction_count: int
    volume_by_currency: dict[str, str]
    refund_rate: str
    chargeback_rate: str
    exceeds_kora_chargeback_threshold: bool
    risk_score: int
    risk_band: str


class MerchantMonitoringService:
    def __init__(self):
        self.activity_log = ActivityLogService()

    async def get_or_create_for_user(self, session: AsyncSession, user_id: UUID) -> Merchant:
        result = await session.execute(select(Merchant).where(Merchant.user_id == user_id))
        merchant = result.scalar_one_or_none()
        if merchant:
            return merchant

        merchant = Merchant(user_id=user_id, status=MerchantStatus.PENDING_REVIEW, kyc_tier=KYCTier.NONE)
        session.add(merchant)
        await session.flush()

        await self.activity_log.log(
            session,
            ActivityEventType.MERCHANT_CREATED,
            f"Compte marchand créé pour l'utilisateur {user_id}",
            merchant_id=merchant.id,
            user_id=user_id,
            entity_type="merchant",
            entity_id=merchant.id,
        )
        return merchant

    async def get_by_id(self, session: AsyncSession, merchant_id: UUID) -> Merchant:
        merchant = await session.get(Merchant, merchant_id)
        if not merchant:
            raise NotFoundError(f"Merchant {merchant_id} not found")
        return merchant

    def _compute_risk_score(
        self,
        chargeback_rate: Decimal,
        refund_rate: Decimal,
        kyc_tier: KYCTier,
        account_age_days: int,
    ) -> tuple[int, str]:
        """
        Heuristique de premier niveau, volontairement simple et lisible —
        PAS un modèle de scoring de fraude. Sert à trier/prioriser une revue
        humaine, pas à bloquer automatiquement un marchand. Documenté ainsi
        pour ne pas survendre ce que fait réellement ce calcul (même principe
        que le reste du projet : pas d'affirmation qui dépasse ce que le code
        démontre).
        """
        score = 0

        if chargeback_rate > KORA_MAX_CHARGEBACK_RATE * 2:
            score += 70
        elif chargeback_rate > KORA_MAX_CHARGEBACK_RATE:
            score += 40

        if refund_rate > Decimal("0.10"):
            score += 20
        elif refund_rate > Decimal("0.05"):
            score += 10

        if kyc_tier == KYCTier.NONE:
            score += 20
        elif kyc_tier == KYCTier.BASIC:
            score += 5

        if account_age_days < 30:
            score += 10

        score = min(score, 100)

        if score >= 60:
            band = "high"
        elif score >= 30:
            band = "medium"
        else:
            band = "low"

        return score, band

    async def compute_monitoring(
        self, session: AsyncSession, merchant: Merchant, window_days: int = DEFAULT_MONITORING_WINDOW_DAYS
    ) -> MerchantMonitoring:
        since = datetime.now(timezone.utc) - timedelta(days=window_days)

        base_conditions = [Transaction.user_id == merchant.user_id, Transaction.created_at >= since]

        total_count = (await session.execute(
            select(func.count(Transaction.id)).where(and_(*base_conditions))
        )).scalar_one()

        volume_rows = (await session.execute(
            select(Transaction.currency, func.sum(Transaction.amount))
            .where(and_(*base_conditions))
            .group_by(Transaction.currency)
        )).all()
        volume_by_currency = {cur: str(vol) for cur, vol in volume_rows}

        refunded_count = (await session.execute(
            select(func.count(Transaction.id)).where(
                and_(*base_conditions, Transaction.status == PaymentStatus.REFUNDED)
            )
        )).scalar_one()

        chargeback_count = (await session.execute(
            select(func.count(ChargebackEvent.id)).where(
                ChargebackEvent.merchant_id == merchant.id,
                ChargebackEvent.opened_at >= since,
            )
        )).scalar_one()

        refund_rate = (Decimal(refunded_count) / Decimal(total_count)) if total_count else Decimal("0")
        chargeback_rate = (Decimal(chargeback_count) / Decimal(total_count)) if total_count else Decimal("0")

        account_age_days = (datetime.now(timezone.utc) - merchant.created_at).days if merchant.created_at else 0
        risk_score, risk_band = self._compute_risk_score(
            chargeback_rate, refund_rate, merchant.kyc_tier, account_age_days
        )

        return MerchantMonitoring(
            merchant_id=str(merchant.id),
            status=merchant.status.value,
            kyc_tier=merchant.kyc_tier.value,
            window_days=window_days,
            transaction_count=total_count,
            volume_by_currency=volume_by_currency,
            refund_rate=str(refund_rate.quantize(Decimal("0.0001"))),
            chargeback_rate=str(chargeback_rate.quantize(Decimal("0.0001"))),
            exceeds_kora_chargeback_threshold=chargeback_rate > KORA_MAX_CHARGEBACK_RATE,
            risk_score=risk_score,
            risk_band=risk_band,
        )

    async def initiate_refund(
        self, session: AsyncSession, transaction: Transaction, merchant: Merchant, reason: str
    ) -> Transaction:
        if transaction.status != PaymentStatus.COMPLETED:
            raise ValidationError(
                f"Cannot refund a transaction in status={transaction.status.value} — only COMPLETED transactions can be refunded"
            )

        await self.activity_log.log(
            session, ActivityEventType.REFUND_INITIATED,
            f"Remboursement initié pour la transaction {transaction.id} — motif: {reason}",
            merchant_id=merchant.id, entity_type="transaction", entity_id=transaction.id,
            extra_data={"reason": reason},
        )

        journal_id = (transaction.extra_data or {}).get("ledger_journal_id")
        if journal_id:
            reversal_id = await ledger_engine.reverse_journal(
                session, UUID(journal_id), reason=f"Refund: {reason}", transaction_id=transaction.id,
            )
            transaction.extra_data = {**(transaction.extra_data or {}), "refund_reversal_journal_id": str(reversal_id)}
        else:
            # Pas de journal ledger à contrepasser (ex: transaction complétée
            # avant le déploiement du ledger) — on log l'anomalie plutôt que
            # de la masquer, mais on ne bloque pas le remboursement.
            logger.warning("Refund without a ledger journal to reverse", transaction_id=str(transaction.id))

        transaction.status = PaymentStatus.REFUNDED
        await session.flush()

        await self.activity_log.log(
            session, ActivityEventType.REFUND_COMPLETED,
            f"Remboursement complété pour la transaction {transaction.id}",
            merchant_id=merchant.id, entity_type="transaction", entity_id=transaction.id,
        )

        return transaction

    async def open_chargeback(
        self, session: AsyncSession, transaction: Transaction, merchant: Merchant, reason: str
    ) -> ChargebackEvent:
        chargeback = ChargebackEvent(
            transaction_id=transaction.id,
            merchant_id=merchant.id,
            amount=transaction.amount,
            currency=transaction.currency,
            status=ChargebackStatus.OPENED,
            reason=reason,
        )
        session.add(chargeback)
        await session.flush()

        await self.activity_log.log(
            session, ActivityEventType.CHARGEBACK_OPENED,
            f"Chargeback ouvert sur la transaction {transaction.id} — motif: {reason}",
            merchant_id=merchant.id, entity_type="chargeback", entity_id=chargeback.id,
            extra_data={"amount": str(transaction.amount), "currency": transaction.currency},
        )
        return chargeback

    async def resolve_chargeback(
        self, session: AsyncSession, chargeback: ChargebackEvent, outcome: ChargebackStatus, merchant: Merchant
    ) -> ChargebackEvent:
        if chargeback.status != ChargebackStatus.OPENED:
            raise ValidationError(f"Chargeback {chargeback.id} is already resolved ({chargeback.status.value})")
        if outcome not in (ChargebackStatus.WON, ChargebackStatus.LOST):
            raise ValidationError("outcome must be WON or LOST")

        if outcome == ChargebackStatus.LOST:
            transaction = await session.get(Transaction, chargeback.transaction_id)
            journal_id = (transaction.extra_data or {}).get("ledger_journal_id") if transaction else None
            if journal_id:
                reversal_id = await ledger_engine.reverse_journal(
                    session, UUID(journal_id),
                    reason=f"Chargeback perdu: {chargeback.reason}",
                    transaction_id=chargeback.transaction_id,
                )
                chargeback.reversal_journal_id = reversal_id

        chargeback.status = outcome
        chargeback.closed_at = datetime.now(timezone.utc)
        await session.flush()

        await self.activity_log.log(
            session, ActivityEventType.CHARGEBACK_CLOSED,
            f"Chargeback {chargeback.id} résolu — issue: {outcome.value}",
            merchant_id=merchant.id, entity_type="chargeback", entity_id=chargeback.id,
            extra_data={"outcome": outcome.value},
        )
        return chargeback


@dataclass
class PortfolioSummary:
    total_merchants: int
    merchants_by_status: dict[str, int]
    # Le cœur de la cohérence stratégique : combien de marchands du
    # portefeuille sont de VRAIS clients d'orchestration (>=3 rails) plutôt
    # que des clients mono-rail pour qui FrontierPay n'ajoute pas de valeur
    # mesurable — c'est la métrique que le positionnement commercial promet,
    # vérifiée sur l'ensemble du livre, pas sur un seul compte de démo.
    orchestration_qualified_merchants: int
    orchestration_qualified_pct: float
    platform_rail_volume: list  # RailBreakdownItem-like, toute-plateforme


class PortfolioMonitoringService:
    async def compute_portfolio_summary(self, session: AsyncSession) -> PortfolioSummary:
        total_merchants = (await session.execute(select(func.count(Merchant.id)))).scalar_one()

        status_rows = (await session.execute(
            select(Merchant.status, func.count(Merchant.id)).group_by(Merchant.status)
        )).all()
        merchants_by_status = {s.value: c for s, c in status_rows}

        # Rails distincts par marchand, sur TOUT l'historique (pas de fenêtre
        # glissante ici — c'est un état structurel du portefeuille, pas une
        # mesure d'activité récente).
        rail_rows = (await session.execute(
            select(Transaction.user_id, Transaction.psp).distinct()
        )).all()
        rails_by_user: dict = {}
        for user_id, psp in rail_rows:
            rails_by_user.setdefault(user_id, set()).add(psp)

        qualified = sum(1 for rails in rails_by_user.values() if len(rails) >= ORCHESTRATION_RAIL_THRESHOLD)
        merchants_with_activity = len(rails_by_user)
        qualified_pct = (qualified / merchants_with_activity * 100) if merchants_with_activity else 0.0

        volume_rows = (await session.execute(
            select(Transaction.psp, Transaction.currency, func.count(Transaction.id), func.sum(Transaction.amount))
            .group_by(Transaction.psp, Transaction.currency)
        )).all()
        platform_rail_volume = [
            {"psp": psp.value, "currency": currency, "transaction_count": cnt, "volume": str(vol or 0)}
            for psp, currency, cnt, vol in volume_rows
        ]

        return PortfolioSummary(
            total_merchants=total_merchants,
            merchants_by_status=merchants_by_status,
            orchestration_qualified_merchants=qualified,
            orchestration_qualified_pct=round(qualified_pct, 1),
            platform_rail_volume=platform_rail_volume,
        )

    async def list_all_merchants(
        self, session: AsyncSession, status: Optional[MerchantStatus] = None, page: int = 1, page_size: int = 50,
    ) -> tuple[list[Merchant], int]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)

        conditions = [Merchant.status == status] if status else []
        count_stmt = select(func.count(Merchant.id))
        stmt = select(Merchant).order_by(Merchant.created_at.desc())
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            stmt = stmt.where(*conditions)

        total = (await session.execute(count_stmt)).scalar_one()
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        items = list((await session.execute(stmt)).scalars().all())
        return items, total


portfolio_monitoring_service = PortfolioMonitoringService()
merchant_monitoring_service = MerchantMonitoringService()
activity_log_service = ActivityLogService()
