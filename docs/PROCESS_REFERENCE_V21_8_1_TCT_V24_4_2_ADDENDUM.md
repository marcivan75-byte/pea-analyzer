# Process V21.8.1 — Addendum normatif TCT V24.4.2

Date : 21/08/2026

Cet addendum supersède V24.4.1 pour **les nouveaux snapshots TCT next-session**. V24.4.0/V24.4.1 restent historiques. La production canonique V21.8.1 demeure inchangée.

## Statut

- Production canonique : V21.8.1.
- Baseline TCT/T1-T2 : V24.1.7, ACTION TCT uniquement.
- Daily/Weekly : V24.3.1 SHADOW.
- Next-session : V24.4.2 SHADOW.
- Epoch PIT : `V24.4.2_ONLY_NO_MIX_WITH_PRIOR_EPOCHS`.
- Influence production : 0.
- Ordres réels : désactivés.
- Holdout final : fermé.
- CT : gelé pour ce chantier.

## Source normative

Les valeurs numériques V24.4.2 sont centralisées dans `TCT_REFERENTIEL_V24_4_2_FINAL.md` et les JSON V24.4.2. Le CDC n'est plus une seconde copie des poids/seuils.

## Changements structurants issus de l'audit externe

1. Runner refactoré par injection de dépendances ; aucune version ne monkey-patch une autre.
2. Nouvelle preuve PIT OHLC : open/high/low/close J+1, gap, range, excursions, MAE et close.
3. Mouvement significatif défini explicitement par seuil absolu + ATR.
4. Classification news externalisée, négations, confiance de match et golden-set.
5. Sélection candidats diversifiée par quotas et raison de ranking publiée.
6. Cache news exact-window avec télémétrie et circuit-breaker de temps.
7. Risk-on davantage adapté aux actions PEA avec bloc Europe explicite.
8. Validation enrichie par stabilité secteur/régime/VIX et mesures absolues de mouvement.
9. Sorties mobile recentrées sur Top5, conflits, exit-risk et qualité.
10. Calibration uniquement offline SHADOW ; aucun poids n'est réinjecté automatiquement.

## Point non activé

La seconde source news est définie comme exigence fail-soft mais n'est pas activée dans V24.4.2 tant qu'un provider secondaire n'a pas été qualifié sur timestamps PIT, pertinence, latence, déduplication et provenance. Le système préfère une couverture dégradée explicite à une information secondaire non auditée.

## Règle de preuve

V24.4.2 modifie la sémantique du ranking et du label d'amplitude ; aucune observation V24.4.0/V24.4.1 ne compte dans sa maturité. Toute future modification de poids, quotas, catalogue avec effet de score, provider news actif ou formule d'outcome ouvre une nouvelle epoch.

## Non-promotions

L'ajout de labels OHLC, d'un bloc Europe, du cache, des quotas et des nouvelles catégories news améliore la conception et la testabilité mais ne constitue pas une preuve de performance financière. La promotion reste conditionnée à la maturité forward-PIT, à la comparaison V24.3.1 et à une décision séparée.
