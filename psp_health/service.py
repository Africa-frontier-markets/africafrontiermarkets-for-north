"""
AFM PSP Health Service.

Répond à l'exigence Kora "PSP Health — Latency, Availability, Success Rate"
(Kora / Payoneer / Alpaca dans la grille transmise ; ce dépôt ne couvre que
les PSP de paiement effectivement intégrés — Alpaca relève de
market_gateway, absent de ce codebase).

Distinction délibérée entre deux métriques, alignée sur la classification
ambiguous/failed déjà construite dans payment_service._call_psp_api :
- `success_rate`  = décisions métier positives (le PSP a répondu ET accepté)
- `availability`  = 1 - (part des appels où le PSP n'a pas répondu de façon
  exploitable). Un refus explicite (carte refusée, solde insuffisant côté
  PSP) compte contre le success_rate mais PAS contre l'availability — le PSP
  a répondu, il n'est pas "en panne". Un timeout/erreur réseau compte contre
  les deux.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from config.database import AsyncSessionLocal
from config.logging_config import configure_logging
from psp_health.models import PSPCallLog, PSPCallOutcome, PSPOperation

logger = configure_logging()

DEFAULT_HEALTH_WINDOW_MINUTES = 60

# Seuils de statut — pas des constantes contractuelles (contrairement à
# KORA_MAX_CHARGEBACK_RATE dans merchant/service.py), volontairement
# ajustables sans lien à une clause précise du contrat Kora.
HEALTHY_SUCCESS_RATE = 0.98
DEGRADED_SUCCESS_RATE = 0.90


@dataclass
class PSPHealthSnapshot:
    psp: str
    window_minutes: int
    total_calls: int
    success_count: int
    failed_count: int
    ambiguous_count: int
    success_rate: Optional[float]
    availability: Optional[float]
    avg_latency_ms: Optional[float]
    max_latency_ms: Optional[int]
    status: str  # "healthy" | "degraded" | "down" | "unknown"


class PSPHealthService:
    async def record_call(
        self,
        psp: str,
        operation: PSPOperation,
        outcome: PSPCallOutcome,
        latency_ms: int,
        error_message: Optional[str] = None,
    ) -> None:
        """Écrit dans SA PROPRE session, indépendante de celle du paiement en
        cours — un souci d'écriture de télémétrie ne doit jamais faire
        échouer ni ralentir un paiement réel."""
        try:
            async with AsyncSessionLocal() as session:
                session.add(PSPCallLog(
                    psp=psp, operation=operation, outcome=outcome,
                    latency_ms=latency_ms, error_message=(error_message or "")[:500] or None,
                ))
                await session.commit()
        except Exception as e:
            # Ne jamais laisser un problème de logging remonter à l'appelant.
            logger.error("Failed to record PSP health telemetry", psp=psp, error=str(e))

    async def compute_health(
        self, session: AsyncSession, psp: Optional[str] = None, window_minutes: int = DEFAULT_HEALTH_WINDOW_MINUTES
    ) -> list[PSPHealthSnapshot]:
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        conditions = [PSPCallLog.created_at >= since, PSPCallLog.operation == PSPOperation.CHARGE]
        if psp:
            conditions.append(PSPCallLog.psp == psp)

        rows = (await session.execute(
            select(
                PSPCallLog.psp,
                PSPCallLog.outcome,
                func.count(PSPCallLog.id),
                func.avg(PSPCallLog.latency_ms),
                func.max(PSPCallLog.latency_ms),
            )
            .where(and_(*conditions))
            .group_by(PSPCallLog.psp, PSPCallLog.outcome)
        )).all()

        by_psp: dict[str, dict] = {}
        for psp_name, outcome, count, avg_latency, max_latency in rows:
            entry = by_psp.setdefault(psp_name, {
                "success": 0, "failed": 0, "ambiguous": 0,
                "latency_sum_weighted": 0.0, "max_latency": 0, "total": 0,
            })
            entry[outcome.value] += count
            entry["total"] += count
            entry["latency_sum_weighted"] += float(avg_latency or 0) * count
            entry["max_latency"] = max(entry["max_latency"], max_latency or 0)

        snapshots = []
        for psp_name, stats in by_psp.items():
            total = stats["total"]
            success = stats["success"]
            failed = stats["failed"]
            ambiguous = stats["ambiguous"]

            success_rate = success / total if total else None
            availability = (1 - (ambiguous / total)) if total else None
            avg_latency = stats["latency_sum_weighted"] / total if total else None

            if total == 0:
                status = "unknown"
            elif success_rate >= HEALTHY_SUCCESS_RATE:
                status = "healthy"
            elif success_rate >= DEGRADED_SUCCESS_RATE:
                status = "degraded"
            else:
                status = "down"

            snapshots.append(PSPHealthSnapshot(
                psp=psp_name, window_minutes=window_minutes, total_calls=total,
                success_count=success, failed_count=failed, ambiguous_count=ambiguous,
                success_rate=success_rate, availability=availability,
                avg_latency_ms=avg_latency, max_latency_ms=stats["max_latency"],
                status=status,
            ))

        return sorted(snapshots, key=lambda s: s.psp)


psp_health_service = PSPHealthService()
