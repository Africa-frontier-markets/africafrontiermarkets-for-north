"""
AFM Compliance Models — Sanctions/PEP screening, EDD cases, KYC documents.

Point de départ honnête, pas une simulation déguisée en conformité :
- ScreeningResult persiste CHAQUE exécution de contrôle (sanctions + PEP),
  jamais un simple booléen recalculé à la volée et donc invisible à un
  auditeur externe. Un régulateur demande la preuve qu'un contrôle a eu
  lieu à une date donnée avec telle source de données — pas seulement que
  le marchand "n'est pas actuellement flaggé".
- La liste de sanctions embarquée (compliance/sanctions_data.py) est un jeu
  de données RESTREINT et explicitement marqué comme tel. Ce module
  implémente l'algorithme de screening réel (normalisation, matching flou,
  scoring, seuils), mais PAS une alimentation de données OFAC/UN/EU à jour
  en continu — ça, c'est un flux payant (Dow Jones, ComplyAdvantage, Refinitiv
  World-Check, etc.) qui n'existe nulle part dans ce projet et doit être
  branché avant toute mise en production réelle. Voir sanctions_data.py.
- KYCTier reste défini dans merchant/models.py (déjà existant, conservé comme
  source de vérité) ; ce module ajoute le WORKFLOW qui mène à un changement
  de tier, pas un second champ concurrent.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlalchemy import Column, String, DateTime, Integer, Enum as SQLEnum, ForeignKey, JSON, Index, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID

from config.database import Base


class ScreeningType(str, Enum):
    SANCTIONS = "sanctions"
    PEP = "pep"


class ScreeningOutcome(str, Enum):
    CLEAR = "clear"                # aucun candidat au-dessus du seuil
    POTENTIAL_MATCH = "potential_match"  # candidat(s) au-dessus du seuil — revue humaine requise
    CONFIRMED_MATCH = "confirmed_match"  # revu par un humain et confirmé — bloquant


class ScreeningResult(Base):
    """
    Une exécution de contrôle = une ligne, jamais réécrite. Le statut d'un
    marchand vis-à-vis de l'AML se lit en prenant le dernier ScreeningResult
    par (merchant_id, screening_type), pas en maintenant un champ mutable.
    """
    __tablename__ = "compliance_screening_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True)

    screening_type = Column(SQLEnum(ScreeningType), nullable=False)
    outcome = Column(SQLEnum(ScreeningOutcome), nullable=False)

    # Nom + pays + date de naissance (si dispo) tels que soumis, pour que le
    # résultat reste interprétable sans recharger le dossier merchant.
    subject_name = Column(String(255), nullable=False)
    subject_country = Column(String(2), nullable=True)
    subject_dob = Column(String(10), nullable=True)  # YYYY-MM-DD, texte : jamais utilisé pour du calcul

    # Détail des candidats retenus (nom de la liste, score, entrée matchée) —
    # nécessaire pour qu'une revue humaine (ou un auditeur) comprenne POURQUOI
    # un score a été attribué, pas seulement le score final.
    candidates = Column(JSON, default=list)
    top_score = Column(Integer, default=0)  # 0-100, similarité du meilleur candidat

    # Source du jeu de données utilisé — traçabilité obligatoire. Volontairement
    # explicite ("embedded_sample_v1") plutôt qu'un nom qui suggérerait un flux
    # commercial réel non branché.
    data_source = Column(String(100), nullable=False, default="embedded_sample_v1")

    reviewed_by_user_id = Column(UUID(as_uuid=True), nullable=True)  # revue humaine, si POTENTIAL_MATCH
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    __table_args__ = (
        Index("ix_screening_merchant_type_created", "merchant_id", "screening_type", "created_at"),
    )


class EDDStatus(str, Enum):
    OPEN = "open"                # dossier EDD ouvert, revue en cours
    APPROVED = "approved"        # revu, marchand autorisé à continuer (tier ENHANCED)
    REJECTED = "rejected"        # revu, marchand refusé/suspendu


class EDDCase(Base):
    """
    Enhanced Due Diligence — ouvert automatiquement quand un screening
    produit POTENTIAL_MATCH/CONFIRMED_MATCH, ou quand une règle de risque
    l'exige (ex: volume élevé + KYC BASIC). Ce n'est pas juste un flag :
    c'est un dossier avec une raison d'ouverture et une décision tracée.
    """
    __tablename__ = "compliance_edd_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True)
    triggering_screening_id = Column(UUID(as_uuid=True), ForeignKey("compliance_screening_results.id"), nullable=True)

    status = Column(SQLEnum(EDDStatus), default=EDDStatus.OPEN, nullable=False)
    reason = Column(String(500), nullable=False)

    opened_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_edd_merchant_status", "merchant_id", "status"),
    )


class KYCDocumentType(str, Enum):
    GOVERNMENT_ID = "government_id"
    PROOF_OF_ADDRESS = "proof_of_address"
    BUSINESS_REGISTRATION = "business_registration"  # RC / registre du commerce
    BENEFICIAL_OWNERSHIP = "beneficial_ownership"


class KYCDocumentStatus(str, Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class KYCDocument(Base):
    """
    Trace un document soumis pour vérification. Le fichier lui-même n'est
    PAS stocké ici (ni en base ni sur disque par ce module) — seule une
    référence (document_ref) l'est, fournie par le caller (ex: clé S3/blob
    storage). Ce module gère le WORKFLOW de vérification, pas le stockage
    de fichiers, qui est un problème distinct (chiffrement au repos,
    rétention légale) hors périmètre de ce soir.
    """
    __tablename__ = "compliance_kyc_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id = Column(UUID(as_uuid=True), ForeignKey("merchants.id"), nullable=False, index=True)

    document_type = Column(SQLEnum(KYCDocumentType), nullable=False)
    document_ref = Column(String(500), nullable=False)  # référence externe (ex: clé de stockage), pas le fichier
    status = Column(SQLEnum(KYCDocumentStatus), default=KYCDocumentStatus.SUBMITTED, nullable=False)

    reviewed_by_user_id = Column(UUID(as_uuid=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(String(500), nullable=True)

    submitted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_kyc_docs_merchant_type", "merchant_id", "document_type"),
    )
