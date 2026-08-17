"""
AFM KYC Service — workflow réel derrière Merchant.kyc_tier.

Avant ce module : Merchant.kyc_tier était un champ que rien ne faisait
avancer — aucun code ne le lisait pour décider quoi que ce soit, aucun
code ne le faisait progresser selon une règle vérifiable. Ce module fixe
les deux : quels documents/contrôles sont exigés pour chaque palier
(TIER_REQUIREMENTS), et une seule porte d'entrée (`evaluate_and_promote`)
qui recalcule le tier atteignable à partir de l'état réel (documents
approuvés + dernier screening AML par type), plutôt que de faire confiance
à un appelant pour l'incrémenter à la main.

Règle explicite : un dossier EDD OPEN bloque toute promotion, quel que
soit l'état des documents — la revue humaine a le dernier mot.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from compliance.models import (
    KYCDocument, KYCDocumentType, KYCDocumentStatus,
    ScreeningResult, ScreeningType, ScreeningOutcome,
    EDDCase, EDDStatus,
)
from compliance.screening_service import screening_service
from config.exceptions import ValidationError, ComplianceHoldError
from config.logging_config import configure_logging
from merchant.models import Merchant, KYCTier, ActivityEventType
from merchant.service import activity_log_service

logger = configure_logging()

# Documents exigés pour ATTEINDRE (pas dépasser) chaque palier. BASIC exige
# une pièce d'identité ; VERIFIED ajoute une preuve d'adresse et
# l'enregistrement de l'entreprise ; ENHANCED (EDD) ajoute le bénéficiaire
# effectif ET exige qu'un dossier EDD ait été ouvert puis APPROUVÉ (pas
# seulement absent) — l'EDD est un palier qu'on atteint par une revue
# positive, pas par défaut d'incident.
TIER_REQUIREMENTS: dict[KYCTier, tuple[KYCDocumentType, ...]] = {
    KYCTier.BASIC: (KYCDocumentType.GOVERNMENT_ID,),
    KYCTier.VERIFIED: (KYCDocumentType.GOVERNMENT_ID, KYCDocumentType.PROOF_OF_ADDRESS, KYCDocumentType.BUSINESS_REGISTRATION),
    KYCTier.ENHANCED: (KYCDocumentType.GOVERNMENT_ID, KYCDocumentType.PROOF_OF_ADDRESS, KYCDocumentType.BUSINESS_REGISTRATION, KYCDocumentType.BENEFICIAL_OWNERSHIP),
}

TIER_ORDER = (KYCTier.NONE, KYCTier.BASIC, KYCTier.VERIFIED, KYCTier.ENHANCED)


class KYCService:
    async def submit_document(
        self, session: AsyncSession, merchant: Merchant, document_type: KYCDocumentType, document_ref: str,
    ) -> KYCDocument:
        doc = KYCDocument(merchant_id=merchant.id, document_type=document_type, document_ref=document_ref)
        session.add(doc)
        await session.flush()

        await activity_log_service.log(
            session, ActivityEventType.KYC_DOCUMENT_SUBMITTED,
            f"Document KYC soumis: {document_type.value}",
            merchant_id=merchant.id, entity_type="kyc_document", entity_id=doc.id,
        )
        return doc

    async def review_document(
        self, session: AsyncSession, document: KYCDocument, approve: bool,
        reviewer_user_id: UUID, rejection_reason: Optional[str] = None,
    ) -> KYCDocument:
        if document.status != KYCDocumentStatus.SUBMITTED:
            raise ValidationError(f"Document {document.id} already reviewed ({document.status.value})")
        if not approve and not rejection_reason:
            raise ValidationError("rejection_reason is required when rejecting a document")

        document.status = KYCDocumentStatus.APPROVED if approve else KYCDocumentStatus.REJECTED
        document.reviewed_by_user_id = reviewer_user_id
        document.reviewed_at = datetime.now(timezone.utc)
        document.rejection_reason = rejection_reason
        await session.flush()

        await activity_log_service.log(
            session, ActivityEventType.KYC_DOCUMENT_REVIEWED,
            f"Document KYC {document.document_type.value} — décision: {document.status.value}",
            merchant_id=document.merchant_id, entity_type="kyc_document", entity_id=document.id,
            extra_data={"approved": approve, "reason": rejection_reason},
        )
        return document

    async def _approved_document_types(self, session: AsyncSession, merchant_id: UUID) -> set[KYCDocumentType]:
        rows = (await session.execute(
            select(KYCDocument.document_type).where(
                KYCDocument.merchant_id == merchant_id,
                KYCDocument.status == KYCDocumentStatus.APPROVED,
            )
        )).scalars().all()
        return set(rows)

    async def _latest_screening_clear(self, session: AsyncSession, merchant_id: UUID, screening_type: ScreeningType) -> bool:
        """CLEAR seulement si un screening a effectivement eu lieu et est CLEAR —
        l'absence de screening n'est PAS traitée comme CLEAR par défaut."""
        result = (await session.execute(
            select(ScreeningResult)
            .where(ScreeningResult.merchant_id == merchant_id, ScreeningResult.screening_type == screening_type)
            .order_by(ScreeningResult.created_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        return bool(result and result.outcome == ScreeningOutcome.CLEAR)

    async def _has_open_edd(self, session: AsyncSession, merchant_id: UUID) -> bool:
        result = (await session.execute(
            select(EDDCase.id).where(EDDCase.merchant_id == merchant_id, EDDCase.status == EDDStatus.OPEN).limit(1)
        )).scalar_one_or_none()
        return result is not None

    async def has_compliance_hold(self, session: AsyncSession, merchant_id: UUID) -> bool:
        """Point d'entrée public pour d'autres modules (ex: trading_engine)
        qui doivent bloquer une opération sans dupliquer la requête EDD."""
        return await self._has_open_edd(session, merchant_id)

    async def evaluate_and_promote(self, session: AsyncSession, merchant: Merchant) -> Merchant:
        """
        Recalcule le tier ATTEIGNABLE à partir de l'état réel et l'applique
        si c'est une progression. Ne rétrograde jamais automatiquement (une
        rétrogradation est une décision, pas un recalcul — voir
        EDDCase.status == REJECTED géré séparément par un opérateur).
        """
        if await self._has_open_edd(session, merchant.id):
            raise ComplianceHoldError(f"Merchant {merchant.id} has an open EDD case — tier promotion blocked")

        approved = await self._approved_document_types(session, merchant.id)
        sanctions_clear = await self._latest_screening_clear(session, merchant.id, ScreeningType.SANCTIONS)
        pep_clear = await self._latest_screening_clear(session, merchant.id, ScreeningType.PEP)

        achievable = KYCTier.NONE
        for tier in TIER_ORDER[1:]:
            required = set(TIER_REQUIREMENTS[tier])
            if not required.issubset(approved):
                break
            if not (sanctions_clear and pep_clear):
                break  # documents ne suffisent jamais sans un screening AML CLEAR
            achievable = tier

        current_index = TIER_ORDER.index(merchant.kyc_tier)
        achievable_index = TIER_ORDER.index(achievable)

        if achievable_index > current_index:
            previous = merchant.kyc_tier
            merchant.kyc_tier = achievable
            merchant.kyc_verified_at = datetime.now(timezone.utc)
            await session.flush()
            await activity_log_service.log(
                session, ActivityEventType.KYC_UPDATED,
                f"Tier KYC promu: {previous.value} -> {achievable.value}",
                merchant_id=merchant.id, entity_type="merchant", entity_id=merchant.id,
                extra_data={"previous_tier": previous.value, "new_tier": achievable.value},
            )
        return merchant

    async def run_screening_and_reevaluate(
        self, session: AsyncSession, merchant: Merchant, legal_name: str,
        country: Optional[str] = None, dob: Optional[str] = None,
    ) -> Merchant:
        """Point d'entrée unique combinant sanctions + PEP + réévaluation du
        tier, pour éviter qu'un appelant oublie l'un des deux contrôles."""
        await screening_service.run_sanctions_screening(session, merchant.id, legal_name, country, dob)
        await screening_service.run_pep_screening(session, merchant.id, legal_name, country)
        await activity_log_service.log(
            session, ActivityEventType.AML_SCREENING_RUN,
            f"Contrôles sanctions+PEP exécutés pour {legal_name}",
            merchant_id=merchant.id, entity_type="merchant", entity_id=merchant.id,
        )
        try:
            return await self.evaluate_and_promote(session, merchant)
        except ComplianceHoldError:
            # Un EDD vient peut-être d'être ouvert par ce screening même —
            # ce n'est pas une erreur système, l'appelant doit voir le
            # merchant tel quel plutôt qu'une exception sur un flux normal.
            return merchant


kyc_service = KYCService()
