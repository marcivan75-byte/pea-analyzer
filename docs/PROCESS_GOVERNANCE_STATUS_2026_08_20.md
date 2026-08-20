# PEA Analyzer — Statut global de gouvernance WIP=1

Date de référence : 20/08/2026

## Portée normative

Cette note synchronise le statut de gouvernance du process après intégration de TCT V24.4.0 et ETF Fund Flows V1.0. Elle complète `PROCESS_REFERENCE_V21_8_1_FINAL.md`, `PROCESS_REFERENCE_V21_8_1_TCT_V24_4_ADDENDUM.md` et `ETF_FUND_FLOWS_V1_SHADOW.md`.

En cas de contradiction portant uniquement sur le **statut WIP / chantier actif**, la présente note prévaut. Elle ne modifie aucune règle de score, pondération, seuil, univers, source, stop, sizing ou décision de production.

## 1. Production canonique

- Baseline Entry/Exit : **V21.8.1 inchangée**.
- Holdout final : **FERMÉ**.
- Aucun ordre réel.
- T1/T2 : **ACTION TCT uniquement**.
- Aucun take-profit fixe opérationnel.
- Ancien objectif +4 % : non opérationnel.
- Ancien stop ETF -18 % : non opérationnel.
- Sector/Theme Rotation V2 et Risk V1.1/Bêta : **CONTEXT_ONLY** tant qu'une promotion PIT/OOS n'est pas explicitement validée.

## 2. Règle WIP=1 corrigée

La règle WIP=1 s'applique aux **travaux de développement, correction, retuning, intégration ou promotion**.

Une couche SHADOW dont :

1. l'implémentation est terminée et auditée ;
2. les règles sont gelées ;
3. l'influence de production est égale à zéro ;
4. aucune promotion automatique n'est possible ;
5. le seul travail restant est l'accumulation prospective de preuves PIT ;

est classée `SHADOW_EVIDENCE_ACCUMULATION` et **ne consomme plus le slot WIP de développement**.

Toute modification de ses poids, seuils, logique, sources structurantes ou critères de promotion la fait immédiatement redevenir un chantier WIP actif. Une promotion vers la production constitue également un nouveau chantier WIP distinct.

## 3. TCT V24.4.0

Statut : **IMPLEMENTATION_CLOSED / SHADOW_EVIDENCE_ACCUMULATION**.

- V24.3.1 Daily/Weekly reste SHADOW.
- V24.4.0 Next-Session Catalyst Cycle reste SHADOW.
- Influence décision, score de production, sizing, stop et CT : **0**.
- CT reste non modifié tant qu'une validation spécifique n'est pas démontrée.
- Le ledger PIT continue à accumuler les snapshots PREOPEN/POSTMARKET et leurs issues futures.
- Aucun retuning avant maturité.
- Les seuils de maturité et d'acceptation restent ceux de `config/TCT_V24_4_0_VALIDATION_GATES.json`.
- L'accumulation PIT n'est pas assimilée à un chantier actif.

La promotion éventuelle de V24.4.0 ne pourra être étudiée qu'après maturité et démonstration PIT/OOS selon les gates pré-enregistrés.

## 4. ETF Fund Flows V1.0

Statut : **IMPLEMENTATION_CLOSED / SHADOW_EVIDENCE_ACCUMULATION**.

- Module `SHADOW_ONLY`.
- Influence décisions, ETF MT, Sector Rotation V2, Gold, sizing, stops et ordres : **0**.
- Historique alimenté uniquement par observations PIT réellement persistées ; aucun backfill depuis un snapshot courant.
- Les poids EFS, overlay PEA, SRFS et Gold sont des hypothèses de recherche pré-enregistrées et gelées.
- Aucun retuning automatique sur les premières observations.
- La maturité et toute promotion future nécessitent un protocole PIT/OOS dédié.
- L'accumulation PIT n'est pas assimilée à un chantier actif.

## 5. Statut GitHub de clôture

- PR #98 `RUN ONLY — hydratation finale gouvernée V21.9` : **fermée sans fusion** après validations SUCCESS, conformément à son objet temporaire.
- PR #99 `ETF Fund Flows V1.0 SHADOW` : **fusionnée dans `main`**.

## 6. Slot WIP actuel

Au 20/08/2026 : **AUCUN CHANTIER DE DÉVELOPPEMENT ACTIF**.

TCT V24.4.0 et ETF Fund Flows V1.0 continuent uniquement en accumulation prospective de preuves SHADOW. Ils ne doivent pas être retunés ou modifiés en parallèle d'un nouveau chantier sans décision explicite de réouverture.

Le prochain chantier peut donc être sélectionné dans la file d'attente selon la priorité du process, tout en laissant les collecteurs SHADOW accumuler leurs preuves sans intervention.

## 7. Definition of Done — clarification

Pour éviter de bloquer indéfiniment WIP=1 sur un module qui exige plusieurs semaines de données prospectives :

- **DoD implémentation** : audit, corrections, intégration, tests, run représentatif, documentation et influence de production conforme ;
- **DoD promotion** : maturité statistique + validation PIT/OOS + décision explicite de promotion.

Entre les deux, le statut obligatoire est `SHADOW_EVIDENCE_ACCUMULATION` avec logique gelée et influence production nulle.

Cette séparation ne réduit pas les exigences de preuve : elle empêche seulement qu'une collecte prospective passive monopolise artificiellement le slot WIP=1.