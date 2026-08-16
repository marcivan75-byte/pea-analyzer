# SECTOR ROTATION V2 — protocole PIT/OOS verrouillé

## Objet

Ce protocole transforme le module `SECTOR_ROTATION_V2` déjà fusionné en expérimentation économique auditable sans modifier son statut `SHADOW_ONLY`.

Il ne cherche pas à reconstruire rétrospectivement des signaux V2 qui n'ont jamais été observés en temps réel. Une reconstruction à partir de l'univers, des révisions, des news ou des constituants actuels ne constitue pas une preuve de promotion.

## Preuves figées à chaque run

Chaque snapshot conserve désormais :

- le score et le rang V2 ;
- le score et le rang V1 observés au même instant ;
- le DQS et les warnings V2 ;
- la composition exacte de chaque secteur scoré ;
- l'ISIN et le ticker Yahoo des constituants disponibles au moment du signal.

La composition figée évite qu'un secteur soit évalué plus tard avec un univers survivant différent de celui réellement disponible à la date du signal.

## Construction des résultats futurs

L'horizon principal est fixé à 60 séances.

Pour chaque secteur et snapshot :

1. les constituants figés au jour du signal sont repris ;
2. leur historique OHLCV auto-ajusté est utilisé uniquement après la date du signal ;
3. chaque trajectoire est normalisée à 1 à l'entrée ;
4. le panier sectoriel est égal-pondéré par offset de séance ;
5. rendement J+60, MAE et MFE sont calculés ;
6. au moins 3 constituants et 70 % de couverture prix sont requis.

Les mêmes résultats sectoriels servent à comparer V2, V1 et la baseline neutre. Le résultat économique n'est donc pas redéfini selon le modèle testé.

## Fenêtres verrouillées avant résultats

- `VALIDATION_OOS` : 1 septembre 2026 au 31 décembre 2026 ;
- `DIAGNOSTIC_OOS` : 1 janvier 2027 au 30 avril 2027 ;
- holdout final verrouillé à partir du 1 mai 2027.

Le code ne calcule pas les performances du holdout final. Un protocole séparé sera nécessaire pour l'ouvrir.

## Gates de pré-holdout

Chaque période OOS doit simultanément satisfaire :

- au moins 8 snapshots indépendants espacés d'au moins 10 jours ;
- DQS minimum 80 ;
- au moins 6 secteurs éligibles par snapshot ;
- couverture V1 et couverture des résultats futurs d'au moins 80 % ;
- Top 3 V2 supérieur au Top 3 V1 d'au moins 0,50 point de rendement moyen ;
- Top 3 V2 supérieur à l'égal-pondération de tous les secteurs éligibles d'au moins 0,75 point ;
- taux de snapshots positifs dégradé de moins de 5 points par rapport à V1 ;
- MAE moyenne dégradée de moins de 1 point par rapport à V1 ;
- percentile 10 des rendements dégradé de moins de 1 point par rapport à V1.

Même si tous les gates passent, `promotion_ready` reste `false` : le holdout final demeure fermé.

## Validation spécifique du warning de survalorisation

Le warning `PROMISING_BUT_OVERVALUED` est évalué séparément parmi les leaders `RLS >= 70` et `DQS >= 80`.

Il faut au minimum :

- 8 leaders flaggés ;
- 12 leaders non flaggés ;
- et soit au moins 1 point de sous-performance future des flaggés, soit au moins 1,5 point de MAE plus défavorable.

Cette règle reconnaît qu'un secteur cher peut continuer à monter tout en présentant un risque de drawdown sensiblement supérieur. Le warning reste donc un mécanisme `NO_CHASE`/prudence, pas un ordre de vente automatique.

## Gouvernance

Le protocole interdit :

- le retuning des seuils après lecture des résultats ;
- l'optimisation des poids sur ces mêmes périodes ;
- toute modification automatique des scores Actions ou ETF ;
- tout BUY/SELL ou ordre réel ;
- toute promotion fondée sur des tests synthétiques ;
- toute utilisation du holdout final avant un protocole d'ouverture distinct.

Le statut normal à court terme est `WAIT_FOR_PIT_HISTORY`. Ce statut est attendu et préférable à un faux résultat OOS obtenu par reconstruction rétrospective.
