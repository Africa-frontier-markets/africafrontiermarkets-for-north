# Proposition contrôlée — KYC 1 jusqu’à 10 000 000 XAF mensuels

**Statut : proposition non appliquée et non déployée.** Toute modification du code, du manuel AML ou des plafonds opérationnels doit être validée par le CCO et, si nécessaire, par les partenaires et autorités compétents.

## Principe

Le plafond mensuel de 10 000 000 XAF serait une limite interne AFM de cumul pour certains particuliers sur des corridors africains autorisés. Il ne s’agit ni d’un plafond réglementaire universel, ni d’un plafond Kora, ni d’une preuve que le client peut accéder à tous les corridors avec un KYC minimal.

Les limites Kora publiquement consultées indiquent un maximum de 500 000 XAF/XOF par pay-in Mobile Money et un maximum de 500 000 XAF/XOF par payout, par transaction. Les limites opérateur peuvent être supérieures ou différentes selon le pays, le produit et le niveau du portefeuille. AFM doit donc appliquer le minimum entre la limite opérateur confirmée, la limite Kora confirmée, la limite de corridor et la limite interne AFM.

## Matrice proposée

| Couche de contrôle | Proposition | Action en cas de dépassement |
|---|---:|---|
| Par transaction | `min(opérateur, Kora, corridor, plafond AFM)` ; valeur Kora publique de référence XAF/XOF : 500 000 par transaction | Refus technique si la limite partenaire est certaine ; sinon revue et confirmation du plafond courant. |
| Seuil journalier souple | Limite réelle communiquée par l’opérateur et Kora ; 50 000 XAF reste un indicateur comportemental AFM, non un blocage automatique | Alerte de vélocité/concentration ; pas de rétrogradation automatique. |
| Cumul mensuel KYC 1 | Jusqu’à 10 000 000 XAF pour le périmètre approuvé | Blocage du dépassement et escalade avant toute opération supplémentaire. |
| Entre 500 000 et 10 000 000 XAF/mois | KYC 1 renforcé, avec screening, preuve opérateur et monitoring continu | Revue automatique selon risque, corridor et comportement. |
| Au-delà de 10 000 000 XAF/mois | Sortie du KYC 1 | Passage KYC 2/EDD ou refus selon le profil. |
| Corridors Europe/Asie | Non inclus par défaut dans ce KYC 1 | KYC 2/KYC 3, KYB ou EDD selon le manuel. |
| PSP, fintech, corporate | Jamais couverts par ce KYC 1 particulier | KYB complet, UBO, licences, politique AML et responsable conformité. |

## Contrôles obligatoires avant chaque opération

AFM devrait vérifier l’identité du client déjà enregistrée, le bénéficiaire, le corridor, la devise, la limite par transaction, le cumul du jour, le cumul mensuel, la fréquence, le statut de screening sanctions/PEP et la réponse opérateur. Le `mobile_money_phone` de la transaction doit rester indépendant du `whatsapp_phone` éventuellement utilisé pour une authentification ou une notification.

Une réponse opérateur doit au minimum fournir une référence vérifiable, le numéro ou l’identifiant de portefeuille utile à la correspondance, le statut de l’opération, la date et le corridor. Une transaction réussie sans donnée fiable sur le portefeuille ne doit pas être traitée comme une preuve complète de propriété.

## Escalades proposées

| Déclencheur | Traitement proposé |
|---|---|
| Cumul mensuel supérieur à 5 000 000 XAF | Revue de profil et source économique déclarée ; maintien possible en KYC 1 uniquement si le CCO valide le périmètre. |
| Cumul mensuel supérieur à 8 000 000 XAF | Revue renforcée avant poursuite ; limitation aux corridors africains à faible risque. |
| Cumul mensuel supérieur à 10 000 000 XAF | Blocage et passage KYC 2/EDD. |
| Plusieurs opérations proches du plafond partenaire | Alerte structuring ; HOLD possible même sous 10 000 000 XAF. |
| PEP, sanction, pays à risque ou mismatch opérateur | HOLD immédiat et revue conformité, indépendamment du cumul. |
| Afrique–Europe ou Afrique–Asie | Application du niveau supérieur prévu par le manuel, pas d’activation automatique du KYC 1. |

## Compatibilité avec le manuel AML

Le manuel FrontierPay définit actuellement KYC 1 comme un profil particulier à faible valeur avec 50 000 XAF/jour et 500 000 XAF/mois, tandis qu’il prévoit des exigences renforcées pour KYC 2/KYC 3, les corridors Europe/Asie, les PEP, les PSP et les profils à risque. Le passage à 10 000 000 XAF/mois est donc une modification substantielle de la politique interne. Il doit être approuvé, motivé par une analyse de risque, accompagné de contrôles renforcés et reflété dans les procédures de monitoring et d’escalade.

## Sources de comparaison

- Orange Money Cameroun, seuils publics : https://orangemoney.orange.cm/fr/tarification-orange-money.html
- Kora, payouts : https://support.korapay.com/hc/en-us/articles/33568259700370-Payouts-on-the-Dashboard
- Kora, pay-in Mobile Money XAF/XOF : https://support.korapay.com/hc/en-us/articles/33568205803922-Pay-With-Mobile-Money-XAF-XOF
- NALA, limites de comptes bénéficiaires : https://help.nala.money/en/articles/6946502-what-are-the-transaction-limits-for-mobile-money-recipient-accounts

Ces sources ne constituent pas une validation réglementaire de la matrice proposée. Les plafonds doivent être confirmés dans les contrats, dashboards et règles actuelles de chaque opérateur et de Kora avant activation.
