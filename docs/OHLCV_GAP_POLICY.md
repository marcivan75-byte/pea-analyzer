# Barres OHLCV manquantes — politique

Date : 2026-08-28

## Verdict

Les barres absentes du cache ZIP **ne peuvent pas être complétées sans inventer des prix**.

Tentative live yfinance (2023-01-01 → 2026-08-28, auto_adjust) sur 33 tickers à trou interne ≥ 2 % ou série vide :
- 25 identifiants valides : Yahoo renvoie des séances **déjà présentes** dans le cache → **0 barre nouvelle** sur l’index union.
- 8 identifiants invalides / délistés : `2ANTIN`, `2EAPI`, `2OVH`, `4UBI`, `4VLA`, `ALVER.PA`, `RYA.L`, `LCEZ.PA`.

## Décomposition des « trous »

| Cause | Actions | ETF | Complétable ? |
|---|---|---|---|
| Calendrier union multi-places | majoritaire dans les 5,4 % | partie des 16 % | Non — ce n’est pas une séance |
| Introduction après 2023-01-02 | rare (p90 retard = 1j) | dominant (p90 = 784j) | Non — pas de cotation |
| Trou interne first→last valid | 1,06 % | 0,30 % | Non confirmé par Yahoo |
| Ticker mort / mauvais symbole | 7 | 1 (`LCEZ.PA`) | Non tant que l’identité n’est pas remappée |

## Interdit

- forward-fill / back-fill
- interpolation
- barre week-end
- copier un close de la veille
- inventer une pré-introduction

Un PIT honnête laisse le trou. Le score ou le forward du snapshot concerné reste `INDISPONIBLE`.
