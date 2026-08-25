# Protocole AFM Mobile Money par corridor

## Principes communs

AFM crée d’abord une intention interne `pending` avec un identifiant idempotent, le client PSP, le corridor, les devises et les bénéficiaires. La cotation est demandée côté serveur et enregistrée avec sa référence et son expiration. Une cotation XOF↔XAF ne doit pas être présentée comme directe : Kora documente XOF↔USD et XAF↔USD, donc AFM utilise deux jambes `XOF→USD→XAF` ou `XAF→USD→XOF` lorsque le service Currency Conversion est activé.

Le pay-in Mobile Money est déclenché dans la devise locale de collecte, avec une référence unique, l’email du client, le numéro du portefeuille et le modèle d’autorisation retourné par Kora (`OTP`, `STK_PROMPT` ou `REDIRECT`). AFM ne collecte jamais le PIN du portefeuille. Après un webhook `charge.success` authentifié et idempotent, AFM crédite uniquement le sous-ledger virtuel du client concerné.

Le payout est préparé uniquement lorsque les fonds disponibles couvrent le montant net et les frais. Le réseau Mobile Money est résolu par l’endpoint Kora List MMO du pays ; le slug ne doit pas être deviné. Pour le Nigeria, où la documentation Mobile Money Kora consultée ne liste pas de réseau de collecte ou de payout Mobile Money, le repli opérationnel est le compte bancaire avec code bancaire validé.

L’interface affiche un seul montant `Platform fees`, égal à la somme des frais pay-in, payout, conversion, commission Kaybic lorsqu’elle est due et commission AFM. La commission AFM reste fixée à **2 %**. Le détail reste disponible dans le dashboard interne. Le bénéficiaire reçoit la différence nette après déduction de l’ensemble des frais.

## Matrice des corridors prioritaires

| Corridor | Pay-in local | Opérateurs de collecte documentés | Conversion | Payout prioritaire | Opérateurs de payout à résoudre | Repli |
|---|---|---|---|---|---|---|
| Côte d’Ivoire → Ghana | XOF | MTN, Orange, Moov, Wave | XOF→USD→GHS | Mobile Money GHS | MTN Momo, AirtelTigo, Vodafone | Compte bancaire GHS si le réseau est indisponible |
| Côte d’Ivoire → Nigeria | XOF | MTN, Orange, Moov, Wave | XOF→USD→NGN | Compte bancaire NGN | Code bancaire Nigeria, résolu par List Banks | Aucun payout Mobile Money tant que Kora ne confirme pas un réseau NGN |
| Bénin → Nigeria | XOF | Non confirmé par la documentation Kora Mobile Money consultée | À confirmer ; ne pas simuler une paire non activée | Compte bancaire NGN via un PSP activé | Code bancaire Nigeria | Orienter vers un PSP couvrant le Bénin ou traiter le Bénin comme corridor en attente |
| Cameroun → Nigeria | XAF | MTN, Orange | XAF→USD→NGN | Compte bancaire NGN | Code bancaire Nigeria, résolu par List Banks | Aucun payout Mobile Money NGN sans activation Kora confirmée |

Les opérateurs indiqués sont des réseaux de marché et non des valeurs de configuration définitives. Avant une transaction, AFM doit interroger les opérateurs activés sur le compte Kora et vérifier les limites minimales, maximales et la disponibilité. Les identifiants actuellement utilisés pour les tests sandbox XAF (`mtn-cm`) et XOF (`mtn-ci`) restent strictement réservés au mode Test.

## Séquence par corridor

### Côte d’Ivoire → Ghana

AFM collecte le XOF sur MTN, Orange, Moov ou Wave. Après succès du pay-in, le ledger virtuel est crédité en XOF, puis AFM obtient les deux cotations nécessaires vers GHS. Le payout est envoyé vers MTN Momo, AirtelTigo ou Vodafone après résolution du slug et contrôle de la limite. Le montant envoyé est le montant net calculé après tous les frais.

### Côte d’Ivoire → Nigeria

AFM collecte le XOF sur le réseau ivoirien choisi. Après conversion XOF→USD→NGN, le bénéficiaire est payé sur un compte bancaire nigérian avec un code obtenu dynamiquement auprès de List Banks. La sélection d’un numéro Mobile Money NGN doit être refusée tant que Kora ne fournit pas explicitement un réseau NGN activé pour le compte AFM.

### Bénin → Nigeria

Ce corridor ne doit pas être activé comme Mobile Money Kora par défaut. La documentation consultée ne confirme pas le pay-in Mobile Money XOF au Bénin. AFM doit afficher le corridor comme `en attente d’activation`, ou le faire passer par un PSP disposant d’une couverture Bénin vérifiée. Une fois les fonds collectés par un rail autorisé, le payout cible reste bancaire NGN avec résolution du code bancaire.

### Cameroun → Nigeria

AFM collecte le XAF sur MTN ou Orange Cameroun. Après `charge.success`, le ledger XAF est crédité et la conversion XAF→USD→NGN est cotée. Le payout prioritaire est bancaire NGN ; le payout Mobile Money NGN ne doit pas être proposé en l’absence de réseau Kora officiellement activé.

## Ledger et réconciliation

Le ledger AFM sépare au minimum les mouvements suivants : fonds collectés, payable net du client, frais de pay-in, frais de payout, frais de change, commission Kaybic et commission AFM. La présentation publique consolide ces postes sous `Platform fees`, mais le dashboard interne conserve chaque composante et sa devise.

Un webhook ne crée jamais de mouvement financier initial. Il rapproche une transaction AFM existante par référence, vérifie la signature HMAC et déduplique l’événement. Un `charge.success` crédite le sous-ledger virtuel uniquement une fois. Un `transfer.success` ou payout `settled` marque la transaction comme complétée et crée un débit payout unique. Un événement `failed`, `cancelled` ou `reversed` conserve la valeur en exception et ne doit pas produire de débit final.

Les contrôles obligatoires avant `settled` sont la correspondance de référence, la devise, le montant, le client, le corridor, le statut Kora, l’unicité de l’événement et la disponibilité du solde virtuel. Toute divergence bloque la réconciliation et déclenche une alerte interne.

## Références officielles

[1]: https://developers.korapay.com/docs/mobile-money "Kora — Mobile Money Payments"
[2]: https://developers.korapay.com/docs/payout-via-api "Kora — Payout API"
[3]: https://developers.korapay.com/docs/convert-currency "Kora — Currency Conversion"
[4]: https://developers.korapay.com/docs/testing-your-integration "Kora — Testing your integration"
