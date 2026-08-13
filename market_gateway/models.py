"""SQLAlchemy models for market gateway."""

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Column, String, DateTime, Boolean, Float, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
import uuid

from config.database import Base


class MarketStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    HALTED = "halted"
    PRE_MARKET = "pre_market"
    AFTER_HOURS = "after_hours"


class Market(Base):
    __tablename__ = "markets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(10), nullable=False, unique=True, index=True)
    name = Column(String(100), nullable=False)
    country = Column(String(2), nullable=False, index=True)
    timezone = Column(String(50), nullable=False)
    currency = Column(String(3), nullable=False)
    open_time = Column(String(5), nullable=False)
    close_time = Column(String(5), nullable=False)
    weekend_days = Column(String(20), default="6,7")
    status = Column(SQLEnum(MarketStatus), default=MarketStatus.CLOSED)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_markets_active_country", "is_active", "country"),
    )


class Broker(Base):
    __tablename__ = "brokers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    api_endpoint = Column(String(255), nullable=False)
    supported_markets = Column(String(500), nullable=False)
    supported_order_types = Column(String(200), default='["market","limit"]')
    latency_ms = Column(Float, default=100.0)
    is_active = Column(Boolean, default=True)
    priority = Column(Float, default=1.0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
