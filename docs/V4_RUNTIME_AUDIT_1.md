# Optimisation de durée V4 — audit 1/3 — profil initial

Date : 2026-08-24

## Mesure

- Trois exécutions du gate : 1,748 s; 0,782 s; 0,759 s.
- Trois exécutions CI Light autonome : 1,984 s; 0,774 s; 0,731 s.
- Durée moyenne du couple : **2,259 s**.

## Goulot identifié

Le runner V4 appelait deux fois `enrich_selected_rows_v4` sur les mêmes 15 ISIN : d'abord pour le gate, puis pour CI Light. La seconde passe relisait les masters et caches, reconstruisait les observations, réécrivait les preuves et retentait la page TradingView incomplète. Elle ne fournissait aucun champ décisionnel nouveau.

## Contraintes de l'optimisation

- mêmes 15 ISIN et mêmes horizons;
- mêmes états Boursorama et TradingView;
- mêmes raisons d'inclusion/rejet Light;
- mêmes seuils, scores et règles Actions/ETF;
- aucune baisse des TTL ou des délais fournisseur;
- aucune perte des observations, échecs, URLs ou empreintes;
- aucun ordre réel.

Statut : **PASS — goulot reproductible identifié**.
