# TCT V24.4.0 — Renforcement de la lignée PIT

Date : 19/08/2026

## Objet

Renforcer la preuve PIT de V24.4 avant accumulation de l'échantillon forward réel, sans modifier les scores, poids, seuils de trading ou la production.

## Défaut corrigé

L'étiquetage initial pouvait utiliser la dernière clôture disponible dans le seed quotidien. Si un run journalier était manqué, cette clôture pouvait être postérieure à la première séance suivant le PREOPEN et transformer involontairement un résultat J+1 en résultat J+2/J+3.

Cette méthode est remplacée par une source d'outcome dédiée.

## Ledger compact de clôtures

Le workflow quotidien extrait uniquement du cache OHLCV daily déjà présent les dernières clôtures des tickers :

- présents dans le seed TCT V24.3.1 du jour ; ou
- encore présents dans le ledger V24.4 et donc susceptibles de nécessiter un outcome.

Fichier :

`state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv`

Aucun téléchargement réseau supplémentaire n'est réalisé par ce module.

Les 10 dernières barres daily disponibles sont relues afin qu'un run manqué ne fasse pas perdre la vraie première séance suivante. Les dates sont celles des barres OHLCV réellement observées : les week-ends et jours sans cotation ne sont donc pas inventés.

## Règle d'outcome

Pour chaque snapshot PREOPEN :

1. prendre `as_of_date`, date de la clôture connue avant le snapshot ;
2. chercher dans le ledger de clôtures la première barre du même ISIN dont la date est strictement supérieure ;
3. cette barre devient l'unique outcome J+1 de validation ;
4. les clôtures ultérieures ne peuvent pas remplacer cet outcome.

Les anciens labels éventuellement présents sont reconstruits à partir de cette règle avant passage au validateur.

## Couverture minimale par snapshot

Un snapshot PREOPEN n'alimente les statistiques du validateur que si au moins 80 % de ses candidats disposent d'un vrai outcome J+1.

En dessous de 80 % :

- les outcomes bruts peuvent être conservés pour diagnostic ;
- `realized_abs_return_pct` et `realized_close_to_close_return_pct` restent vides pour le validateur ;
- le snapshot ne contribue pas aux métriques ni aux seuils de maturité.

Cette règle évite de mesurer le Recall Top 10 ou le lift sur un sous-échantillon sélectionné par la seule disponibilité des prix.

## Empreinte immuable

Chaque ligne du ledger reçoit `snapshot_payload_sha256`, calculé sur le contenu prédictif en excluant uniquement les champs d'outcome et de lignée PIT.

Lors des runs suivants :

- l'empreinte est recalculée ;
- toute modification historique du contenu prédictif produit un mismatch ;
- le workflow PIT échoue en mode fail-closed si un mismatch est détecté.

## Replay historique

Le replay historique V24.4 n'est pas autorisé tant que les snapshots historiques PIT correspondants du filtre TCT/T1/T2 et de leurs données d'entrée ne sont pas disponibles.

La disponibilité des news GDELT historiques et des cours daily ne suffit pas à elle seule à reconstruire causalement le classement des candidats qui aurait réellement existé à chaque date.

Le forward-PIT reste donc la source de vérité.

## Gouvernance

- influence production : 0 ;
- holdout : fermé ;
- retuning : interdit ;
- promotion automatique : interdite ;
- CT : inchangé ;
- aucun nouveau flux intraday ou quasi temps réel.
