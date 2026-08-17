"""
AFM PSP Health — modèle de télémétrie des appels sortants vers chaque PSP.

Distinct volontairement de ActivityLog/LedgerEntry : ceci est de la
télémétrie opérationnelle (latence, disponibilité), pas un fait financier ou
un événement métier auditable. Pas de contrainte d'immuabilité stricte ici —
mais toujours écrit dans sa propre session, indépendamment de la transaction
de paiement en cours, pour qu'un problème d'écriture de log de santé ne
puisse jamais faire échouer un paiement.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Integer, Enum as SQLEnum, Index

from config.database import Base


class PSPCallOutcome(str, Enum):
    SUCCESS = "success"       # le PSP a confirmé le traitement
    FAILED = "failed"         # le PSP a répondu avec un refus/erreur explicite (pas une panne)
    AMBIGUOUS = "ambiguous"   # timeout/erreur réseau — le PSP n'a pas répondu de façon exploitable


class PSPOperation(str, Enum):
    CHARGE = "charge"
    STATUS_CHECK = "status_check"


class PSPCallLog(Base):
    __tablename__ = "psp_call_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    psp = Column(String(32), nullable=False, index=True)
    operation = Column(SQLEnum(PSPOperation), nullable=False)
    outcome = Column(SQLEnum(PSPCallOutcome), nullable=False)
    latency_ms = Column(Integer, nullable=False)
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_psp_call_logs_psp_created", "psp", "created_at"),
    )
