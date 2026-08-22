# Committee Master V21 — périmètre actif V21.13.7

## Scope

Le Comité conserve les référentiels exhaustifs Actions PEA et ETF PEA, mais ne
calcule plus que les horizons utiles suivants :

### Actions PEA

- TCT : adapter SHADOW ; T1/T2 exclusivement Actions TCT ;
- CT et MT : scoring depuis le registre Actions actif ;
- Short-risk et Top-Down : modules transverses conservés lorsque requis par le
  Comité ;
- Actions LT : fonction retirée.

### ETF PEA

- CT : pondération active V20.7.1 ;
- MT : moteur dynamique V20.8.1 à 38 critères ;
- référentiel structurel complet conservé pour la qualité des données ;
- ETF LT : fonction retirée.

Les processus Gold, Crypto/ETP et IPO sont retirés : aucun score, état bloqué,
fallback ou sortie de ces modules ne doit être émis.

## Sorties

`outputs/committee_master/` :

- `COMMITTEE_DECISIONS.csv` ;
- `SECTOR_RANKING.csv` ;
- `CRITERIA_COVERAGE.csv` ;
- `SUMMARY.json`.

## Qualité et exécution

Les critères pondérés manquants réduisent la couverture. Sous le seuil de
l'horizon, l'instrument passe `BLOCK_DATA` ; aucune valeur neutre n'est
fabriquée. Le workflow hebdomadaire collecte Actions/ETF, calcule ETF MT, lance
le Comité, puis publie les artefacts et audits actifs. Les ordres réels restent
désactivés.
