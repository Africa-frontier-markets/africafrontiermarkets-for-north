# Sécurité des callbacks Visa Direct

Les routes `POST /webhooks/visa/receive-side` et `POST /webhooks/visa/issuer` sont servies par l’API FastAPI existante. Elles accusent réception sans déclencher d’opération financière ; le traitement métier idempotent doit rester séparé de l’acknowledgement.

Le module `api_gateway/visa_webhooks.py` applique une limite de payload de 1 MiB et valide que le corps est un objet JSON. Le transport Two-Way SSL/mTLS doit être terminé par l’ingress de confiance ou par Uvicorn. Lorsque l’ingress transmet une assertion authentifiée, l’application peut exiger simultanément `x-client-cert-verify: SUCCESS` et une assertion HMAC `x-afm-mtls-assertion` calculée sur `visa-client-cert:SUCCESS` avec `VISA_MTLS_PROXY_SECRET`. Un simple header de certificat non authentifié n’est jamais accepté.

Le contrôle HMAC applicatif est activé uniquement si `VISA_WEBHOOK_SHARED_SECRET` est défini. Dans ce mode, Visa doit transmettre `x-visa-signature` au format `sha256=<hex digest>` calculé sur les octets bruts du payload avec HMAC-SHA256. Tant que Visa n’a pas confirmé ce header et ce format pour le projet sandbox, laisser cette variable absente afin de ne pas rejeter les callbacks Two-Way SSL valides ; le mTLS réseau reste la protection principale.

| Variable | Effet | Valeur de production |
|---|---|---|
| `VISA_MTLS_REQUIRED` | Exige l’assertion mTLS authentifiée | `true` seulement après configuration de l’ingress |
| `VISA_MTLS_PROXY_SECRET` | Secret partagé entre ingress et API | secret Northflank, jamais commité |
| `VISA_WEBHOOK_SHARED_SECRET` | Active la vérification HMAC du payload Visa | secret uniquement après confirmation Visa |

Les tests ciblés couvrent le JSON sandbox, le rejet de signature HMAC invalide et le rejet d’assertion mTLS absente. Le module ne journalise ni le payload complet ni les secrets.
