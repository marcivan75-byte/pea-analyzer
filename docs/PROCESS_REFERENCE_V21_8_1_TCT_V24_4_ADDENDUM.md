# Process V21.8.1 — Addendum normatif TCT V24.4.0

Date : 19/08/2026

Cet addendum complète le process de référence V21.8.1 et la note V24.3.1. En cas d'ambiguïté sur le chantier TCT courant, les règles ci-dessous sont normatives pour V24.4.0.

## Statut

- Production canonique : **V21.8.1 inchangée**.
- Challenger TCT daily/weekly : **V24.3.1 SHADOW**.
- Couche de contexte prochaine séance : **V24.4.0 Next-Session Catalyst Cycle SHADOW**.
- WIP actif : amélioration TCT ; CT reste gelé.
- Holdout final : fermé.
- Ordres réels : désactivés.

## Règle fonctionnelle

V24.4.0 ne fait pas de day trading. Il ajoute au TCT deux contextes discrets :

1. `POSTMARKET` après les clôtures Europe/US ;
2. `PREOPEN` avant l'ouverture européenne.

Il n'existe aucun troisième cycle, aucune surveillance continue et aucune donnée 1m/5m.

## Sources autorisées dans V24.4.0

- seed V24.3.1 construit sur OHLCV daily ;
- métadonnées déjà collectées : news catalyst, résultats à venir, secteur, capitalisation ;
- GDELT pour les news de la fenêtre temporelle ;
- un snapshot groupé Yahoo Finance en `1d` pour indices/futures/or/pétrole/EURUSD.

Aucune cotation extended-hours individuelle d'une action PEA n'est assimilée à une information obligatoire.

## Décision

V24.4.0 produit deux dimensions séparées :

- `movement_potential_score` : potentiel d'amplitude ;
- `direction_bias_score` : biais de direction.

Ces scores sont SHADOW et ont une influence de production égale à zéro.

## Anti-look-ahead

- News : timestamp obligatoire et fenêtre strictement filtrée.
- PREOPEN : ancrage sur la dernière vraie date daily disponible, y compris après week-end/jour férié.
- Premier snapshot candidat/jour/phase : figé dans le ledger PIT.
- Résultats réels : ajoutés uniquement après une clôture daily ultérieure.
- Les labels futurs ne participent jamais au score qui les précède.

## Coût

Le workflow V24.4.0 est séparé du run complet : il ne relance ni la collecte générale, ni le Comité, ni le moteur TCT canonique. Maximum deux exécutions planifiées par jour ouvré.

## Validation

Les seuils de maturité sont pré-enregistrés dans `config/TCT_V24_4_0_VALIDATION_GATES.json`. Aucun retuning n'est autorisé avant maturité et aucune promotion automatique n'est possible après maturité.

La prochaine promotion TCT éventuelle devra démontrer en PIT/OOS que V24.4.0 améliore réellement l'identification des mouvements quotidiens importants par rapport à V24.3.1 seul.
