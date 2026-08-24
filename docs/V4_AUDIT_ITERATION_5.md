# V4 Hebdo — audit 5/5 — adversarial, reproductibilité et release

Date: 2026-08-24

## Périmètre

- Suite complète et validation statique.
- Entrées falsifiées, caches incohérents, données absentes et univers non borné.
- Matérialisation fidèle de la présélection gelée, double génération et manifeste cryptographique.
- Résultat opérationnel final Actions/ETF.

## Constats

1. La suite complète passe sur Windows après correction du test de chemin de l'itération 1.
2. Le gate strict rejette les 15 candidats actuels : leur confiance V22.2.1 reste sous 66; 10 Actions ont aussi un potentiel de consensus inférieur à 20 %, une Action n'a pas de consensus de potentiel admissible et les deux ETF n'ont pas de note Morningstar factuelle dans le master livré.
3. TradingView est exploitable pour 14 titres sur 15; la page TWEKA ne fournit pas la synthèse complète exigée.
4. Boursorama est exploitable pour 7 Actions sur 13 et 2 ETF sur 2. Les six Actions sans code déterministe restent explicitement non résolues.
5. CI Light retient zéro titre : aucun ne satisfait simultanément toutes les règles strictes applicables à sa classe d'actif et les trois horizons TradingView.

## Durcissements

- Une altération de 1 point de pourcentage d'un poids fait échouer l'audit de gouvernance.
- Un cache périmé, lié à un autre symbole ou à un autre code instrument est rejeté; l'échec de rafraîchissement ne réactive pas la donnée périmée.
- Un upstream supérieur à 40 instruments est refusé et la matérialisation V22.2.1 prouve que l'ensemble des ISIN et les scores restent inchangés.
- Le constructeur de release refuse d'écraser un répertoire non vide.
- Le ZIP est ordonné, horodaté de façon fixe et couvert par un manifeste SHA-256; deux générations identiques produisent les mêmes octets.

## Validation

- Suite complète finale : **900 PASS + 7 sous-tests PASS**.
- Ruff : **PASS**.
- Compilation Python : **PASS**.
- JSON de gouvernance et de sources : **valides**.
- Candidat gelé : **15 lignes / 15 ISIN**, SHA-256 consigné, aucun changement de score ou de population.
- Ordres réels : **désactivés**.

La sortie vide est le résultat factuel des règles approuvées. La V4 ne baisse pas les seuils et n'invente pas de données pour produire artificiellement des recommandations.

Statut de l'itération: **PASS**.
