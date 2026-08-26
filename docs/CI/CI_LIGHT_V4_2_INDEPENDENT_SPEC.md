# CI LIGHT V4.2 — processus indépendant

## Architecture

CI LIGHT utilise son propre univers borné `inputs/CI_LIGHT_UNIVERSE_V4.csv`. Il ne lit aucune sortie CI, ne réutilise aucune présélection CI et n'admet dans son entrée aucun score, confiance, rang ou décision CI.

Il collecte séparément les pages publiques Boursorama et TradingView dans des caches dédiés à CI LIGHT. Investing est désactivé. CI LIGHT peut créer un candidat CI LIGHT, mais ne peut créer ni modifier un candidat CI et ne peut produire aucun ordre réel.

## Actions

Une Action est admise lorsque toutes les conditions suivantes sont satisfaites :

- recommandation Boursorama `BUY` ou `STRONG_BUY` ;
- plus de 10 analystes ;
- potentiel strictement supérieur à 20 % ;
- TradingView `BUY` ou `STRONG_BUY` en journalier, hebdomadaire et mensuel.

## ETF

Un ETF est admis lorsque sa fiche Boursorama exacte confirme son éligibilité PEA et que TradingView fournit `BUY` ou `STRONG_BUY` en journalier, hebdomadaire et mensuel.

Si TradingView ne fournit pas le signal hebdomadaire (moyen terme) ou mensuel (long terme), le seul horizon manquant peut être validé par une note Morningstar de 4 ou 5 étoiles. Le fallback ne s'applique jamais à une Action et ne remplace jamais un signal TradingView présent `NEUTRAL`, `SELL` ou `STRONG_SELL`.

Chaque fallback conserve la note, les horizons remplacés, la source et la date. Une note absente ou inférieure à 4 produit un rejet explicite.

## Sorties et preuves

- `outputs/committee_master/CI_LIGHT_V4.csv` ;
- `outputs/committee_master/CI_LIGHT_REJECTED_V4.csv` ;
- `outputs/committee_master/CI_LIGHT_V4.xlsx` ;
- `outputs/mobile/ANDROID_CI_LIGHT_V4.md` ;
- `outputs/audit/CI_LIGHT_V4.json` ;
- `outputs/audit/CI_LIGHT_V4_INDEPENDENT_SOURCE_CONTEXT.json` ;
- `outputs/source_context/CI_LIGHT_V4_INDEPENDENT_SOURCE_OBSERVATIONS.csv` ;
- `outputs/source_context/CI_LIGHT_V4_INDEPENDENT_SOURCE_FAILURES.csv`.
