# Revue du manuel AML MEL / FrontierPay v2.0

Document fourni par l’utilisateur : `pasted_file_xcPJER_MEL_FrontierPay_Compliance_AML_EN_v2.0.pdf`, version 2.0, juin 2026.

## Constats documentaires

- Le manuel désigne MEL Investment Banking SARL comme opérateur de FrontierPay et décrit un périmètre Cameroun/CEMAC avec des corridors Afrique–Europe, Afrique–Asie et intra-Afrique (p. 1–4).
- Il prévoit une approche fondée sur les risques, trois lignes de défense, une politique de tolérance zéro AML/CFT, des seuils géographiques, de montant, de fréquence, de type de client, de corridor et de conversion FX (p. 5–6).
- Les signaux d’alerte FrontierPay comprennent notamment le layering entre corridors, les boucles FX, le structuring, les décaissements vers de nouveaux wallets, les incohérences d’origine/destination, le relais par PEP/sanctionné, les pics de vélocité et les activités multi-zones (p. 5–6).
- La section KYC indique que tous les clients Pay-In, Pay-Out, bulk, Visa Direct et FX doivent avoir une vérification identitaire validée avant tout flux (p. 7). Elle prévoit KYC 1 pour les particuliers à faible valeur avec téléphone, nom complet et date de naissance ; KYC 2 avec pièce officielle, preuve d’adresse et selfie ; KYC 3 avec KYB complet, bénéficiaires effectifs et surveillance continue (p. 7).
- La procédure d’onboarding décrite comprend collecte, vérification documentaire, filtrage automatisé multi-listes, contrôle PEP, scoring corridor, validation conformité, activation avec journal immuable et revues périodiques (p. 8–9).
- L’EDD est obligatoire pour les PEP, juridictions FATF à risque, profils multi-zones, montants supérieurs à USD 10 000 équivalent et PSPs multi-juridictions ; elle comprend notamment source des fonds, entretien, approbation de la direction, surveillance renforcée et notifications selon le corridor (p. 9–10).
- Le KYB des fintechs, PSPs, banques correspondantes, MTOs et clients institutionnels requiert notamment immatriculation, statuts, identifiant fiscal, registre des bénéficiaires effectifs, adresse, licences applicables, politique AML du partenaire et responsable conformité (p. 11–12).
- Le filtrage prévoit SDN OFAC, ONU, UE, UK OFSI, listes GIABA, COBAC/BEAC, MAS, HKMA, CBUAE, RBI et bases PEP, avec filtrage à l’onboarding, à chaque transaction, quotidiennement pour les profils actifs et lors des mises à jour de listes (p. 13–14).
- Le manuel prévoit HOLD sur correspondance similaire, blocage sur hit confirmé et résolution par conformité, avec une architecture indiquant `POST /compliance/kyc` (p. 13–14).
- Les sections de surveillance et STR/SAR prévoient des règles de blocage/alerte, une procédure de collecte de preuves, un dépôt auprès de l’ANIF et des autorités de corridor lorsque requis, ainsi qu’un archivage des dossiers STR (p. 15–18).
- La politique de conservation mentionne dix ans pour les dossiers KYC/KYB et les dossiers STR/SAR, avec des références au droit camerounais, COBAC et FATF ; la section protection des données mentionne également les documents KYC/KYB, résultats de screening, scores de risque et références STR (p. 19, 26 dans la table des matières).

## Point de comparaison avec l’architecture AFM allégée

Le manuel est plus exigeant que le parcours actuel e-mail/Google + contrôle opérateur. Le KYC 1 documenté utilise le téléphone, le nom complet et la date de naissance, mais le manuel impose aussi une vérification identitaire validée avant le premier flux et prévoit des contrôles AML indépendants du canal d’authentification. La suppression du stockage des documents peut être compatible avec une reliance documentée sur l’opérateur, mais le manuel doit être amendé pour préciser cette reliance, les données minimales reçues, la preuve d’identité disponible chez l’opérateur, l’auditabilité, les responsabilités et les cas d’escalade vers KYC 2/KYC 3.

Le numéro WhatsApp ou l’e-mail ne remplacent pas le filtrage sanctions/PEP, le scoring de risque, le contrôle du bénéficiaire effectif, la surveillance transactionnelle et les obligations STR/SAR. Le numéro Mobile Money doit rester lié à chaque transaction et à la preuve opérateur ; il peut être différent du numéro WhatsApp.
