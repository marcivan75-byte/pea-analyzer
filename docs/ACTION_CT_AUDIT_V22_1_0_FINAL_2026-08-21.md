# Audit final Actions CT V22.1.0

Date : 21/08/2026

## Objet

Porter le module Actions CT au niveau méthodologique atteint par le TCT finalisé, sans transférer les formules T1/T2 et sans promouvoir des paramètres non validés.

## Principaux constats corrigés

### 1. Dépendance excessive aux champs déjà materialisés

V22.0 exploitait le contexte présent dans le master, mais certains champs utiles pouvaient être absents alors que les briques nécessaires existaient déjà dans le process.

**V22.1 :** reconstruction au run de la rotation sectorielle, du catch-up, de la force relative cross-sectionnelle et des features Actions dérivées gouvernées.

### 2. Rotation sectorielle insuffisamment garantie dans le CT

**V22.1 :** appel direct au moteur `sector_rotation.py`, avec conservation de son recovery gate. La distance au plus haut 52 semaines ne suffit donc jamais à favoriser une falling knife.

### 3. Force relative trop dépendante des sources externes

**V22.1 :** fallback cross-sectionnel 1m/3m/6m à partir du snapshot courant, minimum 20 Actions, sans écraser une mesure déjà observée.

### 4. Qualité et potentiel cible sous-exploités dans le timing CT

**V22.1 :** ajout d'un bloc `quality_target` utilisant Morningstar Actions et les scores de potentiel cible déjà dérivés par le process.

### 5. Thèmes et macro non reliés explicitement au CT

**V22.1 :** ajout d'un bloc optionnel `theme_macro`. Le macro n'est pris en compte que si `macro_evidence_sufficient=true`. L'absence de donnée réduit la couverture au lieu de générer une neutralité artificielle.

### 6. Survalorisation d'un thème prometteur

**V22.1 :** ajout de `THEME_OVERVALUATION_RISK`, complémentaire de `SECTOR_HOT_VALUATION_RISK`. Une survalorisation thématique combinée à un contexte macro adverse peut produire `WAIT_CONTEXT_RISK_SHADOW`.

### 7. Risque événementiel et valorisation dans la sortie

**V22.1 :** nouveau bloc `valuation_event_risk` dans le risque de sortie, fondé uniquement sur les données disponibles : valuation discount, AVCR thématique et proximité des résultats.

### 8. Validation et comparaison

V22.0 reste exécuté comme parent et comparateur secondaire. V22.1 possède son propre epoch PIT et son propre ledger SHA-256. La baseline V21.0 reste le comparateur principal.

## Pondérations V22.1

Entrée :

- trend 20 % ;
- momentum 18 % ;
- weekly 16 % ;
- relative strength / secteur 14 % ;
- volume / liquidité 10 % ;
- catalyseurs / consensus 10 % ;
- quality / target 6 % ;
- theme / macro 6 %.

Sortie :

- trend break 26 % ;
- momentum deterioration 18 % ;
- weekly deterioration 18 % ;
- distribution / volume 10 % ;
- relative deterioration 10 % ;
- volatility 10 % ;
- valuation / event 8 %.

Ces pondérations sont pré-enregistrées en SHADOW ; elles ne constituent pas encore des poids de production.

## Garde-fous confirmés

- T1/T2 interdits hors TCT ;
- intraday et 5m interdits ;
- bougies daily terminées ;
- weekly terminé uniquement ;
- aucune imputation neutre ;
- aucun take-profit fixe ;
- aucun stop-loss fixe promu ;
- aucun ordre réel ;
- holdout verrouillé ;
- premier snapshot PIT immuable ;
- aucun backfill de snapshots avec des valeurs actuelles ;
- aucune promotion automatique.

## Package

Le package complet `ACTION_CT_V22_1_0_COMPLETE` contient les configurations, moteurs V22.0/V22.1, dépendances de contexte, baseline, référentiel Actions, gouvernance Entry/Exit, runners, tests, documentation et workflows.

Il est construit de manière déterministe par `scripts/build_action_ct_package_v22_1.py` et publié comme artefact GitHub par la CI dédiée.

## Conclusion

V22.1 constitue une amélioration substantielle du processus CT : il augmente le nombre de signaux réellement exploitables et leur contextualisation sans contourner la discipline PIT. Sa mise en workflow peut être immédiate ; sa promotion comme nouvelle baseline devra attendre la preuve forward-PIT pré-enregistrée.
