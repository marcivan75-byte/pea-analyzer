# Weekly Heavy runtime — finalisation statique V21.16.2

Date: 2026-08-23

## Statut

Candidat de finalisation statique sur `codex/runtime-weekly-15min-v21-16-0` / PR #184.

Aucun GitHub Actions run n'est déclenché par cette passe. La cible 15 minutes reste donc un objectif à confirmer par un run représentatif réussi. Les optimisations de cette branche ne modifient ni critères, ni poids, ni seuils, ni règles PIT, ni univers, ni politique de données manquantes.

## Référence mesurée du 22 août 2026

Run Weekly V21.8.1 #32580878176, annulé avant fin du pipeline:

- début du pipeline: ~15:10:52 UTC;
- WAVE_01 Actions OHLCV: fin 15:14:51.97, soit ~3 min 43 s après WAVE_00;
- WAVE_02 ETF OHLCV: ~18 s;
- WAVE_03 dérivés locaux: ~1 min 51 s;
- WAVE_04 fondamentaux Actions: ~12 min 52 s;
- WAVE_05 consensus: ~44 s;
- WAVE_06 ETF info: ~1 min 25 s;
- WAVE_06B: ~44 s;
- Top-Down en cours au moment de l'annulation, plus de 14 minutes après son démarrage visible;
- pipeline principal non terminé après ~36 min 56 s.

Cette référence est volontairement conservatrice: elle précède les optimisations V21.16 et a néanmoins permis de réchauffer puis sauvegarder `state/provenance`, notamment les caches source.

## Optimisations actives

1. Cache OHLCV persistant avec refresh incrémental et conservation de l'historique valide.
2. Cache Yahoo fondamentaux persistant HOT/WARM/COLD, univers complet conservé, budget ordinaire Weekly de 320 refreshs.
3. WAVE_05 Finnhub et WAVE_06 Yahoo ETF exécutées en parallèle.
4. Committee multi-horizons parallélisé à deux workers avec ordre déterministe conservé.
5. Résolution canonique des critères mémoïsée uniquement dans le run courant.
6. Yahoo OHLCV: retry groupé des symboles manquants et coupure de la longue traîne singleton sur panne fournisseur, sans suppression du bootstrap des nouveaux titres.
7. Sector Rotation V2 chevauchée avec la branche ETF MT après disponibilité de la structure ETF.
8. Finalisation vendredi: réutilisation du Committee courant, suppression des recalculs tactiques redondants, démarrage Postmarket dès la fin TCT, Decision Brief et bundle post-décision parallélisés.
9. GDELT V21.16.2: une `requests.Session` par worker, réutilisée entre requêtes; cadence globale 1 requête/seconde, retries, timeout, fenêtres et requêtes inchangés.

## Effet attendu du cache Yahoo fondamentaux

Le run du 22 août a parcouru pratiquement tout l'univers Action pendant WAVE_04. À 0,4 s entre départs de requêtes, 1 829 départs représentent à eux seuls ~12,19 min, cohérents avec les ~12,87 min observées.

En régime stabilisé, le budget ordinaire est 320 refreshs: 319 intervalles x 0,4 s = 127,6 s, soit ~2,13 min de plancher de départs réseau avant latence résiduelle. Le cache sauvegardé après le run annulé du 22 août doit donc faire disparaître l'essentiel de la pénalité bootstrap WAVE_04, sous réserve qu'il soit restauré et valide.

## Estimation du prochain Weekly stabilisé

Les fourchettes ci-dessous sont des estimations statiques, pas des temps mesurés V21.16.

| Bloc | Estimation stabilisée |
|---|---:|
| Setup / restore / installation | 0,6–0,9 min |
| WAVE_01 + WAVE_02 OHLCV incrémental | 2,0–3,5 min |
| WAVE_03 calculs locaux | 1,4–1,9 min |
| WAVE_04 fondamentaux Action cache chaud | 2,2–3,2 min |
| WAVE_05 + WAVE_06 + WAVE_06B | 0,8–1,6 min |
| WAVE_09 Top-Down FRED + GDELT | 2,8–5,0 min fournisseur sain |
| WAVE_10/11 + persistance/qualité | 0,4–0,8 min |
| ETF/Committee/Rotation/Risk/CI, avec chevauchements V21.16 | 1,8–3,0 min |
| Tail vendredi optimisé | 0,8–1,5 min |

Les chevauchements du DAG évitent d'additionner intégralement toutes ces lignes.

### Fourchette de décision

- scénario optimiste, caches chauds et fournisseurs sains: **14–16 min**;
- scénario central réaliste: **16–19 min**;
- scénario fournisseurs dégradés / retries: **23–35+ min**;
- cache source froid ou bootstrap massif: **27–40+ min**.

La cible 15 minutes est donc atteignable dans de bonnes conditions, mais elle n'est pas encore une cible robuste P50/P80.

## Goulot résiduel

Le principal risque de longue traîne est maintenant WAVE_09/GDELT. Le code conserve volontairement une cadence fournisseur-safe d'une requête par seconde, un timeout de 20 secondes et deux retries bornés. Le pooling HTTP V21.16.2 réduit le coût de connexion sans modifier les données recueillies, mais ne peut pas neutraliser une dégradation du fournisseur.

Le second gisement d'optimisation est le chevauchement de la collecte réseau WAVE_04 avec WAVE_02/WAVE_03 local. Il est techniquement possible sans perte de données à condition de séparer strictement le préfetch réseau Yahoo de la matérialisation des ratios `last_close × fondamentaux` après WAVE_03. Gain attendu: environ 1,3–1,9 min. Cette modification est plus invasive et ne doit être promue qu'avec tests d'identité des observations et de la provenance.

## Critère de clôture

La branche peut être considérée comme finalisée statiquement, mais pas validée en production. La clôture runtime exige un run Weekly représentatif réussi publiant les audits `WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_1.json` et `WEEKLY_TAIL_SUPER_RUNTIME_V21_16_0.json`, avec comparaison du temps réel aux fourchettes ci-dessus.
