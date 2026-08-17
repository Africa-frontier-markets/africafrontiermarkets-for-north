"""
AFM Merchant & Activity Log models.

Construits directement contre la grille de conformité Kora fournie (pas une
interprétation libre) :
- art. 5.5 "detailed activity logs relating to the transactions" -> ActivityLog,
  typé par event_type, jamais un texte de log libre non interrogeable.
- art. 7 (chargebacks, refunds) -> ChargebackEvent + Transaction.status=REFUNDED
  (déjà existant).
- art. 7.11 (taux de chargeback < 0.5%) -> calculé par
  merchant.service.MerchantMonitoringService, pas juste stocké : le seuil est
  vérifié à chaque calcul, pas assumé respecté.
- art. 10.6 (KYC marchand) -> Merchant.kyc_tier, distinct du champ texte libre
  User.kyc_status qui existait déjà (conservé pour compat, mais plus la source
  de vérité pour le monitoring marchand).

Principe d'immuabilité pour ActivityLog : comme pour LedgerEntry, aucune
méthode d'update/delete n'est exposée. Un événement de log ne se corrige pas,
il se complète par un nouvel événement (ex: CHARGEBACK_CLOSED après
CHARGEBACK_OPENED, jamais une réécriture du premier).
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Numeric, Integer, Enum as SQLEnum, ForeignKey, JSON, Index, Text
from sqlalchemy.dialects.postgresql import UUID

from config.database import Base


class MerchantStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class KYCTier(str, Enum):
    NONE = "none"
    BASIC = "basic"
    VERIFIED = "verified"
    ENHANCED = "enhanced"  # EDD — Enhanced Due Diligence


class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # 1:1 avec User dans ce modèle d'auth self-serve — un compte Merchant est
    # provisionné automatiquement à l'inscription (voir api_gateway/main.py:register).
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    business_name = Column(String(255))
    country = Column(String(2))

    kyc_tier = Column(SQLEnum(KYCTier), default=KYCTier.NONE, nullable=False)
    kyc_verified_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(SQLEnum(MerchantStatus), default=MerchantStatus.PENDING_REVIEW, nullable=False)
    status_reason = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_merchants_status", "status"),
    )


class ChargebackStatus(str, Enum):
    OPENED = "opened"
    WON = "won"      # AFM/marchand gagne le litige — pas de perte
    LOST = "lost"     # AFM/marchand perd — fonds débités définitivement


class ChargebackEvent(Base):
    __tablename__ = "chargeback_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False, index=True)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True)

    amount = Column(Numeric(19, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(SQLEnum(ChargebackStatus), default=ChargebackStatus.OPENED, nullable=False)
    reason = Column(String(255))

    # Référence au journal ledger de contrepassation, posé uniquement si
    # status passe à LOST (voir merchant/service.py:resolve_chargeback) —
    # un chargeback OPENED ou WON ne bouge aucun fonds.
    reversal_journal_id = Column(UUID(as_uuid=True), nullable=True)

    opened_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_chargebacks_merchant_status", "merchant_id", "status"),
    )


class ActivityEventType(str, Enum):
    # Liste alignée sur l'exigence Kora art. 5.5 telle que transmise, plus
    # quelques événements opérationnels utiles (KYC, statut marchand).
    MERCHANT_CREATED = "merchant_created"
    MERCHANT_STATUS_CHANGED = "merchant_status_changed"
    KYC_UPDATED = "kyc_updated"
    PAYMENT_INITIATED = "payment_initiated"
    PAYMENT_COMPLETED = "payment_completed"
    PAYMENT_FAILED = "payment_failed"
    WEBHOOK_RECEIVED = "webhook_received"
    SETTLEMENT_RECEIVED = "settlement_received"
    REFUND_INITIATED = "refund_initiated"
    REFUND_COMPLETED = "refund_completed"
    CHARGEBACK_OPENED = "chargeback_opened"
    CHARGEBACK_CLOSED = "chargeback_closed"
    # AJOUT — workflow KYC/AML réel (compliance/), en remplacement du champ
    # Merchant.kyc_tier resté jusqu'ici sans historique d'événements derrière lui.
    KYC_DOCUMENT_SUBMITTED = "kyc_document_submitted"
    KYC_DOCUMENT_REVIEWED = "kyc_document_reviewed"
    AML_SCREENING_RUN = "aml_screening_run"
    EDD_CASE_OPENED = "edd_case_opened"
    EDD_CASE_RESOLVED = "edd_case_resolved"
    # AJOUT — trading_engine
    TRADE_ORDER_SUBMITTED = "trade_order_submitted"
    TRADE_ORDER_FILLED = "trade_order_filled"
    TRADE_ORDER_REJECTED = "trade_order_rejected"


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    event_type = Column(SQLEnum(ActivityEventType), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True)   # ex: "transaction", "chargeback"
    entity_id = Column(String(100), nullable=True)    # UUID en texte — l'entité référencée peut
                                                        # ne pas exister côté FK (ex: webhook rejeté
                                                        # avant qu'une transaction ne soit trouvée)
    description = Column(Text, nullable=False)
    extra_data = Column(JSON, default=dict)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_activity_logs_merchant_created", "merchant_id", "created_at"),
    )

    # Append-only, comme LedgerEntry — aucune méthode d'update/delete exposée
    # nulle part dans ce module. Une correction se journalise, elle ne réécrit
    # jamais un événement passé.
