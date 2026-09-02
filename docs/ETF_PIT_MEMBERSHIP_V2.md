# ETF PIT Membership V2

Date: 2026-09-03

## Objet

Réduire le survivorship bias de la base ETF de backtest sans inventer des dates historiques d'éligibilité PEA.

## Principe

La première séance observée dans l'historique OHLCV permet d'établir qu'un instrument existait au plus tard à cette date. Elle ne prouve ni son éligibilité PEA à cette date, ni son appartenance à l'univers investissable utilisé par le modèle.

V2 ajoute donc trois couches distinctes:

1. `trading_existence_start/end`: existence/cotation documentée;
2. `membership_start/end`: présence dans l'univers de recherche/éligible historique;
3. `pea_eligibility_start/end`: éligibilité PEA documentée.

La sélection PIT stricte à une date donnée exige que les trois périodes soient compatibles avec cette date. Une valeur inconnue est toujours traitée comme **non éligible pour une validation de promotion**, jamais comme vraie par défaut.

## Données dérivables automatiquement

À partir de l'historique audité V1:

- `trading_existence_start` = première séance réellement observée;
- `last_observed_trading_date` = dernière séance observée, à titre de preuve de couverture;
- aucune date `pea_eligibility_start` n'est déduite des prix;
- aucune date `membership_start` n'est déduite de la seule présence dans le référentiel courant.

## Données restant à documenter par archives

Pour rendre un instrument promotion-eligible il faut encore documenter:

- première date certaine d'éligibilité PEA;
- éventuelle date de fin d'éligibilité;
- date d'entrée dans l'univers historique retenu;
- date de sortie/fusion/liquidation si applicable;
- source et preuve pour ces dates;
- alias de ticker historiques si nécessaire.

Sources préférées: documents émetteur/KID, Euronext, archives de courtiers PEA, archives de listes ETF PEA, documents AMF/émetteurs. Une valeur actuelle ne doit pas être rétro-projetée sans preuve.

## Gouvernance

`row_is_pit_eligible()` est fail-closed: une date manquante empêche l'instrument d'être inclus dans un backtest qualifié PIT.

La table actuelle issue des 102 ETF contemporains reste donc `promotion_eligible=false` tant que `membership_start` et `pea_eligibility_start` ne sont pas documentés. Cette règle est volontaire et protège les résultats historiques contre un faux univers rétrospectif.
