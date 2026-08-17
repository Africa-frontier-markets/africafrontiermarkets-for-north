"""
Jeu de données de sanctions/PEP EMBARQUÉ — ÉCHANTILLON, PAS UNE SOURCE DE
PRODUCTION.

Ce fichier contient une poignée d'entrées illustratives (noms publics et
notoires de listes de sanctions internationales, utilisés ici uniquement
pour que l'algorithme de matching ait quelque chose de réel à comparer,
et une poignée de profils PEP génériques par fonction plutôt que par nom).
Il ne prétend PAS reproduire un flux OFAC SDN / UN Consolidated / EU
Sanctions List / PEP à jour — ces listes changent en continu et leur
distribution est encadrée. Avant toute mise en production, DATA_SOURCE_NAME
doit être remplacé par une intégration réelle (API d'un fournisseur agréé :
ComplyAdvantage, Refinitiv World-Check, Dow Jones Risk & Compliance, ou le
flux OFAC/UN téléchargé et rafraîchi automatiquement) branchée derrière la
même interface (`load_sanctions_entries` / `load_pep_entries`) pour que
screening_service.py n'ait rien à changer côté algorithme.
"""

from dataclasses import dataclass

DATA_SOURCE_NAME = "embedded_sample_v1"


@dataclass(frozen=True)
class SanctionsEntry:
    list_name: str
    full_name: str
    aliases: tuple[str, ...]
    country: str | None
    dob: str | None  # YYYY-MM-DD si connu, sinon None


@dataclass(frozen=True)
class PEPEntry:
    category: str          # ex: "head_of_state", "minister", "central_bank_official"
    full_name: str
    country: str | None


# Échantillon volontairement réduit — voir avertissement en tête de fichier.
_SANCTIONS_ENTRIES: tuple[SanctionsEntry, ...] = (
    SanctionsEntry("OFAC_SDN_SAMPLE", "Viktor Bout", ("Viktor Anatolyevich Bout",), "RU", "1967-01-13"),
    SanctionsEntry("OFAC_SDN_SAMPLE", "Dmitry Konstantinovich Kiselev", ("Dmitriy Kiselyov",), "RU", None),
    SanctionsEntry("UN_CONSOLIDATED_SAMPLE", "Aleksandr Grigoryevich Lukashenko", ("Alexander Lukashenko",), "BY", "1954-08-30"),
    SanctionsEntry("EU_SAMPLE", "Nicolas Maduro Moros", ("Nicolas Maduro",), "VE", "1962-11-23"),
)

# PEP par catégorie générique, pas par personne nommée en dur au-delà de
# quelques exemples publics — la couverture réelle d'un flux PEP dépend de
# la juridiction et doit venir d'un fournisseur (ex: World-Check One).
_PEP_ENTRIES: tuple[PEPEntry, ...] = (
    PEPEntry("head_of_state", "Paul Biya", "CM"),
    PEPEntry("head_of_state", "Denis Sassou Nguesso", "CG"),
    PEPEntry("central_bank_official", "Abbas Mahamat Tolli", "CM"),  # Gouverneur BEAC (fonction publique notoire)
)


def load_sanctions_entries() -> tuple[SanctionsEntry, ...]:
    return _SANCTIONS_ENTRIES


def load_pep_entries() -> tuple[PEPEntry, ...]:
    return _PEP_ENTRIES
