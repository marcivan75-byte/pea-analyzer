# TABPORT — horizon de détention gelé — résultats développement/OOS

## Gouvernance

- Sélection de l'horizon : **2010–2022 uniquement**.
- Holdout : **2023–2026 en évaluation uniquement**, sur la partie où chaque signal dispose réellement de 252 séances futures par titre.
- Famille figée avant lecture du holdout : 63 / 126 / 189 / 252 séances.
- Même univers de signaux confirmés, mêmes stops, mêmes frais, même slippage, mêmes limites de capacité.
- Seul `max_hold_sessions` change.
- Aucun signal n'est conservé si le titre ne dispose pas d'au moins 252 barres futures après la confirmation.
- Aucun `EOP_DATA_END` autorisé dans la comparaison.
- Aucune imputation synthétique.
- Aucune promotion production automatique.

Run de référence : `33620294387` — **SUCCESS**.

Artefact : `TABPORT-HOLD-HORIZON-33620294387`, ID `9842895680`.

Tests : **68 passés**.

Commits de l'étude :
- module : `5335f0b00d0f81f93391505acc67747a175d90b1`;
- tests : `bba2df5152a359aa1b3261b91b89ca30c5693de3`;
- workflow : `feecaf39271200e893a78120ffde1a8e3f22bc65`.

## Cohorte commune réellement maturée

- signaux confirmés en entrée : `4 380`;
- signaux communs admissibles à 252 séances : **`4 168`**;
- signaux exclus pour historique futur insuffisant : `212`;
- première date de signal retenue : `2010-10-18`;
- dernière date de signal retenue : **`2025-08-25`**;
- sorties forcées de fin de données : **0 pour les quatre horizons**.

La ligne annuelle 2026 ne constitue donc pas une année de génération de signaux 2026 comparable : elle contient seulement des sorties en 2026 de positions issues de signaux antérieurs au 25 août 2025. Elle ne doit pas être interprétée comme une validation indépendante de signaux 2026.

## Sélection strictement développement 2010–2022

Fonction objectif de stabilité/performance, calculée uniquement sur 2011–2022 :

| Modèle | Horizon | Score développement |
|---|---:|---:|
| H63 | 63 séances | 6.9133 |
| **H126_BASELINE** | **126 séances** | **20.2942** |
| H189 | 189 séances | 17.5063 |
| H252 | 252 séances | 19.8741 |

**H126_BASELINE est donc le modèle sélectionné ex ante.**

H252 est proche mais reste inférieur au score de sélection de H126. H189, malgré un comportement OOS agrégé très favorable, ne peut pas être promu a posteriori puisqu'il n'a pas gagné la sélection développement.

## Comparaison développement

| Modèle | Trades | PF | RR | Espérance/trade | Rendement portefeuille | DD max | Stops |
|---|---:|---:|---:|---:|---:|---:|---:|
| H63 | 495 | 0.976 | 1.450 | -0.13% | -4.22% | -27.08% | 49.29% |
| **H126_BASELINE** | **451** | **1.463** | **2.563** | **+2.73%** | **+82.98%** | **-17.82%** | **58.54%** |
| H189 | 341 | 1.756 | 3.404 | +4.65% | +105.87% | -17.21% | 63.34% |
| H252 | 293 | 2.098 | 4.301 | +7.02% | +136.61% | -25.38% | 66.21% |

H252 augmente fortement la convexité et le RR mais au prix d'un drawdown développement sensiblement supérieur et d'un taux de stops plus élevé. Sa trajectoire annuelle reste irrégulière, notamment 2011, 2016, 2020 et 2022.

## Comparaison OOS — cohorte commune 252 séances

| Modèle | Trades | Win rate | PF | RR | Espérance/trade | Rendement segment | DD max | Stops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H63 | 120 | 36.67% | 0.711 | 1.239 | -1.58% | -13.92% | -20.26% | 53.33% |
| **H126_BASELINE** | **91** | **46.15%** | **1.578** | **1.845** | **+2.71%** | **+9.72%** | **-6.98%** | **45.05%** |
| H189 | 77 | 40.26% | 2.176 | 3.228 | +7.10% | +19.63% | -7.58% | 57.14% |
| H252 | 66 | 33.33% | 1.747 | 3.478 | +5.36% | +12.32% | -5.46% | 65.15% |

## Lecture des variantes longues

### H189

C'est la variante OOS agrégée la plus intéressante : PF `2.176`, RR `3.228`, espérance `+7.10%` par trade et rendement segment `+19.63%` pour un DD de `-7.58%`.

Mais elle n'est pas le modèle sélectionné sur le développement. Elle reste donc **RESEARCH_ONLY / NON PROMUE**. En outre, 2023 conserve un PF inférieur à 1 (`0.860`), ce qui empêche de conclure à une domination uniforme dans le temps.

### H252

La variante 252 séances produit le meilleur RR développement (`4.301`) et un RR OOS élevé (`3.478`), avec un DD OOS faible (`-5.46%`). Elle montre que certains gagnants bénéficient d'une détention longue.

Cependant :
- le DD développement monte à `-25.38%`;
- le taux de stops développement atteint `66.21%` et OOS `65.15%`;
- le rendement OOS reste inférieur à H189;
- son score développement (`19.8741`) reste inférieur à H126 (`20.2942`).

Elle reste donc **RESEARCH_ONLY / NON PROMUE**.

## Décision

1. **Conserver 126 séances comme horizon TABPORT de référence.**
2. Rejeter 63 séances : performance et stabilité insuffisantes en développement comme en OOS.
3. Conserver H189 comme piste de recherche forte, sans promotion production.
4. Conserver H252 comme piste de convexité/RR, sans promotion production.
5. Ne pas utiliser le meilleur résultat OOS pour modifier a posteriori le choix effectué sur le développement.

## Conséquence méthodologique

Les études successives convergent :
- filtrer davantage les entrées détruit souvent les rebonds gagnants;
- sortir plus tôt détruit la convexité;
- modifier le classement ne bat pas la baseline sous sélection développement;
- allonger uniformément la durée ne bat pas non plus H126 selon le critère ex ante.

Le prochain levier rationnel doit donc porter sur **l'allocation du capital / dimensionnement des positions**, sans supprimer les signaux, sans retoucher les sorties et sans choisir les paramètres sur le holdout.
