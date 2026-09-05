# ETF MT — optimisation de process V21.0

Date : 5 septembre 2026  
Statut : **SHADOW / no real orders / decision_influence = 0**  
Cœur gelé : **V20.8.1 38 PIT** (seul livre autorisé à citer le 90,91 % OOS 2021–2023)

## Diagnostic

Le process actuel mélange trois objets différents :

1. Un **moteur de précision** backtesté (38 critères prix/volume, seuil 82, Top 2, `MOMO_RISK_ON`, sortie +4 % / −18 % / 168 séances).
2. Des **challengers** (V20.8.2 renormalisé, GROK2 CDC, overlay Morningstar/SRI) sans attribution historique.
3. Un **CDC moyen terme réel** (thèse 18–60 mois, look-through, véhicule, invalidation écrite) qui n’est pas le moteur +4 %.

Conséquences :

- Le +4 % / 168 séances n’est pas un horizon moyen terme. C’est un scalp de précision sur un score MT. Win rate élevé, expectancy limitée (replay GROK V1 corrigé : 90,41 %, +2,74 % / trade).
- Le proxy de sortie « thèse » GROK2 (`Q63 / H378 / S25`) a **dégradé** 2024–2026. Il reste rejeté. On ne le réintroduit pas dans le score.
- V20.8.2 renormalise les critères manquants : utile pour la couverture, dangereux comme décision. Il ne doit plus alimenter le livre de référence.
- TER / AUM V21.10 existent sur le snapshot courant (preuve A/B) mais n’ont pas d’historique PIT. Ils peuvent **veto aujourd’hui**, pas scorer 2012–2023.

## Décision d’architecture

Deux livres, une seule source de signal chiffré.

| Livre | Rôle | Sortie | Attribution |
|---|---|---|---|
| **PRECISION** | Cœur V20.8.1 inchangé | +4 / −18 / 168 | seul 90,91 % |
| **THESIS_MT** | Sleeve 18–60 mois | pas de target fixe ; stop −18 ; revue 63 s ; max 378 s ; invalidation écrite | aucune |

Règle d’entrée THESIS_MT : uniquement un `BUY_CANDIDATE` PRECISION **après** gates opérationnels du snapshot courant.

Interdit :

- retuner les 38 poids ;
- ouvrir le holdout pour calibrer ;
- backfiller TER/AUM/look-through 2026 dans un replay ;
- laisser l’overlay structurel créer un BUY ;
- donner une influence d’ordre réel.

## Gates opérationnels (snapshot courant)

Fail-closed sur l’entrée THESIS_MT, sans imputation :

- non PEA → BLOCK
- AUM < 50 M€ → BLOCK ; < 100 M€ → WARN
- TER > 0,25 % (large) ou > 0,50 % (thème/facteur) → WARN
- SRI ≥ 6 → DOWNGRADE / WARN
- données dynamiques > 7 jours → BLOCK
- thèse < 40 caractères ou invalidation < 20 → BLOCK
- pas issu du Top 2 PRECISION → BLOCK

Les champs manquants **avertissent**, ils ne sont pas mis à 0.

## Mapping CDC → runtime

| CDC | Runtime |
|---|---|
| Job + thèse + invalidation | fiche obligatoire avant THESIS_MT |
| Filtres véhicule | `etf_mt_operational_gates.py` |
| Shortlist 3–5 pairs | documenté, pas dans le score historique (`peer_rank_weight = 0`) |
| Look-through | preuve opérationnelle si holdings disponibles ; jamais backfillé |
| Score 70/100 CDC | ne remplace pas le seuil 82 PRECISION |
| Sizing satellite | 5–10 % ligne, cap thème 20 %, cap livre 30 %, max 6 lignes |
| Revue trimestrielle | 63 séances |

## Ce qui n’est pas « optimisé »

On n’améliore pas le win rate en relâchant le régime, le seuil 82, la complétude des 38 critères ou la sortie +4 % du livre PRECISION. La doctrine reste *precision over coverage*.

L’optimisation est processuelle : séparer scalp de précision et détention MT, brancher les données véhicule déjà collectées comme veto, et interdire la contamination d’attribution.

## Fichiers

- `config/ETF_MT_PROCESS_V21.json`
- `src/v182/decision/etf_mt_operational_gates.py`
- `tests/test_etf_mt_operational_gates.py`

Le workflow quotidien V20.8.1 n’est pas modifié par cette passe.
