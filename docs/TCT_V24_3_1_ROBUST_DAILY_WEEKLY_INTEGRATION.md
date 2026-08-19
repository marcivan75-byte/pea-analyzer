# TCT V24.3.1 — Robust Daily/Weekly Trader Tools SHADOW

Date : 19/08/2026

## Statut

V24.3.1 améliore V24.3.0 sans changer le besoin fonctionnel : TCT sur données quotidiennes, horizon de quelques séances à environ une semaine. Il ne s'agit pas de day trading.

Cette note complète et supersède, pour le runtime TCT challenger, la section V24.3.0 du process de référence V21.8.1. La production canonique reste V21.8.1 ; V24.3.1 reste SHADOW_RESEARCH_ONLY.

Influence décision/score/sizing/stop/CT = 0. Aucun ordre réel. Holdout final fermé. Aucun take-profit fixe ni stop-loss fixe n'est promu.

## Données

- OHLCV daily uniquement ;
- weekly dérivé localement du daily ;
- aucun 1m/5m ;
- aucun quasi temps réel ;
- aucun carnet/order flow/spread live ;
- aucun fournisseur ou abonnement supplémentaire ;
- réutilisation exclusive de `data/cache/actions`.

Un run manuel avant 18 h heure de Paris ne peut pas exploiter la bougie quotidienne du jour si elle est encore potentiellement incomplète. Cette bougie est différée jusqu'à une exécution post-clôture.

## Améliorations V24.3.1

### 1. Weekly causal renforcé

La semaine en cours n'est plus assimilée à une semaine terminée du lundi au jeudi. La tendance 10 semaines et le momentum 4 semaines utilisent les semaines terminées. La semaine en cours reste disponible séparément comme contexte.

### 2. Mémoire du breakout

Le moteur conserve, sur la fenêtre de retest, le dernier niveau de breakout 20/55 jours. Il peut donc distinguer :

- breakout récent encore valide ;
- retest réussi ;
- failed breakout ;
- ancien niveau devenu invalidation structurelle.

Cette correction évite de perdre le contexte dès le lendemain de la cassure.

### 3. Confluence obligatoire avant entrée SHADOW

Un score élevé ne suffit plus. Les confirmations possibles sont :

- structure breakout/retest ;
- RVOL quotidien ;
- accélération du volume ;
- expansion après compression ;
- alignement weekly ;
- clôture journalière forte.

`ENTRY_READY_SHADOW` exige au moins 2 confirmations et un trigger structure/volume/volatilité. `ENTRY_STRONG_SHADOW` exige au moins 3 confirmations.

### 4. Gates anti-faux positifs

Même avec un score élevé, l'entrée est bloquée ou différée si :

- failed breakout ;
- risque de sortie déjà >= 50 ;
- weekly fortement adverse ;
- invalidation structurelle à plus de 7 % ;
- extension excessive vs prix pondéré volume 20 jours ;
- gap excessif en ATR ;
- liquidité quotidienne insuffisante.

États supplémentaires : `ENTRY_CONFLICT_SHADOW`, `WEEKLY_CONFLICT_SHADOW`, `WAIT_RISK_SHADOW`.

### 5. Sorties plus robustes

Le score de risque de sortie est conservé mais `EXIT_RISK_HIGH_SHADOW` exige désormais une confirmation structurelle :

- failed breakout ; ou
- clôture sous le plus bas de la veille ; ou
- au moins 2 journées de distribution sur les 3 dernières séances.

`EXIT_WATCH_SHADOW` peut également être déclenché par une forte mèche haute avec volume ou une clôture sous le plus bas précédent.

### 6. Qualité de tendance

Un ratio d'efficacité de tendance sur 20 séances est publié. Il permet de différencier une progression directionnelle d'un parcours erratique, sans introduire de donnée supplémentaire.

### 7. Accélération du volume corrigée

Le ratio 5 jours / 20 jours intègre désormais la séance courante dans la moyenne 5 jours, tandis que la référence 20 jours reste antérieure à la décision. La mesure devient cohérente avec une décision prise après clôture.

## Seuils pré-enregistrés V24.3.1

- couverture entrée minimale : 85 % ;
- confirmations ENTRY_READY : 2 ;
- confirmations ENTRY_STRONG : 3 ;
- weekly aligné : score >= 65 ;
- weekly adverse : score < 35 ;
- risque de sortie incompatible avec entrée : >= 50 ;
- distribution structurelle : 2 jours sur 3 ;
- plafond d'invalidation structurelle : 7 % ;
- turnover médian minimum de recherche : 500 k€.

Ces seuils sont SHADOW. Ils ne constituent pas une preuve de performance et ne peuvent être promus sans validation PIT/OOS dédiée.

## Validation avant intégration

La PR doit obligatoirement valider :

1. compilation de tous les modules ;
2. Ruff ;
3. audit statique ;
4. intégrité référentielle/gouvernance ;
5. suite pytest complète ;
6. non-régression ETF MT ;
7. tests V24.3.1 spécifiques : semaine partielle, mémoire breakout, failed breakout, bougie courante différée, confluence et absence d'intraday.

Après CI verte, V24.3.1 devient le challenger TCT quotidien actif dans le workflow. V21.8.1 reste la production canonique et V24.3.0 reste disponible uniquement comme baseline de comparaison V24.3.x.
