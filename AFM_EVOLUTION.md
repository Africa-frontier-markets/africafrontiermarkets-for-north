# AFM — Évolution intégrée depuis l’archive

## Décision d’intégration

L’archive est traitée comme une évolution de la plateforme existante, et non comme un remplacement complet. La branche conserve l’API actuelle, ses endpoints de santé, ses intégrations de marché et ses garde-fous GitHub. Les éléments nouveaux sont ajoutés de manière additive afin de permettre une revue fonctionnelle avant toute migration de données ou activation en production.

## Éléments intégrés

| Élément | Intégration | Pertinence stratégique |
|---|---|---|
| `public/index.html` | Vitrine servie sur `/` par FastAPI | Élevée : présence publique cohérente avec le domaine AFM |
| `public/dashboard.html` | Console servie sur `/dashboard` | Élevée : surface opérationnelle distincte de la vitrine |
| `compliance/` | Services KYC/AML et modèles isolés | Élevée, mais activation conditionnée à une migration de schéma et une revue réglementaire |
| `merchant/` | Modèles marchands, chargebacks et journal d’activité | Élevée pour FrontierPay et la traçabilité |
| `psp_health/` | Suivi de santé des PSP | Élevée pour la fiabilité des paiements |
| `payment_hub/ledger_*`, reconciliation et queries | Briques de ledger et rapprochement | Élevée, mais nécessite validation des modèles et migrations avant usage réel |
| `market_gateway/alpaca_client.py`, `gateway.py` | Abstraction de connectivité Alpaca | Élevée pour EasyMarket, à maintenir en mode paper/sandbox tant que les contrôles ne sont pas validés |
| `scripts/` | Opérations explicites hors surface publique | Moyenne : utiles pour l’exploitation, à protéger par accès opérateur |

## Cohérence technique

La branche actuelle et l’archive ont des modèles SQLAlchemy, des migrations Alembic et des services de paiement différents. Les fichiers métier chevauchants n’ont donc pas été remplacés. Les nouveaux modules sont présents pour revue et évolution, tandis que la route frontend a été intégrée dans l’API existante avec une route `/` statique, `/dashboard` et `/static`.

Les exceptions communes nécessaires aux modules ajoutés ont été complétées de façon additive. Aucune migration destructive, aucun secret de production et aucune activation de paiement réel n’est inclus dans cette évolution.

## Cohérence stratégique

L’évolution est pertinente : elle rend visible la proposition de valeur AFM, ajoute une console d’exploitation, formalise la conformité marchand/KYC, améliore la traçabilité du ledger et prépare la santé des PSP. La priorité de mise en production doit rester l’ordre suivant : vitrine et dashboard en lecture contrôlée, observabilité et conformité, migration de schéma testée en staging, puis activation progressive des paiements et du trading.

## Risques et conditions de validation

Avant fusion, il faut comparer les modèles avec la base Northflank, produire une migration Alembic additive, exécuter la suite de tests avec PostgreSQL et Redis, vérifier les contrôles d’accès du dashboard et valider les flux PSP en simulation. La branche ne doit pas être déployée automatiquement tant que ces conditions ne sont pas satisfaites.
