"""
AFM Screening Service — sanctions & PEP matching.

L'algorithme est réel (normalisation, matching flou par nom + alias,
bonus de corroboration pays/date de naissance, seuils), pas un mock qui
retourne toujours CLEAR. Ce qui n'est PAS réel : le jeu de données (voir
sanctions_data.py). Documenté ainsi pour ne pas faire croire que "zéro
ligne de code" est devenu "prêt pour la production" — ça devient "l'AML a
maintenant un moteur, il lui manque un flux de données agréé".
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from compliance.models import ScreeningResult, ScreeningType, ScreeningOutcome, EDDCase, EDDStatus
from compliance.sanctions_data import (
    DATA_SOURCE_NAME, load_sanctions_entries, load_pep_entries, SanctionsEntry, PEPEntry,
)
from config.logging_config import configure_logging

logger = configure_logging()

# Seuils de similarité (0-100). Un score >= MATCH_THRESHOLD ouvre une revue
# humaine (POTENTIAL_MATCH) — le système ne bloque JAMAIS automatiquement un
# marchand sur un simple score : il escalade vers un dossier EDD, comme pour
# le scoring de risque marchand déjà en place (merchant/service.py).
MATCH_THRESHOLD = 82
STRONG_MATCH_THRESHOLD = 92  # score seul, sans corroboration, jugé suffisant pour ouvrir un EDD immédiatement


def _normalize(name: str) -> str:
    """Minuscules, accents retirés, ponctuation réduite à des espaces simples —
    pour ne pas rater 'José Ovono' vs 'Jose Ovono' à cause d'un accent."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", ascii_only)
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _name_similarity(a: str, b: str) -> int:
    return round(SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() * 100)


@dataclass
class ScreeningCandidate:
    list_name: str
    matched_name: str
    score: int
    country_match: bool
    dob_match: bool


def _screen_against_sanctions(
    subject_name: str, subject_country: Optional[str], subject_dob: Optional[str],
    entries: tuple[SanctionsEntry, ...],
) -> list[ScreeningCandidate]:
    candidates: list[ScreeningCandidate] = []
    for entry in entries:
        names_to_check = (entry.full_name, *entry.aliases)
        best_score = max(_name_similarity(subject_name, n) for n in names_to_check)
        if best_score < MATCH_THRESHOLD - 15:
            continue  # trop loin pour valoir la peine d'être retenu, même comme faible signal
        country_match = bool(subject_country and entry.country and subject_country.upper() == entry.country.upper())
        dob_match = bool(subject_dob and entry.dob and subject_dob == entry.dob)
        candidates.append(ScreeningCandidate(entry.list_name, entry.full_name, best_score, country_match, dob_match))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _screen_against_pep(
    subject_name: str, subject_country: Optional[str], entries: tuple[PEPEntry, ...],
) -> list[ScreeningCandidate]:
    candidates: list[ScreeningCandidate] = []
    for entry in entries:
        score = _name_similarity(subject_name, entry.full_name)
        if score < MATCH_THRESHOLD - 15:
            continue
        country_match = bool(subject_country and entry.country and subject_country.upper() == entry.country.upper())
        candidates.append(ScreeningCandidate(entry.category, entry.full_name, score, country_match, False))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _resolve_outcome(candidates: list[ScreeningCandidate]) -> ScreeningOutcome:
    if not candidates:
        return ScreeningOutcome.CLEAR
    top = candidates[0]
    # Corroboration (pays ou date de naissance identiques) abaisse la barre :
    # un nom moyennement proche + le même pays est plus significatif qu'un
    # nom très proche isolé (les noms se répètent beaucoup plus que les
    # coïncidences pays+nom).
    effective_threshold = MATCH_THRESHOLD - 10 if (top.country_match or top.dob_match) else MATCH_THRESHOLD
    if top.score >= effective_threshold:
        return ScreeningOutcome.POTENTIAL_MATCH
    return ScreeningOutcome.CLEAR


class ScreeningService:
    async def run_sanctions_screening(
        self, session: AsyncSession, merchant_id: UUID,
        subject_name: str, subject_country: Optional[str] = None, subject_dob: Optional[str] = None,
    ) -> ScreeningResult:
        candidates = _screen_against_sanctions(subject_name, subject_country, subject_dob, load_sanctions_entries())
        outcome = _resolve_outcome(candidates)
        result = self._persist(session, merchant_id, ScreeningType.SANCTIONS, outcome, subject_name, subject_country, subject_dob, candidates)
        await self._maybe_open_edd(session, merchant_id, result, candidates)
        return result

    async def run_pep_screening(
        self, session: AsyncSession, merchant_id: UUID,
        subject_name: str, subject_country: Optional[str] = None,
    ) -> ScreeningResult:
        candidates = _screen_against_pep(subject_name, subject_country, load_pep_entries())
        outcome = _resolve_outcome(candidates)
        result = self._persist(session, merchant_id, ScreeningType.PEP, outcome, subject_name, subject_country, None, candidates)
        await self._maybe_open_edd(session, merchant_id, result, candidates)
        return result

    def _persist(
        self, session: AsyncSession, merchant_id: UUID, screening_type: ScreeningType,
        outcome: ScreeningOutcome, subject_name: str, subject_country: Optional[str],
        subject_dob: Optional[str], candidates: list[ScreeningCandidate],
    ) -> ScreeningResult:
        result = ScreeningResult(
            merchant_id=merchant_id,
            screening_type=screening_type,
            outcome=outcome,
            subject_name=subject_name,
            subject_country=subject_country,
            subject_dob=subject_dob,
            candidates=[c.__dict__ for c in candidates[:10]],
            top_score=candidates[0].score if candidates else 0,
            data_source=DATA_SOURCE_NAME,
        )
        session.add(result)
        return result

    async def _maybe_open_edd(
        self, session: AsyncSession, merchant_id: UUID, result: ScreeningResult, candidates: list[ScreeningCandidate],
    ) -> Optional[EDDCase]:
        if result.outcome == ScreeningOutcome.CLEAR:
            return None
        await session.flush()  # obtient result.id
        top = candidates[0]
        reason = (
            f"{result.screening_type.value} screening: correspondance potentielle avec "
            f"'{top.matched_name}' ({top.list_name}), score={top.score}"
            + (", pays corroboré" if top.country_match else "")
            + (", date de naissance corroborée" if top.dob_match else "")
        )
        case = EDDCase(
            merchant_id=merchant_id,
            triggering_screening_id=result.id,
            status=EDDStatus.OPEN,
            reason=reason,
        )
        session.add(case)
        await session.flush()
        logger.warning("EDD case opened", merchant_id=str(merchant_id), screening_type=result.screening_type.value, score=top.score)
        return case


screening_service = ScreeningService()
