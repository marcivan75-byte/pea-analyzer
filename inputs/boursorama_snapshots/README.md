# Boursorama — intake attribué haute priorité

Ce répertoire alimente le process PEA avec des données Boursorama à forte valeur décisionnelle, sans récupération automatisée du site.

## Règle d'accès

Le runtime GitHub ne lance aucun robot, navigateur ni scraper contre Boursorama. Il ingère uniquement des pages ou tableaux que l'utilisateur (ou une source autorisée) a enregistrés/exportés et déposés ici. Chaque donnée conservée garde son URL Boursorama, sa date et le fournisseur sous-jacent lorsqu'il est identifiable (notamment FactSet, Cofisem, TEC ou Morningstar/Sustainalytics).

## Formats pris en charge

- `.html` / `.htm` : page Boursorama enregistrée depuis le navigateur ;
- `.csv` / `.xlsx` / `.xlsm` : tableau attribué contenant obligatoirement `isin`, `source_url`, `source_date`.

Sous-répertoires recommandés :

- `actions/consensus/` : fiches `.../cours/consensus/...` ;
- `actions/profile/` : fiches `.../cours/societe/profil/...` ;
- `actions/key_figures/` : fiches `.../cours/societe/chiffres-cles/...` ;
- `actions/technical/` : pages `.../cours/analyses/...` ;
- `bulk/consensus/` : pages consensus/recommandations multi-actions ;
- `bulk/per/` : pages `.../bourse/actions/palmares/per/...` ;
- `bulk/dividends/` : pages `.../bourse/actions/palmares/dividendes/...` ;
- `bulk/extremes/` : pages `.../bourse/actions/palmares/extremes-annuels/...` ;
- `bulk/calendars/` : pages Boursorama de calendrier sociétés cotées et dividendes ;
- `etf/search/` : pages de recherche/palmarès ETF ;
- `etf/detail/` : pages individuelles ETF, performances/risques et composition.

## Actions — consensus et prévisions

Selon le contenu de la capture :

- recommandation/consensus et historique 3m / 2m / 1m / 7j / actuel ;
- nombre d'analystes ;
- note médiane et évolution 1 mois ;
- objectif de cours et potentiel ;
- BPA/EPS réalisé et prévisionnel ;
- PER réalisé, PER forward FactSet et PER estimé courant ;
- dividende et rendement prévisionnel ;
- chiffre d'affaires, EBITDA, EBIT ;
- dette financière nette ;
- actif net par action et cash-flow par action.

Les champs canoniques déjà utilisés par le moteur sont renseignés lorsque la sémantique correspond exactement :

- `consensus_score_100_v21`
- `consensus_delta_4w`
- `target_upside_pct_v21`
- `per_forward_v21`
- `dividend_yield_v21_pct`
- `market_cap`
- `sector_v21`

Aucun nouveau poids n'est créé. La fusion A/B/C/D décide si une observation Boursorama/FactSet de preuve B remplace la valeur déjà présente.

## Actions — profil société

Les captures de profil peuvent enrichir :

- secteur et indice de référence ;
- ouverture, clôture précédente, haut/bas intraday, volume, capital échangé ;
- capitalisation ;
- PER et rendement estimés ;
- dernier dividende et date ;
- effectif, nombre de titres, segment de marché ;
- éligibilité PEA lorsqu'elle est explicitement observée ;
- risque ESG Morningstar/Sustainalytics disponible sur la page.

Les correspondances sémantiques strictes peuvent compléter `market_cap`, `per_forward_v21`, `dividend_yield_v21_pct` et `sector_v21`. Le reste demeure du contexte attribué.

## Actions — chiffres clés historiques

Les pages `chiffres-cles` sont exploitées pour conserver, lorsqu'ils sont présents :

- chiffre d'affaires, résultat opérationnel, résultat net et résultat net part du groupe ;
- dette financière courante/non courante, dette totale, actif/passif, trésorerie ;
- BPA et BPA dilué ;
- marge opérationnelle ;
- rentabilité financière et ratio d'endettement ;
- effectif ;
- CA trimestriel, semestriel et annuel avec variation calculée entre deux observations publiées du tableau.

Ces champs sont enregistrés sous `boursorama_*`. Ils **ne modifient pas les poids validés** tant qu'un backtest PIT/OOS dédié n'a pas démontré leur valeur incrémentale.

## Pages bulk multi-actions

Les pages consolidées sont prioritaires avant les fiches individuelles, car une capture peut enrichir de nombreux titres :

- recommandations : Reco, cours, objectif, potentiel, analystes, BNA forward, rendement forward, PER forward/réalisé ;
- palmarès PER : PER et BNA par exercice ;
- palmarès dividendes : dividende et rendement par exercice ;
- extrêmes annuels : titres observés sur nouveau plus haut ou plus bas 52 semaines et contexte de séance.

Pour les extrêmes 52 semaines, **absence de la liste ≠ 0** : seul un événement effectivement observé produit un flag positif.

Le mapping utilise un code/ticker Boursorama explicite lorsqu'il est sûr, sinon un nom canonique unique. Aucune correspondance floue n'est acceptée. Pour les autres places européennes, l'ISIN des fiches individuelles reste la clé de référence ; aucun suffixe de marché n'est inventé.

## Calendriers sociétés cotées et dividendes

Le calendrier des sociétés cotées peut fournir, pour une société reconnue sans fuzzy matching :

- date et heure du prochain événement visible ;
- libellé de l'événement ;
- classe `RESULTS`, `REVENUE`, `ANALYST_INVESTOR_MEETING`, `GENERAL_MEETING`, `DIVIDEND`, `REPORT` ou `OTHER` ;
- nombre d'événements futurs visibles dans la capture ;
- jours avant le prochain événement et flags contextuels 7j/30j.

Ces champs restent sous `boursorama_*` : un résultat ou un chiffre d'affaires du calendrier Boursorama n'est pas artificiellement renommé `days_to_earnings`, afin de préserver la sémantique du calendrier Finnhub.

Le calendrier dividendes peut en plus fournir :

- date du prochain événement de dividende ;
- type d'événement ;
- montant ;
- rendement affiché ;
- nombre de jours avant l'événement.

Le rendement ponctuel d'un événement n'est **pas** assimilé automatiquement au rendement annuel canonique `dividend_yield_v21_pct`.

## Analyse technique Boursorama / TEC

Lorsqu'une page d'analyse contient une synthèse TEC datée, le process peut conserver :

- résumé textuel attribué ;
- MACD positif/négatif et au-dessus/en-dessous du signal lorsque ces formulations sont explicitement présentes ;
- RSI surachat/survente ;
- stochastique surachat/survente.

Ces données sont classées **contexte/shadow de preuve C** et ne remplacent pas les indicateurs techniques PIT calculés directement à partir de l'OHLCV.

## ETF — Morningstar, caractéristiques et risque

Le process conserve lorsque cela est réellement observable :

- `morningstar_rating` ;
- `morningstar_category` ;
- `risk_indicator` seulement si le numérateur 1–7 est explicite ;
- indice de référence ;
- date de création ;
- société de gestion / gérants ;
- forme juridique ;
- classe d'actifs et zone géographique ;
- politique de distribution/capitalisation ;
- réplication ;
- frais de gestion maximum ;
- actif net et date ;
- performances ETF ;
- performances de la catégorie Morningstar et rang lorsque disponibles ;
- Top 10 des positions et date du portefeuille ;
- date des données Morningstar.

Garde-fous ETF :

- un simple `/7` graphique ne devient jamais `7/7` ;
- `frais de gestion maximum` n'est jamais renommé `TER` sans preuve sémantique ;
- performance de catégorie Morningstar ≠ performance de l'ETF ;
- ces nouveaux champs structurels/performance/composition n'héritent pas du taux historique 90,91 % du cœur exact V20.8.1.

Les champs `morningstar_rating`, `morningstar_category` et `risk_indicator` peuvent alimenter le mécanisme ETF déjà existant lorsqu'ils sont réellement observés. Le cœur MT V20.8.1 reste inchangé.

## Stratégie de capture prioritaire

1. Capturer d'abord les pages bulk par marché (France, Allemagne, Pays-Bas, Belgique, Espagne, Italie, Portugal).
2. Capturer ensuite les palmarès PER/dividendes/extrêmes et les calendriers sociétés cotées/dividendes.
3. Pour le Top Comité/Watch, compléter avec les fiches consensus + profil + chiffres clés + analyse technique.
4. Pour les 102 ETF, privilégier les pages recherche puis les pages individuelles nécessaires pour caractéristiques, performances et composition.
5. Le worklist identifie ensuite les champs encore manquants ; aucune valeur n'est inventée pour fermer artificiellement les trous.

## Audit produit à chaque run

- `outputs/data_audit/BOURSORAMA_IMPORT_OBSERVATIONS.csv`
- `outputs/data_audit/BOURSORAMA_IMPORT_FAILURES.csv`
- `outputs/data_audit/BOURSORAMA_IMPORT_SUMMARY.json`
- `outputs/gaps/V21_BOURSORAMA_CAPTURE_WORKLIST.csv`
- `outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx` est régénéré après la fusion Boursorama.

Le worklist priorise les titres du Comité/Watch, les ETF et les instruments dont consensus, objectif, PER forward ou contexte Boursorama à forte valeur restent incomplets.
