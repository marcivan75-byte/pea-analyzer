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

### 7. Dimensionnement fixe des positions

Run `33621469608` — **SUCCESS**, 68 tests.  
Artefact ID `9843367678`.

Famille figée : `3 000`, `3 750`, `4 500`, `5 000`, `5 400 EUR` par ligne. Seul `max_position_eur` change.

Objectif développement :
- 3 000 : `14,9265` ;
- 3 750 : `15,1545` ;
- **4 500 baseline : `15,8383`** ;
- 5 000 : `15,8065` ;
- 5 400 : `15,7756`.

Les tailles supérieures augmentent le rendement OOS mais aussi le drawdown. Les tailles inférieures réduisent le risque mais pénalisent davantage le rendement.

Décision : **conserver 4 500 EUR**.

### 8. Sensibilité du stop 7 / 8 / 9 / 10 / 11 %

Run `33631983745` — **SUCCESS**, 67 tests.  
Artefact ID `9847536688`.

Objectif développement :
- stop 7 % : `15,6575` ;
- stop 8 % : `13,3172` ;
- **stop 9 % baseline : `16,0902`** ;
- stop 10 % : `12,8343` ;
- stop 11 % : `14,2939`.

OOS :
- 7 % : PF 1,139 ; RR 2,670 ; rendement +2,97 % ; DD -9,64 % ;
- 8 % : PF 1,222 ; RR 2,117 ; rendement +5,74 % ; DD -8,87 % ;
- **9 % : PF 2,042 ; RR 2,246 ; rendement +19,08 % ; DD -9,85 %** ;
- 10 % : PF 1,854 ; RR 1,958 ; rendement +14,39 % ; DD -5,77 % ;
- 11 % : PF 1,322 ; RR 1,813 ; rendement +8,55 % ; DD -7,58 %.

Décision : **conserver le stop -9 %**.

### 9. Dimensionnement adaptatif au risque `prob_stop_9`

Run `33634156295` — **SUCCESS**, 68 tests.  
Artefact `TABPORT-RISK-SIZING-33634156295`, ID `9848329360`.

Aucun signal n'est filtré ; classement, stop -9 %, horizon 126 séances et sorties sont inchangés. Seul le budget de ligne varie selon `prob_stop_9`, avec seuils appris exclusivement sur 2010–2022 : q33 `0,128813`, q60 `0,178491`, q67 `0,265445`.

Objectif développement :
- **BASELINE_4500 : `16,8630`** ;
- HIGH_RISK_3750 : `15,6569` ;
- HIGH_RISK_3000 : `14,9728` ;
- THREE_TIER_4500_3750_3000 : `15,0417` ;
- UPSIDE_5000_4500_3750 : `15,9183`.

OOS :
- baseline : PF 2,042 ; rendement +19,08 % ; DD -9,85 % ;
- HIGH_RISK_3750 : PF 2,082 ; rendement +17,65 % ; DD -8,29 % ;
- HIGH_RISK_3000 : PF 2,109 ; rendement +16,84 % ; DD -6,97 % ;
- THREE_TIER : PF 2,095 ; rendement +17,17 % ; DD -7,31 % ;
- UPSIDE : PF 2,103 ; rendement +19,17 % ; DD -8,36 %.

Le sizing adaptatif réduit effectivement le drawdown OOS, mais **aucune politique ne bat la baseline dans la sélection développement**. La variante UPSIDE est intéressante a posteriori mais ne peut pas être promue.

Décision : **conserver 4 500 EUR fixes ; sizing adaptatif RESEARCH_ONLY**.

## Diagnostic transversal des pertes persistantes

Analyse sur les trades 2010–2022 :
- les mauvaises années 2011 / 2018 / 2022 ont un `prob_stop_9` moyen plus élevé (~`0,227`) que les bonnes années 2013 / 2017 / 2019 / 2021 (~`0,176`) ;
- ATR médian également plus élevé dans les mauvaises années (~`3,05 %` contre ~`2,58 %`) ;
- `drawdown_4w` est plus négatif dans les mauvaises années ;
- `vol_z` distingue partiellement stops et gagnants mais n'est pas monotone de façon assez robuste ;
- le quintile de risque `prob_stop_9` le plus élevé contient encore des gagnants fortement convexes : un filtre binaire supprimerait donc aussi des gagnants importants.

Point structurel majeur :
- `EV_net` est pratiquement constant autour de `0,044` entre gagnants et perdants ;
- `prob_meta` est observé à `0,5` de façon quasi/totalement constante sur la cohorte étudiée.

Ces deux variables ne fournissent donc actuellement presque aucune discrimination économique entre candidats. Ce constat devient prioritaire par rapport à de nouveaux balayages de seuils.

## État de décision au 2 septembre 2026

Aucune étude post-Audit 73 n'a justifié une modification de production.

**Baseline active de référence :**
- sélection / classement EV existant ;
- confirmation J+1 existante ;
- stop fixe -9 % ;
- horizon maximum 126 séances ;
- position fixe 4 500 EUR ;
- capacité 12 lignes / 5 entrées par mois / 40 par an ;
- aucune sortie précoce supplémentaire ;
- aucun filtre de régime marché ;
- aucun filtre consensus rétroactif.

Pistes conservées uniquement en recherche :
- H189 ;
- H252 / convexité longue ;
- combinaison volume + risque ;
- confirmation J1 intraday forte ;
- sizing adaptatif au risque ;
- consensus Boursorama/Finnhub en forward validation réelle.

## Prochain axe autorisé

Les études d'entrée, régime, sortie, reclassement, horizon, sizing fixe, sizing adaptatif et stop montrent qu'un nouveau balayage de paramètres isolés a désormais une faible probabilité d'apporter une amélioration robuste.

Le prochain chantier prioritaire est donc un **audit de calibration / dégénérescence du score META** :
- expliquer pourquoi `prob_meta` vaut environ `0,5` de façon constante ;
- expliquer pourquoi `EV_net` vaut environ `0,044` de façon quasi constante ;
- déterminer si ces valeurs proviennent d'un fallback, d'une calibration neutralisée, d'un composant indisponible ou d'un calcul réellement non discriminant ;
- vérifier si le classement EV actuel est effectivement capable d'ordonner les candidats ;
- corriger uniquement une anomalie structurelle démontrée, sans inventer un nouveau score à partir du holdout.

Toute calibration éventuelle doit être apprise exclusivement sur **2010–2022** puis gelée avant une unique validation **2023–2026**.

## Discipline de reprise

À chaque reprise :
1. relire le HEAD `hebdo-at-meta` ;
2. vérifier les derniers runs Actions ;
3. préserver Audit 73 comme référence fonctionnelle tant qu'aucun audit métier ultérieur n'est explicitement validé ;
4. préserver PIT/no-look-ahead et la séparation 2010–2022 / 2023–2026 ;
5. ne promouvoir aucune variante à partir d'un meilleur résultat holdout observé a posteriori ;
6. mettre à jour ce document après toute validation qui change réellement l'état de référence.
