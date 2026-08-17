"""
AFM Ledger Engine — Comptabilité en partie double, appliquée par le code.

Principe central : `post_journal()` REFUSE d'écrire tout ensemble
d'écritures qui ne s'équilibre pas exactement (somme débits == somme
crédits, par devise). Ce n'est pas une convention documentée que les
développeurs doivent respecter — c'est une contrainte vérifiée en code
avant tout INSERT, et la seule porte d'entrée pour écrire dans
ledger_entries. Aucun autre module n'écrit directement dans cette table.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import AsyncSessionLocal
from config.exceptions import LedgerError
from config.logging_config import configure_logging
from payment_hub.ledger_models import (
    LedgerAccount, LedgerEntry, LedgerAccountType, LedgerOwnerType, LedgerDirection,
)

logger = configure_logging()


@dataclass
class EntryInput:
    account_code: str
    direction: LedgerDirection
    amount: Decimal
    currency: str


# Comptes système (plan de comptes minimal mais réel — pas de placeholder).
# Un compte par devise supportée est créé au premier accès (idempotent).
SYSTEM_ACCOUNTS = {
    "psp_transit": {
        "name": "Fonds en transit PSP",
        "account_type": LedgerAccountType.ASSET,
        "owner_type": LedgerOwnerType.PSP_TRANSIT,
    },
    "afm_revenue": {
        "name": "Revenus de commission AFM",
        "account_type": LedgerAccountType.REVENUE,
        "owner_type": LedgerOwnerType.AFM_REVENUE,
    },
    "afm_treasury": {
        "name": "Trésorerie AFM",
        "account_type": LedgerAccountType.ASSET,
        "owner_type": LedgerOwnerType.AFM_TREASURY,
    },
    "suspense": {
        "name": "Compte de suspens (écarts non réconciliés)",
        "account_type": LedgerAccountType.SUSPENSE,
        "owner_type": LedgerOwnerType.SUSPENSE,
    },
    # AJOUT — trading_engine. Fonds bloqués/réglés côté broker (Alpaca) pour
    # des ordres exécutés, symétrique de psp_transit côté paiements : un
    # actif transitoire, pas la trésorerie AFM elle-même.
    "trading_settlement": {
        "name": "Fonds en règlement broker (trading)",
        "account_type": LedgerAccountType.ASSET,
        "owner_type": LedgerOwnerType.BROKER_TRANSIT,
    },
}


class LedgerEngine:

    async def _get_or_create_system_account(
        self, session: AsyncSession, key: str, currency: str
    ) -> LedgerAccount:
        spec = SYSTEM_ACCOUNTS[key]
        code = f"{key}:{currency}"
        result = await session.execute(select(LedgerAccount).where(LedgerAccount.code == code))
        account = result.scalar_one_or_none()
        if account:
            return account
        account = LedgerAccount(
            code=code,
            name=f"{spec['name']} ({currency})",
            account_type=spec["account_type"],
            owner_type=spec["owner_type"],
            owner_id=None,
            currency=currency,
        )
        session.add(account)
        await session.flush()  # obtient l'id sans committer — le caller gère la transaction
        return account

    async def get_or_create_user_account(
        self, session: AsyncSession, user_id: UUID, currency: str
    ) -> LedgerAccount:
        """Compte 'payable' représentant les fonds dus à un utilisateur/marchand."""
        code = f"user_payable:{user_id}:{currency}"
        result = await session.execute(select(LedgerAccount).where(LedgerAccount.code == code))
        account = result.scalar_one_or_none()
        if account:
            return account
        account = LedgerAccount(
            code=code,
            name=f"Fonds dus — utilisateur {user_id} ({currency})",
            account_type=LedgerAccountType.LIABILITY,
            owner_type=LedgerOwnerType.USER,
            owner_id=user_id,
            currency=currency,
        )
        session.add(account)
        await session.flush()
        return account

    async def post_journal(
        self,
        session: AsyncSession,
        entries: list[dict],
        transaction_id: Optional[UUID] = None,
        description: str = "",
        reverses_journal_id: Optional[UUID] = None,
    ) -> UUID:
        """
        Poste un ensemble d'écritures comme un seul journal atomique.

        entries: liste de dicts {"account": LedgerAccount, "direction":
        LedgerDirection, "amount": Decimal, "currency": str}

        Lève LedgerError si :
        - la liste est vide
        - un montant est <= 0
        - pour une devise donnée, somme(débits) != somme(crédits)

        Ne fait PAS de commit — le caller (payment_service, reconciliation)
        contrôle la transaction DB englobante pour que le postage comptable
        et la mise à jour du statut de paiement soient atomiques ensemble.
        """
        if not entries:
            raise LedgerError("Cannot post an empty journal")

        totals: dict[str, dict[str, Decimal]] = {}
        for e in entries:
            if e["amount"] <= 0:
                raise LedgerError(f"Ledger entry amount must be > 0, got {e['amount']}")
            cur = e["currency"]
            totals.setdefault(cur, {"debit": Decimal("0"), "credit": Decimal("0")})
            if e["direction"] == LedgerDirection.DEBIT:
                totals[cur]["debit"] += e["amount"]
            else:
                totals[cur]["credit"] += e["amount"]

        for cur, sums in totals.items():
            if sums["debit"] != sums["credit"]:
                raise LedgerError(
                    f"Journal not balanced for {cur}: "
                    f"debits={sums['debit']} credits={sums['credit']}. "
                    "Refusing to post — a payment engine must never write an "
                    "unbalanced ledger entry."
                )

        journal_id = uuid4()
        for e in entries:
            session.add(LedgerEntry(
                journal_id=journal_id,
                account_id=e["account"].id,
                direction=e["direction"],
                amount=e["amount"],
                currency=e["currency"],
                transaction_id=transaction_id,
                description=description,
                reverses_journal_id=reverses_journal_id,
            ))

        logger.info(
            "Ledger journal posted",
            journal_id=str(journal_id),
            transaction_id=str(transaction_id) if transaction_id else None,
            entry_count=len(entries),
            currencies=list(totals.keys()),
        )
        return journal_id

    async def post_payment_completed(
        self,
        session: AsyncSession,
        user_id: UUID,
        amount: Decimal,
        fee_amount: Decimal,
        net_amount: Decimal,
        currency: str,
        transaction_id: UUID,
    ) -> UUID:
        """
        Écriture standard pour un paiement de collecte réussi :

          DEBIT  psp_transit (actif)         amount       — fonds reçus du PSP, pas encore réglés
          CREDIT user_payable (passif)       net_amount   — désormais dus à l'utilisateur/marchand
          CREDIT afm_revenue (produit)       fee_amount   — commission AFM

        Équilibre : amount == net_amount + fee_amount (garanti par
        revenue_engine.calculate_fee — net_amount = amount - fee_amount).
        Toutes les lignes sont dans LA MÊME devise : c'est une correction
        déterminante par rapport à l'ancien code, qui étiquetait
        fee_currency="USD" en dur alors que amount est dans la devise
        locale (XOF, XAF, etc.) — un journal ne peut pas s'équilibrer si
        ses lignes ne sont pas dans la même devise sans passer par un
        compte de conversion FX dédié (non nécessaire ici car
        payment_service fixe désormais fee_currency = currency).
        """
        if fee_amount + net_amount != amount:
            # Filet de sécurité : si jamais l'arrondi du calcul de frais
            # dérive d'un centime, on refuse de fabriquer un ledger
            # déséquilibré plutôt que de laisser passer un écart silencieux.
            raise LedgerError(
                f"Fee split does not reconcile to gross amount: "
                f"{net_amount} + {fee_amount} != {amount}"
            )

        transit = await self._get_or_create_system_account(session, "psp_transit", currency)
        revenue = await self._get_or_create_system_account(session, "afm_revenue", currency)
        user_account = await self.get_or_create_user_account(session, user_id, currency)

        return await self.post_journal(
            session,
            entries=[
                {"account": transit, "direction": LedgerDirection.DEBIT, "amount": amount, "currency": currency},
                {"account": user_account, "direction": LedgerDirection.CREDIT, "amount": net_amount, "currency": currency},
                {"account": revenue, "direction": LedgerDirection.CREDIT, "amount": fee_amount, "currency": currency},
            ],
            transaction_id=transaction_id,
            description=f"Paiement complété — transaction {transaction_id}",
        )

    async def post_trade_filled(
        self,
        session: AsyncSession,
        user_id: UUID,
        side: str,
        cash_amount: Decimal,
        currency: str,
        trade_order_id: UUID,
    ) -> UUID:
        """
        Écriture pour un ordre exécuté (fill), premier jet volontairement
        simple :

          BUY  : DEBIT  trading_settlement (actif)   CREDIT user_payable
                 — le cash de l'utilisateur part vers le règlement broker.
          SELL : DEBIT  user_payable                 CREDIT trading_settlement
                 — le produit de la vente revient à l'utilisateur.

        Ne modélise PAS encore : les frais de courtage (aucun n'est
        actuellement configuré côté Alpaca paper trading), ni la position
        elle-même (voir trading_engine.models.Position, distincte du
        ledger — le ledger suit le cash, Position suit les titres détenus).
        """
        trading_settlement = await self._get_or_create_system_account(session, "trading_settlement", currency)
        user_account = await self.get_or_create_user_account(session, user_id, currency)

        if side == "buy":
            entries = [
                {"account": trading_settlement, "direction": LedgerDirection.DEBIT, "amount": cash_amount, "currency": currency},
                {"account": user_account, "direction": LedgerDirection.CREDIT, "amount": cash_amount, "currency": currency},
            ]
        elif side == "sell":
            entries = [
                {"account": user_account, "direction": LedgerDirection.DEBIT, "amount": cash_amount, "currency": currency},
                {"account": trading_settlement, "direction": LedgerDirection.CREDIT, "amount": cash_amount, "currency": currency},
            ]
        else:
            raise LedgerError(f"Unknown trade side: {side}")

        return await self.post_journal(
            session, entries=entries, description=f"Trade réglé — ordre {trade_order_id} ({side})",
        )

    async def reverse_journal(
        self,
        session: AsyncSession,
        original_journal_id: UUID,
        reason: str,
        transaction_id: Optional[UUID] = None,
    ) -> UUID:
        """
        Contrepassation : ne modifie JAMAIS les écritures existantes.
        Poste un nouveau journal avec les directions inversées, en gardant
        une référence (reverses_journal_id) vers le journal original.
        Utilisé pour refunds, chargebacks, rejets tardifs (cf. audit,
        section 4.2 : "Contrepassations, refunds, chargebacks et rejets
        tardifs").
        """
        result = await session.execute(
            select(LedgerEntry).where(LedgerEntry.journal_id == original_journal_id)
        )
        original_entries = result.scalars().all()
        if not original_entries:
            raise LedgerError(f"No entries found for journal_id={original_journal_id}")

        reversed_entries = [
            {
                "account": await session.get(LedgerAccount, e.account_id),
                "direction": LedgerDirection.CREDIT if e.direction == LedgerDirection.DEBIT else LedgerDirection.DEBIT,
                "amount": e.amount,
                "currency": e.currency,
            }
            for e in original_entries
        ]

        return await self.post_journal(
            session,
            entries=reversed_entries,
            transaction_id=transaction_id,
            description=f"Contrepassation de {original_journal_id} — {reason}",
            reverses_journal_id=original_journal_id,
        )

    async def get_balance(self, session: AsyncSession, account_id: UUID) -> Decimal:
        """
        Solde d'un compte = somme des débits - somme des crédits pour les
        comptes de nature ACTIF/CHARGE, ou l'inverse pour
        PASSIF/PRODUIT/SUSPENS. Calculé à la volée depuis le journal
        immuable — jamais depuis un champ 'balance' mis à jour
        indépendamment (c'est précisément ce que l'audit reproche à
        l'architecture actuelle : "un champ balance ne constitue pas un
        ledger financier").
        """
        account = await session.get(LedgerAccount, account_id)
        if not account:
            raise LedgerError(f"Unknown ledger account {account_id}")

        debit_sum = await session.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.account_id == account_id, LedgerEntry.direction == LedgerDirection.DEBIT
            )
        )
        credit_sum = await session.execute(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
                LedgerEntry.account_id == account_id, LedgerEntry.direction == LedgerDirection.CREDIT
            )
        )
        debit_total = Decimal(debit_sum.scalar_one())
        credit_total = Decimal(credit_sum.scalar_one())

        if account.account_type in (LedgerAccountType.ASSET, LedgerAccountType.EXPENSE):
            return debit_total - credit_total
        return credit_total - debit_total


# Singleton — pas d'état de connexion mis en cache dessus (voir
# compliance_engine/payment_service pour la raison : conflits d'event loop
# entre process/tests si une connexion est mise en cache sur l'instance).
ledger_engine = LedgerEngine()
