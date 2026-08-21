# PEA Analyzer — Package ETF V21.18.1R3

Package autonome de distribution construit à partir du commit `49c53d87855ce27e76213c65916cb5a579cdf590`.

## Contenu

- `reference/` : référentiel XLSX et registres machine-readable.
- `documents/` : cahier des charges, rapport d’audit et audit source communiqué.
- `src/` : modules Python du packaging ETF.
- `config/` : configurations ETF, rotation, benchmark et Entry/Exit.
- `tests/` et `evidence/` : tests et preuves d’exécution.
- `cache/` : arborescence prête à l’emploi, contrats et exemples — aucune donnée fabriquée.
- `schemas/` : schémas JSON des observations, métriques et décisions de promotion.
- `tools/validate_package.py` : validation autonome des fichiers et garde-fous.
- `MANIFEST.json` et `SHA256SUMS.txt` : inventaire et intégrité interne.

## Validation rapide

```text
python tools/validate_package.py
```

Résultat attendu : `PACKAGE VALIDATION PASS`.

## Garde-fous

Tracking error, poids adaptatifs, ordres réels et couches shadow live restent désactivés. Les caches doivent être alimentés exclusivement par un run réel avec timestamp, provenance et hash.

## Réserve connue

La matrice canonique `03_ETF_268` citée par le manifeste du dépôt n’est pas disponible dans le commit. Le registre distingue 222 lignes vérifiées et 46 lignes reconstruites depuis les configurations versionnées.
