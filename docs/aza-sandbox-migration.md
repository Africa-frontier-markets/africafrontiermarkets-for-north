# Migration FrontierPay vers AZA sandbox

## État

FrontierPay utilise désormais l’endpoint AZA `POST /v1/transactions/calculate` pour ses aperçus publics. Cet endpoint calcule les montants et le taux sans créer, financer ou payer une transaction.

Les anciennes routes Kora restent présentes uniquement pour compatibilité de données et d’anciens clients ; elles ne sont plus utilisées par le simulateur FrontierPay ni par le routeur PSP actif.

## Variables Northflank

À injecter dans le service backend, sans les committer :

- `AZA_API_KEY`
- `AZA_API_SECRET`
- `AZA_API_BASE_URL=https://api-sandbox.transferzero.com/v1`
- `AZA_ALLOW_TRANSACTION_WRITES=false`

Les valeurs ne doivent jamais apparaître dans les logs, le dépôt ou les réponses HTTP.

## Couverture documentée

| Corridor AFM | Devise destination | Méthode AZA sandbox |
|---|---:|---|
| Cameroun → Côte d’Ivoire | XOF | `XOF::Mobile` |
| Côte d’Ivoire → Cameroun | XAF | `XAF::Mobile` |
| Côte d’Ivoire → Ghana | GHS | `GHS::Mobile` |
| Côte d’Ivoire → Nigeria | NGN | `NGN::Bank` |
| Cameroun → Nigeria | NGN | `NGN::Bank` |
| Bénin → Nigeria | NGN | `NGN::Bank` |

**Limitation importante :** la documentation AZA sandbox consultée expose `NGN::Bank`, pas `NGN::Mobile`. Les trois corridors vers le Nigeria sont donc affichés comme décaissements bancaires, et non comme payout wallet mobile. Une couverture NGN Mobile nécessiterait une confirmation contractuelle séparée d’AZA.

## Sécurité

- L’authentification AZA est signée HMAC-SHA512 avec nonce unique par requête.
- Le endpoint public appelle seulement `calculate`.
- La création et le payout de transactions sont bloqués par défaut avec `AZA_ALLOW_TRANSACTION_WRITES=false`.
- La migration de base ajoute la valeur PSP `aza`; les tables historiques Kora ne sont pas supprimées afin de préserver l’audit et les données existantes.

## Validation

```bash
python3 -m py_compile api_gateway/main.py payment_hub/aza_client.py payment_hub/models.py config/config.py
node --check public/afm-web.js
pnpm check  # dans afm-mobile
```

Après injection des variables Northflank, redéployer le backend puis vérifier le simulateur avec un montant d’au moins 100 000 XAF/XOF. Aucun endpoint de création de transaction ne doit être appelé pour ce contrôle.
