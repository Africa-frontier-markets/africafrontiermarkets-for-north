# Intégration Nexus sandbox dans AFM

## Documentation de référence

- Documentation IA complète : <https://docs.nexus-africa.io/llms-full.txt>
- Index : <https://docs.nexus-africa.io/llms.txt>
- OpenAPI : <https://docs.nexus-africa.io/openapi.yaml>

## Ce qui est intégré

AFM contient désormais `payment_hub/nexus_client.py` avec :

- Basic Auth HTTPS : clé secrète Nexus comme username, mot de passe vide ;
- URL sandbox par défaut : `https://api.dev.neero.io/payment-gateway/api/v1` ;
- lecture des Payment Methods et Transaction Intents ;
- création de Payment Methods Mobile Money ;
- création de Cash-Out protégée par `NEXUS_ALLOW_TRANSACTION_WRITES=false` ;
- clé `X-IDEMPOTENCY-KEY` obligatoire pour les écritures ;
- gestion des erreurs HTTP et des codes métier sans journaliser les secrets ;
- vérification HMAC-SHA512 des webhooks.

## Variables Northflank

À injecter dans le groupe sandbox, sans les committer :

```text
NEXUS_SECRET_KEY=<clé test Nexus>
NEXUS_API_BASE_URL=https://api.dev.neero.io/payment-gateway/api/v1
NEXUS_PLATFORM_CODE=<code créé dans Dashboard > Compliance>
NEXUS_WEBHOOK_SECRET=<secret HMAC webhook, si Nexus en fournit un>
NEXUS_ALLOW_TRANSACTION_WRITES=false
```

## Limites importantes

La documentation Nexus décrit principalement les rails CEMAC et fournit des numéros sandbox camerounais (`+237`). Elle ne confirme pas, à elle seule, un corridor transfrontalier Cameroun → Côte d’Ivoire ni un payout vers le Nigeria. L’adaptateur Nexus est donc ajouté sans remplacer automatiquement l’adaptateur AZA pour les corridors XOF/NGN.

Avant toute activation d’écriture :

1. déclarer la plateforme AFM dans Nexus Compliance ;
2. confirmer les Payment Methods marchand et Mobile Money ;
3. confirmer par écrit les pays, devises et opérateurs autorisés sur le compte ;
4. exécuter uniquement les numéros sandbox officiels ;
5. garder `NEXUS_ALLOW_TRANSACTION_WRITES=false` jusqu’à validation du parcours ;
6. utiliser une clé d’idempotence persistante et unique par intention de paiement.

## Exemple de test de lecture

```bash
curl -u "${NEXUS_SECRET_KEY}:" \
  -H 'Accept: application/json' \
  "${NEXUS_API_BASE_URL}/payment-methods"
```

Aucune commande de cash-in/cash-out ne doit être exécutée avec `confirm: true` pendant le préflight.
