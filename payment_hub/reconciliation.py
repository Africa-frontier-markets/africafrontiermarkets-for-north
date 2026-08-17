"""
AFM Reconciliation Service — Rapprochement PSP et procédure d'apurement.

Ajouté en réponse à l'audit investisseur (section 4.2, 10 et 11) :
- "Rapprochement intraday et fin de journée avec chaque PSP"
- "Procédure d'apurement des écarts et double validation"
- Test pratique n°4 exigé : réconciliation après incident

Principe : ce module NE MODIFIE JAMAIS le ledger directement pour
"corriger" un écart. Il détecte, journalise dans
`reconciliation_discrepancies`, et exige une double validation
(deux personnes distinctes) avant qu'une contrepassation corrective
puisse être postée. C'est le contrôle à quatre yeux demandé par l'audit
(section 4.4) appliqué spécifiquement à l'apurement financier.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select

from config.database import AsyncSessionLocal
from config.exceptions import ReconciliationError
from config.logging_config import configure_logging
from payment_hub.models import Transaction, PaymentStatus, PSPType
from payment_hub.ledger_models import ReconciliationDiscrepancy
from payment_hub.ledger_engine import ledger_engine

logger = configure_logging()


@dataclass
class PSPStatementLine:
    """Une ligne du relevé PSP (référence externe, montant, devise, statut)."""
    reference: str
    amount: Decimal
    currency: str
    status: str  # "success" | "failed" | "pending"


class ReconciliationService:

    async def _fetch_psp_statement(self, psp: PSPType) -> list[PSPStatementLine]:
        """
        ATTENTION (point d'intégration non simulable honnêtement) : ceci doit
        appeler le vrai relevé de transactions du PSP (endpoint de
        settlement/statement de Kora, Fincra, etc.). Sans identifiants
        réels, on ne peut pas fabriquer un relevé PSP crédible — le
        retourner vide plutôt que d'inventer des données qui donneraient
        une fausse impression de réconciliation réussie.

        En test, cette méthode doit être mockée pour simuler un relevé PSP
        contrôlé (voir tests/test_reconciliation.py).
        """
        logger.warning(
            "_fetch_psp_statement non implémenté pour un PSP réel — "
            "aucune donnée de relevé disponible, la réconciliation ne peut "
            "détecter que des écarts 'missing_on_psp' par défaut",
            psp=psp.value,
        )
        return []

    async def run_reconciliation(
        self, psp: PSPType, window_start, window_end
    ) -> dict:
        """
        Compare les transactions COMPLETED d'AFM pour ce PSP sur la fenêtre
        donnée avec le relevé PSP. Détecte trois types d'écarts :
        - missing_on_psp : AFM dit COMPLETED, absent du relevé PSP
        - missing_on_afm : présent chez le PSP, aucune transaction AFM correspondante
        - amount_mismatch : présent des deux côtés, montants différents

        Chaque écart est journalisé dans reconciliation_discrepancies — sans
        aucune correction automatique du ledger.
        """
        statement = await self._fetch_psp_statement(psp)
        statement_by_ref = {line.reference: line for line in statement}

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Transaction).where(
                    Transaction.psp == psp,
                    Transaction.status == PaymentStatus.COMPLETED,
                    Transaction.created_at >= window_start,
                    Transaction.created_at < window_end,
                )
            )
            afm_transactions = result.scalars().all()
            afm_by_ref = {t.idempotency_key: t for t in afm_transactions}

            discrepancies = []

            for ref, tx in afm_by_ref.items():
                line = statement_by_ref.get(ref)
                if line is None:
                    discrepancies.append(ReconciliationDiscrepancy(
                        psp=psp.value, transaction_id=tx.id, discrepancy_type="missing_on_psp",
                        afm_amount=tx.amount, psp_amount=None, currency=tx.currency,
                        details=f"Transaction {tx.id} marquée COMPLETED côté AFM, absente du relevé {psp.value}",
                    ))
                elif line.amount != tx.amount:
                    discrepancies.append(ReconciliationDiscrepancy(
                        psp=psp.value, transaction_id=tx.id, discrepancy_type="amount_mismatch",
                        afm_amount=tx.amount, psp_amount=line.amount, currency=tx.currency,
                        details=f"AFM={tx.amount} {tx.currency} vs PSP={line.amount} {line.currency}",
                    ))

            for ref, line in statement_by_ref.items():
                if ref not in afm_by_ref and line.status == "success":
                    discrepancies.append(ReconciliationDiscrepancy(
                        psp=psp.value, transaction_id=None, discrepancy_type="missing_on_afm",
                        afm_amount=None, psp_amount=line.amount, currency=line.currency,
                        details=f"Référence {ref} confirmée par {psp.value}, aucune transaction AFM correspondante",
                    ))

            for d in discrepancies:
                session.add(d)
            await session.commit()

            logger.info(
                "Reconciliation run completed",
                psp=psp.value,
                afm_transaction_count=len(afm_transactions),
                psp_statement_count=len(statement),
                discrepancy_count=len(discrepancies),
            )

            return {
                "psp": psp.value,
                "afm_transaction_count": len(afm_transactions),
                "psp_statement_count": len(statement),
                "discrepancy_count": len(discrepancies),
            }

    async def approve_discrepancy(
        self, discrepancy_id: UUID, approver_id: UUID, notes: Optional[str] = None
    ) -> dict:
        """
        Enregistre une approbation d'apurement. Exige deux approbateurs
        DISTINCTS avant que l'écart soit considéré comme résolu — contrôle
        à quatre yeux explicitement demandé par l'audit. Aucune écriture de
        contrepassation n'est postée automatiquement même après la 2e
        approbation : ça reste une action distincte et délibérée (voir
        `apply_correction`), pour qu'une approbation ne déclenche jamais
        silencieusement un mouvement de fonds.
        """
        async with AsyncSessionLocal() as session:
            discrepancy = await session.get(ReconciliationDiscrepancy, discrepancy_id)
            if not discrepancy:
                raise ReconciliationError(f"Discrepancy {discrepancy_id} not found")
            if discrepancy.resolved_at:
                raise ReconciliationError(f"Discrepancy {discrepancy_id} already resolved")

            if discrepancy.first_approved_by is None:
                discrepancy.first_approved_by = approver_id
                from datetime import datetime, timezone
                discrepancy.first_approved_at = datetime.now(timezone.utc)
                stage = "first_approval_recorded"
            elif discrepancy.second_approved_by is None:
                if discrepancy.first_approved_by == approver_id:
                    raise ReconciliationError(
                        "Second approval must come from a different approver than the first "
                        "(contrôle à quatre yeux — un seul et même approbateur ne peut pas valider deux fois)"
                    )
                from datetime import datetime, timezone
                discrepancy.second_approved_by = approver_id
                discrepancy.second_approved_at = datetime.now(timezone.utc)
                discrepancy.resolution_notes = notes
                stage = "second_approval_recorded_ready_for_correction"
            else:
                raise ReconciliationError(f"Discrepancy {discrepancy_id} already has two approvals")

            await session.commit()
            return {"discrepancy_id": str(discrepancy_id), "stage": stage}


reconciliation_service = ReconciliationService()
