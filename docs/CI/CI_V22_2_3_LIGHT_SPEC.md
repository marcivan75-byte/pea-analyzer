# CI LIGHT V22.2.3 — contrat fonctionnel

## Séparation avec le CI complet

Le CI complet reste la référence d'analyse d'investissement. Il conserve l'étude complète des données, tous les critères pondérés, les scores, la confiance, les contrôles de risque, les règles PIT et les règles de gouvernance existantes.

CI LIGHT est une liste parallèle, minimaliste et purement informative. Elle ne modifie jamais le score, les pondérations, les seuils, la décision ou l'univers du CI complet et ne peut créer aucun ordre réel.

## Univers traité

CI LIGHT travaille sur le pool borné des instruments déjà amenés jusqu'au stade CI/source-enrichment. Il ne lance pas de scraping Boursorama/Investing sur tout l'univers. Il n'hérite toutefois pas des seuils finaux du CI global `score >= 77` ou `confiance >= 66` comme critères d'admission LIGHT.

## Critères d'admission CI LIGHT

Un instrument, Action ou ETF, n'est inclus que si toutes les conditions suivantes sont vraies simultanément :

1. Recommandation Boursorama positive correspondant à **ACHETER** ou **RENFORCER**.
   - Le parseur gouverné expose actuellement des valeurs canoniques : `STRONG_BUY` est présenté en CI LIGHT comme `ACHETER`, et `BUY` comme `RENFORCER`.
   - Aucune autre recommandation n'est admise.
2. Nombre d'analystes Boursorama **strictement supérieur à 10**.
3. Potentiel Boursorama **strictement supérieur à 20 %**.
4. Signal Investing **Daily** dans `{BUY, STRONG_BUY}`.
5. Signal Investing **Weekly** dans `{BUY, STRONG_BUY}`.
6. Signal Investing **Monthly** dans `{BUY, STRONG_BUY}`.

Les trois signaux Investing sont obligatoires simultanément. Un seul signal manquant, NEUTRAL, SELL ou STRONG_SELL exclut l'instrument de CI LIGHT.

## ETF

Les ETF suivent exactement les mêmes règles Boursorama et Investing que les Actions pour CI LIGHT. Morningstar n'est pas utilisé comme substitut à un consensus analystes Boursorama. Un ETF pour lequel le nombre d'analystes, la recommandation ou le potentiel Boursorama est absent est exclu en mode fail-closed de CI LIGHT.

## Restitution

Pour chaque instrument retenu, CI LIGHT restitue au minimum :

- nom ;
- ISIN ;
- classe d'actif ;
- horizon CI TCT / CT / MT ;
- recommandation Boursorama ;
- nombre d'analystes Boursorama ;
- potentiel Boursorama ;
- signal Investing Daily ;
- signal Investing Weekly ;
- signal Investing Monthly ;
- URL Boursorama ;
- URL Investing / fiche technique ;
- score et confiance du CI complet uniquement comme contexte, sans effet sur l'admission LIGHT.

Les sorties sont :

- `outputs/committee_master/CI_LIGHT_V22_2_3.csv`
- `outputs/committee_master/CI_LIGHT_REJECTED_V22_2_3.csv`
- `outputs/committee_master/CI_LIGHT_V22_2_3.xlsx`
- `outputs/mobile/ANDROID_CI_LIGHT_V22_2_3.md`
- `outputs/audit/CI_LIGHT_V22_2_3.json`

## Gouvernance

- `full_ci_changed = false`
- `full_ci_weighted_analysis_preserved = true`
- `source_can_create_candidate = false`
- `morningstar_used_as_consensus_substitute = false`
- `real_orders_enabled = false`
- WAVE09 reste désactivée.
