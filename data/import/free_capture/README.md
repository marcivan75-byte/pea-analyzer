# V21.1 FREE_ONLY - Imports manuels gratuits

Ce répertoire sert uniquement à déposer des exports gratuits obtenus légitimement par l'utilisateur.
Aucun identifiant de connexion n'est automatisé par GitHub.

## ABC Bourse

Nom recommandé : `abc_bourse_<date>.csv`.
Le collecteur accepte les colonnes équivalentes à :
- `ISIN` (préféré), ou `ticker/symbol`
- `date`
- `open/ouverture`
- `high/plus haut`
- `low/plus bas`
- `close/clôture/cours`
- `volume`

## ProRealTime

Nom recommandé : `prorealtime_<date>.csv` ou `prt_<date>.csv`.
Même schéma logique que ci-dessus. Si l'ISIN n'est pas présent, le ticker est rapproché du référentiel canonique ; les lignes non résolues sont rejetées et comptées dans l'audit.

## Gouvernance

- Les données sont normalisées dans `outputs/free_capture/V21.1_MARKET_DAILY.csv`.
- La clé de déduplication est `(isin, date, source)`.
- Les indicateurs techniques sont ensuite recalculés localement par `v182.free_capture.derive_market`.
- Les fichiers importés ne remplacent pas une observation de source officielle de priorité supérieure.
- Ne déposer aucune clé API, mot de passe ou cookie dans ce répertoire.
