# Impact du modèle d’onboarding allégé sur la politique AML MEL / FrontierPay

**Document examiné :** AML/CFT & Compliance Policy Manual MEL Investment Banking — FrontierPay, version 2.0, juin 2026.

## Conclusion exécutive

Le manuel AML prévoit bien un modèle gradué. Son niveau KYC 1 pour les particuliers à faible valeur mentionne le téléphone, le nom complet et la date de naissance, avec des limites de 50 000 XAF par jour et 500 000 XAF par mois pour l’Afrique. Le modèle AFM sans documents peut donc être rapproché d’un **KYC 1 allégé**, mais uniquement pour un périmètre de risque et de corridors défini.

Le manuel ne permet pas de conclure qu’un e-mail/Google et une preuve opérateur suffisent pour tous les clients, toutes les transactions ou tous les corridors. Il exige une vérification identitaire validée avant tout flux, un filtrage sanctions/PEP, un scoring de risque, une surveillance transactionnelle et une escalade vers KYC 2 ou KYC 3 selon le profil, le montant, le corridor et le comportement.

## Compatibilité et écarts

| Domaine | Ce que prévoit le manuel | Compatibilité avec AFM allégé | Adaptation nécessaire |
|---|---|---|---|
| Client particulier faible valeur | KYC 1 : téléphone, nom complet, date de naissance ; plafond Afrique | Compatible en principe | Documenter que l’opérateur est la source de confiance, conserver la preuve de correspondance et appliquer les plafonds. |
| E-mail/Google | Non présenté comme preuve KYC autonome | Complément d’authentification, pas remplacement automatique | Qualifier l’e-mail comme identifiant de compte ; ne pas le traiter comme preuve suffisante d’identité légale. |
| Numéro Mobile Money | Le manuel exige un KYC validé avant le flux | Compatible si l’opérateur fournit une preuve fiable | Contrôler le numéro retourné par l’opérateur, la référence, le statut signé, la date et le corridor de la transaction. |
| Documents d’identité | KYC 2 et KYC 3 en exigent | Non compatible pour ces niveaux si AFM ne conserve aucune pièce | Mettre en place une reliance contractuelle sur l’opérateur ou un prestataire vérificateur ; prévoir une escalade lorsque la preuve opérateur est insuffisante. |
| Fintechs, PSPs, corporate | KYB complet, UBO >25 %, licences, politique AML, Compliance Officer | Non compatible avec le parcours particulier allégé | Maintenir un onboarding B2B séparé et documentaire. |
| Europe et Asie | KYC 2 minimum pour Afrique–Europe ; KYC 3 pour plusieurs corridors Afrique–Asie | Non compatible par défaut | Bloquer ces corridors pour KYC 1 ou exiger l’escalade et l’EDD prévues au manuel. |
| PEP et juridictions à risque | EDD, approbation direction, source des fonds, surveillance renforcée | Non compatible avec le parcours minimal seul | Screening obligatoire et revue humaine avant activation ou transaction. |
| Sanctions | Filtrage à l’onboarding, à chaque transaction, quotidiennement et lors des mises à jour | Non couvert par l’e-mail/OTP | Implémenter ou connecter le screening et conserver le résultat, la liste, la date et la décision. |
| Surveillance | Seuils, structuring, vélocité, layering, wallets inconnus, multi-zones | Ledger/réconciliation seuls insuffisants | Relier les règles AML au moteur de transaction, au HOLD, à l’escalade et à l’audit immuable. |
| STR/SAR | Détection, analyse, dépôt ANIF et autorités de corridor ; interdiction de tipping-off | Non couvert par l’onboarding | Procédure et responsabilités opérationnelles, avec conservation des preuves. |
| Conservation | Dix ans pour transactions, KYC/KYB et STR selon le manuel | Compatible avec l’absence de documents AFM si les preuves nécessaires restent accessibles | Définir la source de conservation : opérateur, partenaire ou AFM, et garantir l’accès/auditabilité. |

## Impacts principaux

### 1. L’allègement porte sur la conservation, pas sur la vigilance

Le fait qu’AFM ne conserve pas de pièce d’identité réduit l’exposition aux fuites, la surface de données et les obligations de sécurité documentaire. Il ne supprime toutefois pas l’obligation de savoir qui est le client, d’évaluer le risque, de filtrer les parties et de pouvoir démontrer la diligence effectuée.

La politique doit donc être reformulée pour distinguer le **propriétaire de la donnée documentaire** — l’opérateur ou le prestataire qui a effectué la vérification — de la **responsabilité AML de MEL/FrontierPay**. La reliance sur un tiers doit préciser les informations accessibles à AFM, les responsabilités respectives, le droit d’audit, la disponibilité des preuves et la conduite à tenir en cas d’anomalie.

### 2. Le KYC 1 doit être borné par des limites et corridors

Le manuel prévoit déjà un niveau Basic. L’architecture AFM peut s’y intégrer si elle applique réellement les limites du KYC 1 et ne permet pas à un client minimal d’accéder aux corridors ou produits nécessitant KYC 2/KYC 3. Le montant de 100 000 XAF envisagé pour certains transferts dépasse le seuil de 50 000 XAF/jour du tableau KYC 1 ; ce point doit être résolu par une règle de plafond, une escalade ou une révision formelle de la politique.

Le manuel prévoit aussi une alerte sur une transaction individuelle supérieure à 1 000 000 XAF, une alerte d’accumulation supérieure à 2 000 000 XAF sur 24 heures, et une déclaration/évaluation renforcée au-delà de 5 000 000 XAF ou USD 10 000 équivalent. Ces seuils doivent être codés et testés, sans être présentés comme des seuils universels applicables à chaque juridiction.

### 3. Le numéro WhatsApp et le numéro Mobile Money restent deux objets distincts

Le manuel ne justifie pas de transformer l’OTP WhatsApp en preuve de propriété du portefeuille Mobile Money. L’OTP WhatsApp confirme le contrôle d’un canal de communication. La preuve Mobile Money doit être rattachée à la transaction et provenir d’un retour opérateur fiable. Le modèle AFM actuel, qui sépare `whatsapp_phone` et `mobile_money_phone`, est cohérent avec cette distinction.

### 4. Le modèle particulier ne peut pas être étendu aux PSP et entreprises

La politique prévoit explicitement un KYB pour fintechs, PSPs, banques correspondantes, MTOs et clients institutionnels, incluant immatriculation, bénéficiaires effectifs, licences et responsable conformité. Le parcours e-mail/Google + opérateur peut ouvrir un compte particulier à faible risque, mais ne doit pas activer les fonctionnalités B2B ou les corridors professionnels.

## Contrôles minimaux à relier au parcours AFM

Avant le premier flux, AFM devrait enregistrer un niveau de risque, un périmètre de corridors autorisés, un plafond journalier et mensuel, le résultat du screening applicable, la source de la preuve opérateur et le consentement. À chaque opération, le système devrait contrôler le payeur, le bénéficiaire, le corridor, la devise, le montant cumulé, la vélocité, le statut opérateur et la correspondance exacte du numéro Mobile Money.

Une incohérence, un hit sanctions/PEP, un dépassement de plafond, une vélocité inhabituelle ou un retour opérateur incomplet doit provoquer un HOLD et une revue conformité. L’interface utilisateur ne doit pas révéler l’existence ou le contenu d’une éventuelle déclaration STR/SAR.

## Conclusion opérationnelle

L’architecture allégée est potentiellement compatible avec le **KYC 1 particulier à faible valeur**, à condition d’être explicitement encadrée dans la politique AML et techniquement limitée. Elle n’est pas suffisante seule pour KYC 2, KYC 3, les PSP/fintechs/corporates, les corridors Afrique–Europe ou Afrique–Asie soumis à des exigences renforcées, les PEP, les juridictions à risque ou les opérations dépassant les seuils internes.

La modification prioritaire n’est donc pas de réintroduire des documents dans AFM, mais de formaliser une **politique de reliance opérateur + matrice d’escalade + screening + surveillance transactionnelle**. Le dossier doit aussi préciser que l’absence de documents dans la base AFM ne signifie pas absence de vérification : la preuve peut être détenue par l’opérateur ou le prestataire responsable, sous réserve d’un accès réglementaire et contractuel approprié.
