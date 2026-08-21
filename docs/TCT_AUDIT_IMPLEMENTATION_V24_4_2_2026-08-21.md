# TCT V24.4.2 — mise en œuvre de l'audit externe du 21/08/2026

Source audit : `AUDIT_TCT_V24_4_1_CORRECTIONS_PERFORMANCE.doc` fourni par l'utilisateur.

## Synthèse

L'audit considère V24.4.1 comme une base SHADOW solide sur la causalité PIT, le fail-closed et l'isolation d'epoch, mais relève un runner muté à chaud, une couche news fragile, une classification lexicale limitée, des poids a priori, un Top60 peu discriminatif, des outcomes close-only, une duplication documentaire et des lacunes de performance/stabilité/observabilité.

V24.4.2 traite ces points sans modifier la production V21.8.1 ni ouvrir le holdout.

## Matrice de traitement

| Audit | Traitement V24.4.2 | Statut |
|---|---|---|
| F01/A1 monkey-patching runner | moteur `tct_next_session_catalyst_engine.py`, injection explicite ; runner V24.4.1 refactoré aussi | CORRIGÉ |
| F02 GDELT unique | cache, télémétrie, circuit-breaker ; contrat secondary fail-soft | PARTIEL — provider secondaire non activé avant qualification |
| F03/A3 lexicale naïve | patterns JSON, négations, confiance de match, golden-set | CORRIGÉ |
| F04 poids a priori | aucun poids modifié ; outil calibration offline après maturité | ENCADRÉ, PAS DE RETUNING |
| F05/A4 Top60 | score multi-axes + quotas + `candidate_rank_reason` | CORRIGÉ |
| F06/A2 close-only | ledger OHLC + gap/range/excursions/MAE/close/significant flag | CORRIGÉ |
| F07 redondance CDC/Réf. | Référentiel = source numérique ; CDC = exigences/interfaces | CORRIGÉ |
| F08 workers/rate-limit | workers configurables, vagues, p50/p95 | CORRIGÉ |
| F09 force relative sectorielle | non ajoutée sans univers sectoriel PIT stable | DIFFÉRÉ GOUVERNÉ |
| F10 stabilité | secteur/régime/VIX + non-dégradation directionnelle | CORRIGÉ |
| F11 cache news | cache persistent exact-window TTL | CORRIGÉ |
| F12 sorties mobiles | Top5, up, conflits, EXIT_RISK_HIGH, couverture/runtime | CORRIGÉ |
| R01 amplitude non définie | extrême de séance >= max(floor absolu, multiple ATR) | CORRIGÉ |
| R02 outcome limité | OHLC J+1 multi-label | CORRIGÉ |
| R03 catalogue PEA | OPA, suspension, placement privé, indices, consensus, warning partiel | CORRIGÉ |
| R04 Europe absente risk-on | EuroStoxx/CAC/DAX intégrés au cœur du risk-on | CORRIGÉ |
| R05 seuils non calibrés | clause de revue quantiles après maturité, pas d'auto-retuning | CORRIGÉ EN GOUVERNANCE |
| C02 budget runtime | SLA internes et circuit-breaker | CORRIGÉ |
| C03 source de secours | contrat défini, activation différée à qualification | PARTIEL |
| C04 golden-set | fixture >=15 cas + test de régression | CORRIGÉ |
| C05 mobile | template normatif actionnable | CORRIGÉ |

## Choix de version

La version passe de V24.4.1 à V24.4.2 car les corrections modifient : sélection de candidats, classification news, contexte global, label primaire de validation et empreinte prédictive. Mélanger les résultats avant/après créerait une rupture de définition. L'epoch V24.4.2 est donc séparée.

## Choix conservateurs

### Poids

Aucune optimisation a posteriori des poids n'est intégrée. Les poids V24.4.1 sont conservés pour isoler l'effet des corrections de qualité. Un outil de recherche offline peut explorer des candidats une fois la maturité atteinte mais ne modifie jamais le runtime.

### Source secondaire

L'audit recommande une source de secours. Plutôt que d'activer une source sans contrat PIT vérifié, V24.4.2 prépare la politique de fallback et conserve un statut explicite non actif. Une activation future doit faire l'objet de tests de timestamp, duplication, pertinence, débit et provenance et, si elle modifie les scores, d'une nouvelle epoch.

### Relative strength secteur

La feature sector-relative recommandée n'est pas pondérée dans le score tant que la stabilité de l'univers secteur/benchmark n'est pas démontrée point-in-time. Les slices sectorielles sont en revanche mesurées immédiatement.

## Effets attendus — sans revendication de performance

- robustesse logicielle : hausse attendue grâce à l'absence de mutation globale ;
- pertinence de validation : hausse attendue via OHLC J+1 et définition de mouvement significatif ;
- pertinence news : réduction attendue des faux positifs par négation/confiance/golden-set ;
- couverture Top60 : diversification par quotas ;
- latence : diminution possible via cache et circuit-breaker ;
- interprétabilité : amélioration via rank_reason, labels OHLC et mobile Top5.

Ces effets sont des objectifs d'ingénierie. L'uplift prédictif doit être mesuré dans l'epoch forward-PIT V24.4.2.

## Definition of Done

La fusion est interdite tant que compilation, Ruff, audit statique, intégrité de gouvernance, suite pytest complète, golden-set, tests OHLC/lineage et tests workflows ne sont pas tous verts.
