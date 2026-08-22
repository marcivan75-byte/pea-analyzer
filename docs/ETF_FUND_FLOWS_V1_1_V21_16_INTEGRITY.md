# V21.16 — ETF Fund Flows V1.1 integrity hardening

> Preuve historique antérieure à V21.13.7. Le runtime actif conserve uniquement
> ETF PEA et les sentinelles sectorielles non Gold/non Crypto ; les 40 satellites
> Gold/Crypto, leurs contrôles et leurs sorties ont été retirés. Les règles
> d'intégrité temporelle décrites ci-dessous restent applicables au périmètre PEA.

Date : 20/08/2026

## Objet

V21.16 durcit le module `ETF Fund Flows` avant l'accumulation de son historique PIT. Le module reste `SHADOW_ONLY` : influence décisionnelle, ventes, ordres réels et promotion restent à zéro.

Aucun poids pré-enregistré n'est modifié. Les seuils restent 20 flux valides pour accéder au stade `PRELIMINARY` et 60 pour être candidat au stade `MATURE`, avec exigence supplémentaire que la fenêtre courante correspondante soit réellement complète.

## Défaut 1 — AUM et shares Yahoo sans date source explicite

Yahoo peut fournir `totalAssets`, `navPrice` et `sharesOutstanding` sans date structurelle explicite. Attribuer automatiquement le jour du run à ces champs pouvait créer deux faux signaux :

- un AUM inchangé combiné à un prix mobile pouvait produire un faux flux via `AUM_t - AUM_t-1 × (1 + total_return_t)` ;
- des `sharesOutstanding` non datées et inchangées pouvaient produire artificiellement un flux nul quotidien et faire mûrir l'historique.

### Correction V21.16

- la date du dernier cours réellement observé devient la date de marché Yahoo quand elle est disponible ;
- AUM, NAV, shares et market price portent chacun un indicateur `*_as_of_explicit` ;
- ces indicateurs suivent le champ lors d'une fusion de sources le même jour ;
- les `sharesOutstanding` non datées restent auditables dans la collecte mais sont supprimées du calcul de flux ;
- un AUM non daté et inchangé devient `UNSCORABLE_UNDATED_AUM_UNCHANGED` ;
- un AUM non daté qui change exige un rendement NAV/cours explicitement daté ; sinon `UNSCORABLE_UNDATED_AUM_WITHOUT_DATED_RETURN` ;
- lorsqu'un AUM non daté peut être utilisé avec un rendement daté, la méthode est explicitement `AUM_PERFORMANCE_ADJUSTED_UNDATED_VENDOR_UPDATE` ;
- le rendement de marché inclut les distributions observées ;
- un prix de marché daté est préféré à un NAV non daté ;
- le dénominateur des flux roulants utilise l'AUM ou, à défaut, `shares × NAV`.

Le principe est fail-closed : une absence d'information temporelle ne devient jamais une hypothèse de flux.

## Défaut 2 — breadth / SRFS avec données manquantes

La breadth utilisait une comparaison qui transformait implicitement un `NaN` en valeur non positive. Un instrument sans historique 20 observations pouvait donc pénaliser artificiellement la breadth.

De plus, le `Sector Rotation Flow Score` pouvait être construit avant que les instruments aient atteint 20 flux valides.

### Correction V21.16

- breadth calculée uniquement sur les taux 20 observations réellement disponibles ;
- confirmation régionale calculée uniquement sur les régions disposant d'un taux 20 observations valide ;
- SRFS exclut tout instrument sous le gate `PRELIMINARY` ;
- aucun score sectoriel n'est produit si aucun instrument du secteur n'est prêt ;
- les valeurs manquantes restent manquantes et ne sont jamais transformées en zéro négatif implicite.

## Défaut 3 — historique cumulatif long mais fenêtre courante trouée

`flow_observations` est cumulatif. Un instrument pouvait donc avoir plus de 20 flux valides au total alors que les 20 observations les plus récentes contenaient un trou. EFS pouvait alors être renormalisé sur trop peu de composantes.

Le même risque existait pour l'étiquette `MATURE_60_PLUS`.

### Correction V21.16

- `current_20d_window_complete` exige un taux organique 20 observations et une persistance 20 observations calculables ;
- cumul ≥20 mais fenêtre courante incomplète : `efs_shadow = NaN` et `DATA_INSUFFICIENT_CURRENT_20D_WINDOW` ;
- `MATURE_60_PLUS` exige cumul ≥60 et fenêtre courante 60 observations complète ;
- cumul ≥60 mais fenêtre 60 observations trouée : `PRELIMINARY_GAPPED_60_PLUS` ;
- les seuils numériques 20/60 restent inchangés : il s'agit d'une condition d'intégrité, pas d'un retuning.

## Défaut 4 — provenance temporelle BlackRock / iShares

L'ancien parseur pouvait trouver une date `as of` quelque part sur une page BlackRock/iShares et considérer ensuite AUM, NAV et shares comme explicitement datés, même lorsqu'un champ avait été extrait par un fallback sans sa propre date.

### Correction V21.16

- AUM, NAV et shares sont parsés avec leur propre couple `valeur + date` ;
- une date trouvée pour un champ n'est jamais héritée par un autre champ ;
- la date du snapshot officiel est choisie à partir de la couverture des champs explicitement datés ;
- un champ explicitement daté à une date différente du snapshot est supprimé du snapshot utilisé pour les calculs ;
- les champs non datés peuvent rester visibles mais leur flag explicite reste `false` ;
- `official_field_dates`, `official_field_date_conflict` et `official_conflicting_fields_dropped` rendent les conflits auditables.

Des tests couvrent notamment AUM/NAV/shares à dates identiques, AUM conflictuel, shares conflictuel et champs totalement non datés.

## Persistance PIT durcie

Le workflow quotidien conserve la logique de cache historique, mais V21.16 ajoute des protections opérationnelles :

- les tests V21.16 et Ruff sont obligatoires avant collecte ;
- les invariants SHADOW/fail-closed sont revérifiés après calcul ;
- le cache PIT n'est sauvegardé que si le run complet est `SUCCESS` ;
- un run partiellement échoué ne peut donc plus remplacer l'état PIT valide ;
- chaque run produit `ETF_FUND_FLOW_STATE_AUDIT.json` avec nombre de lignes, nombre d'instruments, dates min/max, doublons, présence des flags temporels et SHA-256 ;
- le CSV d'historique PIT est également conservé comme artifact pendant 30 jours en complément du cache.

## Preuve réseau réelle V21.16

Workflow de preuve : run `32414437190`, head `75618f538beccb783f7637d904a6f6aecd27d3d6`.

Résultat : `SUCCESS` sur la collecte réelle et tous les invariants fail-closed.

- univers : 142 instruments, dont 102 ETF PEA et 40 satellites/contrôles ;
- snapshot réel retenu : 139 observations ;
- instruments observés : 138 ;
- ETF PEA observés : 100 ;
- `sharesOutstanding` non datées neutralisées : 16 ;
- instruments scorables : 0 ;
- instruments matures : 0 ;
- secteurs SRFS scorables : 0 ;
- lignes rotation : 0 ;
- `decision_influence = 0.0` ;
- `live_orders_enabled = false` ;
- 5 échecs de collecte explicites et séparés des données valides.

L'absence de score est le comportement attendu : il n'existait pas encore assez d'historique PIT pour atteindre le gate 20 observations.

Artifact réel : `V21_16_REAL_FUND_FLOW_SHADOW_32414437190`, digest `sha256:16f75f165e5d56f5cf6e6089550001e1ae2b0c192fb680ab90b342de29caf137`.

## Preuve de persistance isolée

Le même run a sauvegardé l'état produit dans un namespace de cache de test, sans muter le cache de production. Un second job indépendant a restauré exactement cette clé.

Résultat `V21.16_CACHE_ROUNDTRIP_PROOF` :

- `status = SUCCESS` ;
- `exact_cache_hit = true` ;
- 139 lignes restaurées ;
- 138 instruments restaurés ;
- `production_cache_mutated = false`.

Artifact : `V21_16_CACHE_ROUNDTRIP_32414437190`, digest `sha256:885275f9527ee3b2ce92b506da70f82106037effb8138366e8f0ee0597a5ed3b`.

## Pourquoi aucun cache Fund Flows de production n'était encore présent

Le module Fund Flows V1.0 a été fusionné sur `main` le 20/08/2026 à 10:28 heure de Paris. Son workflow quotidien est planifié à `21:45 UTC`, soit 23:45 heure de Paris en été.

Lors des preuves V21.16 effectuées avant cette première échéance, aucune exécution quotidienne de production n'avait donc encore pu créer le premier cache. Il ne s'agissait pas d'une perte d'historique : l'accumulation planifiée n'avait simplement pas encore commencé.

## Gouvernance inchangée

- `decision_influence = 0.0` ;
- ETF MT 38 PIT inchangé ;
- Sector Rotation V2 inchangé ;
- Gold V1.1 inchangé ;
- Crypto sans influence ordre ;
- T1/T2 non concernés ;
- aucun holdout ouvert ;
- aucun retuning des poids ou seuils ;
- aucune promotion autorisée par V21.16.

## Validation

Sur le head de code testé :

- compilation : PASS ;
- Ruff : PASS ;
- audit Python statique : PASS ;
- intégrité master : PASS ;
- intégrité référentiels/gouvernance : PASS ;
- suite pytest complète : PASS ;
- audit identité : PASS ;
- collecte réelle SHADOW : PASS ;
- invariants fail-closed : PASS ;
- cache round-trip isolé : PASS.

La promotion éventuelle de Fund Flows reste conditionnée à l'accumulation d'un historique PIT suffisant puis à une validation PIT/OOS dédiée. V21.16 sécurise la collecte et la maturité des données ; elle ne prétend pas démontrer une performance alpha.
