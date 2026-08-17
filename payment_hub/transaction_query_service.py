"""
AFM Transaction Query Service — API /transactions

Conçu pour incarner la thèse stratégique d'AFM (orchestrateur pur, pas un PSP de
plus) plutôt qu'une simple liste paginée :

- Chaque réponse porte un `orchestration_summary` qui calcule, sur l'ensemble
  filtré, le nombre de rails distincts utilisés (`distinct_rails_used`) et si ce
  volume qualifie le client comme un vrai client d'orchestration au sens du
  positionnement FrontierPay (3 rails ou plus — voir la doc commerciale
  "Notre principe d'engagement"). C'est la métrique qui rend la stratégie
  vérifiable dans le produit lui-même, pas seulement dans un deck.
- La répartition par rail (`rail_breakdown`) groupe le volume PAR DEVISE
  (`volume_by_currency`), jamais en un seul total toutes devises confondues —
  c'est précisément le genre d'erreur (additionner du XOF et de l'USD comme
  si c'était la même unité) qui a été trouvée et corrigée ailleurs dans ce
  projet (bornes de commission USD appliquées à des montants en devise
  locale). Le même principe s'applique ici par cohérence.
- Isolation stricte : toute requête est bornée à `user_id == current_user_id`.
  Aucun paramètre ne permet de contourner cette borne — c'est délibéré (test
  d'isolation multi-tenant exigé par l'audit).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from payment_hub.models import Transaction, PaymentStatus, PSPType

# Hard ceiling on export size — a filterless export from an authenticated but
# careless/malicious client should not be able to force an unbounded query.
# Paginate on the UI side for anything larger; this is an export safety valve,
# not a product limit.
MAX_EXPORT_ROWS = 10_000

# Orchestration qualification threshold, kept in sync with the commercial
# positioning ("le client a besoin de 3 rails ou plus pour que l'orchestration
# crée de la valeur mesurable").
ORCHESTRATION_RAIL_THRESHOLD = 3


@dataclass
class TransactionFilters:
    status: Optional[PaymentStatus] = None
    psp: Optional[PSPType] = None
    currency: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    search: Optional[str] = None  # matches transaction id / psp_transaction_id / idempotency_key


@dataclass
class RailBreakdownItem:
    psp: str
    count: int
    volume_by_currency: dict[str, str] = field(default_factory=dict)


@dataclass
class OrchestrationSummary:
    distinct_rails_used: int
    rails: list[str]
    qualifies_as_orchestration_client: bool
    rail_breakdown: list[RailBreakdownItem]


@dataclass
class TransactionPage:
    items: list[Transaction]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    orchestration_summary: OrchestrationSummary


class TransactionQueryService:
    def _apply_filters(self, stmt, user_id: str, filters: TransactionFilters):
        # user_id scoping is NOT optional and NOT part of TransactionFilters —
        # keeping it out of the filter dataclass means there is no code path
        # where forgetting to pass it silently drops tenant isolation.
        conditions = [Transaction.user_id == user_id]

        if filters.status:
            conditions.append(Transaction.status == filters.status)
        if filters.psp:
            conditions.append(Transaction.psp == filters.psp)
        if filters.currency:
            conditions.append(Transaction.currency == filters.currency.upper())
        if filters.date_from:
            conditions.append(Transaction.created_at >= filters.date_from)
        if filters.date_to:
            conditions.append(Transaction.created_at <= filters.date_to)
        if filters.min_amount is not None:
            conditions.append(Transaction.amount >= filters.min_amount)
        if filters.max_amount is not None:
            conditions.append(Transaction.amount <= filters.max_amount)
        if filters.search:
            like = f"%{filters.search}%"
            conditions.append(or_(
                Transaction.psp_transaction_id.ilike(like),
                Transaction.idempotency_key.ilike(like),
                # UUID -> text cast for a partial-id search (e.g. the first 8
                # characters of a transaction_id copy-pasted from a support ticket)
                Transaction.id.cast(__import__("sqlalchemy").String).ilike(like),
            ))

        return stmt.where(and_(*conditions))

    async def _compute_orchestration_summary(
        self, db: AsyncSession, user_id: str, filters: TransactionFilters
    ) -> OrchestrationSummary:
        """Rail distribution over the SAME filtered set the caller is looking at —
        so the summary always matches what's on screen, not the user's all-time
        history regardless of filters applied."""
        stmt = select(
            Transaction.psp,
            Transaction.currency,
            func.count(Transaction.id).label("cnt"),
            func.sum(Transaction.amount).label("vol"),
        ).group_by(Transaction.psp, Transaction.currency)
        stmt = self._apply_filters(stmt, user_id, filters)

        result = await db.execute(stmt)
        rows = result.all()

        by_psp: dict[str, RailBreakdownItem] = {}
        for psp, currency, cnt, vol in rows:
            psp_value = psp.value if hasattr(psp, "value") else str(psp)
            item = by_psp.setdefault(psp_value, RailBreakdownItem(psp=psp_value, count=0))
            item.count += cnt
            existing = Decimal(item.volume_by_currency.get(currency, "0"))
            item.volume_by_currency[currency] = str(existing + (vol or Decimal("0")))

        rails = sorted(by_psp.keys())
        return OrchestrationSummary(
            distinct_rails_used=len(rails),
            rails=rails,
            qualifies_as_orchestration_client=len(rails) >= ORCHESTRATION_RAIL_THRESHOLD,
            rail_breakdown=list(by_psp.values()),
        )

    async def list_transactions(
        self,
        db: AsyncSession,
        user_id: str,
        filters: TransactionFilters,
        page: int = 1,
        page_size: int = 20,
        sort_desc: bool = True,
    ) -> TransactionPage:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)

        count_stmt = self._apply_filters(select(func.count(Transaction.id)), user_id, filters)
        total_count = (await db.execute(count_stmt)).scalar_one()

        order_col = Transaction.created_at.desc() if sort_desc else Transaction.created_at.asc()
        stmt = self._apply_filters(select(Transaction), user_id, filters)
        stmt = stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(stmt)
        items = list(result.scalars().all())

        summary = await self._compute_orchestration_summary(db, user_id, filters)

        total_pages = (total_count + page_size - 1) // page_size if total_count else 0

        return TransactionPage(
            items=items,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            orchestration_summary=summary,
        )

    async def list_for_export(
        self, db: AsyncSession, user_id: str, filters: TransactionFilters
    ) -> list[Transaction]:
        stmt = self._apply_filters(select(Transaction), user_id, filters)
        stmt = stmt.order_by(Transaction.created_at.desc()).limit(MAX_EXPORT_ROWS)
        result = await db.execute(stmt)
        return list(result.scalars().all())


transaction_query_service = TransactionQueryService()
