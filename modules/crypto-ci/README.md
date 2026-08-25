# CI Crypto V1.6.1 — 37 critères légers, entrées et objectifs CT

Moteur autonome **Crypto uniquement** inspiré des garde-fous TCT/CT Actions, sans partage d'univers, de score ou d'état avec les Actions/ETF.

Il produit deux lectures complémentaires :

- `CRYPTO_TCT` : horizon tactique de 1 à 7 jours ;
- `CRYPTO_CT` : horizon de 2 à 12 semaines ;
- `CI_CRYPTO` : restitution consolidée explicable, sans ordre réel.

À chaque run réseau, l'univers traite exactement les **100 premières cryptos par capitalisation CoinGecko**. Les 10 entrées BTC, ETH, SOL, BNB, XRP, ADA, LINK, AVAX, DOT et NEAR servent uniquement d'overrides d'identité et de sources. T1/T2 s'applique exclusivement à l'horizon TCT des 100 actifs.

## Principes

- identités exactes par `CoinGecko id`, symbole de marché et, lorsqu'applicable, adresse de contrat ;
- découverte PIT du Top 100, sans remplissage silencieux si CoinGecko retourne moins de 100 identifiants uniques ;
- stablecoins, actifs wrapped et tokens à levier présents dans le Top 100 analysés et publiés, mais bloqués pour la sélection ;
- sources primaires/attribuées, cache TTL, retries bornés, limiteurs par fournisseur et collecte parallèle ;
- aucune imputation neutre : un bloc absent réduit la couverture et n'est jamais noté 50 ;
- cinq critères supplémentaires dérivés des historiques existants : accélération du momentum, confirmation prix-volume, force relative au BTC, illiquidité d'Amihud et volatilité baissière ;
- aucun open interest, aucune liquidation et aucun flux vers les exchanges ; aucune requête réseau ajoutée pour ces cinq critères ;
- score renormalisé seulement après le gate de couverture ;
- risque, qualité, fraîcheur et divergence inter-sources peuvent bloquer un score élevé ;
- données point-in-time, horodatage UTC, empreinte SHA-256 et aucun look-ahead ;
- `SHADOW_RESEARCH_ONLY`, `real_orders_enabled=false` et aucune promotion automatique des poids.

## Exécution

```bash
python -m pip install -e ".[test]"
crypto-ci validate --root .
crypto-ci run --root .
crypto-ci run --root . --full-output
crypto-ci audit --root .
crypto-ci performance-audit --root .
pytest -q
```

Le run réseau utilise les endpoints publics configurés. `COINGECKO_API_KEY` (Demo) et `COINGECKO_PRO_API_KEY` (Pro) sont optionnelles ; le mode Pro augmente automatiquement le débit autorisé. Les sorties complètes sont toujours écrites sous `outputs/`. Par défaut le terminal affiche un résumé compact ; `--full-output` affiche aussi les 200 lignes. À froid et sans clé, la centaine d'historiques CoinGecko domine la durée (budget indicatif : environ 270 s) ; le cache TTL accélère fortement les relances. Les réponses `429` propagent désormais le délai `Retry-After` à tous les workers du même fournisseur afin d'éviter les tentatives inutiles sans supprimer de collecte.

Pour un run déterministe hors réseau :

```bash
crypto-ci run --root . --snapshot tests/fixtures/snapshot.json --as-of 2026-08-24T20:00:00Z
```

## Livrables principaux

- `config/CRYPTO_SOURCE_REGISTRY_V1.json` : hiérarchie et contrats des sources ;
- `config/CRYPTO_CRITERIA_REGISTRY_V1.json` : critères, direction, horizon et preuve ;
- `config/CRYPTO_GOVERNANCE_V1.json` : pondérations, gates, seuils et budgets ;
- `config/CRYPTO_UNIVERSE_V1.json` : politique Top 100 dynamique, classifications bloquantes et overrides de mapping ;
- `docs/CRYPTO_PROCESS_REFERENCE_V1.md` : référentiel détaillé ;
- `docs/CRYPTO_T1_T2_REFERENCE_V1.md` : détection T1, confirmation T2 et état temporel 24/7 ;
- `outputs/ci/CI_CRYPTO.json|csv|md` : comité Crypto ;
- `outputs/audit/AUDIT_01..05` : cinq audits enchaînés.
- `outputs/audit/performance/AUDIT_PERF_01..03` : trois audits consécutifs dédiés à la durée et à l'équivalence informationnelle.
- `scripts/simulate_entry_setups.py` : simulation des entrées E1 à E4 sur les actifs présélectionnés ;
- `scripts/simulate_ct_targets.py` : objectifs CT prudent, central et optimiste, invalidation et rendement/risque ;
- `outputs/simulations/` : dernières simulations d'entrée et d'objectifs CT ;
- `data/cache/` : cache opérationnel versionné dans le package complet, sans cache de développement ;
- `PACKAGE_MANIFEST.json` et `SHA256SUMS.txt` : inventaire, tailles et empreintes de l'archive complète.

Ce moteur est un outil de recherche et d'aide à la revue, pas un conseil financier ni un système d'exécution.
