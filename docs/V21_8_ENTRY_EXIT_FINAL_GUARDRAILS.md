# V21.8 — ENTRY / EXIT FINAL GUARDRAILS

## Statut de référence

V21.8 est la baseline officielle de **décision-support entrée / conservation / protection / sortie** du process. Elle est exécutée après la construction des décisions Comité et publie une vue séparée :

`outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv`

Elle ne remplace pas la sélection, ne modifie pas `COMMITTEE_DECISIONS.csv` et n'émet aucun ordre réel.

## Entrée

La sélection et le timing d'entrée sont séparés.

- une décision non sélectionnée reste `TEMPORARY_REJECT` ;
- une sélection sans preuve de timing suffisante reste `WAIT` ;
- un score extrême n'est pas un bonus d'entrée automatique : il impose une revue de sur-extension ;
- une décélération de momentum ou une structure sous MM200 impose `WAIT` ;
- T1/T2 sont strictement réservés aux Actions TCT ;
- en TCT, T1 seul ne peut jamais ouvrir une position V21.8 : T2 exact est requis.

Aucun seuil numérique de timing supplémentaire n'est promu par cette version.

## Gestion d'une position

Les états sont `HOLD`, `PROTECT`, `EXIT`, `EMERGENCY_EXIT`.

- un niveau de gain ne déclenche jamais une vente à lui seul ;
- un giveback de gain est uniquement contextuel ;
- la première détérioration multifactorielle conduit à `PROTECT` ;
- `EXIT` exige une détérioration structurelle + momentum, renforcée par un second facteur structurel ou un régime marché dégradé, puis une confirmation temporelle ;
- `EMERGENCY_EXIT` exige un drapeau de risque d'urgence explicite.

La confirmation temporelle n'est pas simulée dans la même exécution. L'état V21.8 de chaque clé `(asset_class, horizon, isin)` est conservé dans :

`state/provenance/V21_8_ENTRY_EXIT_STATE.csv`

Ce fichier utilise le cache de provenance déjà restauré et sauvegardé par le workflow quotidien. Une détérioration multifactorielle observée une première fois produit donc `PROTECT`; si elle persiste lors d'une exécution ultérieure, le contexte peut passer à `EXIT`. L'état reste du **decision-support** et ne déclenche aucune transaction.

## Stops, sizing et pertes

- aucun take-profit fixe n'est actif ;
- l'ancien objectif +4 % n'est pas une règle opérationnelle ;
- l'ancien stop ETF -18 % n'est pas une règle opérationnelle V21.8 ;
- les anciennes hypothèses de stops TCT/CT/MT/LT du moteur de performance virtuel ne sont pas opérationnelles sous V21.8 ;
- aucun nouveau hard stop n'est promu ;
- la cible de risque de perte de 7 % reste un **plafond de recherche**, pas un stop aveugle ;
- gaps et slippage peuvent dépasser tout niveau de stop théorique.

Le moteur historique `committee_performance_v21_4` calculait le sizing et certaines sorties à partir de stops fixes devenus incompatibles avec V21.8. Son exécution est donc `SKIPPED_GOVERNANCE` dans le runner unifié. Son historique peut être conservé pour audit, mais il ne doit plus créer ni clôturer de position virtuelle tant qu'une politique de sizing indépendante de ces stops n'a pas été validée séparément.

## Gouvernance PIT/OOS

Les diagnostics MAE/MFE, délais J+1/J+3/J+5 et études de breach servent à comprendre les trajectoires. Ils ne permettent pas de retuner automatiquement les seuils, poids ou sorties. Le holdout final reste fermé.

Les résultats antérieurs n'ont pas démontré une règle fixe de stop ou de take-profit suffisamment robuste pour remplacer V21.8. Toute nouvelle règle doit faire l'objet d'un protocole PIT/OOS séparé et pré-enregistré.

## Invariants

- sélection et poids inchangés ;
- ETF : référentiel 268 critères conservé ; attribution historique 90,91 % limitée au sous-bloc dynamique PIT de 38 critères ;
- T1/T2 = ACTION TCT uniquement ;
- influence V21.8 sur score Comité = 0 ;
- influence V21.8 sur décision de sélection = 0 ;
- influence V21.8 sur sizing = 0 ;
- moteur historique de performance virtuelle = `SKIPPED_GOVERNANCE` sous V21.8 ;
- holdout final fermé ;
- aucun ordre réel.
