# TCT V24.1.4 + V21.3 EXPLOSIF DATA-RICH

Application unifiée PEA Analyzer — **RESEARCH_ONLY / SHADOW**.

Cette version V24.1.4 est la version applicative auditée et durcie du socle V24.1.2. La pondération de scoring reste **V24.1.2** ; V24.1.4 correspond aux corrections de code, de contrôle de données, de gouvernance décisionnelle et d'industrialisation.

## Architecture d'exécution

Flux nominal :

`Free Capture enrichi → contrat d'entrée → Universe Gate PEA → Shorts/Squeeze → Meta → Gap Risk → Sizing V24 → Comité → V21.3 → exports`

Le moteur de scoring ne doit pas fabriquer silencieusement une donnée critique manquante.

### Contrat Free Capture

Deux contrats sont acceptés :

- le contrat TCT natif (`isin`, identité, prix, liquidité, earnings, preuve PEA, setup éventuel) ;
- le **V21.1 Free Capture réel du repo** via `src/data/repo_adapter.py` (`last_close`, `yahoo_ticker`, `volume_avg_20d`, etc.).

L'adaptateur conserve les colonnes originales, ne transforme aucun score historique en probabilité Meta et ne fabrique pas de setup T1/T2. Les doublons d'ISIN font échouer le run.

Le chemin par défaut est `data/processed/latest_signals.parquet` puis `.csv`. Pour le repo, utiliser `TCT_FREE_CAPTURE_PATH=.../V21.1_ACTIONS_PEA_REFERENCE_MERGED.csv`. Le séparateur `;`/`,` est détecté automatiquement.

## T1 / T2

Le détecteur historique reste disponible dans `src/signals/t1_t2.py` et les indicateurs dans `src/data/features.py`.

Les snapshots canoniques Actions/ETF actuellement disponibles ne fournissent pas un setup T1/T2 confirmé. L'adaptateur marque donc `setup_source=UNCONFIRMED_SINGLE_SNAPSHOT`, laisse `setup` vide et applique bonus zéro. Le moteur TCT n'effectue volontairement **aucun téléchargement OHLC caché** pour reconstruire 110+ séances pendant le scoring. Un calcul T1/T2 exact devra être ajouté à l'historique Free Capture ou à une étape amont dédiée.

## Meta-labeling

- Si un modèle réel validé est présent dans `models/meta_labeling`, il est utilisé.
- Si le modèle est absent, les `meta_proba` valides fournies par Free Capture sont conservées.
- Si une probabilité est elle-même absente, le fallback est **0,50**, sous le seuil de décision de 0,55 : la ligne ne peut donc pas devenir un signal exécutable uniquement grâce au fallback.
- Le script d'entraînement inclus utilise des données synthétiques et est réservé aux tests DEMO ; il n'est jamais lancé automatiquement en production.

## Gap Risk

- Modèle ML si des artefacts réels sont installés dans `models/gap_risk`.
- Sinon fallback rules-based.
- Les données de risque critiques manquantes ne sont plus interprétées comme un faible risque : absence de date d'earnings ou déficit important de variables critiques déclenche un comportement conservateur pouvant conduire à `IGNORE`.

## Position sizing

Le sizing combine :

`meta × gap risk × proximité earnings × liquidité × RL éventuel`

Paramètres par défaut :

- risque de base : 0,8 % du capital ;
- position minimale : 0,4 % ;
- position maximale : 6 % ;
- maximum : 12 positions `TAKE`.

Les valeurs `NaN`, prix invalides, liquidité absente, capital invalide, Universe Gate `REJECT/QUARANTINE` ou risque de gap dur conduisent à un comportement fail-closed.

## Universe Gate et gouvernance décisionnelle

Le contrôle PEA/ISIN/ticker est appliqué **avant** le sizing V24 puis à nouveau dans V21.3.

Un titre :

- non PEA → `REJECT` ;
- sans preuve PEA ou ticker → `QUARANTINE` ;
- `IGNORE` au sizing → ne peut pas être promu ensuite en recommandation exécutable par le comité ou V21.3.

Le comité complet conserve les lignes bloquées pour audit, mais leur délai devient `NE PAS ENTRER`, leur verdict `EVITER_BLOQUE` et `execution_eligible=False`. Les sorties Top50/ULTRA ne retiennent que les lignes exécutables.

`proba_pct` reste une **heuristique non calibrée** du CDC, identifiée par `proba_type=HEURISTIC_NON_CALIBRATED`. Elle ne doit pas être confondue avec une probabilité statistique validée.

## Short Interest AMF / Euronext

Le module AMF traite le fichier réglementaire comme un historique :

- sélection des positions actuellement publiées ;
- déduplication par détenteur/ISIN ;
- agrégation uniquement des positions courantes ;
- valeurs conservées en points de pourcentage (`0.52` = `0,52 %`) ;
- fermeture conventionnelle sous le seuil public de 0,5 % ;
- aucun fallback DEMO en exécution normale.

Les autres places Euronext peuvent être enrichies par les fichiers NCA intégrés au Free Capture.

Dans `pea-analyzer`, le Free Capture Actions contient déjà ces champs ; les ETF n'utilisent pas de short-interest comme signal de décision par défaut. Le runner d'intégration fixe donc `TCT_SKIP_SHORT_REFRESH=1` afin d'éviter un second appel réseau pendant le scoring.

## Apprentissage adaptatif

### Pondérations adaptatives

- apprentissage uniquement sur outcomes réellement réalisés ;
- aucun auto-label proxy issu du score courant ;
- bornes 2 %–30 % réellement respectées après normalisation ;
- critère `setup` correctement converti en variable numérique pour l'apprentissage.

### Reinforcement Learning

Le RL est conservé mais **shadow par défaut** (`apply_to_sizing: false`).

Même si ce paramètre est activé par erreur, le RL ne peut influencer le sizing que si `models/rl/model_meta.json` atteste d'au moins `min_validated_samples` outcomes réels (100 par défaut). Les artefacts RL synthétiques fournis historiquement ont été retirés de la version auditée. Un futur état ne sera considéré comme validé qu'avec la preuve `model_meta.json` ci-dessus.

Le workflow GitHub restaure/persiste l'état d'apprentissage entre les runners via `actions/cache`.

## Données de démonstration

Le mode normal est fail-closed : si Free Capture manque, `main.py` échoue.

Pour un test local uniquement, définir :

```yaml
runtime:
  allow_demo_fallback: true
```

Aucun fallback synthétique n'est autorisé par défaut pour Free Capture ou les shorts.

## GitHub Actions

Workflow autonome du paquet : `.github/workflows/tct_daily.yml`. Pour `pea-analyzer`, l'intégration racine utilise `.github/workflows/V24.1_tct.yml` et restaure deux contrats canoniques : le dernier artefact V21.1 Free Capture Actions et le dernier référentiel V20.7 ETF102. Les deux sont concaténés avec contrôle d'unicité ISIN avant injection via `TCT_FREE_CAPTURE_PATH`.

Le runner accepte transitoirement **1 429 Actions + 102 ETF = 1 531 instruments** tant que le nouveau Free Capture 1 829 Actions n'a pas encore produit un artefact réussi. Le statut d'audit reste `TRANSITIONAL_SCOPE` dans ce cas. La cible canonique est **1 829 Actions + 102 ETF = 1 931 instruments**, qui seule produit `scope_status=FULL_CANONICAL_1931`.

- le paquet standalone sait planifier 18:00 `Europe/Paris`, mais le runner repo reste manuel/validation tant que le contrat 1 931 n'a pas été validé bout-en-bout ;
- Python 3.12 ;
- compilation statique ;
- suite `pytest` obligatoire avant le run ;
- aucun entraînement synthétique automatique ;
- actions GitHub sur runtime Node 24 ;
- persistance de l'état T1/apprentissage via cache Node 24 ;
- aucun `git push` automatique depuis le runner ;
- artefacts `output/`, `logs/`, `data/pit/`, `data/persistence/` conservés 90 jours.

## Installation

```bash
git clone <repo>
cd tct_v24_1
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pytest tests/ -q
python main.py
```

Sous Windows :

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest tests/ -q
python main.py
```

Dashboard :

```bash
streamlit run dashboard/app.py
```

## Entraînement synthétique DEMO uniquement

Les commandes suivantes sont volontairement bloquées sans consentement explicite :

```bash
python -m src.ml.train_models --all --allow-synthetic-demo-training
```

Les modèles produits par cette commande sont des prototypes DEMO et ne doivent pas être utilisés comme modèles validés de décision.

## Validation V24.1.4

La version auditée inclut **44 tests** de non-régression couvrant notamment : scoring partiel, politique de poids manquants du repo, adaptateur V21.1, Meta absent, `NaN`, sizing réduit, AMF, collision Gap, cap de positions, Universe Gate, comité, fail-closed Free Capture, apprentissage réel et validation RL. Elle a aussi été exécutée bout-en-bout sur le dernier Free Capture Actions réel de 1 429 titres, puis sur un contrat combiné réel **1 429 Actions + 102 ETF = 1 531 instruments**. Le passage au plein contrat 1 931 attend le premier artefact Free Capture 1 829 Actions réussi.

Le rapport d'audit détaillé est dans `AUDIT_V24_1_4.md`.

## Avertissement

**RESEARCH_ONLY / SHADOW.** Ne constitue pas un conseil en investissement. Les outputs doivent rester soumis au comité et aux contrôles de risque du système global.

## Licence

MIT


## Corrections complémentaires V24.1.4

V24.1.4 ajoute le chaînage réel `build_signals`, le TTL T1 de 40 séances, le pacing Finnhub par requête, la protection anti-poids adaptatifs non validés et la validation obligatoire des métadonnées des modèles Meta/Gap. Le mode repo continue à privilégier le Free Capture V21.1 comme source canonique et n'effectue aucun refresh Yahoo massif caché.
