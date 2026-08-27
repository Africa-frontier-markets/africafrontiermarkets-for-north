# AFM — Modèle d’identité minimale

> Document de conception — à faire valider par le conseil juridique et la fonction conformité avant ouverture commerciale.

AFM ne collecte ni ne conserve de document d’identité sur sa plateforme. La vérification repose sur l’authentification du compte e-mail ou Google, le consentement explicite au partage des attributs nécessaires, puis la confirmation par OTP du numéro de téléphone associé au compte mobile Money utilisé pour le flux. Le statut technique est considéré comme vérifié lorsque l’e-mail et le téléphone sont tous deux confirmés ; toute suspension fondée sur une anomalie opérationnelle ou réglementaire reste possible.

| Donnée | Conservation AFM | Usage |
|---|---|---|
| E-mail | Oui, adresse normalisée | Compte, notifications, reçu de transaction |
| Identifiant Google/OAuth | Oui, identifiant technique | Liaison du compte authentifié, sans mot de passe Google |
| Nom et prénom | Oui | Profil utilisateur et contrôles opérationnels |
| Date de naissance | Oui, si fournie par le fournisseur d’identité avec consentement | Contrôle de cohérence minimal |
| Numéro de téléphone | Oui, normalisé | OTP et association du bénéficiaire mobile Money |
| Statut e-mail vérifié | Oui | Condition d’accès |
| Statut téléphone vérifié | Oui | Condition d’accès aux pay-ins/payouts |
| Statut identité technique | Oui | Synthèse des deux vérifications, révocable |
| Horodatage du consentement | Oui | Preuve technique du partage autorisé |
| Pièce d’identité, image, numéro complet | **Non** | Jamais reçu ni stocké par AFM |
| Transactions | Oui | Exécution, ledger, réconciliation et obligations applicables |

Le statut `verified` signifie uniquement que les contrôles techniques prévus par AFM sont satisfaits ; il ne s’agit pas d’une certification réglementaire autonome. L’opérateur mobile Money reste responsable de ses propres contrôles d’entrée en relation et AFM doit conserver un mécanisme de suspension et de revue des anomalies.

Les secrets d’OTP sont stockés uniquement sous forme de digest HMAC, avec expiration, limite de tentatives et invalidation après usage. Les codes en clair, les documents et les réponses sensibles des fournisseurs d’identité ne doivent jamais apparaître dans les logs.

## Séparation WhatsApp et Mobile Money

Le champ `whatsapp_phone` sert exclusivement à l’envoi et à la vérification de l’OTP WhatsApp. Le champ `mobile_money_phone` sert exclusivement à sélectionner le portefeuille ou la destination d’une opération. Ces deux valeurs peuvent être différentes et ne doivent jamais être utilisées comme alias l’une de l’autre.

Pour chaque pay-in ou payout, AFM conserve le numéro Mobile Money utilisé et la référence opérateur. Le champ `mobile_money_owner_verified_at` n’est renseigné que lorsqu’une réponse opérateur signée contient un numéro correspondant exactement au numéro de la transaction. La vérification WhatsApp ne renseigne jamais ce champ.

Une transaction peut donc être autorisée techniquement par un utilisateur dont l’OTP WhatsApp est vérifié, puis être exécutée vers un autre numéro Mobile Money, sous réserve des contrôles opérateur, de la confirmation explicite du bénéficiaire et des règles de risque applicables au corridor.
