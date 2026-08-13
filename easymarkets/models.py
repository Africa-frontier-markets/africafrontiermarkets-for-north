"""EasyMarkets models."""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey, Enum as SQLEnum, Integer
from sqlalchemy.dialects.postgresql import UUID
import uuid

from config.database import Base


class WalletTransactionType(str, Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class CopyTradeStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)
    currency = Column(String(3), nullable=False)
    balance = Column(Numeric(19, 8), default=Decimal("0"))
    locked_balance = Column(Numeric(19, 8), default=Decimal("0"))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wallet_id = Column(UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False)
    type = Column(SQLEnum(WalletTransactionType), nullable=False)
    amount = Column(Numeric(19, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    reference = Column(String(255))
    status = Column(String(50), default="pending")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CopyTrade(Base):
    __tablename__ = "copy_trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    follower_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    leader_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    allocation_percent = Column(Integer, nullable=False)
    max_drawdown = Column(Numeric(5, 2), default=Decimal("20.0"))
    status = Column(SQLEnum(CopyTradeStatus), default=CopyTradeStatus.ACTIVE)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    stopped_at = Column(DateTime(timezone=True))
