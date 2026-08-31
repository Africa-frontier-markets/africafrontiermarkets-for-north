# Monitoring Northflank AFM

Le service `africafrontiermarkets-for-north` reste la cible unique de production. Le déploiement de la révision `9239ca6` a été déclenché automatiquement après la fusion de la PR #50 ; Northflank affichait `Deploying` avec une nouvelle instance. L’interface Observe rencontrait toutefois une erreur de connexion au backend de logs.

Le dépôt contient désormais `scripts/northflank_logs_probe.py`, un fallback sans dépendance externe. Il appelle l’endpoint REST officiel `GET /v1/projects/{projectId}/services/{serviceId}/logs`, filtre les logs `runtime`, limite la fenêtre à 15 minutes par défaut et produit un JSON exploitable par une alerte ou un collecteur externe.

Exemple d’exécution hors dépôt, avec les valeurs injectées par le gestionnaire de secrets ou la CI :

```sh
export NORTHFLANK_API_TOKEN='secret-injecte-par-le-runner'
export NORTHFLANK_PROJECT_ID='africafrontiermarkets'
export NORTHFLANK_SERVICE_ID='africafrontiermarkets-for-north'
python scripts/northflank_logs_probe.py
```

Le token n’est pas placé dans Git, dans les arguments de commande enregistrés, ni dans les logs applicatifs. La permission minimale documentée par Northflank est `Project > Services > Deployment > View Observability`. Pour une surveillance sans token, les sondes HTTPS peuvent vérifier périodiquement `/health`, `/ready` et `/` du domaine public ; elles ne remplacent pas la collecte des logs.

Une alternative de long terme est de configurer un log sink Northflank vers un collecteur HTTP, Better Stack, Loki ou Axiom. Cette configuration nécessite une destination et, selon le fournisseur, un token fourni par le propriétaire du compte ; elle ne peut pas être finalisée automatiquement sans ces paramètres.

Références : [Northflank Get service logs](https://northflank.com/docs/v1/api/project/services/get-service-logs), [Northflank Log tailing](https://northflank.com/docs/v1/api/log-tailing), [Northflank Configure log sinks](https://northflank.com/docs/v1/application/observe/configure-log-sinks).
