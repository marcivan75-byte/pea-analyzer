# Daily runtime V21.15.7

## Statut

Candidate finale du Daily TCT/CT/ETF-CT. La clôture définitive reste conditionnée à un run représentatif réussi avec audit des timings et des quality gates.

## Périmètre Daily

- Action TCT : baseline V24.1.8 sur univers canonique, exact T1/T2 limité au Top-N autorisé.
- Action CT : moteur décisionnel historique inchangé ; shadow V22 borné à la présélection quotidienne.
- ETF CT : moteur V20.7.1 inchangé.
- Préopen/postmarket : titres présélectionnés uniquement.
- Restitution CI : générée après les décisions, sans collecte ni recalcul des moteurs.

## W09

W09 est WEEKLY_ONLY.

Le Daily effectue zéro appel FRED/GDELT W09. Priorité de réutilisation :

1. fast-state Daily validé ;
2. snapshot master Hebdo validé ;
3. uniquement pour le bootstrap, seed Action W09 validé issu du run GitHub `32626511307` du 23/08/2026.

Le seed reproduit les valeurs W09 Action du run source et conserve les sémantiques de provenance : FRED, GDELT ou calcul interne selon le champ. Aucun W09 ETF n'est fabriqué : le périmètre ETF Daily est CT et ses critères actifs n'utilisent pas W09.

Après un bootstrap complet réussi, les masters enrichis Actions/ETF sont promus atomiquement dans `state/provenance/daily_fast_master_v1/` et réutilisés par les Daily suivants.

Un Weekly Heavy n'est donc pas requis uniquement pour initialiser W09. Le prochain Weekly réussi remplace naturellement le bootstrap par le snapshot hebdomadaire autoritaire.

## Fast-state

L'identité du cache ne dépend plus du SHA GitHub complet. Elle repose sur :

- contrat statique des inputs/configs ;
- intégrité SHA256 des masters ;
- contrat fonctionnel du code de collecte ;
- contrat des caches fournisseurs.

Un changement workflow/documentation ne doit donc plus forcer artificiellement un cold start.

## TCT borné et cas vide

Le baseline TCT reste calculé sur l'univers Action complet. L'exact T1/T2 est limité au Top-N/minimum de couverture autorisé et reste exclusivement ACTION TCT.

Si aucune Action n'est éligible à l'exact Daily, `TCT_SHADOW_V24_1_7.csv` est désormais écrit avec le schéma complet mais zéro ligne. Cela évite les erreurs de lecture CSV sur fichier sans en-têtes et ne crée aucune décision artificielle.

Le fichier décisionnel TCT complet conserve les lignes hors Top-N sous forme de placeholders non autoritaires `NO_T1_T2`, avec influence score nulle. La recherche exacte full-universe reste réservée au Weekly.

## Restitution CI quotidienne

Source : `outputs/daily_tct_ct/DAILY_TCT_CT_V21_8.csv`.

Périmètre :

- ACTION TCT ;
- ACTION CT ;
- ETF CT.

Livrables :

- `outputs/committee_master/CI_COMITE_INVESTISSEMENT.docx` ;
- `outputs/committee_master/CI_REFERENTIEL_PONDERE.xlsx` ;
- `outputs/mobile/ANDROID_CI_CONTROL_CENTER.md` ;
- `outputs/audit/DAILY_CI_RESTITUTION_V21_15_7.json`.

La restitution CI effectue zéro appel externe et zéro rerun de modèle. Elle ne modifie aucun score, poids, seuil ou décision. Pour TCT, les piliers V24.1.8 sont renormalisés comme dans le moteur ; T1/T2 restent du contexte à poids et influence score nuls.

## Invariants

- T1/T2 = ACTION_TCT_ONLY.
- Aucun ordre réel.
- Aucun fixed take-profit réintroduit.
- Aucun legacy fixed stop réintroduit.
- Aucun changement de critère, poids ou seuil décisionnel autorisé par l'optimisation runtime.
- Les états `DAILY_LATEST` ne remplacent pas les états de recherche hebdomadaire complets.
- W09 Hebdo reste complète et autoritaire lors de son prochain refresh.

## Validation requise

Le prochain workflow Daily doit confirmer :

1. W09 `daily_fred_calls = 0` et `daily_gdelt_calls = 0` ;
2. bootstrap seed uniquement si aucun fast-state/snapshot Hebdo n'est disponible ;
3. promotion du fast-state si le run démarre sans état rapide ;
4. génération Word + Excel CI avec `external_collection_calls = 0` et `model_reruns = 0` ;
5. quality gates collection et PIT sans régression ;
6. TCT exact borné et fichier shadow lisible même si l'exact scope est vide ;
7. timings détaillés collection / ETF replay / DAG tactique / CI / total ;
8. run suivant en `DELTA_ONLY` si contrats/caches restent compatibles.
