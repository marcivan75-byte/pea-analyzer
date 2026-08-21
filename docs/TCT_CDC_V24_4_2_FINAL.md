# Cahier des charges TCT V24.4.2 — FINAL SHADOW

Date : 21/08/2026  
Projet : PEA Analyzer  
Module : Actions PEA — Très Court Terme  
Production : V21.8.1 inchangée  
Challenger : V24.4.2 SHADOW

La production canonique V21.8.1 reste inchangée pendant toute la validation de V24.4.2.

## 1. Règle documentaire

Le présent CDC définit **fonctions, interfaces, flux, exigences, modes dégradés, contrôles et critères d'acceptation**. Il ne constitue pas une seconde source de valeurs numériques. Toutes les pondérations, seuils, quotas, budgets, gates et définitions quantitatives sont normatifs dans `docs/TCT_REFERENTIEL_V24_4_2_FINAL.md` et dans les JSON V24.4.2 correspondants.

Cette séparation corrige le risque de divergence CDC/Référentiel identifié par l'audit externe.

## 2. Finalité

Le TCT doit aider à sélectionner et temporiser des positions ACTION PEA de quelques séances à environ une semaine. V24.4.2 enrichit le process quotidien par des outils de traders actifs sans devenir intraday : structure daily/weekly, catalyseurs après clôture/overnight, contexte mondial ponctuel, ranking d'amplitude prochaine séance, diagnostic directionnel et preuve PIT causale.

Le système reste un outil d'aide à la décision ; aucun module V24.4.2 ne possède d'autorité d'ordre.

## 3. Architecture obligatoire

### Couche A — Baseline TCT/T1-T2

La baseline existante détermine l'univers et le timing T1/T2. T1/T2 est limité à ACTION TCT. Les couches aval ne doivent pas recréer un univers parallèle ni propager T1/T2 vers ETF/CT.

### Couche B — Daily/Weekly V24.3.1

Entrée : cache OHLCV daily et métadonnées existantes. Weekly calculé localement. Sortie : scores/états entrée-sortie, confirmations, niveaux structurels, volatilité, liquidité et seed persistant.

### Couche C — Next-Session V24.4.2

Deux phases discrètes seulement : PREOPEN et POSTMARKET. Le moteur sélectionne un ensemble borné de candidats selon le référentiel, récupère les news de la fenêtre, photographie le contexte global `1d`, calcule potentiel de mouvement et biais directionnel, puis persiste le premier snapshot de chaque clé PIT.

### Couche D — OHLC lineage / validation

Le process daily persiste les OHLC des séances terminées depuis le cache existant. La lineage associe à un PREOPEN la première séance future observée, calcule les labels multi-mesures et n'expose au validateur que les snapshots suffisamment couverts. Le validateur compare V24.4.2 à V24.3.1 et mesure stabilité et qualité.

## 4. Contrats de données

### 4.1 Daily seed

Fichier : `state/tct_context/TCT_DAILY_TRADER_LATEST.csv`.

Doit fournir l'identité candidat, date de référence, dernier close complet, états/scores V24.3.1, paramètres techniques nécessaires au scoring et les métadonnées disponibles. Une colonne absente doit produire donnée manquante ou axe nul documenté, jamais une exception ou une confiance inventée.

### 4.2 News

Chaque article admis doit comporter un titre et un timestamp parsable strictement inclus dans la fenêtre PIT. Les titres sont dédupliqués. Le moteur distingue explicitement : requête réussie sans article ; erreur fournisseur ; arrêt par budget ; résultat provenant du cache.

Les patterns métier sont configurés hors code. Le classifieur doit gérer les négations sensibles et produire une confiance de match. Le golden-set est un contrat de non-régression.

### 4.3 Contexte global

Un unique téléchargement groupé `1d` par snapshot est permis. Les marchés et pondérations sont définis dans le Référentiel. Les valeurs européennes et VIX nécessaires aux slices de stabilité doivent être persistées avec chaque prédiction.

### 4.4 Ledger OHLC

Fichier V24.4.2 : `state/tct_context/TCT_DAILY_OHLC_LEDGER.csv`.

Champs minimum : date, ISIN, ticker, open, high, low, close, horodatage d'observation et provenance locale. Aucun téléchargement réseau n'est nécessaire pour ce ledger : il réutilise le cache daily.

### 4.5 Ledger predictions

Fichier : `state/tct_context/TCT_V24_4_2_CATALYST_LEDGER.csv`.

Le premier snapshot de chaque clé PIT est immuable sur ses champs prédictifs. Les colonnes d'outcome sont seules enrichies après la séance future.

## 5. Sélection des candidats

Le sélecteur doit :

1. calculer les axes normalisés décrits dans le Référentiel ;
2. appliquer les quotas multi-axes ;
3. empêcher qu'un même candidat consomme plusieurs quotas ;
4. compléter les places restantes par priorité composite ;
5. publier rang, score de priorité et raisons ;
6. fonctionner même si certaines colonnes facultatives sont absentes ;
7. ne jamais sélectionner plus que la limite normative.

Toute évolution de formule/quotas change la sémantique du snapshot et exige une nouvelle epoch de preuve.

## 6. Classification catalysts

Le moteur doit reconnaître le catalogue PEA du Référentiel en anglais/français et, lorsque configuré, en allemand. Les patterns vivent dans le JSON actif. Un terme négatif nié explicitement ne doit pas déclencher l'événement sévère correspondant.

La confiance de match sert à atténuer les correspondances faibles ; elle ne doit jamais augmenter la magnitude au-delà de la valeur de l'événement. Un événement générique dont le sens dépend du rôle de l'émetteur conserve une direction neutre tant que le rôle n'est pas résolu.

## 7. Cache news et performance

Le cache est strictement lié à la requête et à la fenêtre exacte ; il ne peut pas réutiliser une réponse d'une autre fenêtre comme si elle avait été observée à l'époque. TTL et budgets sont ceux du Référentiel.

Le fetch fonctionne avec un parallélisme borné et des vagues. L'audit doit exposer : nombre demandé/terminé, erreurs, cache hits, workers, p50/p95, temps total, budget et état circuit-breaker.

La seconde source news demeure soumise à une qualification spécifique : timestamps, pertinence, duplication, latence et provenance. Son activation doit rester fail-soft et ne doit jamais masquer un échec de la source primaire.

## 8. Budget d'exécution

Les SLA PREOPEN/POSTMARKET sont ceux du Référentiel. Le runtime mesure le temps réel. Le bloc news reçoit une fraction bornée du budget. En épuisement, les requêtes restantes sont marquées circuit-breaker ; le système termine avec une qualité dégradée et garde l'influence production à zéro.

Le timeout GitHub Actions est une sécurité technique supérieure au SLA ; il ne remplace pas le budget métier interne.

## 9. Moteur d'exécution

Le runner doit utiliser **injection de dépendances explicite** : config, version, fenêtre, sélection, scoring et source news sont passés au moteur. Il est interdit à une version de modifier les globals d'une autre version par monkey-patching.

Le runner V24.4.1 historique doit lui-même être importable sans muter le runner V24.4.0.

V24.4.2 ne doit pas pré-étiqueter ses outcomes à partir d'un seed de clôture. Les labels proviennent exclusivement de la lineage OHLC dédiée.

## 10. Scoring

Les formules et valeurs numériques sont celles du Référentiel. Exigences fonctionnelles :

- potentiel de mouvement et direction restent deux dimensions distinctes ;
- même information ne peut pas alimenter deux blocs par double comptage ;
- source dominante manquante réduit la couverture ;
- score final absent si couverture minimale non atteinte ;
- état directionnel interdit si couverture direction insuffisante ;
- `NEWS_CONFLICT_SHADOW` reste prioritaire lorsque news et technique sont opposées avec intensité suffisante ;
- `TECHNICAL_ONLY_SHADOW` peut afficher un flag actionnable SHADOW selon le Référentiel, sans devenir ordre.

## 11. PIT / anti-look-ahead

La lineage V24.4.2 doit :

- utiliser la première séance future réellement observée ;
- ne jamais sauter J+1 au profit de J+2/J+3 lorsque J+1 existe ;
- calculer tous les labels OHLC définis dans le Référentiel ;
- appliquer le seuil de couverture snapshot ;
- recalculer l'empreinte des champs prédictifs et fail-closed sur mutation ;
- isoler strictement l'epoch V24.4.2 ;
- refuser tout faux replay historique reconstituant aujourd'hui un univers TCT qui n'avait pas été persisté au point dans le temps.

## 12. Validation

Le validateur cible prioritairement l'amplitude de séance, pas seulement close-to-close. Il doit publier : ranking Top10, lift, Spearman, précision/recall du mouvement significatif, direction, faux positifs, gap, range, MAE et slices.

Les slices obligatoires incluent secteur, régime, VIX lorsque l'échantillon permet des quintiles fiables, type de news, état entrée, proximité earnings et raison de sélection.

Une maturité ou un PASS recherche ne confère jamais d'autorité de promotion.

## 13. Calibration offline

Le script de calibration est hors workflows. Avant maturité, il doit refuser de produire une pondération candidate. Après maturité, il peut produire un fichier de recherche mais ne doit : ni modifier le JSON actif, ni écrire dans un référentiel de production, ni ouvrir le holdout, ni s'auto-promouvoir.

Une adoption future d'un poids candidat nécessite pré-enregistrement, nouvelle epoch et comparaison hors échantillon.

## 14. Sorties Android

Le résumé next-session doit contenir au minimum : phase, heure, qualité/couverture news, runtime/budget, contexte global, Top5 potentiel, biais haussiers qualifiés, conflits news/tech, EXIT_RISK_HIGH du seed et nombre de lignes dégradées.

Le résumé PIT doit afficher maturité, verdict recherche, statut stabilité, progression de l'échantillon et principales métriques amplitude/gap/range.

## 15. Workflows

Le workflow daily conserve la collecte, le scoring canonique TCT/CT et V24.3.1 ; il ajoute uniquement la persistance OHLC V24.4.2 depuis le cache daily.

Le workflow catalyst suit : restore state → snapshot V24.4.2 → lineage OHLC V24.4.2 → validation PIT V24.4.2 → publication → sauvegarde state.

Aucune étape V24.4.2 ne doit faire de 1m/5m ou exécuter un ordre réel.

## 16. Definition of Done

Avant fusion :

- `compileall` complet ;
- Ruff sans erreur ;
- audit statique sans finding HIGH ;
- intégrité référentielle/gouvernance verte ;
- `pytest` complet vert ;
- golden-set métier vert ;
- tests de négation/classification ;
- tests quota/rank_reason ;
- tests OHLC J+1 multi-label ;
- tests V24.4.1 sans monkey-patch ;
- tests workflow V24.4.2 ;
- poids/gates/configs cohérents ;
- production V21.8.1 inchangée ;
- holdout fermé.

## 17. Statut des points différés

Les recommandations non activées sans preuve suffisante restent explicitement documentées et nécessitent une nouvelle qualification avant toute promotion.

Un classifieur NLP parallèle peut également être étudié ultérieurement mais ne remplace pas la lexicale V24.4.2 avant comparaison PIT.
