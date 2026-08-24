# V4 Hebdo — audit 2/5 — collecte et preuve des sources

Date: 2026-08-24

## Périmètre

- Collecte strictement limitée aux 15 ISIN de la présélection gelée V22.2.
- Boursorama Actions et ETF, puis TradingView journalier, hebdomadaire et mensuel.
- Identité instrument, fraîcheur, cache, traçabilité de page et comportement en cas d'échec.
- Investing explicitement désactivé.

## Constats

1. La présélection transporte plusieurs alias versionnés de ticker Yahoo sans toujours renseigner le champ canonique. Cela empêchait une résolution exacte pourtant disponible.
2. Le cache TradingView V1 pouvait survivre à un changement de symbole ou dépasser largement le TTL nominal.
3. Une redirection HTTP vers un hôte inattendu n'était pas contrôlée après la requête.
4. Les caches Boursorama conservaient des champs dynamiques périmés si le rafraîchissement échouait et n'étaient pas liés assez strictement au code instrument courant.
5. Les horodatages et empreintes de preuve Boursorama devaient être portés champ par champ selon la page dynamique ou profonde réellement utilisée.

## Corrections

- Promotion déterministe des alias `yahoo_ticker_v22_2`, `yahoo_ticker_v22_1` et `ticker_yahoo` vers le champ canonique, sans recherche libre par nom.
- Cache TradingView V2 lié à l'ISIN et au symbole qualifié par place, TTL strict de 6 heures et rejet des entrées incomplètes, périmées ou incohérentes.
- Validation de l'hôte et du chemin de l'URL finale TradingView.
- Exigence simultanée des synthèses 1D, 1W et 1M, de l'empreinte SHA-256 et de la date de collecte.
- Invalidation des caches Boursorama lors d'un changement de code et suppression des champs périmés avant tentative de rafraîchissement.
- Orchestrateur V4 unique : Boursorama + TradingView, Investing désactivé, influence sur le score égale à zéro et impossibilité de créer un candidat.
- Audit réel borné et reproductible dans `scripts/audit_v4_sources_live.py`.

## Validation réelle bornée

- Périmètre : **15/15 ISIN**, sans extension de l'univers.
- TradingView : **13/15 instruments utilisables**, 169 observations factuelles.
- Échecs TradingView explicites : 1 identité sans ticker déterministe et 1 page sans synthèse complète 1D/1W/1M.
- Boursorama : **6/13 Actions** et **2/2 ETF** utilisables, 248 observations.
- Échecs Boursorama explicites : 7 codes instrument non déterministes; aucune recherche libre n'est tentée.
- Alias d'identité hydratés : **6**.
- HTML brut persistant : **0**.
- Tests ciblés : **24 PASS**.
- Ruff : **PASS**.

Les absences restent `NA/WAIT`; elles ne sont jamais converties en signal neutre ou baissier. La couverture réelle incomplète est donc visible mais ne peut ni contaminer le score de référence ni produire une recommandation artificielle.

Statut de l'itération: **PASS**.
