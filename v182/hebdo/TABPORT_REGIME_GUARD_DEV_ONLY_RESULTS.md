# TABPORT — garde-fous développement-only

Date : 2026-09-02
Run : `33597645889`
Commit exécuté : `b1519f0db419ec219b4b09b291a7a7669cf2e72b`
Artefact : `TABPORT-REGIME-GUARD-33597645889` / ID `9834182754`

## Gouvernance

Les seuils ont été appris exclusivement sur les signaux confirmés 2010-2022 puis gelés avant l'évaluation 2023-2026. Le holdout n'a servi ni à choisir les quantiles ni à modifier les combinaisons. Aucune imputation synthétique. Aucune promotion production automatique.

Seuils issus du développement :

- `vol_z <= 0.7951468731` (q60 développement) ;
- `prob_stop_9 <= 0.1486109597` (q50 développement) ;
- `atr_14_pct >= 0.0332683728` (q70 développement).

## Résultats holdout 2023-2026

| Modèle | Trades | Win % | Espérance % | PF | RR | Stops % | P&L clôturé € | Rendement NAV segment % | DD max % |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| BASELINE | 105 | 47.62 | 4.71 | 2.04 | 2.25 | 43.81 | 22,067.87 | 19.08 | -9.85 |
| VOL_Z <= q60 | 119 | 40.34 | 1.57 | 1.29 | 1.90 | 54.62 | 8,464.80 | 9.12 | -10.90 |
| PROB_STOP <= q50 | 104 | 50.00 | 3.94 | 1.82 | 1.82 | 46.15 | 18,281.35 | 17.03 | -5.00 |
| ATR >= q70 | 127 | 31.50 | 1.93 | 1.28 | 2.80 | 67.72 | 10,870.65 | 9.92 | -10.74 |
| VOL_Z <= q60 + PROB_STOP <= q50 | 101 | 49.50 | 4.61 | 1.98 | 2.02 | 45.54 | 20,820.80 | 23.29 | -6.13 |
| VOL_Z <= q60 + ATR >= q70 | 126 | 30.95 | 2.75 | 1.40 | 3.15 | 68.25 | 15,281.39 | 15.51 | -10.07 |

## Décision

Aucun garde-fou n'est promu dans le process actif.

- Le filtre volume seul est rejeté comme garde-fou : il dégrade nettement PF, espérance, rendement et stop-rate OOS.
- Le filtre ATR seul est rejeté : RR augmente mais au prix d'une forte chute de win rate, PF et rendement et d'une hausse massive des stops.
- Le filtre probabilité de stop améliore le drawdown mais dégrade rendement, PF et RR ; il reste insuffisant pour une activation.
- La combinaison `VOL_Z <= q60 + PROB_STOP <= q50` est la seule piste à conserver : rendement NAV OOS 23.29% contre 19.08% et DD -6.13% contre -9.85%. Toutefois PF, RR, espérance et stop-rate ne s'améliorent pas. Son profil annuel n'est pas uniformément supérieur et 2026 partiel devient négatif. Elle reste donc `RESEARCH_ONLY`.

## Conclusion méthodologique

Les mauvaises années ne sont pas expliquées suffisamment par un simple seuil individuel au niveau du titre. La prochaine recherche doit se déplacer vers le **régime de marché ex ante** : breadth, tendance agrégée et proportion de titres au-dessus de leur SMA200/retour médian, calculées uniquement avec les OHLCV disponibles à la date de décision. Les seuils doivent encore être ajustés uniquement sur 2010-2022 puis gelés sur 2023-2026.
