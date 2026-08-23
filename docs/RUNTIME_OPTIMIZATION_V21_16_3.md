# Optimisation runtime V21.16.3

## Statut

Architecture **statiquement optimisée et auditée**. Les temps ci-dessous sont des budgets issus de l'analyse du chemin critique ; ils ne sont pas des mesures de run.

Aucun workflow, run marché, backtest ou holdout n'a été lancé pendant cette boucle d'optimisation. La mesure autoritative reste à effectuer sur un quotidien normal et un vendredi normal lorsque l'exécution sera explicitement autorisée.

## Invariants non négociables

L'optimisation ne réduit pas le modèle financier :

- univers Actions : 1 829 ;
- critères Actions : 633 ;
- critères ETF : 268 ;
- T1/T2 : ACTION TCT uniquement ;
- aucun changement de poids ;
- aucun changement de seuil ;
- aucune réduction d'univers ;
- source gate : influence score 0 et aucune mutation de décision interne ;
- aucun ordre réel ;
- parallélisme fournisseur identique ou plus prudent, jamais augmenté pour gagner artificiellement du temps.

## Budgets statiques

Avant la boucle V21.16 :

- quotidien : environ 10,1 min murales / 11 min facturables ;
- hebdomadaire : environ 25,2 min murales / 26 min facturables.

Budget statique V21.16.3 :

- quotidien Mon–Jeu : **3,8–5,5 min**, budget facturable 6 min, alerte 7 min ;
- hebdomadaire vendredi : **15,5–18,5 min**, budget facturable 19 min, alerte 22 min.

Sur les seuls jobs quotidien + hebdomadaire, l'arithmétique budgétaire passe de 304,26 à 186,96 min facturables par mois moyen, soit -38,6 %. Cette projection exclut le préopen autonome et les autres jobs hors contrat.

## Architecture quotidienne

### 1. Contexte lent hebdomadaire réutilisé

Le quotidien réutilise le dernier baseline complet ayant passé les quality gates du vendredi. Âge maximal : 8 jours. Le quotidien ne modifie pas la date de fraîcheur du dernier full refresh.

Si le baseline manque ou devient trop ancien, le système fail-safe force le collecteur complet LIVE historique avant de reconstruire un baseline valide.

### 2. Collecte marché rapide complète

L'univers complet reste traité chaque jour : 1 829 Actions et 102 ETF.

Le chemin normal conserve :

- OHLCV Yahoo incrémental sur 5 jours ;
- features OHLCV locales ;
- rotation sectorielle locale ;
- scénarios locaux ;
- facteurs décisionnels Actions locaux.

Les vagues lentes de contexte complet W04/W05/W06/W09 sont retirées du chemin quotidien normal et restent propriétaires du vendredi.

### 3. Handoff mémoire

Le bundle quotidien est mono-processus. Les DataFrames enrichis sont transmis directement de `daily_fast_collection` à `daily_tct_ct_runner`.

Dans le chemin planifié :

- pas d'écriture puis relecture des deux masters enrichis complets ;
- pas de réécriture quotidienne des parquets du baseline hebdomadaire ;
- pas d'exports sectoriels auxiliaires inutiles ;
- export TCT baseline compact conservant tous les champs nécessaires à la reconstruction/audit.

Le mode standalone garde les persistances historiques par défaut.

### 4. TCT exact limité au scope gouverné

Le baseline TCT reste calculé sur tout l'univers Action. Le calcul exact T1/T2 quotidien, plus coûteux, ne s'exécute que sur le Top 20 baseline respectant la couverture minimale déjà imposée par la gouvernance.

Le vendredi conserve l'évaluation exhaustive de recherche T1/T2.

### 5. Préchauffe sources sélectionnées

Un seed prioritaire de maximum 20 ISIN est préchauffé sur Boursorama/Investing pendant le refresh OHLCV Yahoo. Les fournisseurs sont distincts.

La préchauffe est non bloquante pour la logique financière et est jointe avant le gate source courant. Celui-ci reste responsable de compléter tous les nouveaux candidats.

### 6. Postmarket

Le quotidien conserve les deux fonctions opérationnelles :

- ledger OHLC PIT ;
- contexte catalyst/news postmarket.

La lineage PIT et le validator historique sont reportés au vendredi car ils ont une influence décisionnelle nulle.

Dans le moteur catalyst, la collecte news des candidats et le snapshot marché global sont chevauchés avec deux workers bornés. Le ledger OHLC reste séquentiel avant le catalyst pour préserver l'ordre d'état.

### 7. Dépendances GitHub allégées

Le job quotidien installe `requirements-daily-fast.txt` : la pile document/browser inutile au quotidien est exclue (`pypdf`, `playwright`, `openpyxl`, `python-docx`).

Le package général du projet n'est pas modifié.

## Architecture hebdomadaire

### 1. Full collection et qualité intactes

Le vendredi conserve :

- full live source refresh ;
- universe complet ;
- provenance ;
- quality gates ;
- Comité complet ;
- CI Word/Excel ;
- risk context ;
- rotation sectorielle ;
- fund flows ;
- validation/recherche TCT complète.

### 2. Préchauffe source Comité précédent

Jusqu'à 40 ISIN du dernier Comité sont préchauffés pendant la collecte complète. La tâche est jointe avant le gate source du Comité courant. Le gate courant complète toujours les nouveaux candidats.

### 3. WAVE09 masque WAVE10/WAVE11

Pendant les appels réseau FRED/GDELT de WAVE09, le système pré-calcule sur le snapshot exact post-WAVE08 :

- WAVE10 rotation sectorielle ;
- WAVE11 facteurs décisionnels Actions.

L'ordre d'application reste W09 → W10 → W11. WAVE10 a été audité comme indépendant des champs `funnel_*` produits par WAVE09.

### 4. Parallélisme après refresh et après Comité

Les branches indépendantes sont chevauchées sans partager d'écrivain d'état incompatible :

- après refresh : ETF structure, ETF MT cache reuse, Sector Rotation V2 ;
- après Comité : risk context, CI explainability, persistence du baseline Mon–Jeu.

### 5. Vendredi tactique sans recalcul

Le vendredi ne relance pas le runner quotidien TCT/CT. Il réutilise les décisions Comité courantes et le résultat V21.8 :

- réseau : 0 ;
- rescoring : 0 ;
- source gate supplémentaire : 0 ;
- recalcul entry/exit : 0.

### 6. Fin hebdomadaire parallèle

La fin du job contient cinq lanes :

1. TCT tactical puis full postmarket, sérialisés car ils partagent l'état TCT ;
2. Decision Brief ;
3. ETF Fund Flows ;
4. Criteria Governance ;
5. Identity Hydration diagnostic.

`identity_hydration` a été retiré du chemin séquentiel avant le bundle. Il ne pilote pas l'identité réellement utilisée par la collecte : l'overlay gouverné est appliqué directement par WAVE01 lors de la qualification Yahoo. Le worklist est donc publié en fin de chaîne, non bloquant, sans influence score/décision. Cette lane légère s'exécute dans un thread du processus parent : aucun nouvel interpréteur Python n'est démarré pour elle, tandis que les anciens modules de fin de chaîne qui nécessitent l'isolation conservent leurs sous-processus.

### 7. Sérialisation et installation

- les audits intermédiaires GitHub sont CSV/JSON compacts ;
- le dernier audit de collecte Excel reste publié ;
- les trois anciens workbooks raw non consommés sont supprimés ;
- le job installe `requirements-market-runtime.txt`, sans `pypdf`/`playwright` mais avec `openpyxl` et `python-docx` nécessaires au CI.

## Télémétrie et validation future

Le temps autoritatif sera `GITHUB_JOB_RUNTIME_V21_16`, couvrant préparation, cache restore, installation, traitement, cache save et upload artefact, hors file d'attente runner et checkout initial selon le contrat.

Les chronos Python internes ne servent qu'au diagnostic du chemin critique.

Critère de clôture d'exécution futur :

- un quotidien normal mesuré ;
- un vendredi normal mesuré ;
- aucune régression de données, critères, décisions ou livrables CI ;
- si une durée dépasse le seuil d'alerte, réouverture d'un audit **runtime-only** sans retuning du modèle.

## Ce qui n'a volontairement pas été optimisé davantage

Certaines pistes ont été rejetées pour préserver la robustesse :

- hausse agressive des batchs/requêtes Yahoo ;
- réduction de la fenêtre OHLCV ;
- parallélisation de deux vagues utilisant le même fournisseur ;
- vectorisation transversale du moteur central `decisions_from_scores` sans validation exécutable ;
- réduction du cache GitHub sans mesure de taille/temps ;
- suppression de données ou critères pour tenir un objectif de durée.

Le principe retenu est de masquer les latences et supprimer les duplications, jamais de sacrifier la qualité financière du processus.
