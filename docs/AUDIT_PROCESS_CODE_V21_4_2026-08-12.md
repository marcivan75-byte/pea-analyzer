# Audit process et code Python — PEA Analyzer V21.4

Date : 12 août 2026

## Conclusion

La V21.3 était fonctionnellement riche et ses workflows principaux passaient, mais plusieurs défauts de gouvernance et de simulation empêchaient de la considérer comme une architecture optimale pour mesurer une performance réelle. La V21.4 corrige ces défauts sans supprimer de critères ni transférer artificiellement les performances historiques aux nouveaux modèles.

**V21.4 = architecture optimale/hardened pour poursuivre la validation. Elle n'est pas présentée comme une pondération statistiquement optimale tant que les challengers n'ont pas passé leurs backtests PIT/OOS dédiés.**

## Périmètre audité

- collecte et fusion des données ;
- provenance et niveaux de preuve ;
- 1 829 Actions PEA / 633 critères ;
- 102 ETF / 268 champs ;
- Actions CT/MT/LT/Short/Top-Down ;
- TCT V24.1.8 + timing T1/T2 V24.1.7 ;
- ETF MT V20.8.1 référence et V20.8.2 challenger ;
- OR V1.1 ;
- rotation sectorielle / catch-up / distance 52 semaines ;
- Morningstar Actions ;
- objectifs de cours et dividendes >4 % ;
- Committee Master ;
- suivi des BUY et money management virtuel ;
- runner unifié ;
- CI et sécurité statique Python.

## Défauts critiques identifiés et corrigés

### C1 — Morningstar Actions était collecté mais rejeté par la fusion

Le loader émettait `validation_status=ATTRIBUTED`, alors que la fusion n'acceptait que `VALIDATED`, `ISIN_MATCHED` et `AUTO_MATCH`. Conséquence : les observations Morningstar pouvaient être mises en quarantaine au lieu d'alimenter le master.

**Correction V21.4 :** `ATTRIBUTED` est un statut accepté et chaque observation est enregistrée dans la provenance par champ.

### C2 — Qualité de source/fraîcheur pilotée par une métadonnée de ligne

La fusion utilisait `evidence_level` et `as_of_date` de la ligne entière pour arbitrer un champ individuel. Un niveau A appartenant à une donnée pouvait donc empêcher le remplacement d'une autre donnée de niveau C par une nouvelle donnée B.

**Correction V21.4 :** registre append-only de provenance `(ISIN, champ)` ; le dernier niveau de preuve et `as_of` du champ concerné sont utilisés pour le merge.

### C3 — L'Excel de collecte indiquait surtout des sources théoriques

La colonne source provenait du nom du champ, pas de l'observation réellement reçue.

**Correction V21.4 :** l'Excel distingue désormais `sources_reelles`, URL, niveau de preuve, dernier `as_of` et `source_theorique`. La provenance est persistée dans `state/provenance/` entre les runs GitHub.

### C4 — Biais de look-ahead dans le suivi des BUY

Le signal et le prix d'entrée virtuel étaient pris sur le même `last_close`. Un signal construit avec les données de clôture ne peut pas être exécuté rétroactivement à cette même clôture.

**Correction V21.4 :** `SIGNAL -> PENDING_ENTRY -> NEXT_RUN_OBSERVED_PRICE_AFTER_SIGNAL_DATE`. Aucun signal du jour ne peut être exécuté le jour même.

### C5 — Plusieurs horizons pouvaient ouvrir plusieurs positions sur le même ISIN

CT, MT et LT pouvaient chacun créer une position pour la même action, contournant le plafond de position instrument.

**Correction V21.4 :** une seule position par ISIN ; le signal le mieux noté devient l'horizon principal et les autres horizons favorables sont conservés comme `contributing_horizons`.

### C6 — Le suivi virtuel pouvait s'exécuter après un échec du Comité

Le runner était tolérant aux erreurs mais le tracker pouvait alors lire un ancien fichier `COMMITTEE_DECISIONS.csv` resté dans le workspace.

**Correction V21.4 :** performance = `SKIPPED_DEPENDENCY` sauf si `refresh=SUCCESS` et `committee=SUCCESS` dans le run courant.

## Défauts de gouvernance/modèle corrigés

### G1 — Bonus 52 semaines non backtesté pouvant créer un BUY

Une action WATCH à 76 pouvait recevoir +4 et devenir BUY à 80 uniquement grâce à un overlay non validé.

**Correction V21.4 :** le score challenger ajusté reste visible, mais un bonus positif non validé ne peut pas promouvoir la décision finale. Un malus négatif peut dégrader le risque.

### G2 — Pondérations Actions enrichies non backtestées utilisées comme décision principale

La V21.3 indiquait explicitement que les nouveaux poids n'avaient pas de performance historique attribuable, mais ces poids alimentaient néanmoins le score principal.

**Correction V21.4 : double piste.**

- **Référence Actions :** poids V21.0 pré-V21.3, gelés dans `V21_ACTIONS_REFERENCE_V21_0.json`.
- **Challenger Actions :** V21.4 avec Morningstar, rotation, catch-up, seuils >4 %, repondération dynamique.
- La décision finale Actions reste la référence jusqu'à validation PIT/OOS du challenger.
- Export : `ACTION_REFERENCE_VS_CHALLENGER_V21_4.csv` et classement sectoriel challenger séparé.

L'univers courant reste 1 829 Actions ; la piste de référence utilise donc les poids V21.0 sur le nouvel univers. Elle sert de **référence opérationnelle**, pas de reproduction à l'identique d'un backtest historique à 1 429 titres.

### G3 — Double comptage implicite objectif/dividende

`total_return_potential_score` réutilisait l'objectif de cours et le dividende déjà présents dans d'autres critères.

**Correction V21.4 :** le composite reste disponible pour audit mais son poids actif est supprimé. Il est remplacé à poids global constant par `target_upside_gt4_score`; `dividend_gt4_score` devient un vrai facteur seuil : toutes les valeurs <4 % sont à zéro, puis le score augmente au-delà du seuil.

### G4 — Transformation non linéaire mais monotone ne renforçant pas réellement le seuil

Comme les critères sont ensuite convertis en percentile, une transformation strictement monotone préserve presque le même rang.

**Correction V21.4 :** seuil explicite avec plateau sous 4 %, ce qui crée réellement une famille de priorité >4 % au lieu d'un simple changement d'échelle.

## Money management V21.4

- capital virtuel initial : 100 k€ ;
- aucun ordre réel ;
- prochaine observation après signal pour l'entrée ;
- 1 position maximum par ISIN ;
- position max 5 % ;
- exposition secteur max 20 % ;
- exposition totale max 80 % ;
- 20 positions max ;
- turnover journalier max 20 % ;
- budget de risque par position 0,75 % ;
- stops spécifiques par horizon ;
- drawdown throttle : 5 % -> 75 % du sizing, 10 % -> 50 %, 15 % -> aucune nouvelle position ;
- cohortes séparées par version de modèle ;
- une panne de données ne provoque pas seule une liquidation, mais interdit les nouvelles entrées non qualifiées.

Ces paramètres sont des règles de recherche/gouvernance, pas des paramètres prétendument optimisés par backtest.

## Architecture optimale V21.4

```text
REFERENTIEL MAITRE EXHAUSTIF
        |
        v
COLLECTE MULTI-SOURCES
        |
        +--> PROVENANCE PAR CHAMP / SOURCE / DATE / PREUVE
        |
        v
AUDIT DONNEES MISSING / PARTIAL / AVAILABLE
        |
        v
GATES QUALITE / COUVERTURE / IDENTITE / PIT
        |
        +------------------------------+
        |                              |
        v                              v
REFERENCE VALIDEE                 CHALLENGER ENRICHI
(V21.0 Actions,                  (V21.4 Actions,
 V20.8.1 ETF MT)                 V20.8.2 ETF MT, TCT/OR shadow)
        |                              |
        +--------------+---------------+
                       |
                       v
             COMPARAISON / ABLATION
                       |
                       v
          COMITE + CLASSEMENT SECTORIEL
                       |
                       v
     PERFORMANCE VIRTUELLE BIAS-SAFE PAR VERSION
                       |
                       v
       BACKTEST PIT -> OOS -> HOLDOUT -> PROMOTION
```

## CI Python V21.4

Le CI ne se limite plus à une liste manuelle de fichiers :

1. installation du projet avec dépendances de test ;
2. `compileall` de tout `src/` et `tests/` ;
3. Ruff ciblé sur erreurs Python/symboles critiques ;
4. audit AST de tout le dépôt ;
5. contrôles d'intégrité des référentiels et gouvernance ;
6. `pytest -q` sur toute la suite.

L'audit AST bloque notamment : `bare except`, `except: pass`, argument mutable par défaut. Les comparaisons `== None` sont recensées.

## Ce qui reste à optimiser statistiquement

La V21.4 ne doit pas recevoir artificiellement les performances des modèles antérieurs. Les travaux suivants restent nécessaires :

1. backtest PIT/OOS du challenger Actions V21.4 sur les 1 829 titres, avec comparaison V21.0 ;
2. ablations Morningstar / rotation / catch-up / 52w / target>4 / dividend>4 ;
3. validation ETF MT V20.8.2 contre le V20.8.1 strict ;
4. validation TCT V24.1.8 + T1/T2 stateful ;
5. backtest OR V1.1 ;
6. calibration du money management (stops, max positions, drawdown throttle, turnover) sur données PIT sans utiliser le holdout final pour le tuning ;
7. promotion d'un challenger uniquement s'il améliore OOS expectancy / drawdown / profit factor / stabilité sans dégrader excessivement la fréquence des signaux.

## Règle de promotion

Aucun challenger ne devient la nouvelle référence parce qu'il paraît plus intuitif ou produit un meilleur score instantané. La promotion exige :

- backtest PIT sans look-ahead ;
- validation OOS ;
- attribution marginale par bloc ;
- stabilité par période/régime ;
- coûts et turnover ;
- nombre de signaux suffisant ;
- holdout final utilisé une seule fois selon la gouvernance définie.
