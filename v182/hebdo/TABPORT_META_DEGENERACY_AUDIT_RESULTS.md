# TABPORT META — audit de dégénérescence structurelle

Run : `33650305966` — **SUCCESS**  
Commit : `1987b02ed135d07cdfff9f1d0ab02725fd0ef233`  
Artefact : `TABPORT-META-DEGENERACY-33650305966`, ID `9854582739`.

## Conclusion

La chaîne historique publiée sous la forme `B_V2 -> META -> J1 -> TABPORT` n'utilise pas, dans le backtest longitudinal actuel, un méta-modèle réellement entraîné.

Sur les `12 218` signaux META éligibles :
- `meta_model_status = UNTRAINED` : `12 218 / 12 218` ;
- `mae_model_status = HEURISTIC_UNCALIBRATED` : `12 218 / 12 218` ;
- `ev_model_status = PARAMETRIC_UNCALIBRATED` : `12 218 / 12 218` ;
- `selection_confidence = DEGRADED_UNVALIDATED_COMPONENTS` : `12 218 / 12 218`.

### Développement 2010–2022

- signaux : `8 745` ;
- `prob_meta = 0,5` : **100 %** ;
- groupes de décision dont toutes les `prob_meta` valent 0,5 : **100 %** ;
- EV exactement `0,044` : **99,6569 %** ;
- écart-type de `EV_net` : `0,000400` ;
- médiane de la part du plus gros groupe d'EV ex æquo par date : **100 %** ;
- groupes de décision avec au moins 50 % d'ex æquo EV : `646`.

### Holdout 2023–2026

- signaux : `3 473` ;
- `prob_meta = 0,5` : **100 %** ;
- EV exactement `0,044` : **99,9424 %** ;
- seulement `3` valeurs distinctes d'EV sur tout le segment.

## Origine mécanique

`MetaLabeler.predict_proba()` renvoie `prob_meta = 0,5` tant que le modèle n'a pas été entraîné. Or `build_weekly_meta_signals()` instancie un nouveau `HebdoATMeta()` à chaque date sans appel préalable à `MetaLabeler.train()`.

Le `MAEPredictor` est explicitement `HEURISTIC_UNCALIBRATED`. Par conséquent `ExpectedValueRanker.compute_ev()` n'utilise pas `prob_stop_9` comme probabilité calibrée de perte et remplace cette composante par `p_loss = 0,30`.

Sans malus additionnel :

`0,5 × 0,14 + 0,3 × (-0,09) + 0,2 × 0,02 - 0,003 = 0,044`.

La dispersion résiduelle provient seulement de quelques malus heuristiques. Elle ne constitue pas un méta-classement calibré.

## Conséquence sur les études précédentes

Les backtests longitudinal, stop, sizing, horizon, filtres et sorties restent valides pour les règles effectivement rejouées. En revanche, ils évaluent une baseline dont la brique dite « META » est **dégradée et quasi non discriminante**. Les conclusions sur stop, horizon ou sorties ne doivent pas être réinterprétées comme validation d'un méta-modèle entraîné.

## Décision

- aucune modification de production immédiate ;
- baseline actuelle conservée uniquement comme référence technique ;
- priorité de recherche déplacée vers une chaîne META réellement entraînée en **walk-forward PIT** ;
- entraînement exclusivement sur résultats déjà matures à la date de décision ;
- ajout explicite de `rsi_14_hebdo`, actuellement requis par `MetaLabeler` mais absent du pipeline historique ;
- comparaison ensuite contre la baseline sur 2010–2022, puis validation unique 2023–2026 sans retuning.
