
- [ ] Publier le correctif local 529e0da après rétablissement de l’authentification GitHub
- [ ] Reconstruire le job Northflank afm-kora-main avec le runner pay-in STK/processing corrigé
- [ ] Rejouer la validation sandbox multi-corridors et confirmer le remboursement uniquement après pay-in settled
- [ ] Vérifier les logs diagnostiques sans exposition de secrets

- [ ] Diagnostiquer l’échec du job Northflank signalé après la fusion de la PR 40
- [ ] Corriger la cause racine et relancer le job après validation locale

- [x] Aligner la durée maximale réelle du job Northflank avec la fenêtre de confirmation STK
- [x] Gérer explicitement l’expiration STK et journaliser un statut terminal sûr sans remboursement ambigu
- [x] Tester et publier la correction structurelle sur le runner et la configuration du job

- [x] Vérifier les logs de la dernière exécution Northflank après le passage à 15 minutes
- [x] Simuler localement un timeout STK et vérifier stk_prompt_expired/refund_skipped

- [ ] Déclencher un run sandbox réel avec la révision 663b7ff et la limite Northflank de 900 secondes
- [ ] Autoriser le prompt STK et vérifier pay-in settled, webhook, ledger et refund réconcilié

- [x] Configurer `KORA_TEST_PHONE=237683361360` pour le prochain run sandbox MTN
- [x] Vérifier la configuration du numéro avant tout nouveau pay-in réel

- [x] Déclencher le run réel MTN après confirmation de l’utilisateur
- [ ] Suivre le prompt STK et valider le pay-in, le webhook, le ledger et le remboursement

- [x] Diagnostiquer l’absence de prompt STK sur le run MTN réel sans saisir ni demander de PIN
- [x] Vérifier la cohérence KORA_TEST_PHONE/KORA_TEST_MOBILE_NUMBER et de l’opérateur mtn-cm

- [x] Analyser les logs détaillés du run MTN 9cd954b6-009d-4543-bb20-eda7721810dc pour expliquer l’absence de prompt STK

- [x] Vérifier en lecture seule le statut Kora de KPY-CA-cSA51aKaw5E7 après expiration
- [x] Rechercher un webhook postérieur et documenter toute évolution sans mouvement financier

- [x] Concevoir la réconciliation différée des pay-ins Kora restés en Processing
- [x] Définir les transitions webhook, les délais d’expiration et les tests d’idempotence associés

- [x] Implémenter une tâche persistante de réconciliation des pay-ins Processing
- [x] Ajouter le worker de reprise et empêcher les remboursements sur statut ambigu
- [x] Ajouter les tests webhook/worker et contrôler la migration avant publication

- [x] Vérifier la branche main et l’état Alembic avant migration production 009
- [x] Appliquer la migration 009 additive sur Neon et vérifier la table/index
- [x] Redéployer Northflank et contrôler le démarrage du worker de réconciliation

- [ ] Identifier l’erreur exacte de schéma ou de contrainte dans Neon après le déploiement
- [ ] Corriger l’objet de base concerné sans modifier les données métier
- [ ] Retester le service et le worker après correction

- [x] Isoler la configuration locale AFM de l’URL MySQL externe
- [x] Valider Alembic avec une configuration PostgreSQL/Neon locale explicite
- [x] Documenter la procédure pour éviter une nouvelle confusion d’environnement

- [x] Auditer la branche fix/refund-runner-transient-retry et isoler le périmètre de la PR
- [x] Préparer la Pull Request vers main avec un résumé clair des correctifs
- [x] Exécuter tous les tests unitaires et d’intégration et documenter leur résultat

- [x] Vérifier les conflits potentiels entre fix/refund-runner-transient-retry et main
- [x] Concevoir l’IA de support corrective avec mode proposition, validation et journalisation
- [x] Implémenter l’analyse d’incidents et les actions déterministes sûres dans AFM
- [x] Ajouter les tests de l’IA corrective sans appel réel à un modèle ni à Kora
- [x] Rédiger les instructions de recette UAT sur staging

- [ ] Vérifier les checks et fusionner la PR 44 dans main
- [x] Déployer la révision fusionnée sur l’environnement staging
- [ ] Configurer le prompt LLM externe sécurisé avec sortie structurée et activation contrôlée
- [ ] Exécuter les scénarios UAT staging sans flux financier réel

- [x] Confirmer si la cible est l’environnement Default, où le service AFM existant est déployé
- [x] Ne pas créer de nouveau service dans adept-breath ; redéployer uniquement après confirmation de l’environnement cible

- [ ] Exécuter les scénarios UAT non financiers sur le service AFM déployé
- [ ] Préparer et activer si possible le prompt LLM externe sécurisé dans l’environnement validé
- [ ] Retester le support correctif et contrôler les logs sans flux financier réel

- [ ] Vérifier l’existence et la configuration sandbox du service `kora-sandbox`
- [ ] Exécuter UAT-01 à UAT-08 sur `kora-sandbox` sans flux financier de production
- [ ] Configurer l’enrichissement OpenAI avec secret staging et prompt sécurisé

- [ ] Confirmer la stratégie pour le job `AFM kora` échoué dans `kora-sandbox` avant la recette UAT
- [ ] Ne pas exécuter UAT ni ajouter la clé OpenAI tant que le job n’est pas aligné sur le correctif main et la base sandbox

- [x] Reconstruire le job existant `AFM kora` depuis `loicmpanjo-jpg/AFM`
- [x] Vérifier le build et les logs de démarrage après reconstruction

- [ ] Ajouter `SUPPORT_AI_LLM_BASE_URL` et `SUPPORT_AI_LLM_MODEL` au job afm-kora sans ajouter de clé
- [ ] Vérifier le fallback déterministe puis lancer un seul run sandbox contrôlé

- [ ] Identifier l’IP sortante réelle du serveur AFM Northflank
- [ ] Vérifier si l’IP est statique et distinguer sandbox de production
- [ ] Préparer l’ajout de l’IP à la whitelist payout Kora après confirmation

- [ ] Vérifier les IP egress existantes pour AFM dans Northflank
- [ ] Provisionner ou rattacher une IP egress fixe dédiée au job payout
- [ ] Vérifier l’IP depuis le workload puis l’ajouter à la whitelist Kora

- [ ] Comparer les alternatives gratuites avec IP publique stable pour la whitelist payout Kora
- [ ] Vérifier les risques de disponibilité et de sécurité avant toute recommandation

- [ ] Définir une architecture de proxy egress VPS pour les payouts Korapay
- [ ] Analyser les risques de désactivation temporaire de la whitelist IP en sandbox
- [ ] Documenter les contrôles de sécurité et le plan de retour arrière

- [ ] Définir le schéma PostgreSQL Neon des payouts Korapay
- [ ] Ajouter les contraintes d’idempotence, index et réconciliation
- [ ] Documenter les règles de transition et de ledger

- [ ] Documenter l’authentification sortante et HMAC des webhooks Korapay
- [ ] Documenter l’idempotence, les retries et les garde-fous de payout

- [ ] Auditer les routes d’authentification et le domaine AFM existants
- [ ] Définir le formulaire email/OTP et le dossier KYC minimal
- [ ] Implémenter la persistance et les contrôles de sécurité KYC
- [ ] Tester sur staging et préparer les variables de production

- [ ] Réserver l’accès utilisateur aux routes et corridors de production
- [ ] Interdire l’accès public utilisateur aux routes et jobs sandbox
- [ ] Vérifier la parité code/schéma/configuration entre sandbox et production avec secrets séparés

- [x] Ajouter la migration additive 010 pour les challenges OTP et les profils KYC minimaux
- [x] Implémenter la demande et la vérification OTP par e-mail sans stockage du code en clair
- [x] Ajouter la soumission KYC utilisateur avec statut pending et revue manuelle
- [x] Ajouter la garde serveur KYC vérifié sur les routes de paiement et payout en production
- [x] Créer la page /onboarding dédiée à l’accès production, sans parcours utilisateur vers la sandbox
- [x] Mettre à jour le narratif public pour distinguer utilisateurs finaux, fintechs, PSPs et investisseurs corporate
- [ ] Configurer et tester le relais SMTP de production sans exposer les secrets
- [ ] Appliquer la migration 010 sur Neon production et sandbox de façon contrôlée
- [ ] Exécuter les tests OTP/KYC et un test non financier des routes production
- [ ] Vérifier la parité code et schéma entre environnements avec secrets séparés

- [x] Remplacer le KYC documentaire par une vérification légère sans dépôt ni stockage de documents
- [x] Ajouter la confirmation OTP du numéro mobile Money et son statut de vérification
- [x] Ajouter le consentement explicite au partage des attributs e-mail nécessaires
- [x] Limiter le profil stocké à l’identité déclarée, l’e-mail vérifié, le téléphone vérifié et les transactions
- [x] Retirer de l’interface onboarding le type et les quatre derniers chiffres du document
- [x] Adapter la garde production pour exiger e-mail et téléphone vérifiés, sans KYC documentaire
- [x] Tester la rétention minimale et les parcours e-mail/Google/OTP téléphone

- [x] Séparer le numéro WhatsApp OTP du numéro Mobile Money dans le modèle utilisateur
- [x] Ajouter les variables Meta WhatsApp Business sans secrets dans le dépôt
- [x] Implémenter l’envoi OTP WhatsApp avec expiration, idempotence et absence de logs sensibles
- [x] Ajouter la confirmation indépendante du numéro Mobile Money retourné par l’opérateur
- [x] Empêcher de déduire que le numéro WhatsApp est le propriétaire du numéro Mobile Money
- [x] Adapter l’interface pour saisir et afficher séparément les deux numéros
- [x] Tester un compte WhatsApp différent du numéro utilisé pour le pay-in/payout

- [ ] Vérifier les paramètres Meta Business et le numéro WhatsApp Business
- [ ] Préparer le template d’authentification OTP en français
- [ ] Renseigner le jeton Meta et le Phone Number ID dans l’environnement sécurisé
- [ ] Tester l’envoi OTP WhatsApp en dry-run puis avec confirmation contrôlée
- [ ] Vérifier la séparation du numéro WhatsApp OTP et du numéro Mobile Money

- [ ] Retirer le SMS AFM du parcours principal et utiliser la confirmation opérateur Mobile Money
- [ ] Valider les migrations 010, 011 et 012 avant déploiement
- [ ] Créer un checkpoint backend avant publication
- [ ] Déployer la version AFM mise à jour sur l’environnement de production
- [ ] Vérifier les endpoints publics et privés après déploiement

- [ ] Fusionner les changements distants LLM avec l’onboarding sans SMS
- [ ] Résoudre les suppressions/conflits des fichiers onboarding et WhatsApp sans perte
- [ ] Rejouer la suite complète après fusion
- [ ] Publier la branche intégrée sans force push
- [ ] Vérifier la révision déployée et les endpoints d’onboarding
