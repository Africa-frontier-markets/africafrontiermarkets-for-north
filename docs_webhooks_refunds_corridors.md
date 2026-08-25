# Webhooks Kora, remboursements et corridor Cameroun–Côte d’Ivoire

## 1. Réception et authentification

Dans le dashboard Kora, renseigner l’URL publique `https://africafrontiermarkets.com/webhooks/kora`. L’URL doit être accessible sans session utilisateur et accepter `POST` en HTTPS. Kora documente les événements `charge.success`, `charge.failed`, `transfer.success`, `transfer.failed`, `refund.success` et `refund.failed`.

AFM lit le corps JSON brut, extrait `data`, puis vérifie `x-korapay-signature` avec HMAC-SHA256 sur la sérialisation canonique de l’objet `data` et la clé secrète Kora correspondante à l’environnement. La clé doit rester dans le coffre Northflank sous `KORA_WEBHOOK_SECRET` et ne doit jamais être exposée dans les logs. Une signature absente ou invalide est rejetée en `401` et ne doit créer aucune écriture.

La réponse d’acquittement doit être rapide. Kora indique qu’une réponse autre que HTTP 200 ou un timeout peut entraîner des nouvelles tentatives pendant 72 heures [1]. Le traitement métier peut être séparé par une file interne, mais AFM doit conserver la réception, le hash du payload, l’identifiant d’événement, le type et l’état de traitement.

## 2. Idempotence et machine d’états

La table `kora_webhook_events` est la clé d’idempotence : `event_id` est unique, `payload_hash` permet de détecter une même référence avec un contenu divergent, et `status` évolue de `received` à `processed` ou `failed`. Un doublon déjà traité retourne un acquittement sans rejouer la logique métier. Un même `event_id` avec un hash différent doit être mis en exception et ne doit pas écraser le premier payload.

La transaction AFM est recherchée par référence Kora ou identifiant PSP, dans le namespace `afm_payments`. Les transitions recommandées sont les suivantes : `pending → processing` lors de l’initiation ; `processing → completed` sur `charge.success` ou `transfer.success` validé ; `processing → failed` sur un échec confirmé ; et `completed → refunded` uniquement après confirmation d’un remboursement réussi. Une transition inverse ou ambiguë doit passer en `held`/exception et requérir une revue.

| Événement Kora | Action AFM | Écriture ledger |
|---|---|---|
| `charge.success` | Marquer le pay-in complété après contrôle montant/devise/référence | Crédit unique du sous-ledger client |
| `charge.failed` | Marquer failed, conserver la cause | Aucun crédit |
| `transfer.success` | Marquer le payout complété/settled après contrôle | Débit payout unique |
| `transfer.failed` | Marquer failed ou held selon la cause | Aucun débit final |
| `refund.success` | Rapprocher le remboursement et passer la transaction à refunded | Contre-écriture unique du mouvement concerné |
| `refund.failed` | Conserver completed/held selon le cas et créer une exception | Aucune contre-écriture finale |

## 3. Reprise des échecs

Le traitement d’un événement doit être transactionnel : verrouiller la ligne de transaction, vérifier la référence, la devise, le montant, le corridor et l’état courant, écrire le mouvement ledger idempotent, puis marquer l’événement `processed` dans la même transaction de base de données. En cas d’erreur transitoire, effectuer quelques retries avec backoff exponentiel et gigue, par exemple 1, 5 et 15 minutes, sans renvoyer une seconde écriture métier.

Après épuisement des retries, placer l’événement dans une dead-letter queue ou une table d’exception avec le nombre d’essais, le dernier message d’erreur et la prochaine action. Une alerte doit être déclenchée après le seuil configuré, par exemple trois échecs consécutifs pour le même événement ou la même référence. La reprise manuelle doit réutiliser le même `event_id` et le même payload, jamais créer une nouvelle transaction.

Un job de rapprochement périodique peut interroger Kora pour les transactions `processing` trop anciennes et comparer le statut API au dernier webhook reçu. Il doit uniquement produire une proposition de réconciliation ou un événement interne idempotent ; il ne doit pas marquer `settled` sans preuve Kora et ne doit jamais initier un payout ou un remboursement automatiquement sans règle approuvée.

## 4. Remboursements automatiques contrôlés

Le remboursement automatique doit être déclenché seulement par une règle déterministe : pay-in confirmé mais payout impossible, montant collecté supérieur au montant payable, expiration de la cotation avant le payout, ou annulation conforme à la politique du corridor. Il ne doit pas être déclenché par un simple timeout réseau, un webhook dupliqué ou une réponse temporairement indisponible.

Avant de créer un remboursement, AFM doit vérifier que le pay-in est `completed`, qu’aucun remboursement n’est déjà `requested`, `processing` ou `completed`, que le montant remboursable est positif et que la référence parent est connue. Créer une commande interne `refund_requested` avec une clé idempotente dérivée de la transaction parent, par exemple `refund:{payment_reference}:{amount}:{currency}`. Une seule commande Kora peut être envoyée pour cette clé.

Le endpoint exact de remboursement doit être confirmé dans la documentation ou le compte Kora activé avant implémentation ; la documentation publique consultée confirme les événements `refund.success`/`refund.failed`, mais ne permet pas de déduire un chemin de création fiable. AFM ne doit donc pas inventer un endpoint. Une fois le contrat Kora confirmé, l’adaptateur devra envoyer la référence parent, le montant, la devise et la raison, enregistrer la réponse sans secret, puis attendre `refund.success` avant de passer `refunded`.

La contre-écriture ledger doit être équilibrée et liée à la référence parent : débit du payable client et crédit du compte de retour, ou l’inverse selon le sens comptable choisi par AFM. Elle doit contenir `reference_type=refund`, `reference_id` égal à la référence Kora du remboursement et `parent_reference`. Une contrainte unique sur cette paire empêche le double remboursement. Les frais non remboursables doivent être isolés et affichés séparément dans le dashboard.

## 5. Corridor Cameroun↔Côte d’Ivoire

Le pay-in côté Cameroun est collecté en XAF sur MTN ou Orange. Le pay-in côté Côte d’Ivoire est collecté en XOF sur MTN, Orange, Moov ou Wave. Pour le sens Cameroun→Côte d’Ivoire, le taux est obtenu en deux jambes XAF→USD puis USD→XOF. Pour le sens Côte d’Ivoire→Cameroun, le taux est obtenu par XOF→USD puis USD→XAF. Kora ne documente pas de paire directe XAF/XOF [2].

Le payout du corridor est effectué dans la devise locale du pays destinataire : XOF vers un réseau ivoirien, ou XAF vers un réseau camerounais. AFM doit appeler List MMO par pays et sélectionner le slug activé, puis vérifier le bénéficiaire lorsque le compte Kora le requiert. Les fixtures `mtn-cm` et `mtn-ci` restent limitées au mode Test ; elles ne sont pas des preuves d’activation Live.

| Sens | Collecte | Conversion | Payout | Contrôle clé |
|---|---|---|---|---|
| Cameroun → Côte d’Ivoire | XAF, MTN/Orange | XAF→USD→XOF | XOF, opérateur ivoirien résolu dynamiquement | Référence, devise XOF, limite et nom bénéficiaire |
| Côte d’Ivoire → Cameroun | XOF, MTN/Orange/Moov/Wave | XOF→USD→XAF | XAF, opérateur camerounais résolu dynamiquement | Référence, devise XAF, limite et nom bénéficiaire |

Les frais pay-in, payout, conversion, Kaybic et la commission AFM de 2 % sont calculés avant l’instruction payout. L’utilisateur voit un montant total `Platform fees`; le dashboard interne conserve le détail. Le ledger ne crédite ou ne débite le compte virtuel qu’après les événements Kora vérifiés et idempotents.

## 6. Configuration Kora→AFM

Dans Kora, configurer l’URL webhook distinctement en Test et Live, puis activer les événements de charge, transfert et remboursement requis. Dans Northflank, conserver au minimum `KORA_API_BASE_URL`, `KORA_SANDBOX`, `KORA_WEBHOOK_SECRET`, `KORA_API_KEY` et `KORA_SECRET_KEY` dans les groupes de secrets correspondant à leur environnement. Le job sandbox doit posséder uniquement le groupe Test et `KORA_SANDBOX=true`; le service de production doit utiliser uniquement le groupe Live et ne doit jamais hériter des fixtures de test.

Après configuration, envoyer un événement Test depuis le dashboard Kora ou utiliser une fixture signée contrôlée. Vérifier successivement HTTP 200, création de `kora_webhook_events`, recherche de la transaction AFM, écriture ledger unique, passage à `processed`, puis rejeu identique retournant un acquittement idempotent. Vérifier enfin que les événements `refund.success` et `refund.failed` sont reçus et routés en exception tant que le contrat d’émission du remboursement n’est pas validé.

## Références

[1]: https://developers.korapay.com/docs/webhooks "Kora — Webhooks"
[2]: https://developers.korapay.com/docs/convert-currency "Kora — Currency Conversion"
[3]: https://developers.korapay.com/docs/mobile-money "Kora — Mobile Money Payments"
[4]: https://developers.korapay.com/docs/payout-via-api "Kora — Payout API"
