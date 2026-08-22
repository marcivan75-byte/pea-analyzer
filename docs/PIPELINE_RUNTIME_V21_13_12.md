# V21.13.12 — consolidation runtime POSTMARKET

## Objectif

Réduire le temps facturable GitHub des runs quotidien et hebdomadaire sans modifier les données, les critères, les pondérations, les seuils, les formules, les règles PIT ou la restitution financière.

## Changement d'orchestration

Avant V21.13.12, les workflows principal quotidien et vendredi lançaient quatre processus Python successifs :

1. `tct_pit_ohlc_ledger_v24_4_2` ;
2. `tct_next_session_catalyst_run_v24_4_2` en phase POSTMARKET ;
3. `tct_v24_4_2_pit_lineage` ;
4. `tct_v24_4_2_pit_validator`.

V21.13.12 remplace uniquement ces quatre démarrages par :

`python -m v182.reporting.tct_postmarket_bundle_run`

Le bundle appelle les quatre modules existants dans le même ordre.

## Sémantique d'échec conservée

Les quatre anciennes étapes GitHub étaient chacune `continue-on-error: true`. Le nouvel orchestrateur reproduit ce comportement :

- chaque sous-étape est tentée même si une sous-étape précédente lève une exception ;
- les sorties déjà produites sont conservées ;
- les erreurs sont enregistrées dans l'audit transversal ;
- le bundle lève finalement une erreur s'il existe au moins une erreur de sous-étape, ce qui conserve la visibilité GitHub tout en permettant au workflow principal de continuer.

## Invariants financiers et PIT

V21.13.12 ne modifie pas :

- la sélection TCT/CT ;
- le plafond TCT Top 20 + CT Top 20, union maximale 40 ;
- les requêtes GDELT ou leur cadence ;
- les données sources ;
- les critères, scores, pondérations et seuils ;
- les règles d'entrée ou de sortie ;
- les règles de lineage PIT ;
- l'algorithme de fingerprint ;
- les gates de validation ;
- le holdout ;
- les ordres réels, toujours désactivés.

Le PREMARKET autonome reste inchangé et continue d'utiliser son workflow dédié et son profil de dépendances minimal.

## Gain recherché

Le changement supprime trois démarrages d'interpréteur Python par run principal. Le gain réel dépend des imports, de la vitesse du runner et du cache de dépendances ; il doit être mesuré sur les runs GitHub réels avant d'être considéré acquis.

Le principal objectif de facturation reste de franchir les seuils entiers :

- quotidien : passer sous 10 minutes si le run se situe encore légèrement au-dessus ;
- vendredi : passer sous 25 minutes si le run se situe encore légèrement au-dessus.

## Télémétrie

Le bundle produit :

`outputs/audit/POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json`

L'audit contient notamment :

- durée de chaque sous-étape ;
- durée totale ;
- ordre d'exécution ;
- erreurs éventuelles ;
- nombre de processus Python remplacés ;
- nombre de démarrages d'interpréteur évités ;
- garde-fous attestant l'absence de changement décisionnel, PIT ou de scoring.

## Étape suivante

Le prochain levier significatif concerne GDELT. Le fournisseur impose actuellement une cadence globale conservatrice d'un démarrage de requête par seconde. Toute réduction du nombre de requêtes par regroupement de sociétés doit rester SHADOW tant qu'une comparaison A/B n'a pas démontré une attribution article→ISIN fiable et une équivalence ou une amélioration mesurable par rapport aux requêtes individuelles.
