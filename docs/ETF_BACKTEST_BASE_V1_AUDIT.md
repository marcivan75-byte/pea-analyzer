# ETF Backtest Base V1 — audit, corrections et gouvernance

Date: 2026-09-03

## Objet

Créer une fondation de données dédiée aux backtests ETF MT/GROK sans modifier le cache quotidien opérationnel ni les règles de décision ETF MT.

## Défauts constatés avant correction

1. Le cache de marché opérationnel est ancré à 2023-01-01 et glissant sur 60 mois. Il est adapté au run quotidien mais pas à un backtest long 2010/2012–2026.
2. La collecte opérationnelle utilise `auto_adjust=true`, ce qui est pratique pour les features mais ne conserve pas simultanément le prix brut, l'Adjusted Close et les événements de corporate action.
3. Le référentiel maître courant décrit l'univers actuel. Il ne fournit pas pour chaque ETF une date historique de présence dans l'univers PEA ni une période d'éligibilité PEA PIT complète. Utiliser la liste 2026 rétrospectivement crée un risque de survivorship bias.
4. Une base de backtest doit auditer explicitement les volumes, les invariants OHLC, les doublons, les trous de calendrier et la profondeur historique instrument par instrument.
5. Les données qualitatives actuelles sont utiles au CI mais ne doivent pas être injectées rétroactivement dans un backtest avant d'avoir un `as_of` historique propre.

## Corrections V1

- Nouvelle configuration isolée `config/ETF_BACKTEST_BASE_V1.json`.
- Historique cible à partir du 2010-01-01, non glissant et append-only.
- Collecte `auto_adjust=false` avec conservation séparée de `open/high/low/close`, `adj_close`, `volume`, `dividends`, `stock_splits`.
- Clé primaire instrument = ISIN; ticker Yahoo utilisé comme identifiant de collecte et non comme identité durable.
- Conservation de la date de séance locale de la place boursière: aucune conversion UTC susceptible de déplacer une séance quotidienne au jour calendaire précédent.
- Contrôles de qualité par ETF: couverture Close, couverture Volume, volumes nuls/négatifs, prix non positifs, invariants OHLC, doublons, trous de séances, disponibilité d'au moins 756 séances pour les features 3 ans.
- Deux certifications distinctes: `ETF_MT_CLOSE_ONLY` pour les backtests MT fondés sur Close/Adj Close/Volume et `FULL_OHLC_RESEARCH` pour les stratégies utilisant Open/High/Low.
- Génération d'un manifeste avec hash SHA-256 des fichiers de contrôle.
- Génération d'une table `ETF_PIT_MEMBERSHIP.csv` qui force l'explicitation des dates de présence et d'éligibilité PEA.
- Tant que ces dates PIT ne sont pas complétées, `promotion_eligible=false` par construction.
- Une reconstruction basée uniquement sur l'univers courant reste `RESEARCH_ONLY_CURRENT_UNIVERSE_RECONSTRUCTION` et ne peut pas être présentée comme OOS de promotion.

## Résultats de l'audit exhaustif du 2026-09-03

Audit live sur les 102 ETF du référentiel courant, avec collecte quotidienne demandée à partir du 2010-01-01:

- instruments demandés: **102**;
- instruments construits avec historique: **102 / 102**;
- échecs de mapping ticker: **0**;
- échecs de téléchargement: **0**;
- séries validant la qualité Close/Adj Close/Volume: **101 / 102**;
- séries disposant d'au moins 756 séances: **69 / 102**;
- séries immédiatement utilisables pour le profil ETF MT close-only: **68 / 102**;
- séries validant aussi le profil OHLC intégral strict: **28 / 102**.

Les écarts OHLC observés sur certaines séries Yahoo ne sont donc pas masqués: ils bloquent le profil `FULL_OHLC_RESEARCH`, mais ne doivent pas invalider artificiellement un backtest ETF MT historiquement fondé sur Close/Volume si ces colonnes passent leurs propres contrôles.

Un instrument avec profondeur MT suffisante reste bloqué par le contrôle de continuité: `GRE.PA` / ISIN `FR0010405431` présente un trou maximal observé de 23 jours ouvrés dans la source. Il doit être contrôlé par une source de secours avant d'être admis dans l'échantillon MT historique certifié.

La base reste volontairement `promotion_eligible=false`: les 102 instruments proviennent encore du référentiel courant et les périodes historiques d'appartenance/éligibilité PEA ne sont pas complètes.

## Données qualitatives / structurelles

Les champs qualitatifs et structurels (Morningstar, TER, AUM, risque, réplication, benchmark, dividende, diversification, etc.) restent dans le référentiel maître. Pour un backtest fiable, chaque valeur utilisée devra être matérialisée sous forme historique avec au minimum:

- `isin`
- `field_name`
- `value`
- `valid_from`
- `valid_to` ou statut courant
- `observed_at`
- `source`
- `source_evidence`

Une valeur qualitative connue en 2026 ne doit jamais être appliquée automatiquement à une date 2018 ou 2022.

## Règles de fiabilité avant promotion d'un nouveau modèle

Une simulation peut servir à l'exploration avant complétion PIT, mais elle doit être étiquetée diagnostic. Pour devenir promotion-eligible, il faut au minimum:

1. dates de présence dans l'univers pour chaque instrument;
2. dates d'éligibilité PEA historiques;
3. historique OHLCV complet et audité;
4. événements de splits/dividendes conservés;
5. ticker aliases historiques lorsque nécessaire;
6. absence d'utilisation d'information postérieure au signal;
7. entrée au plus tôt sur la première séance strictement postérieure au signal;
8. périodes de validation/OOS verrouillées avant lecture des résultats;
9. absence de réoptimisation sur le holdout;
10. attribution séparée des données quantitatives et qualitatives.

## Ce que cette V1 ne prétend pas résoudre automatiquement

Les dates historiques d'éligibilité PEA et les ETF disparus/fusionnés ne peuvent pas être déduits de façon fiable de la seule liste actuelle. La V1 bloque donc explicitement la promotion tant que cette table PIT n'est pas documentée. Ce blocage est volontaire: il empêche de transformer une base contemporaine en faux historique sans survivorship bias.
