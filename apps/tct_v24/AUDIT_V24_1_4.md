# Audit technique TCT V24.1.4

**Date : 10/08/2026**  
**Statut : RESEARCH_ONLY_SHADOW**  
**Base : V24.1.3 auditée + corrections complémentaires de `CORRECTIONS_TCT_V24_1.pdf`.**

## 1. Verdict

La V24.1.4 est techniquement validée pour intégration **en remplacement fonctionnel du TCT V21.2 en mode recherche/shadow**. Elle ne doit pas être promue en exécution réelle tant que Meta-Labeling et Gap Risk ne sont pas calibrés et validés sur des outcomes point-in-time réels.

Validation locale finale :

- `compileall` : OK ;
- `pytest` : **44/44** ;
- smoke Actions réel : **1 429 lignes / 1 429 ISIN** ;
- smoke combiné réel disponible : **1 429 Actions + 102 ETF = 1 531 instruments / 1 531 ISIN** ;
- Universe Gate combiné : **1 381 PASS / 150 QUARANTINE / 0 REJECT** ;
- classement recherche : **20 lignes** ;
- exécution : **0 TAKE**, volontairement fail-closed en l'absence de modèles réels validés.

Le repo a déjà basculé son contrat cible Actions à **1 829**, mais aucun artefact Free Capture 1 829 réussi n'est encore disponible. Le runner V24 accepte donc le dernier 1 429 uniquement comme validation transitoire et marque explicitement `scope_status=TRANSITIONAL_SCOPE`. La cible finale est **1 931 = 1 829 Actions + 102 ETF**.

## 2. Corrections complémentaires intégrées

### 2.1 Chaîne réelle données → indicateurs → T1/T2

Nouveau module `src/pipeline/build_signals.py`. Priorité au snapshot réel du repo. La construction native `universe.csv -> OHLCV -> indicateurs -> T1/T2 -> Earnings` reste disponible mais explicitement opt-in. Aucun fallback synthétique silencieux.

Le mode repo **ne déclenche aucun téléchargement Yahoo massif caché** : un snapshot unique ne fabrique jamais un T1/T2. Un refresh OHLCV exact existe mais est désactivé par défaut (`t1_t2.network_refresh.enabled=false`).

### 2.2 Score partiel

La double mise à l'échelle a été supprimée. En mode repo, les poids absents restent fixes et ne sont pas redistribués (`ZERO_FIXED_WEIGHT`).

### 2.3 Persistance T1 inter-runs + TTL

`last_T1_bandwidth.json` stocke désormais largeur + date de détection. Un T1 expire après **40 séances ouvrées**. Les états legacy sans date sont ignorés avec TTL actif. Un T2 confirmé consomme son T1 précédent.

### 2.4 Planification Europe/Paris

Le workflow standalone utilise un timezone IANA `Europe/Paris`; le runner repo reste non planifié jusqu'à validation du contrat 1 931.

### 2.5 Quota Finnhub

`DataLoader.get_earnings_info()` cadence chaque requête Finnhub individuellement. Les dates sont calculées en `Europe/Paris`; une date passée devient `NaN` et ne peut pas être interprétée comme J-1.

### 2.6 Parsing PEA

Les valeurs textuelles `true/false/yes/no/oui/non` sont traitées explicitement. `UNKNOWN` mène à QUARANTINE ; `FAIL` à REJECT.

### 2.7 Source unique des pondérations

Le comité consomme `WEIGHTS_V24_1_2` depuis `src/signals/scoring.py`. La constante V24.1 historique restante est documentaire.

### 2.8 Horodatage

`main.py` et le pipeline quotidien utilisent `Europe/Paris`; les horodatages techniques d'audit sont UTC explicite.

### 2.9 Verdict SAT

`SAT` = satellite de recherche : score final >= 60 sans T1/T2 confirmé. Un veto d'univers ou un `IGNORE` de sizing ne peut jamais être promu en sortie exécutable.

### 2.10 Anti-apprentissage circulaire

`allow_proxy_learning=false` par défaut. Poids adaptatifs et RL ne sont mis à jour qu'avec outcomes réels. Les poids persistés sans provenance `real_outcomes` sont refusés au chargement.

### 2.11 Audit de `train_models.py`

Le script reste DEMO/synthétique uniquement et exige `--allow-synthetic-demo-training`. Chaque entraînement synthétique écrit `model_meta.json` avec `training_source=synthetic_demo`, `validated_for_production=false`, `research_only=true`. Meta/Gap refusent ces artefacts comme modèles actifs.

### 2.12 Contrat ETF102 réellement intégré

Le référentiel V20.7 ETF102 réel a été audité : il expose les champs techniques requis (`last_close`, `yahoo_ticker`, RSI, MACD, Bollinger, RVOL, etc.). `repo_adapter.py` reconnaît maintenant explicitement `asset_class=ETF`, produit `tct_asset_class=ETF` et conserve la provenance `V20.7_ETF102_REFERENCE_MASTER`. Les sorties TOP20/TOP50 gardent la classe d'actif, le nom et le type PEA.

Le smoke combiné 1 531 a validé l'absence de collision d'ISIN et l'intégration des **102/102 ETF**.

### 2.13 Industrialisation GitHub

Le runner racine restaure séparément Actions et ETF, contrôle leur cardinalité, concatène les contrats et échoue sur doublon ISIN. Il n'effectue aucun `git push` automatique. `actions/cache@v5`, `checkout@v6`, `setup-python@v6` et `upload-artifact@v6` évitent la pile Node 20.

## 3. Points du document complémentaire désormais résolus

| Point | Statut V24.1.4 |
|---|---|
| `universe.csv` absent | **Résolu dans le repo** : Actions et ETF sont restaurés depuis leurs référentiels canoniques. `universe.csv` ne sert qu'au mode standalone réseau. |
| `max_positions: 12` non appliqué | **Corrigé** : cap portefeuille post-sizing. |
| Pas de TTL T1 | **Corrigé** : 40 séances ouvrées. |
| `git push` depuis Actions | **Évité** : état via cache/artifacts, pas de push direct. |
| `train_models.py` non audité | **Audité et durci**. |

## 4. Contrat repo et migration 1 931

Deux sources canoniques sont utilisées :

- Actions : `V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv` ;
- ETF : `V20.7_ETF102_REFERENCE_MASTER.csv`.

Le dernier artefact Actions réellement réussi disponible pour ce contrôle contient encore 1 429 lignes. Le repo cible désormais 1 829 Actions ; le workflow V24 acceptera 1 429 uniquement avec `TRANSITIONAL_SCOPE`, puis basculera automatiquement à `FULL_CANONICAL_1931` lorsque le Free Capture 1 829 sera disponible.

## 5. Comparaison avec le TCT V21.2 historique

Sur le dernier artefact V21.2 réussi, quatre titres étaient `SCAN_TCT_EXPLOSIF` : HOEGH AUTOLINERS, THEON, RECTICEL et MELHUS SPAREBANK. Sur le même environnement Actions, MELHUS et HOEGH apparaissent aussi dans le TOP20 recherche V24.1.4. Ce recoupement est un contrôle de cohérence, pas une preuve de performance.

## 6. Critères de promotion hors SHADOW

Aucune promotion tant que les conditions suivantes ne sont pas réunies : historique point-in-time 12/18/36 mois ; backtests purgés et walk-forward ; calibration Meta/Gap sur outcomes réels ; expectancy positive et Profit Factor robuste ; stabilité inter-folds ; audit de fuite ; validation du plein univers 1 931 ; provenance des données ; non-régression du workflow complet.
