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
    hashed_password = Column(String(255))
    full_name = Column(String(255))
    phone = Column(String(50))
    country = Column(String(2))
    is_active = Column(String(1), default="1")
    kyc_status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
