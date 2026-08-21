# Process V21.8.1 — Addendum normatif TCT V24.4.1

Date : 21/08/2026

Cet addendum supersède `PROCESS_REFERENCE_V21_8_1_TCT_V24_4_ADDENDUM.md` pour les nouveaux développements et snapshots TCT catalysts. Il ne modifie pas la production canonique V21.8.1.

## 1. Statut

- Production canonique : **V21.8.1 inchangée**.
- Baseline/timing TCT : **V24.1.7**, T1/T2 ACTION TCT uniquement.
- Daily/Weekly trader-tools : **V24.3.1 SHADOW**, avec correction du fallback structurel du 21/08/2026.
- Next-Session Catalyst : **V24.4.1 SHADOW**.
- V24.4.0 : **SUPERSEDED_FOR_NEW_SNAPSHOTS**, conservée comme historique de recherche.
- Holdout final : **FERMÉ**.
- Ordres réels : **désactivés**.
- Influence décision/score production/sizing/stop/CT V24.3.1 et V24.4.1 : **0**.

## 2. Motif de V24.4.1

L'audit du 21/08/2026 a identifié des défauts déterministes nécessitant une nouvelle version : fallback structurel sur zéro, double comptage possible de la news, renormalisation trop permissive quand une source dominante est absente, classification d'enquête trop large, biais générique M&A et empreinte PIT sensible aux évolutions de schéma.

Ces corrections modifient la sémantique du challenger SHADOW. Elles ne constituent pas une repondération opportuniste issue des résultats de performance et n'ouvrent pas le holdout.

## 3. Règle fonctionnelle

V24.4.1 reste un module TCT quotidien/hebdomadaire. Il ne fait pas de day trading.

Deux snapshots seulement :

1. `PREOPEN` avant l'ouverture européenne ;
2. `POSTMARKET` après les clôtures principales.

Aucun polling, aucune barre 1m/5m, aucun carnet d'ordres obligatoire.

## 4. Données et coût

Sources autorisées : seed V24.3.1 sur OHLCV daily, métadonnées déjà collectées, GDELT time-filtered, un snapshot groupé Yahoo Finance en `1d` pour le contexte mondial.

Le module ne télécharge pas de séries intraday Actions PEA. Le close-ledger utilise le cache daily déjà présent.

## 5. Corrections normatives V24.4.1

### Couverture

- mouvement : minimum 70 % ;
- direction : minimum 70 %.

En dessous de 70 % de couverture mouvement, aucun `movement_potential_score` exploitable n'est publié et l'état est `DATA_DEGRADED_SHADOW`.

En dessous de 70 % de couverture direction, aucun état UP/DOWN n'est autorisé.

### Événements

- Le bloc événement planifié ne réutilise plus le score news ; actuellement il se fonde sur la proximité des résultats.
- Une enquête générique ne vaut plus fraude grave.
- Une acquisition générique conserve une magnitude forte mais direction 0 sans résolution cible/acquéreur.

### PIT

- Nouveau ledger : `TCT_V24_4_1_CATALYST_LEDGER.csv`.
- Epoch : `V24.4.1_ONLY_NO_MIX_WITH_V24.4.0`.
- Fingerprint : `TCT_PIT_SHA256_CANONICAL_V2` sur liste fixe de champs prédictifs.
- Le close-ledger daily peut être partagé car il contient des prix observés indépendants du modèle.

## 6. Validation

Les gates de maturité/acceptation restent aussi exigeants que V24.4.0 et sont recopiés avant les nouvelles observations dans `config/TCT_V24_4_1_VALIDATION_GATES.json`.

Aucun résultat V24.4.0 ne compte dans la maturité V24.4.1. Aucun retuning avant maturité. Même `RESEARCH_CRITERIA_MET` ne possède aucune autorité de promotion.

## 7. Gouvernance WIP

Le présent audit réouvre temporairement le chantier TCT pour correction et publication. Après fusion, CI complète et publication du kit de référence, l'implémentation V24.4.1 pourra revenir au statut `IMPLEMENTATION_CLOSED / SHADOW_EVIDENCE_ACCUMULATION`.

Toute modification ultérieure de poids, seuils, logique structurante, source structurante ou critère de promotion constitue un nouveau chantier et, si la sémantique du score change, une nouvelle epoch PIT doit être créée.

## 8. Documents de référence

- `docs/TCT_AUDIT_V24_4_1_2026-08-21.md`
- `docs/TCT_CDC_V24_4_1_FINAL.md`
- `docs/TCT_REFERENTIEL_V24_4_1_FINAL.md`
- `docs/TCT_RELEASE_MANIFEST_V24_4_1.json`

En cas de contradiction sur V24.4.1, le présent addendum et les configs versionnées V24.4.1 prévalent pour le challenger TCT ; V21.8.1 demeure l'autorité de production jusqu'à promotion explicite.
