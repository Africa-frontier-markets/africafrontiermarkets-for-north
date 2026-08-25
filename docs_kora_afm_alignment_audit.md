# Audit d’alignement Kora–AFM

**Date : 25 août 2026**  
**Périmètre :** Getting Started, pay-ins, payouts, Mobile Money, webhooks, remboursements, conversion et réconciliation AFM.

## Conclusion exécutive

Le protocole AFM est **globalement aligné** avec le parcours Kora : les flux distinguent l’encaissement et le décaissement, utilisent une référence unique, vérifient la signature HMAC, conservent les événements, appliquent l’idempotence et n’écrivent dans le ledger virtuel qu’après rapprochement d’un événement vérifié.

Kora confirme que les pay-ins Mobile Money couvrent notamment le Ghana en GHS, le Cameroun en XAF et la Côte d’Ivoire en XOF. Kora confirme également les payouts Mobile Money en GHS, XAF et XOF, ainsi que les payouts bancaires NGN. [1] [2]

L’écart principal concerne le **contrat exact de statut et d’acquittement webhook**. La documentation Kora décrit `data.status` comme `success` ou `failed` et recommande un HTTP 200 rapide ; elle ne définit pas `settled` comme un événement webhook indépendant. AFM accepte `settled` comme valeur de compatibilité interne, ce qui est acceptable si le statut est prouvé par la réponse API ou le rapprochement, mais le système ne doit pas dépendre d’un événement Kora nommé `settled`. [3]

## Matrice d’alignement

| Domaine | Exigence Kora | Implémentation AFM | Évaluation |
|---|---|---|---|
| Authentification API | Bearer secret key | `Authorization: Bearer KORA_SECRET_KEY` | Aligné |
| Pay-in | `charges/mobile-money`, devise et numéro | `initiate_mobile_money_charge` avec client, devise, référence et numéro | Aligné |
| PIN portefeuille | Ne pas collecter le PIN dans l’application marchande | AFM traite OTP/STK/REDIRECT sans collecter de PIN | Aligné |
| Payout | `transactions/disburse`, destination Mobile Money ou bancaire | `create_payout` avec validation des champs | Aligné |
| Solde | Compte marchand suffisamment financé | Balance API disponible en lecture seule avant décision | Aligné, contrôle opérationnel à renforcer |
| Opérateur | Réseau et slug disponibles par pays | Résolution prévue via List MMO, fixtures limitées au Test | Aligné sous réserve d’activation par compte |
| Webhook URL | POST public, sans session | `/webhooks/kora` public | Aligné |
| Signature | HMAC-SHA256 sur l’objet `data` uniquement | HMAC sur JSON compact et trié | À confirmer avec le sérialiseur exact Kora |
| Acquittement | HTTP 200 rapide ; retry jusqu’à 72 h si autre réponse/timeout | HTTP 200 après traitement ; 401 signature invalide ; 500 erreur métier | Écart à corriger/assumer explicitement |
| Idempotence | Conserver les notifications et éviter le double traitement | `kora_webhook_events.event_id` unique et ledger idempotent | Aligné ; collision de fallback à durcir |
| Statuts | `success` / `failed` dans `data.status` | AFM accepte aussi `successful`, `completed`, `settled` | Compatible, mais `settled` doit rester interne |
| Refund | Événements `refund.success` / `refund.failed` | Route protégée et réconciliation idempotente | Aligné sous réserve du contrat de création |
| Conversion | Produit Currency Conversion et paires activés | XOF↔USD↔XAF ou USD selon corridor | Aligné sous réserve d’activation FX |

## Écarts techniques à traiter

### 1. Sérialisation HMAC

La documentation Kora donne un exemple `JSON.stringify(req.body.data)` sans indiquer explicitement le tri des clés ni une canonicalisation indépendante du langage. AFM utilise actuellement `json.dumps(data, separators=(",", ":"), sort_keys=True)`. Cette méthode est déterministe, mais elle ne garantit pas une égalité byte-à-byte avec le JSON sérialisé par Kora si l’ordre des clés, l’échappement Unicode ou la représentation numérique diffèrent.

**Action recommandée :** conserver une fixture officielle capturée avec sa signature, vérifier la signature AFM sur cette fixture et documenter la canonicalisation réellement attendue. Ne pas modifier la logique HMAC sur la seule base d’un exemple théorique.

### 2. HTTP 200 pour les signatures invalides

Kora indique qu’un code différent de 200 ou un timeout provoque des retries. AFM acquitte désormais HTTP 200 pour une signature invalide, sans persistance ni traitement métier, et conserve HTTP 500 pour une erreur interne transitoire. Ce comportement suit la recommandation d’acquittement Kora tout en maintenant la frontière de sécurité.

**Contrôle appliqué :** une signature invalide est journalisée sans secret, aucun événement métier n’est créé et HTTP 200 est retourné ; HTTP 500 reste réservé aux erreurs internes transitoires. Le comportement est couvert par un test.

### 3. Identifiant d’événement de repli

Lorsque Kora ne fournit pas de champ `id`, AFM dérive désormais l’identifiant de repli de `event_type + reference + payload_hash`, ce qui évite la collision entre deux types d’événements portant la même référence.

**Contrôle appliqué :** AFM utilise `id` Kora lorsqu’il existe ; sinon l’identifiant de repli combine `event_type`, référence et `payload_hash`, tandis que `payload_hash` reste conservé séparément.

### 4. Statut settled

La page Webhooks Kora définit `data.status` comme `success` ou `failed`. AFM accepte `settled` et renseigne `settled_at`, ce qui peut être conservé comme état interne de réconciliation. En revanche, AFM ne doit pas attendre un webhook `settled` spécifique qui n’est pas documenté sur cette page.

**Action recommandée :** documenter la transition `success Kora → completed AFM → reconciliation matched`, et réserver `settled_at` à la preuve API ou à une règle de rapprochement validée. Toute valeur `settled` reçue doit rester tolérée, mais non obligatoire.

### 5. Création des remboursements

La documentation d’intégration confirme les événements `refund.success` et `refund.failed`. Le contrat de création `refunds/initiate` est documenté dans la page Refunds API déjà conservée par AFM, mais doit être testé avec le compte Kora actif avant activation Live. Les champs de simulation `completion_status` et `status_reason` doivent rester strictement sandbox.

**Action recommandée :** interdire ces deux champs dans toute requête Live au niveau du schéma d’entrée ou du client, plutôt que de dépendre uniquement de l’appelant. Conserver la route en validation-only par défaut et exiger confirmation explicite plus clé d’idempotence pour une exécution réelle.

## Corridors

Le corridor Cameroun↔Côte d’Ivoire est cohérent avec les produits Kora documentés : pay-in XAF au Cameroun avec MTN/Orange, pay-in XOF en Côte d’Ivoire avec MTN/Orange/Moov/Wave, puis payout dans la devise locale du bénéficiaire. [1] [2]

Kora ne documente pas une paire directe XAF/XOF dans les pages consultées. AFM doit utiliser XAF→USD→XOF et XOF→USD→XAF, uniquement lorsque Currency Conversion, USD et les deux jambes sont activés sur le compte marchand. Les slugs d’opérateur doivent être résolus dynamiquement par pays ; les valeurs `mtn-cm` et `mtn-ci` restent des fixtures Test et ne prouvent pas une activation Live.

## Verdict

Le protocole et le processus AFM sont **alignés sur le modèle Kora au niveau fonctionnel**. Les trois contrôles prioritaires sont maintenant implémentés et testés localement : fixture de forme issue de l’exemple Kora avec hash HMAC déterministe, acquittement HTTP 200 sans traitement pour signature invalide, et fallback `event_id` sans collision. La fixture de test ne constitue pas une signature émise par Kora ; une notification Kora réelle reste nécessaire pour la validation byte-à-byte externe. Le remboursement Live demeure soumis à la validation du contrat sur le compte Kora actif.

## Références

[1]: https://developers.korapay.com/docs/accept-payments "Kora — Accept Payments"
[2]: https://developers.korapay.com/docs/send-payments "Kora — Send Payments"
[3]: https://developers.korapay.com/docs/webhooks "Kora — Webhooks"
[4]: https://developers.korapay.com/docs/getting-started "Kora — Getting Started"
[5]: https://developers.korapay.com/docs/refunds-api "Kora — Refunds API"
