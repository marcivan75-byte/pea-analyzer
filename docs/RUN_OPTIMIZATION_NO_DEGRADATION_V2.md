# Run optimisation V2 — invariants de non-dégradation

Les optimisations de coût et de durée sont autorisées uniquement si elles ne réduisent ni la fraîcheur requise, ni la couverture, ni les garde-fous, ni la profondeur des validations finales.

## Invariants
- 1 829 Actions et 102 ETF restent dans le contrat canonique.
- 633 critères Actions, 268 critères ETF, 102 critères Gold / 11 blocs restent inchangés.
- T1/T2 restent exclusivement ACTION TCT et leur gouvernance reste inchangée.
- Les règles PIT, provenance, absence d'imputation neutre et quality gates restent inchangées.
- Le full pytest, Ruff, compileall, audit statique et contrôles de référentiels restent obligatoires sur le commit final avant merge.
- Aucun backtest automatique dans le run quotidien.
- Aucun raccourcissement de l'historique requis par les indicateurs.
- Aucun abandon d'une source pour économiser du temps.

## Optimisations autorisées
- cache incrémental OHLCV sans perte d'historique ;
- partage du cache entre modules ;
- reprise après interruption via checkpoint persistant ;
- cache de dépendances Python ;
- suppression des runs réellement redondants ;
- annulation d'un CI obsolète au profit d'un commit plus récent ;
- skip CI uniquement sur commits intermédiaires, jamais sur le commit final candidat au merge ;
- réduction des I/O du reporting à contenu identique.
