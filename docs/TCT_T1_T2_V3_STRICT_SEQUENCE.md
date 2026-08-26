# T1/T2 V3 — séquence technique stricte

## Périmètre

T1/T2 reste limité aux Actions TCT, en mode shadow sans influence sur le score et sans ordre réel. Tous les calculs utilisent des séances journalières terminées.

## T1

Le T1 exige simultanément :

- un squeeze Bollinger pendant chacune des cinq séances ouvrées terminées précédant le signal ;
- un franchissement de la bande supérieure par le cours ;
- une largeur des bandes supérieure à celle de la séance précédente ;
- un véritable croisement stochastique, avec `%K <= %D` la veille puis `%K > %D` au T1 ;
- un volume supérieur à celui de la séance précédente et au moins égal à 1,20 fois la moyenne des 20 séances antérieures ;
- un MACD encore inférieur à son signal, avec histogramme négatif ;
- un cours supérieur au SAR et à la MM50.

Les règles existantes de non-extension, qualité, couverture et appartenance au Top 20 TCT restent obligatoires.

## T2

Le T2 ne peut exister que comme confirmation d'un T1 V3 éligible et persistant. Il exige simultanément :

- un cours toujours strictement supérieur à la bande supérieure de Bollinger ;
- une largeur des bandes supérieure à celle de la séance précédente et au moins 15 % supérieure à sa valeur au T1 ;
- `%K > %D` ;
- un véritable croisement haussier du MACD, avec histogramme inférieur ou égal à zéro la veille puis positif au T2 ;
- un volume supérieur à celui de la séance précédente et au moins égal à 1,20 fois la moyenne des 20 séances antérieures ;
- un cours supérieur au SAR et à la MM50 ;
- le respect des règles de non-extension, qualité, couverture, délai maximal de 10 séances et revalidation du Top 20 TCT.

Un état T1 provenant d'une autre version de formule est rejeté. Le T2 consomme l'état T1 seulement après confirmation complète.
