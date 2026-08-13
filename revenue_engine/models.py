"""Revenue engine models."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Column, String, DateTime, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
import uuid

from config.database import Base


class RevenueRecord(Base):
    __tablename__ = "revenue_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)
    gross_amount = Column(Numeric(19, 8), nullable=False)
    ads_amount = Column(Numeric(19, 8), nullable=False)
    creator_amount = Column(Numeric(19, 8), nullable=False)
    platform_amount = Column(Numeric(19, 8), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
