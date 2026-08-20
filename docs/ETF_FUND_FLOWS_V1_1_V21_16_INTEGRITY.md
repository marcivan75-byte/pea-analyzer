# V21.16 — ETF Fund Flows V1.1 integrity hardening

Date : 20/08/2026

## Objet

V21.16 durcit le module `ETF Fund Flows` avant accumulation supplémentaire de son historique PIT. Le module reste `SHADOW_ONLY` : influence décisionnelle, ordres réels et promotion restent à zéro.

Aucun poids pré-enregistré n'est modifié. Les seuils de maturité restent 20 flux valides pour `PRELIMINARY` et 60 pour `MATURE`.

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

La breadth utilisait `rates.gt(0).mean()`. En pandas, un `NaN` devient `False` dans cette comparaison. Un instrument sans historique 20 jours pouvait donc être compté comme un flux non positif.

De plus, `Sector Rotation Flow Score` pouvait être construit à partir de breadth et confirmation régionale égales artificiellement à zéro avant que les instruments aient atteint 20 flux valides.

### Correction V21.16

- la breadth est calculée uniquement sur les taux 20 jours non manquants ;
- le dénominateur de confirmation régionale ne contient que les régions ayant un taux 20 jours valide ;
- SRFS exclut tout instrument sous le gate `PRELIMINARY` de 20 flux valides ;
- si aucun instrument d'un secteur n'est prêt, aucun score sectoriel n'est produit ;
- les diagnostics publient `undated_unchanged_aum_skipped` et `srfs_scorable_sectors`.

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

V21.16 ajoute des tests couvrant :

1. AUM Yahoo non daté et inchangé : aucun flux calculé ;
2. AUM non daté mais réellement modifié : calcul conservé et explicitement étiqueté ;
3. préférence du cours daté sur un NAV non daté pour le rendement ;
4. date du dernier cours Yahoo ;
5. `shares × NAV` comme dénominateur en absence d'AUM ;
6. SRFS vide avant 20 flux valides ;
7. breadth ignorant les valeurs manquantes ;
8. provenance temporelle champ par champ lors du merge multi-source.

La promotion éventuelle du module reste conditionnée à un historique PIT/OOS suffisant et à une comparaison challenger/baseline dédiée.