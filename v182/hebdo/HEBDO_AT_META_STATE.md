# Hebdo AT Meta — état de référence

Dernière consolidation : **2026-09-02**  
Branche de référence : `hebdo-at-meta`

## Gouvernance

Ce document est la porte d'entrée de l'état courant de HEBDO AT META.

Ordre de priorité en cas de conflit :

1. code et workflows présents au HEAD de `hebdo-at-meta` ;
2. runs GitHub Actions réussis correspondant au HEAD ou à ses ancêtres récents ;
3. dernière décision fonctionnelle Work explicitement identifiée ;
4. artefacts publiés et contrats de validation ;
5. anciennes conversations seulement en dernier recours.

Aucune modification de recherche ne devient une règle active sans validation distincte. Les résultats 2023–2026 sont **holdout/OOS** et ne doivent jamais être utilisés pour retuner une règle choisie sur 2010–2022.

## Référence fonctionnelle officielle

La dernière référence fonctionnelle explicitement identifiée reste **Audit 73 intégré**.

Décision fonctionnelle à préserver : conservation chronologique PIT de l'historique consensus Boursorama, sans antidatation, sans remplacement rétroactif d'une valeur absente et sans activation automatique du consensus dans le scoring.

Les travaux techniques et études postérieurs décrits ci-dessous sont une **consolidation de recherche post-Audit 73**. Ils ne constituent pas un Audit 74 métier.

## Règles PIT et historique

- développement / fit : **2010–2022 uniquement** ;
- holdout / OOS : **2023–2026**, évaluation uniquement ;
- PIT / anti-look-ahead obligatoire ;
- aucune donnée future utilisée pour combler une donnée manquante ;
- aucune imputation synthétique dans les études longitudinales ;
- données OHLCV incomplètes ou incohérentes exclues fail-closed ;
- toute comparaison dépendant d'une donnée consensus/fondamentale doit utiliser une cohorte disposant réellement de cette donnée à la date de décision.

Corpus gouverné PRE2023 :
- run `33550367844` — SUCCESS ;
- développement 2010–2022.

Qualité longitudinalement vérifiée :
- développement : `4 055 044` lignes utilisables, `1 623` tickers, aucune ligne imputée ;
- holdout : `1 578 718` lignes utilisables, `1 782` tickers ;
- `2 143` lignes holdout incomplètes/non fiables exclues ;
- aucune imputation.

## Consensus Audit 73

### Boursorama

L'historique Boursorama doit rester append-only et PIT. Les périodes relatives FactSet ne reçoivent jamais de fausse date historique.

### Finnhub

Le sidecar historique conserve les séries réellement récupérées avec séparation entre `provider_period` et `available_at`. L'identité Finnhub est fail-closed : aucun symbole n'est certifié sans correspondance ISIN exacte.

### Limite historique démontrée

Run longitudinal Audit 73 : `33596161484` — SUCCESS.

Les snapshots consensus certifiés récupérables commencent seulement fin août 2026, alors que les signaux J+1 suffisamment matures du backtest s'arrêtent au premier semestre 2026. Il n'existe donc **aucun chevauchement PIT historique exploitable** permettant d'attribuer une performance aux filtres objectif/consensus.

Conséquence : les variantes objectif >20 %, consensus positif, amélioration du consensus et seuils d'analystes restent **FORWARD_RESEARCH_ONLY**. Aucune valeur actuelle ne doit être appliquée rétrospectivement.

## TABPORT — référence technique

Chaîne de référence :

`B_V2 -> META -> confirmation J+1 -> TABPORT`

Paramètres conservés :
- capital initial : `65 000 EUR` ;
- maximum : `12` lignes ;
- maximum par ligne : `4 500 EUR` ;
- `5` entrées/mois ;
- `40` entrées/an ;
- frais : `0,20 %` par côté ;
- slippage : `0,10 %` par côté ;
- stop fixe : `-9 %` ;
- horizon maximum de référence : **126 séances**.

Longitudinal 2010–2026 de référence :
- 556 trades ;
- win rate 38,49 % ;
- espérance +3,10 % / trade ;
- PF 1,55 ;
- RR 2,48 ;
- rendement cumulé +118,18 % ;
- drawdown max -17,82 % ;
- performance annualisée approximative ~4,8 %/an.

Cette performance reste très inférieure à l'objectif économique du projet (>15 % net annuel, RR de préférence >3,3). La baseline est donc une **référence technique**, pas un modèle final suffisamment performant.

## Consolidation des études post-Audit 73

### 1. Garde-fous au niveau du titre

Les filtres simples `vol_z`, ATR et probabilité de stop ne sont pas suffisamment robustes. Une combinaison volume + risque améliore certains agrégats OOS mais n'est pas stable dans le temps.

Décision : **RESEARCH_ONLY / NON PROMU**.

### 2. Régime de marché

Les filtres imposant un marché « sain » dégradent fortement le holdout. La stratégie B/META bénéficie précisément de phases de capitulation/rebond ; filtrer les marchés faibles supprime des gagnants importants.

Décision : **REJETÉ comme filtre de production**.

### 3. Capitulation × confirmation J+1

Run `33600934483` — SUCCESS, 65 tests, artefact `9835407118`.

Meilleure variante agrégée observée : `J1_INTRADAY_GE_DEV_Q50` :
- PF OOS 2,064 ;
- RR 2,479 ;
- espérance +5,466 % ;
- rendement segment +23,356 % ;
- DD -6,992 %.

Mais l'avantage annuel n'est pas stable et provient surtout de 2024.

Décision : **RESEARCH_ONLY / NON PROMU**.

### 4. Sorties précoces / gestion post-entrée

Run `33602199980` — SUCCESS, 69 tests, artefact `9835942857`.

Les variantes `FAIL_FAST_J2`, invalidation structurelle, momentum J3, break-even/trailing et leurs combinaisons dégradent la performance OOS par rapport à la baseline.

Conclusion : les sorties précoces détruisent une partie de la convexité des rares gros gagnants.

Décision : **REJETÉES ; conserver la logique de sortie baseline**.

### 5. Reclassement des candidats sans exclusion

Run `33608312167` — SUCCESS, artefact `9838303060`.

Famille figée : baseline EV, EV + J1, EV + risque, EV + J1 + risque, EV + J1 + volume, variante équilibrée.

Le modèle choisi uniquement sur 2010–2022 est **BASELINE_EV** avec le meilleur objectif développement (`20,2942`). Les variantes ayant de meilleurs agrégats ponctuels sur le holdout ne peuvent pas être promues a posteriori.

Décision : **conserver le classement baseline**.

### 6. Horizon de détention 63 / 126 / 189 / 252 séances

Run `33620294387` — **SUCCESS**, 68 tests.  
Artefact : `TABPORT-HOLD-HORIZON-33620294387`, ID `9842895680`.  
Résultats détaillés : `TABPORT_HOLD_HORIZON_DEV_ONLY_RESULTS.md`.

Cohorte commune :
- 4 380 signaux confirmés en entrée ;
- 4 168 signaux disposent d'au moins 252 séances futures par titre ;
- 212 signaux exclus pour maturité insuffisante ;
- dernière date de signal commune : `2025-08-25` ;
- zéro sortie `EOP_DATA_END` pour les quatre variantes.

Objectif développement 2011–2022 :
- H63 : `6,9133` ;
- **H126_BASELINE : `20,2942`** ;
- H189 : `17,5063` ;
- H252 : `19,8741`.

Décision ex ante : **H126_BASELINE reste sélectionné**.

Observations OOS sur cohorte commune :
- H126 : PF 1,578 ; RR 1,845 ; espérance +2,71 % ; rendement +9,72 % ; DD -6,98 % ;
- H189 : PF 2,176 ; RR 3,228 ; espérance +7,10 % ; rendement +19,63 % ; DD -7,58 % ;
- H252 : PF 1,747 ; RR 3,478 ; espérance +5,36 % ; rendement +12,32 % ; DD -5,46 %.

H189 est très intéressant OOS et H252 apporte de la convexité, mais aucun des deux n'a gagné la sélection développement. Ils restent **RESEARCH_ONLY**. H63 est rejeté.

Les lignes annuelles 2026 de cette étude correspondent à des sorties de positions issues de signaux antérieurs au 25 août 2025 ; elles ne constituent pas une validation indépendante de génération de signaux 2026.

## État de décision au 2 septembre 2026

Aucune étude post-Audit 73 n'a justifié une modification de production.

**Baseline active de référence :**
- sélection / classement EV existant ;
- confirmation J+1 existante ;
- stop fixe -9 % ;
- horizon maximum 126 séances ;
- capacité 12 lignes / 5 entrées par mois / 40 par an ;
- aucune sortie précoce supplémentaire ;
- aucun filtre de régime marché ;
- aucun filtre consensus rétroactif.

Pistes conservées uniquement en recherche :
- H189 ;
- H252 / convexité longue ;
- combinaison volume + risque ;
- confirmation J1 intraday forte ;
- consensus Boursorama/Finnhub en forward validation réelle.

## Prochain axe autorisé

Les études d'entrée, de régime, de sortie, de reclassement et d'horizon montrent que la baseline est difficile à battre sous sélection ex ante stricte.

Le prochain axe de recherche doit donc porter sur **l'allocation du capital / dimensionnement des positions**, en conservant :
- le même univers de signaux ;
- le classement baseline ;
- les sorties baseline ;
- l'horizon 126 séances ;
- le stop -9 % ;
- la séparation développement 2010–2022 / holdout 2023–2026.

Aucun paramètre d'allocation ne devra être choisi sur le holdout.

## Discipline de reprise

À chaque reprise :
1. relire le HEAD `hebdo-at-meta` ;
2. vérifier les derniers runs Actions ;
3. préserver Audit 73 comme référence fonctionnelle tant qu'aucun audit métier ultérieur n'est explicitement validé ;
4. préserver PIT/no-look-ahead et la séparation 2010–2022 / 2023–2026 ;
5. ne promouvoir aucune variante à partir d'un meilleur résultat holdout observé a posteriori ;
6. mettre à jour ce document après toute validation qui change réellement l'état de référence.
