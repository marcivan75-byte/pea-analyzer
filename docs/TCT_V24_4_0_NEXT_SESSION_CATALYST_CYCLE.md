# TCT V24.4.0 — Next-Session Catalyst Cycle SHADOW

Date : 19/08/2026

## 1. Objet

V24.4.0 complète V24.3.1 pour mieux anticiper les mouvements importants de la prochaine séance tout en conservant le périmètre fonctionnel TCT : décision quotidienne, horizon de quelques séances à environ une semaine.

Il ne s'agit pas de day trading. Le module n'utilise ni barres 1m/5m, ni surveillance continue, ni carnet d'ordres, ni cotations extended-hours individuelles des actions PEA.

La production canonique reste V21.8.1. V24.3.1 et V24.4.0 restent `SHADOW_RESEARCH_ONLY` avec influence décision/score/sizing/stop/CT égale à zéro.

## 2. Cycle quotidien

### POSTMARKET

Snapshot unique après les clôtures Europe et États-Unis.

Objectif : détecter les annonces publiées après la séance européenne, les surprises de résultats/guidance, les opérations capitalistiques et les chocs globaux susceptibles d'affecter la prochaine séance.

Planification GitHub : `21:15 UTC` les jours ouvrés, soit après la clôture US quelle que soit la saison européenne.

### PREOPEN

Snapshot unique avant l'ouverture européenne.

Objectif : mettre à jour les catalyseurs apparus depuis la dernière clôture européenne et ajouter le contexte mondial disponible avant l'ouverture : marchés US clôturés, Japon clôturé, VIX, et un snapshot unique des futures S&P/Nasdaq, pétrole, or et EUR/USD.

Planification GitHub : `06:40 UTC` les jours ouvrés. Il ne s'agit pas d'un polling : un seul téléchargement groupé `1d` est effectué par snapshot.

## 3. Fenêtres temporelles et causalité

Les news sont filtrées sur leur timestamp de publication/observation. Une news sans timestamp exploitable est exclue du score.

- PREOPEN : fenêtre depuis la dernière vraie clôture daily connue dans le seed V24.3.1 jusqu'au snapshot PREOPEN.
- POSTMARKET : fenêtre depuis 17:30 Europe/Paris du jour jusqu'au snapshot POSTMARKET.

Le PREOPEN est ancré sur la date réelle du dernier cours daily. Cette règle couvre les week-ends et jours fériés boursiers sans supposer qu'un simple lundi-vendredi correspond à une séance ouverte.

Si le seed PREOPEN est trop ancien (>5 jours calendaires par défaut), le module se met en garde et n'effectue pas de recherche coûteuse. Si le POSTMARKET ne dispose pas d'un seed correspondant à la séance européenne du jour, le snapshot est également neutralisé.

## 4. News catalysts

Source initiale : GDELT, déjà présent dans le projet.

Les requêtes portent sur le nom exact de la société puis le classement d'événement est effectué après récupération, afin de ne pas exclure les articles européens par un filtre de langue trop restrictif.

Le classifieur couvre actuellement des formulations anglaises, françaises et allemandes usuelles pour :

- profit warning ;
- baisse / hausse de guidance ;
- résultats supérieurs / inférieurs aux attentes ;
- défaut, insolvabilité ;
- fraude, enquête ;
- approbation / rejet réglementaire ;
- acquisition, fusion, OPA ;
- contrat majeur / grosse commande ;
- augmentation de capital / dilution ;
- baisse ou suspension du dividende ;
- rachat d'actions / hausse du dividende ;
- upgrade / downgrade analyste ;
- départ du CEO.

Chaque événement possède deux dimensions séparées :

1. **Magnitude** : probabilité qu'il puisse générer une forte amplitude.
2. **Direction** : biais théorique positif ou négatif.

La fraîcheur et la corroboration par plusieurs sources renforcent modérément la magnitude, mais ne peuvent pas fabriquer artificiellement un catalyseur majeur.

## 5. Contexte mondial

### Séances clôturées

- S&P 500 ;
- Nasdaq ;
- Russell 2000 ;
- VIX ;
- Nikkei ;
- Euro Stoxx 50 ;
- CAC 40 ;
- DAX.

### Snapshot PREOPEN uniquement / contexte non assimilé à une séance cash clôturée

- future S&P 500 (`ES=F`) ;
- future Nasdaq (`NQ=F`) ;
- pétrole ;
- or ;
- EUR/USD.

Les futures US ne représentent qu'un overlay plafonné à 20 % du contexte risk-on PREOPEN. Ils ne remplacent ni la tendance daily ni les news propres au titre.

Hong Kong et Shanghai sont volontairement exclus du score central tant que leur séance n'est pas nécessairement terminée au moment du snapshot PREOPEN.

## 6. Candidats analysés

V24.4.0 ne rescane pas l'ensemble des actions en temps réel. Il part du seed V24.3.1 et retient au maximum 60 candidats selon :

- score d'entrée TCT ;
- risque de sortie ;
- catalyst/news score déjà collecté ;
- proximité des résultats ;
- volatilité ATR.

Cela limite les appels news tout en concentrant le budget de données sur les titres ayant déjà une justification TCT ou un risque de mouvement.

## 7. Deux scores séparés

### `movement_potential_score`

Objectif : classer les actions susceptibles d'avoir les plus fortes amplitudes, que le mouvement soit haussier ou baissier.

Baseline SHADOW pré-enregistrée :

- magnitude news : 45 % ;
- impulsion technique V24.3.1 : 25 % ;
- choc global : 15 % ;
- proximité d'un événement connu : 15 %.

### `direction_bias_score`

Objectif : estimer le sens uniquement lorsque les informations sont suffisamment cohérentes.

Baseline SHADOW :

- direction news : 55 % ;
- direction technique : 25 % ;
- risque de sortie inversé : 10 % ;
- contexte risk-on global : 10 %.

La direction ne doit jamais être confondue avec l'amplitude : une action peut avoir un très fort `movement_potential_score` et un biais directionnel faible.

## 8. États SHADOW

- `UP_CATALYST_SHADOW` ;
- `DOWN_CATALYST_SHADOW` ;
- `VOLATILITY_ALERT_SHADOW` ;
- `NEWS_CONFLICT_SHADOW` ;
- `TECHNICAL_ONLY_SHADOW` ;
- `NO_CATALYST_SHADOW`.

`NEWS_CONFLICT_SHADOW` est volontaire : une news fortement négative face à une structure technique haussière ne doit pas être transformée arbitrairement en signal d'achat ou de vente.

## 9. Ledger PIT et validation des prévisions

Le premier snapshot PREOPEN de chaque titre/jour est figé dans :

`state/tct_context/TCT_V24_4_0_CATALYST_LEDGER.csv`

Un rerun manuel ne remplace pas cette première observation.

Après une clôture daily ultérieure, le ledger ajoute uniquement comme labels de résultat :

- rendement close-to-close réalisé ;
- amplitude absolue ;
- justesse du sens lorsque le biais initial était suffisamment fort ;
- rang d'amplitude réelle dans le snapshot.

Ces labels sont calculés après l'événement et ne peuvent jamais être réinjectés dans le score antérieur.

## 10. Critères de maturité pré-enregistrés

Fichier : `config/TCT_V24_4_0_VALIDATION_GATES.json`.

Aucune repondération n'est autorisée avant au minimum :

- 60 observations PREOPEN étiquetées ;
- 20 ISIN distincts ;
- 15 prédictions `high movement potential` ;
- 20 appels directionnels ;
- 15 séances observées.

Les métriques prioritaires seront notamment : rappel des Top 10 mouvements absolus, lift du décile supérieur, corrélation de rang score/amplitude réelle, taux de réussite directionnel et taux de faux positifs à fort potentiel.

Atteindre ces seuils autorise une revue de recherche, jamais une promotion automatique.

## 11. Coût et fréquence

Le design vise expressément un coût limité :

- 2 snapshots maximum par jour ouvré ;
- aucun polling ;
- aucun 1m/5m ;
- aucun flux Level 2 ;
- aucun abonnement de données nouveau ;
- 1 téléchargement groupé `1d` de contexte mondial par snapshot ;
- appels GDELT limités au Top 60 candidats TCT ;
- réutilisation du seed daily V24.3.1 et des métadonnées déjà collectées.

## 12. Gouvernance

- production canonique : V21.8.1 ;
- V24.4.0 : `SHADOW_RESEARCH_ONLY` ;
- influence décision = 0 ;
- influence score = 0 ;
- influence sizing = 0 ;
- influence stop = 0 ;
- influence CT = 0 ;
- aucun ordre réel ;
- aucun TP fixe ;
- aucun SL fixe promu ;
- holdout final fermé ;
- retuning avant maturité interdit ;
- transfert au CT interdit sans validation séparée.

V24.4.0 enrichit donc la lecture TCT du lendemain ; il ne transforme pas le système en stratégie de day trading.
