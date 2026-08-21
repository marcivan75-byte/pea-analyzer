# Cahier des charges complet — TCT V24.4.1

Date de référence : 21/08/2026  
Projet : PEA Analyzer  
Module : Actions PEA — Très Court Terme (TCT)  
Version du challenger : V24.4.1  
Production canonique : V21.8.1 inchangée

## 1. Objet du document

Ce cahier des charges décrit de façon normative le périmètre fonctionnel, les données, les calculs, les interfaces, les traitements, les sorties, les contrôles de causalité, la gouvernance, les tests et l'exploitation du module TCT V24.4.1.

Le TCT vise des décisions d'aide à l'entrée et à la sortie sur un horizon de quelques séances à environ une semaine. Il exploite une démarche quotidienne enrichie par des outils utilisés par les traders actifs, mais il ne constitue ni un moteur de day trading, ni un moteur d'exécution, ni un système de cotation quasi temps réel.

## 2. Objectifs métier

Le module doit :

1. sélectionner et hiérarchiser les opportunités Actions PEA pertinentes pour le TCT ;
2. améliorer la qualité du timing d'entrée sans augmenter artificiellement la fréquence de trading ;
3. détecter les signes de détérioration ou de failed breakout suffisamment tôt pour aider la décision de sortie ;
4. anticiper avant l'ouverture européenne les titres susceptibles de connaître les plus fortes amplitudes de la séance suivante ;
5. distinguer strictement le potentiel d'amplitude du biais directionnel ;
6. exploiter les news publiées après clôture et pendant la nuit de façon causale ;
7. utiliser le contexte mondial disponible avant l'ouverture sans mettre en place un flux temps réel ;
8. accumuler une preuve forward-PIT auditable avant toute promotion ;
9. rester compatible avec la production V21.8.1 sans modifier ses décisions tant que la validation n'est pas obtenue.

## 3. Hors périmètre

Sont interdits dans V24.4.1 :

- trading intraday ;
- barres 1m ou 5m ;
- polling continu ;
- carnet d'ordres Level 2 ;
- order flow temps réel ;
- spread live comme donnée obligatoire ;
- cotations extended-hours individuelles obligatoires des Actions PEA ;
- exécution d'ordres réels ;
- take-profit fixe opérationnel ;
- stop-loss fixe promu sans validation ;
- utilisation T1/T2 sur ETF ou hors ACTION TCT ;
- ouverture du holdout final ;
- retuning automatique avant maturité ;
- reconstruction historique synthétique de snapshots TCT qui n'existaient pas réellement au point dans le temps.

## 4. Architecture fonctionnelle

Le module est constitué de quatre couches séquentielles.

### 4.1 Couche A — Baseline TCT et timing T1/T2

Rôle : produire l'univers TCT et le signal timing exact en amont. Cette couche reste la source de vérité pour T1/T2.

Contraintes :
- T1/T2 = ACTION TCT uniquement ;
- aucun effet ETF ;
- aucun transfert automatique au CT ;
- règles anti-look-ahead existantes maintenues ;
- influence de toute expérimentation non validée = 0.

### 4.2 Couche B — V24.3.1 Daily/Weekly Trader Tools

Rôle : enrichir le diagnostic de timing, confluence, qualité d'entrée et risque de sortie à partir des seules données daily disponibles.

Entrées : OHLCV daily + candidat TCT + métadonnées déjà collectées.

Sorties principales : `entry_score`, `entry_state`, confirmations, `exit_risk_score`, `exit_state`, niveaux structurels et warnings.

### 4.3 Couche C — V24.4.1 Next-Session Catalyst Cycle

Rôle : construire deux snapshots discrets par jour ouvré, PREOPEN et POSTMARKET, afin de détecter les mouvements potentiellement importants de la prochaine séance.

Entrées : seed V24.3.1, news horodatées, contexte marché mondial ponctuel.

Sorties principales : `movement_potential_score`, `direction_bias_score`, couvertures, qualité des données, état catalyst et preuves news.

### 4.4 Couche D — PIT lineage et validation

Rôle : figer les prédictions, associer uniquement la première clôture future pertinente, vérifier la couverture de l'échantillon et mesurer la valeur incrémentale de V24.4.1 par rapport à la technique V24.3.1 seule.

## 5. Exigences de données

### 5.1 OHLCV quotidien

Source logique : cache Actions existant du projet.

Champs minimum :
- date/index temporel ;
- open ;
- high ;
- low ;
- close ;
- volume.

Exigences :
- données numériques normalisées ;
- dates triées ;
- doublons de date éliminés ;
- minimum 60 barres pour V24.3.1 ;
- aucune barre partielle du jour ne doit être utilisée avant la garde de clôture configurée.

### 5.2 Weekly

Le weekly est dérivé du daily par agrégation `W-FRI` : open premier, high maximum, low minimum, close dernier, volume somme.

Du lundi au jeudi, la semaine courante ne doit pas être assimilée à une semaine complète pour les indicateurs exigeant une semaine achevée. La position de la clôture dans la semaine en cours peut rester un contexte séparé.

### 5.3 News catalysts

Source actuelle : GDELT.

Conditions d'admission :
- nom de société nettoyé ;
- timestamp article parsable ;
- article strictement dans la fenêtre du snapshot ;
- titre non vide ;
- déduplication des titres normalisés ;
- aucune information postérieure au snapshot.

Une erreur réseau/source doit être distinguée d'une requête réussie sans article.

### 5.4 Contexte mondial

Téléchargement groupé ponctuel en intervalle `1d`, période technique 5 jours.

Séances terminées : S&P 500, Nasdaq Composite, Russell 2000, VIX, Nikkei, Euro Stoxx 50, CAC 40, DAX.

Contexte ponctuel : S&P 500 future, Nasdaq future, WTI, or, EUR/USD.

Aucun polling ni barres intraday.

### 5.5 Métadonnées candidat

Le seed peut exposer : ISIN, nom, ticker Yahoo, secteur, industrie, pays, capitalisation, date/résultats à venir, état TCT, qualité T1/T2, score entrée/sortie, ATR, range expansion et contexte news déjà collecté. Ces métadonnées n'autorisent pas le double comptage d'une même information dans plusieurs blocs de score.

## 6. Calculs V24.3.1

### 6.1 Indicateurs techniques

Le moteur doit calculer notamment :
- ATR14 ;
- range quotidien et expansion vs médiane antérieure ;
- RVOL daily vs médiane 20 jours antérieure ;
- accélération volume 5 vs 20 ;
- turnover et médiane 20 jours ;
- EMA9 / EMA20 et pente EMA9 ;
- momentum 5 et 20 jours ;
- breakout 20 jours ;
- breakout 55 jours ;
- mémoire du dernier breakout sur 5 séances ;
- retest ;
- failed breakout ;
- prix roulant pondéré par le volume 20/60 jours ;
- distance au prix pondéré 20 jours en ATR ;
- gap en pourcentage et en ATR ;
- qualité de clôture : CLV, corps, mèches ;
- pivots veille ;
- plus haut/bas/pivot semaine précédente ;
- tendance weekly 10 semaines ;
- momentum weekly 4 semaines ;
- trend efficiency 20 jours ;
- distribution sur 3 séances ;
- invalidation structurelle de recherche.

### 6.2 Score d'entrée

Poids : structure 20 %, volume/liquidité 15 %, price action 15 %, volatilité 15 %, momentum 15 %, weekly 15 %, prix pondéré volume 5 %.

Le score doit être accompagné d'une couverture. Les composants absents ne doivent pas être transformés en valeurs artificielles.

Correction audit V24.4.1 : un score structurel valide égal à zéro ne doit jamais être remplacé par le fallback 40 ; seul `None` déclenche le fallback.

### 6.3 Confluences

Confirmations admises : STRUCTURE, RVOL, VOLUME_ACCELERATION, VOLATILITY_EXPANSION, WEEKLY, PRICE_ACTION.

ENTRY_READY nécessite score >=70, au moins 2 confirmations et un trigger parmi structure/RVOL/expansion volatilité.

ENTRY_STRONG nécessite score >=80, au moins 3 confirmations et un trigger.

### 6.4 Gates d'entrée

Ordre de priorité : donnée insuffisante, liquidité, failed breakout/conflit entrée-sortie, conflit weekly, invalidation trop large, overextension/gap, puis ENTRY_STRONG/READY, sinon WAIT.

### 6.5 Sortie

Poids risque sortie : failed breakout 25 %, rupture tendance rapide 20 %, distribution volume 15 %, détérioration momentum 15 %, détérioration weekly 15 %, volatilité adverse 10 %.

`EXIT_RISK_HIGH_SHADOW` nécessite score >=70 et confirmation structurelle. `EXIT_WATCH_SHADOW` peut être déclenché par score >=50 ou signal structurel secondaire. Aucune vente automatique n'est autorisée.

## 7. Calculs V24.4.1

### 7.1 Fenêtres

PREOPEN : de la dernière véritable clôture daily disponible jusqu'au timestamp du snapshot du matin.

POSTMARKET : de la clôture européenne du jour au timestamp du snapshot du soir.

Les week-ends et jours fériés doivent être traités en s'ancrant sur la dernière date réellement observée, pas sur un simple jour ouvré théorique.

### 7.2 Classification news

La liste normative des événements et poids est définie dans `config/TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json`.

Règles audit V24.4.1 :
- une enquête générique n'est pas une fraude grave ;
- `FRAUD_INVESTIGATION` exige un contexte explicite fraude/comptable/pénal/réglementaire ;
- une acquisition générique conserve une magnitude élevée mais aucun biais directionnel automatique ;
- les événements non reconnus sont `OTHER_NEWS`.

### 7.3 Score de mouvement

`movement_potential_score` = agrégation pondérée de :
- news magnitude 45 % ;
- technical impulse 25 % ;
- global market shock 15 % ;
- known scheduled event proximity 15 %.

Le bloc événement planifié ne doit pas réutiliser le score news. Dans V24.4.1, il repose actuellement sur la proximité des résultats.

### 7.4 Score directionnel

`direction_bias_score` = :
- news direction 55 % ;
- technical direction 25 % ;
- inverse exit risk 10 % ;
- global risk-on 10 %.

### 7.5 Fail-closed couverture

La pondération disponible ne doit jamais être silencieusement renormalisée en alerte forte lorsque les sources dominantes manquent.

Règles :
- couverture mouvement >=70 % pour exposer un score de mouvement ;
- couverture direction >=70 % pour exposer un biais directionnel ;
- si couverture mouvement insuffisante : `DATA_DEGRADED_SHADOW` ;
- si couverture direction insuffisante : aucun état haussier/baissier ;
- une requête GDELT réussie avec zéro article fournit une observation news nulle et compte comme donnée observée.

### 7.6 États V24.4.1

`DATA_DEGRADED_SHADOW`, `NEWS_CONFLICT_SHADOW`, `UP_CATALYST_SHADOW`, `DOWN_CATALYST_SHADOW`, `VOLATILITY_ALERT_SHADOW`, `TECHNICAL_ONLY_SHADOW`, `NO_CATALYST_SHADOW`.

## 8. Contrats de sortie

### 8.1 Seed Daily/Weekly

Fichier : `state/tct_context/TCT_DAILY_TRADER_LATEST.csv`.

Il doit contenir au minimum l'identité candidat, `as_of_date`, `reference_close`, scores et états entrée/sortie, champs techniques nécessaires à V24.4.1 et métadonnées de contexte disponibles.

### 8.2 Snapshot catalyst

Fichier : `outputs/daily_tct_ct/TCT_NEXT_SESSION_CATALYST_V24_4_1.csv`.

Champs critiques : identité, phase, as_of, référence close, scores raw/final, couvertures, data_quality_state, état catalyst, éléments news, scores technique/global, fenêtres temporelles, snapshot key et flags de gouvernance.

### 8.3 Ledger catalyst

Fichier : `state/tct_context/TCT_V24_4_1_CATALYST_LEDGER.csv`.

Le premier snapshot pour une clé donnée doit être conservé. Les résultats futurs peuvent être ajoutés mais aucun champ prédictif historique ne doit être réécrit.

### 8.4 Ledger clôtures

Fichier : `state/tct_context/TCT_DAILY_CLOSE_LEDGER.csv`.

Il contient les clôtures daily observées depuis le cache local pour les candidats présents ou encore nécessaires à l'étiquetage.

## 9. PIT et anti-look-ahead

### 9.1 Epoch de preuve

Toute validation V24.4.1 est isolée de V24.4.0 : `V24.4.1_ONLY_NO_MIX_WITH_V24.4.0`.

### 9.2 Outcome

Pour une prédiction PREOPEN, l'outcome est la première clôture réellement observée dont la date est strictement supérieure à `as_of_date`. Une clôture J+2/J+3 ne peut pas être utilisée si J+1 est disponible dans le close-ledger.

### 9.3 Couverture snapshot

Les métriques du validateur ne reçoivent les labels que lorsque >=80 % des candidats du même snapshot disposent d'un véritable outcome. Cette règle réduit les biais liés aux données manquantes.

### 9.4 Empreinte

Algorithme : `TCT_PIT_SHA256_CANONICAL_V2`.

Le hash porte uniquement sur une liste fixe de champs prédictifs critiques. L'ajout d'une colonne non décisionnelle future ne modifie pas l'empreinte. Toute modification d'un champ décisionnel déjà figé doit déclencher un mismatch et l'arrêt fail-closed de la lineage.

### 9.5 Interdiction de faux replay

Il est interdit de reconstituer a posteriori un snapshot historique complet V24.4.1 à partir de données actuelles lorsque l'univers TCT/T1/T2 et ses métadonnées PIT historiques n'ont pas été réellement persistés à l'époque.

## 10. Validation statistique

Maturité minimale : 60 observations PREOPEN étiquetées, 20 ISIN distincts, 15 alertes fort potentiel, 20 appels directionnels, 15 séances.

Comparateur : V24.3.1 technique seul.

Critères principaux : amélioration Recall Top10 >=10 points, amélioration lift décile >=0,15, amélioration Spearman >=0,10 ; au moins 2 critères sur 3. Faux fort potentiel <=60 %.

Direction : hit rate >=55 % et absence de dégradation vs technique seule.

Même `RESEARCH_CRITERIA_MET` n'autorise pas une promotion automatique.

## 11. Orchestration

### 11.1 Workflow daily

`.github/workflows/committee_tct_ct_daily.yml`

Séquence pertinente : collecte/enrichissement → scoring canonique TCT/CT → V24.3.1 SHADOW → close-ledger V24.4.1 → publication/audit/cache.

### 11.2 Workflow catalysts

`.github/workflows/tct_next_session_context.yml`

Séquence : restore state → snapshot V24.4.1 → lineage fail-closed → validateur V24.4.1 → publication → save state.

Planification : PREOPEN 06:40 UTC ; POSTMARKET 21:15 UTC ; lundi-vendredi. La planification UTC implique une heure locale différente été/hiver, tout en restant avant ouverture pour PREOPEN et après clôtures principales pour POSTMARKET.

## 12. Modes dégradés et erreurs

Le module doit préférer l'absence de score à une confiance artificielle.

Cas principaux :
- seed manquant : aucun candidat ;
- seed PREOPEN trop ancien : aucun snapshot exploitable ;
- POSTMARKET sans seed de séance achevée : warning/aucun snapshot ;
- source news en erreur : couverture réduite, fail-closed ;
- contexte marché partiel : couverture reflétée dans le score ;
- fingerprint mismatch : exception et arrêt lineage ;
- outcome insuffisant : snapshot en attente, non transmis aux métriques ;
- échantillon statistique immature : verdict `NOT_MATURE_ACCUMULATING_PIT` / non évaluable.

## 13. Observabilité

Les workflows doivent publier des audits JSON et résumés Android lisibles. Les audits doivent exposer au minimum version, phase, timestamps, ancre seed, nombre de candidats, couverture, erreurs, chemins d'outputs, influence production nulle, état PIT et maturité.

Les artefacts GitHub sont temporaires ; le state PIT persistant reste la source de vérité pour l'accumulation prospective.

## 14. Sécurité et gouvernance

- Aucun secret ne doit être écrit dans les outputs ou le kit de publication.
- Les clés API sont lues depuis GitHub Secrets ou mécanismes existants.
- Les modules SHADOW n'ont aucun droit d'ordre.
- Aucun changement V24.4.1 ne doit modifier les poids production V21.8.1.
- Toute modification future des poids/seuils/logiques structurantes de V24.4.1 réouvre un chantier de développement et crée une nouvelle epoch de preuve si la sémantique des scores change.
- T1/T2 reste limité à ACTION TCT.

## 15. Tests d'acceptation

La Definition of Done technique exige :
- compilation de tous les modules/tests ;
- Ruff sans erreur ;
- audit statique sans finding HIGH ;
- intégrité référentielle/gouvernance verte ;
- suite pytest complète verte ;
- tests ciblés des corrections audit ;
- workflow daily pointant sur close-ledger 4.1 ;
- workflow catalyst pointant exclusivement sur runtime/lineage/validator 4.1 ;
- absence de 1m/5m dans le workflow catalyst ;
- poids des blocs égaux à 100 % ;
- influence production =0 ;
- holdout non ouvert.

## 16. Migration V24.4.0 vers V24.4.1

V24.4.0 est conservée comme version historique mais superseded pour les nouveaux snapshots. Les corrections changent la sémantique du score ; par conséquent :
- nouveau config 4.1 ;
- nouveau ledger predictions 4.1 ;
- nouveaux outputs/audits 4.1 ;
- nouvelles gates 4.1 identiques en exigence mais nouvelle epoch ;
- close-ledger daily partagé, car il contient des observations de prix indépendantes de la formule de score ;
- aucune concaténation des prédictions 4.0 et 4.1 pour la décision de maturité 4.1.

## 17. Inventaire de code

L'inventaire exact et versionné du kit est publié dans `docs/TCT_RELEASE_MANIFEST_V24_4_1.json`. Il couvre les décisions/baselines TCT nécessaires, features Daily/Weekly, sources catalysts, runtime, lineage, validation, configs, workflows, tests et documentation.

## 18. Critères de promotion futurs

Une promotion éventuelle exige une décision distincte après maturité, revue des slices, contrôle des pertes extrêmes, stabilité par régime/secteur, validation PIT/OOS et vérification de l'absence de détérioration de la baseline. Le holdout final ne doit être ouvert que dans un protocole explicitement autorisé.

## 19. Statut de livraison

V24.4.1 est destinée à devenir, après CI et fusion de la PR d'audit, la version TCT SHADOW de référence. Sa logique reste gelée pendant l'accumulation de preuves. La production V21.8.1 reste inchangée jusqu'à une future décision explicite de promotion.
