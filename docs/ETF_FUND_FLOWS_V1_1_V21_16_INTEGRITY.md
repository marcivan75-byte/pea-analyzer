# V21.16 — ETF Fund Flows V1.1 integrity hardening

Date : 20/08/2026

## Objet

V21.16 durcit le module `ETF Fund Flows` avant accumulation supplémentaire de son historique PIT. Le module reste `SHADOW_ONLY` : influence décisionnelle, ordres réels et promotion restent à zéro.

Aucun poids pré-enregistré n'est modifié. Les seuils restent 20 flux valides pour accéder au stade `PRELIMINARY` et 60 pour être candidat au stade `MATURE`, mais la fenêtre courante correspondante doit désormais être réellement complète.

## Défaut 1 — AUM Yahoo sans date source explicite

Le collecteur Yahoo utilisait `totalAssets`, `navPrice` et `sharesOutstanding` avec une date d'observation égale au jour du run. Yahoo ne fournit cependant pas nécessairement une date source explicite pour ces champs.

Si `totalAssets` restait inchangé alors que le prix ou le NAV variait, le fallback :

`flow_t = AUM_t - AUM_t-1 × (1 + total_return_t)`

pouvait produire un faux flux alors que l'AUM fournisseur était simplement inchangé ou rafraîchi à une fréquence différente.

### Correction V21.16

- la date du dernier cours réellement observé devient la date de marché Yahoo quand elle est disponible ;
- AUM, NAV, shares et market price portent chacun un indicateur `*_as_of_explicit` ;
- ces indicateurs suivent le champ lors d'une fusion de sources le même jour ;
- un AUM non daté explicitement et inchangé entre deux snapshots devient `UNSCORABLE_UNDATED_AUM_UNCHANGED` ;
- si l'AUM non daté change réellement, le calcul reste SHADOW mais est étiqueté `AUM_PERFORMANCE_ADJUSTED_UNDATED_VENDOR_UPDATE` ;
- pour le rendement, un prix de marché explicitement daté est préféré à un NAV non daté ;
- un flux `SHARES_NAV` peut utiliser `shares × NAV` comme dénominateur si l'AUM est absent.

Ce choix est volontairement fail-closed : une absence d'information temporelle ne devient jamais une hypothèse de flux.

## Défaut 2 — breadth / SRFS avec données manquantes

La breadth utilisait `rates.gt(0).mean()`. En pandas, un `NaN` devient `False` dans cette comparaison. Un instrument sans historique 20 observations pouvait donc être compté comme un flux non positif.

De plus, `Sector Rotation Flow Score` pouvait être construit à partir de breadth et confirmation régionale égales artificiellement à zéro avant que les instruments aient atteint 20 flux valides.

### Correction V21.16

- la breadth est calculée uniquement sur les taux 20 observations non manquants ;
- le dénominateur de confirmation régionale ne contient que les régions ayant un taux 20 observations valide ;
- SRFS exclut tout instrument sous le gate `PRELIMINARY` de 20 flux valides ;
- si aucun instrument d'un secteur n'est prêt, aucun score sectoriel n'est produit ;
- les diagnostics publient `undated_unchanged_aum_skipped` et `srfs_scorable_sectors`.

## Défaut 3 — historique cumulatif long mais fenêtre courante trouée

`flow_observations` est un cumul du nombre de flux calculables. Un instrument pouvait donc avoir plus de 20 flux valides au total tout en ayant un trou dans les 20 observations les plus récentes. Dans ce cas, les indicateurs 20 observations étaient `NaN`, mais `_weighted_mean` pouvait encore renormaliser EFS sur les seules composantes restantes.

Le même problème pouvait attribuer `MATURE_60_PLUS` à un instrument ayant au moins 60 flux valides cumulés alors que la fenêtre courante de 60 observations était incomplète.

### Correction V21.16

- `current_20d_window_complete` exige un taux organique 20 observations et une persistance 20 observations réellement calculables ;
- si le cumul atteint 20 mais que cette fenêtre courante est incomplète, `efs_shadow` reste `NaN` avec `DATA_INSUFFICIENT_CURRENT_20D_WINDOW` ;
- `MATURE_60_PLUS` exige à la fois au moins 60 flux valides cumulés et une fenêtre 60 observations complète ;
- un historique ≥60 dont la fenêtre 20 est complète mais la fenêtre 60 est trouée reste `PRELIMINARY_GAPPED_60_PLUS` ;
- les diagnostics comptent `current_20d_window_incomplete` et `mature_60d_window_incomplete` ;
- les seuils numériques 20/60 ne sont pas modifiés : il s'agit d'une condition d'intégrité de fenêtre, pas d'un retuning.

## Gouvernance inchangée

- `decision_influence = 0.0` ;
- ETF MT 38 PIT inchangé ;
- Sector Rotation V2 inchangé ;
- Gold V1.1 inchangé ;
- Crypto sans influence ordre ;
- T1/T2 non concernés ;
- aucun holdout ouvert ;
- aucun retuning des poids ou seuils.

## Tests de régression

V21.16 couvre :

1. AUM Yahoo non daté et inchangé : aucun flux calculé ;
2. AUM non daté mais réellement modifié : calcul conservé et explicitement étiqueté ;
3. préférence du cours daté sur un NAV non daté pour le rendement ;
4. date du dernier cours Yahoo ;
5. `shares × NAV` comme dénominateur en absence d'AUM ;
6. SRFS vide avant 20 flux valides ;
7. breadth ignorant les valeurs manquantes ;
8. provenance temporelle champ par champ lors du merge multi-source ;
9. cumul ≥20 mais fenêtre courante 20 observations trouée : aucun EFS ;
10. cumul ≥60 mais fenêtre 60 observations trouée : statut non mature.

La promotion éventuelle du module reste conditionnée à un historique PIT/OOS suffisant et à une comparaison challenger/baseline dédiée.