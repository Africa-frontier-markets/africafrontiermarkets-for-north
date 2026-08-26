# Recette UAT — Support correctif AFM et réconciliation Kora

## Objectif

Valider sur l’environnement de staging que le support correctif identifie les incidents, propose uniquement des actions autorisées et ne modifie jamais un mouvement financier sans statut fournisseur final et validation opérationnelle.

## Préconditions

Le staging doit utiliser une base PostgreSQL dédiée, des clés Kora sandbox et un `KORA_WEBHOOK_SECRET` de staging distinct de la production. Les numéros sandbox doivent être explicitement configurés dans `KORA_TEST_PHONE` et `KORA_TEST_MOBILE_NUMBER`. Aucun PIN ou OTP ne doit être saisi dans AFM, Northflank ou un outil de test.

| Vérification | Résultat attendu |
|---|---|
| Branche déployée | Révision issue de la PR de support corrective |
| Base | Migration 009 appliquée sur staging uniquement |
| Santé | `/health` et `/ready` renvoient HTTP 200 |
| Worker | Une seule boucle active par instance ; reprise des tâches abandonnées |
| Secrets | Aucun secret présent dans les logs |

## Scénarios fonctionnels

### UAT-01 — Pay-in Processing

Créer un pay-in sandbox qui retourne `processing`. Vérifier que le webhook répond rapidement HTTP 200, que la transaction AFM reste intermédiaire, qu’une tâche `scheduled` est créée et que le diagnostic support propose `enqueue_reconciliation`. Vérifier l’absence de mouvement ledger.

### UAT-02 — Confirmation settled tardive

Après expiration du runner initial, envoyer un webhook signé `settled` ou rendre le statut final disponible via l’API sandbox. Vérifier que la tâche devient `completed`, que la transaction est marquée réglée une seule fois et que le ledger est crédité exactement une fois.

### UAT-03 — Expiration STK

Laisser le prompt STK sans autorisation jusqu’à l’expiration. Vérifier `stk_prompt_expired=true`, `refund_skipped=true`, l’absence de remboursement et la conservation de la transaction dans un état non réglé tant qu’aucun échec fournisseur final n’est confirmé.

### UAT-04 — Webhook dupliqué

Renvoyer deux fois le même webhook signé. Vérifier que le premier événement est `processed`, que le second retourne `already_processed` ou l’équivalent idempotent, et qu’aucune seconde écriture ledger n’est créée.

### UAT-05 — Erreur transitoire Kora

Simuler HTTP 429, 502 ou 503 sur la consultation de statut. Vérifier le backoff borné, l’action proposée `retry_status_lookup`, la conservation du statut financier et l’absence de remboursement.

### UAT-06 — Erreur de schéma

Injecter un scénario contrôlé `undefined column` sur une base de test. Vérifier que le support classe l’incident `database_schema`, propose `open_incident`, exige une validation humaine et ne tente aucune correction SQL automatique.

### UAT-07 — Données sensibles

Injecter des champs nommés `api_key`, `authorization`, `pin`, `otp` et `token` dans un événement de test. Vérifier qu’ils sont remplacés par `[REDACTED]` dans le diagnostic et absents des logs.

### UAT-08 — Concurrence et crash

Lancer deux workers sur le même jeu de tâches puis interrompre l’un pendant le traitement. Vérifier que `FOR UPDATE SKIP LOCKED` empêche le double traitement et que la tâche verrouillée est récupérable après le délai prévu.

## Critères d’acceptation

La recette est acceptée si tous les scénarios UAT-01 à UAT-08 passent, si aucun secret n’apparaît dans les logs, si aucune transaction n’est remboursée depuis un état `processing` ou ambigu, et si chaque mouvement financier final est idempotent.

## Preuves à conserver

Conserver la révision déployée, les résultats de tests, les identifiants d’événements anonymisés, les statuts de tâche, les réponses HTTP et les extraits de logs ne contenant aucune donnée sensible. Ne pas conserver de PIN, OTP, clé API ou valeur complète de secret.
