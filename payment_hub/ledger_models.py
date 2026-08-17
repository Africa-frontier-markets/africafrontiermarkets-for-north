"""
AFM Ledger Models — Grand livre en partie double, écritures immuables.

Ajouté en réponse directe à l'audit investisseur (juillet 2026) :
"Le dossier affirme l'existence d'un ledger en partie double, mais
l'inventaire technique fait surtout apparaître des entités telles que
Transaction, Settlement, Merchant... Une table Transaction ou un champ
balance ne constituent pas un ledger financier."

Ce module implémente les éléments explicitement listés comme manquants :
- Plan de comptes (LedgerAccount) et comptes de transit
- Journal immuable avec écritures débit/crédit (LedgerEntry)
- Gestion multi-devise (chaque écriture porte sa devise ; un journal ne
  peut mélanger des devises sans passer par un compte de conversion FX
  dédié — voir ledger_engine.post_journal)
- Fonds disponibles vs réservés vs en suspense (LedgerOwnerType.SUSPENSE,
  et les comptes "transit" qui représentent des fonds non encore réglés)

Principe d'immuabilité : AUCUNE méthode d'update/delete n'est exposée sur
LedgerEntry. Une correction se fait exclusivement par une contrepassation
(nouvelle écriture inverse, nouveau journal_id) — voir
ledger_engine.reverse_journal(). C'est ce qui rend le journal auditable :
l'historique n'est jamais réécrit, seulement complété.
"""

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import (
    Column, String, DateTime, Numeric, Enum as SQLEnum,
    ForeignKey, Index, CheckConstraint, Text,
)
from sqlalchemy.dialects.postgresql import UUID

from config.database import Base


class LedgerAccountType(str, Enum):
    ASSET = "asset"           # ex: fonds en transit reçus des PSP
    LIABILITY = "liability"   # ex: montant dû à un marchand/utilisateur
    REVENUE = "revenue"       # ex: commission AFM
    EXPENSE = "expense"       # ex: frais PSP payés par AFM
    SUSPENSE = "suspense"     # écarts non réconciliés, en attente d'apurement


class LedgerOwnerType(str, Enum):
    USER = "user"                 # compte "payable" d'un utilisateur/marchand
    PSP_TRANSIT = "psp_transit"   # fonds reçus d'un PSP, pas encore réglés en interne
    AFM_TREASURY = "afm_treasury"
    AFM_REVENUE = "afm_revenue"
    SUSPENSE = "suspense"
    # AJOUT — trading_engine : fonds en règlement chez un broker (Alpaca),
    # distinct de PSP_TRANSIT qui est spécifique aux PSP de paiement.
    BROKER_TRANSIT = "broker_transit"


class LedgerDirection(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerAccount(Base):
    """
    Compte du plan comptable. Un compte est toujours dans UNE devise —
    un utilisateur qui reçoit des paiements en XOF et en XAF a DEUX
    comptes distincts (pas de conversion implicite).
    """
    __tablename__ = "ledger_accounts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    account_type = Column(SQLEnum(LedgerAccountType), nullable=False)
    owner_type = Column(SQLEnum(LedgerOwnerType), nullable=False)
    # NULL pour les comptes système (treasury, transit, revenue, suspense) ;
    # rempli pour un compte utilisateur/marchand individuel.
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    currency = Column(String(3), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_ledger_accounts_owner_currency", "owner_id", "currency"),
    )


class LedgerEntry(Base):
    """
    Écriture individuelle (une ligne débit OU crédit) d'un journal.
    Un journal (groupe d'écritures partageant le même journal_id) doit
    TOUJOURS être équilibré : somme des débits == somme des crédits, par
    devise — vérifié par ledger_engine.post_journal() avant toute écriture,
    jamais après coup.

    Immuable par construction : ce modèle n'expose aucune méthode de mise
    à jour. Toute correction passe par une nouvelle écriture inverse
    (voir ledger_engine.reverse_journal), le journal original restant
    intact pour l'audit.
    """
    __tablename__ = "ledger_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    journal_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    account_id = Column(UUID(as_uuid=True), ForeignKey("ledger_accounts.id"), nullable=False, index=True)
    direction = Column(SQLEnum(LedgerDirection), nullable=False)
    amount = Column(Numeric(19, 8), nullable=False)
    currency = Column(String(3), nullable=False)
    # Référence optionnelle vers la transaction de paiement à l'origine de
    # cette écriture (une transaction peut engendrer plusieurs écritures :
    # transit, payable, revenue — voire une contrepassation plus tard).
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True)
    description = Column(Text, nullable=False)
    # Si cette écriture est la contrepassation d'un journal antérieur,
    # référence le journal_id original (traçabilité de la correction).
    reverses_journal_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_entry_amount_positive"),
        Index("ix_ledger_entries_journal", "journal_id"),
        Index("ix_ledger_entries_account_created", "account_id", "created_at"),
    )


class ReconciliationDiscrepancy(Base):
    """
    Écart détecté lors d'un rapprochement intraday/fin de journée avec un
    PSP (transaction présente d'un côté et absente de l'autre, ou montants
    différents). Ajouté en réponse à l'audit :
    "Procédure d'apurement des écarts et double validation."

    Le champ double validation est représenté par first_approved_by /
    second_approved_by : deux acteurs DISTINCTS doivent approuver avant que
    resolved_at soit renseigné (voir ledger_engine.reconciliation pour
    l'application de cette règle).
    """
    __tablename__ = "reconciliation_discrepancies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    psp = Column(String(32), nullable=False)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True, index=True)
    discrepancy_type = Column(String(32), nullable=False)  # missing_on_psp, missing_on_afm, amount_mismatch
    afm_amount = Column(Numeric(19, 8), nullable=True)
    psp_amount = Column(Numeric(19, 8), nullable=True)
    currency = Column(String(3), nullable=False)
    details = Column(Text, nullable=True)
    detected_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    first_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    first_approved_at = Column(DateTime(timezone=True), nullable=True)
    second_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    second_approved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_reconciliation_psp_resolved", "psp", "resolved_at"),
    )
