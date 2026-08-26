# Optimisation issue du diagnostic Sélection / Objectif / Risque

## Décision d'architecture

Le diagnostic Word est une source de recommandations. Il ne remplace pas les décisions de gouvernance.
Tous les changements sont `shadow_only`, sans ordre réel, sans modification des scores de référence et sans
promotion automatique.

## Changements appliqués

- R/R Actions challenger : TCT 2,0, CT 2,5, MT 3,0; fiabilité minimale 65 %.
- Ranking post-gate : 55 % sélection, 30 % R/R normalisé, 15 % fiabilité.
- Ranking ajusté au downside : 90 % ranking post-gate + 10 % downside observé; absence neutre.
- Confiance d'entrée : 60 en RISK_ON, 62 en NEUTRAL, 70 en RISK_OFF; `WATCH_WITH_TRIGGER` ne devient jamais READY automatiquement.
- Confiance source explicite fondée sur Boursorama, les trois horizons TradingView et les preuves prix/risque.
- Hyper-Selection : stabilité sur observations datées et liquidité relative `rvol20 + spread`, overlays sans influence sur les 15 poids.
- Budget portefeuille : une famille économique, corrélation paire < 0,65, poids thématique plafonné à 30 %; bêta 0,70–1,00 observé jusqu'à disponibilité de poids fiables.
- CI et CI LIGHT restent deux processus indépendants.
- ETF : journalier/hebdomadaire BUY ou STRONG_BUY; mensuel STRONG_BUY; Morningstar 4–5 étoiles seulement en remplacement d'un signal manquant.

## Recommandation non appliquée

Investing n'est pas réactivé. La décision utilisateur antérieure le remplace par TradingView; une source non
factuelle ne doit ni créer ni confirmer un candidat.

## Promotion

Minimum huit semaines shadow, données PIT/OOS, amélioration du win-rate, de l'information ratio et du max drawdown,
tests de non-régression, puis décision explicite. Une sélection vide reste valide.
