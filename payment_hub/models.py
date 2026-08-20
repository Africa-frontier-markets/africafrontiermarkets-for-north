"""
AFM Payment Hub Models — SQLAlchemy 2.0 with DB persistence
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Numeric, Enum as SQLEnum, ForeignKey, JSON, Index, Text
from sqlalchemy.dialects.postgresql import UUID

from config.database import Base


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"
    HELD = "held"


class PSPType(str, Enum):
    KORA = "kora"
    FINCRA = "fincra"
    FLUTTERWAVE = "flutterwave"
    STRIPE = "stripe"
    MTN_MOMO = "mtn_momo"
    ORANGE_MONEY = "orange_money"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    psp = Column(SQLEnum(PSPType), nullable=False)
    psp_transaction_id = Column(String(100))
    psp_response = Column(JSON, default=dict)  # Raw PSP response stored
    amount = Column(Numeric(19, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    fee_amount = Column(Numeric(19, 8), default=Decimal("0"))
    fee_currency = Column(String(3), default="USD")
    net_amount = Column(Numeric(19, 8), default=Decimal("0"))
    status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.PENDING)
    txn_metadata = Column(JSON, default=dict)  # WAS "metadata" — reserved by SQLAlchemy Base.metadata
    error_message = Column(Text)
    webhook_received_at = Column(DateTime(timezone=True))
    settled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_transactions_user_status", "user_id", "status"),
        Index("ix_transactions_created_at", "created_at"),
        Index("ix_transactions_psp_txn", "psp_transaction_id"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    oauth_subject = Column(String(128), unique=True, nullable=True, index=True)
    hashed_password = Column(String(255))
    full_name = Column(String(255))
    phone = Column(String(50))
    country = Column(String(2))
    is_active = Column(String(1), default="1")
    kyc_status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BrokerAccountLink(Base):
    """Ownership link between an AFM user and one Alpaca Broker account."""

    __tablename__ = "broker_account_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    alpaca_account_id = Column(String(100), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class VirtualAccount(Base):
    """AFM-owned account that partitions the transitional omnibus ledger."""

    __tablename__ = "virtual_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="active")
    currency = Column(String(3), nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class VirtualPosition(Base):
    """Current quantity and cost projection belonging to one virtual account."""

    __tablename__ = "virtual_positions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    virtual_account_id = Column(UUID(as_uuid=True), ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol = Column(String(32), nullable=False)
    quantity = Column(Numeric(28, 10), nullable=False, default=Decimal("0"))
    average_cost = Column(Numeric(19, 8), nullable=False, default=Decimal("0"))
    currency = Column(String(3), nullable=False, default="USD")
    position_metadata = Column(JSON, default=dict)
    reconciled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("uq_virtual_positions_account_symbol", "virtual_account_id", "symbol", unique=True),
    )


class VirtualLedgerEntry(Base):
    """Immutable cash, execution, fee, corporate-action or reconciliation entry."""

    __tablename__ = "virtual_ledger_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    virtual_account_id = Column(UUID(as_uuid=True), ForeignKey("virtual_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    entry_type = Column(String(40), nullable=False)
    direction = Column(String(8), nullable=False)
    amount = Column(Numeric(19, 8), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    symbol = Column(String(32))
    quantity = Column(Numeric(28, 10))
    reference_type = Column(String(40))
    reference_id = Column(String(128))
    description = Column(String(255))
    entry_metadata = Column(JSON, default=dict)
    occurred_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_virtual_ledger_account_occurred", "virtual_account_id", "occurred_at"),
        Index("ix_virtual_ledger_reference", "reference_type", "reference_id"),
    )


class KoraWebhookEvent(Base):
    """Durable receipt record preventing duplicate Kora webhook processing."""

    __tablename__ = "kora_webhook_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_id = Column(String(128), nullable=False, unique=True, index=True)
    event_type = Column(String(80), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="received")
    payload = Column(JSON, nullable=False, default=dict)
    received_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime(timezone=True))
    error_message = Column(String(255))
