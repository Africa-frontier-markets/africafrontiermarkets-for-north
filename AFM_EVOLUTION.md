# AFM — Évolution sans coût additionnel

Cette branche reste strictement alignée sur le service Northflank et les outils déjà déployés. Elle n’ajoute ni staging, ni job payant, ni migration de base, ni nouvelle infrastructure.

## Évolution conservée

La vitrine `public/index.html` et le dashboard `public/dashboard.html` sont ajoutés au dépôt existant. L’API actuelle sert la vitrine sur `/`, le dashboard sur `/dashboard`, les assets sur `/static`, et conserve ses endpoints `/health`, `/ready`, `/metrics` ainsi que ses routes métier existantes. Le Dockerfile et le port 8000 déjà utilisés par Northflank restent inchangés.

## Évolutions volontairement différées

Les modules conformité, marchand, PSP health, ledger, rapprochement et Alpaca supplémentaires de l’archive ne sont pas fusionnés dans cette PR. Ils nécessitent une comparaison de schéma, des migrations Alembic, des tests PostgreSQL/Redis et une validation métier qui ne peuvent pas être effectués gratuitement dans l’environnement actuel.

## Vérifications sans coût

La branche est vérifiable par compilation Python, contrôle des fichiers statiques et exécution du serveur avec les dépendances déjà présentes dans le service. La fusion doit rester soumise à revue humaine. Northflank peut ensuite reconstruire le service existant depuis cette branche, sans créer de nouvelle ressource.
