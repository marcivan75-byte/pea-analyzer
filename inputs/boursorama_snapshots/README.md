# Boursorama — intake attribué haute priorité

Ce répertoire alimente le process PEA avec des données Boursorama à forte valeur décisionnelle, sans récupération automatisée du site.

## Règle d'accès

Le runtime GitHub ne lance aucun robot, navigateur ni scraper contre Boursorama. Il ingère uniquement des pages ou tableaux que l'utilisateur (ou une source autorisée) a enregistrés/exportés et déposés ici. Chaque donnée conservée garde son URL Boursorama, sa date et le fournisseur sous-jacent lorsqu'il est identifiable (notamment FactSet ou Morningstar/Sustainalytics).

## Formats pris en charge

- `.html` / `.htm` : page Boursorama enregistrée depuis le navigateur ;
- `.csv` / `.xlsx` / `.xlsm` : tableau attribué contenant obligatoirement `isin`, `source_url`, `source_date`.

Les sous-répertoires sont libres. Recommandation :

- `actions/` : fiches `.../cours/consensus/...` enregistrées, y compris les valeurs européennes hors Paris lorsque Boursorama les couvre ;
- `bulk/` : pages `.../bourse/actions/consensus/recommandations-paris/...` ou équivalentes ;
- `etf/` : pages de recherche/caractéristiques ETF présentant ISIN, Morningstar et risque.

## Champs Actions extraits automatiquement

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
- actif net par action et cash-flow par action ;
- capitalisation ;
- secteur et éligibilité PEA lorsqu'ils sont explicitement observés ;
- contexte ESG Morningstar/Sustainalytics lorsqu'il est présent.

Les champs canoniques déjà utilisés par le moteur sont renseignés lorsque la sémantique correspond exactement :

- `consensus_score_100_v21`
- `consensus_delta_4w`
- `target_upside_pct_v21`
- `per_forward_v21`
- `dividend_yield_v21_pct`
- `market_cap`
- `sector_v21`

Aucun nouveau poids n'est créé. La fusion standard A/B/C/D décide si l'observation Boursorama/FactSet de preuve B remplace ou non la valeur déjà présente.

## Pages bulk multi-actions

Une capture de la page de recommandations peut enrichir de nombreux titres en une seule fois. Le parseur récupère notamment :

- Reco.
- Der. Cours
- Obj. Cours
- Potentiel
- Nb. Analystes
- BNA forward
- Rendement forward
- PER forward
- PER réalisé

Le mapping utilise d'abord le code Boursorama explicite quand il permet une correspondance Euronext Paris non ambiguë, puis un nom canonique unique. Aucune correspondance floue n'est acceptée. Pour Amsterdam, Milan, Madrid, Francfort et les autres places européennes, les fiches individuelles enregistrées restent prises en charge par ISIN ; aucun suffixe/ticker de marché n'est inventé.

## Champs ETF extraits

- `morningstar_rating`
- `morningstar_category`
- `risk_indicator`
- performances/prix/devise Boursorama disponibles dans la table

Ces champs alimentent le mécanisme ETF déjà existant : 4 étoiles = bonus +3, 3 étoiles = +1, et risque SRI 6–7 = malus -3, sans modifier le cœur MT V20.8.1.

## Audit produit à chaque run

- `outputs/data_audit/BOURSORAMA_IMPORT_OBSERVATIONS.csv`
- `outputs/data_audit/BOURSORAMA_IMPORT_FAILURES.csv`
- `outputs/data_audit/BOURSORAMA_IMPORT_SUMMARY.json`
- `outputs/gaps/V21_BOURSORAMA_CAPTURE_WORKLIST.csv`
- `outputs/data_audit/COLLECTION_DATA_AVAILABILITY_LATEST.xlsx` est régénéré après la fusion Boursorama.

Le worklist priorise les titres du Comité/Watch et ceux dont consensus, objectif ou PER forward manquent encore.
