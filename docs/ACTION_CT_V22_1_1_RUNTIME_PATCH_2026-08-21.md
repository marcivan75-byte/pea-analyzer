# ACTION CT V22.1.1 — Performance & Observability Runtime Patch

Date: 2026-08-21

## Statut

- décision/scoring enregistré: `ACTION_CT_V22.1.0_CONTEXT_ENRICHED_SHADOW`;
- patch runtime: `ACTION_CT_V22.1.1_PERFORMANCE_OBSERVABILITY_PATCH`;
- statut: `SHADOW_RESEARCH_ONLY`;
- baseline production: `V21_ACTIONS_REFERENCE_V21_0`, inchangée;
- epoch PIT: `ACTION_CT_V22.1.0_ONLY`, inchangé;
- T1/T2: interdits hors ACTION TCT;
- intraday/5m: interdits;
- ordres réels: interdits;
- take-profit fixe / stop-loss fixe: non activés.

Le patch améliore l'exécution, la robustesse et l'auditabilité sans modifier les poids d'entrée V22.1.0 ni promouvoir de nouveau critère en production.

## Améliorations intégrées

### 1. Performance

- factorisation des helpers numériques dans `src/v182/features/ct_math.py`;
- force relative cross-sectionnelle 1m/3m/6m vectorisée;
- mapping ISIN/ticker vectorisé;
- chargement OHLCV batch conservé;
- calcul des snapshots parallélisable par threads pour les univers suffisamment grands;
- mesure séparée des temps `context_overlay`, `history_batch_load`, `baseline`, `snapshot_compute`, `state/write`, `PIT` et total.

La parallélisation est un mécanisme runtime et ne change ni l'ordre économique des observations ni la définition des snapshots PIT.

### 2. Rotation sectorielle

Les hypothèses auparavant implicites sont maintenant paramétrables dans `context_overlay.sector_rotation`:

- `min_sector_size`;
- `catchup_distance_scale_pct`;
- `recovery_gate_cap`;
- seuils marché/secteur/action pour candidat rotation;
- seuil HHI de concentration.

Un `rotation_hhi_10000` et un `rotation_concentration_warning` sont exposés dans le diagnostic afin de distinguer une rotation large d'une rotation dominée par peu de secteurs.

### 3. Risque asymétrique et traçabilité des risques

Nouveaux diagnostics SHADOW sans poids décisionnel propre:

- `drawdown_20d_pct`;
- `gain_loss_ratio_20d`;
- `downside_volatility_20d_pct`;
- `asymmetric_risk_score`;
- `valuation_risk_score`;
- `event_risk_score`;
- `valuation_event_risk_score` conservé pour compatibilité du score V22.1.0;
- `liquidity_quality_score`;
- `context_richness_score`.

Le split valuation/event est destiné à l'attribution et à la validation future. Le composant agrégé existant reste le seul utilisé dans le poids de sortie enregistré, afin de ne pas changer l'epoch PIT en cours.

### 4. Liquidité

Le floor historique de turnover reste inchangé. Deux niveaux diagnostiques supplémentaires permettent de distinguer:

- sous le floor: `LIQUIDITY_BELOW_FLOOR`;
- liquidité mince: `LIQUIDITY_THIN`;
- liquidité intermédiaire;
- liquidité robuste.

Ces niveaux servent à l'analyse forward-PIT avant toute modification d'un gate de production.

### 5. Qualité des données et observabilité

Le runner produit désormais:

- validation de schéma du master avant calcul;
- couverture par champ de contexte;
- `context_richness_pct`;
- distribution des états d'entrée et de sortie;
- médianes de coverage entrée/sortie;
- temps de run par étape;
- journal d'erreurs avec traceback limité;
- divergence V21 vs V22.1;
- comparaison V22.0 vs V22.1 lorsque le parent est disponible.

Fichiers dédiés:

- `outputs/audit/ACTION_CT_V22_1_0_RUNTIME.json`;
- `outputs/audit/ACTION_CT_V22_1_0_ERRORS.json`;
- `outputs/audit/ACTION_CT_V22_1_0_DIVERGENCES.csv`.

### 6. CI et tests

Ajouts:

- tests du module math commun;
- test de performance de l'overlay sur 500 actions synthétiques;
- test des paramètres de rotation et du HHI;
- tests des risques asymétrique/valuation/event;
- test fail-closed du schéma master;
- assertions de non-activation de la sensibilité des poids;
- assertions d'immutabilité de l'epoch PIT;
- enrichissement du test end-to-end runner avec audits runtime/divergence.

Le workflow Action CT compile les nouveaux modules, exécute les tests V22.0/V22.1/runtime patch et reconstruit le package complet.

## Améliorations volontairement non activées

### Pondérations quality/theme

La sensibilité `quality/theme` est pré-enregistrée dans `entry_weights_sensitivity`, mais `enabled=false`.

Aucune hausse de 6%+6% vers 8–10% par bloc n'est appliquée dans l'epoch `ACTION_CT_V22.1.0_ONLY`. Toute activation devra:

1. ouvrir un nouvel epoch challenger;
2. utiliser les observations PIT accumulées;
3. comparer les variantes sans fuite de holdout;
4. satisfaire les gates de performance et de risque;
5. rester sans promotion automatique.

### Changement du score de sortie

`valuation_risk` et `event_risk` sont séparés pour diagnostic, mais le poids de sortie agrégé `valuation_event_risk=0.08` reste inchangé afin de préserver la comparabilité PIT.

## Critères de réussite runtime

Le patch expose les métriques nécessaires pour mesurer objectivement:

- wall-clock total;
- temps du compute;
- coverage médiane;
- couverture quality/theme;
- accord/divergence V21/V22.1;
- concentration sectorielle;
- fréquence des warnings asymétriques et de liquidité;
- erreurs par run.

Les gains de performance financière ne sont pas revendiqués avant validation forward-PIT. Le patch rend précisément cette validation plus rapide, plus robuste et plus auditable.
