# PIT V15 — contrat de données et protocole de qualification

Statut : **WIP=1 / qualification data uniquement**. Aucun backtest de performance V15 n'est autorisé tant que tous les quality gates ne sont pas prouvés par artefacts.

## 1. Pivot historique

- Clé principale historique : **ISIN**, jamais ticker seul.
- Mapping versionné obligatoire : `isin`, `ticker`, `valid_from`, `valid_to`, `delisted`, `last_trading_date`.
- Les titres radiés sont conservés dans l'historique pour éviter le survivorship bias.
- L'univers PEA est reconstitué à chaque `date_signal` avec `pea_eligible_of_record`.

## 2. Table PIT minimale

Clé logique unique : `(isin, date_signal, source)`.

Colonnes minimales :
- `ticker_pit`
- `isin`
- `date_signal`
- `knowledge_date`
- `publication_date`
- `target_mean_pit`
- `analyst_count`
- `eps_revision_4w`
- `eps_revision_13w`
- `source` (`P1_FACTSET`, `P2_EPS`, `P3_MODEL`)

Contraintes bloquantes :
- `knowledge_date <= date_signal`
- `publication_date <= date_signal`
- P1 : `analyst_count >= 3`
- aucun doublon `(isin, date_signal, source)`
- aucune reconstruction du passé avec une donnée courante

## 3. Priorité des sources

1. **P1_FACTSET** : objectif analystes daté PIT. Source principale pour le potentiel %.
2. **P2_EPS** : révisions d'estimations datées. Fallback de couverture ; une hausse EPS >5% sur 4 semaines peut produire un signal binaire, jamais un faux potentiel en %.
3. **P3_MODEL** : modèle interne PIT validé indépendamment, dernier recours. Ne compte pas dans la couverture P1/P2 nécessaire au GO.

Pour chaque source réelle utilisée, documenter fournisseur, granularité, lag observé, couverture PEA, licence et coût. Toute source sans horodatage vérifiable est interdite.

## 4. Quality gates

Le GO exige simultanément :
- couverture P1/P2 **>80%** des trades historiques ;
- lag médian **<=5 jours** ; NO-GO dur si **>7 jours** ;
- mapping ISIN historique valide **>95%** ;
- corrélation absolue potentiel PIT / potentiel actuel **<0,30** sur les observations communes ;
- univers benchmark PEA PIT reconstitué ;
- tests anti-look-ahead au vert.

Si un seul gate manque, statut = `NO_GO_DATA_COVERAGE` et `performance_backtest_authorized = False`.

## 5. Règle cible V15

Le seuil est verrouillé :
- `potentiel_pct > 20%` : éligible à l'étape suivante ;
- `potentiel_pct <= 20%` : rejet ;
- absence de vraie valeur PIT en % : N/A, jamais remplacée par un proxy technique.

## 6. Livrables attendus

- `audit_v15.csv` : une ligne par trade historique ;
- `coverage_v15.json` : couverture, lag, mapping, anti-fuite, statut GO/NO-GO ;
- `PIT_V15_QUALIFICATION.md` : synthèse lisible ;
- table historique ISIN/ticker ;
- snapshots hebdomadaires de l'univers PEA PIT ;
- preuve des tests unitaires anti-look-ahead ;
- documentation des sources et des lags.

## 7. Interdictions pendant cette phase

Pas d'optimisation technique, pas de tuning du seuil 20%, pas de sélection ML, pas de backtest de rendement. Le seul chantier actif est la qualification des données PIT.
