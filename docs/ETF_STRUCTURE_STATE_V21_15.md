# V21.15 — Persistance gouvernée des données structurelles ETF

## Problème observé

Le run réel V21.14 du 20/08/2026 a montré que le chemin quotidien CT/LT voyait seulement 11 TER, 1 actif fonds EUR et 0 score de diversification directe, alors que la qualification V21.10 avait démontré que le collecteur structurel savait retrouver 102 TER et 102 actifs fonds EUR sur l'univers canonique de 102 ETF.

La cause est d'orchestration et non de scoring :

- `unified_runner` hebdomadaire appelle `etf_structure_refresh` après le refresh général ;
- le workflow quotidien appelle `reporting.run`, puis directement le module CT/LT V21.14 ;
- le ledger de provenance conserve les métadonnées et le hash des valeurs, mais pas les valeurs elles-mêmes ;
- sans snapshot structurel persistant, les données V21.10 collectées lors du run lourd ne sont donc pas rejouables par le run quotidien suivant.

## Architecture retenue

V21.15 sépare la **fréquence de collecte réseau** de la **fréquence d'utilisation du dernier fait gouverné**.

1. Le run lourd hebdomadaire conserve la responsabilité des accès réseau émetteurs / justETF / yfinance funds.
2. Après fusion gouvernée, `etf_structure_refresh` écrit `state/provenance/etf_structure/ETF_STRUCTURE_SNAPSHOT.csv`.
3. Ce fichier réutilise le cache persistant `state/provenance/` déjà restauré/sauvegardé par les workflows weekly et daily : aucun nouveau cache ni cron n'est créé.
4. Une valeur n'entre dans ce snapshot que si son hash correspond exactement au dernier enregistrement de provenance effectivement retenu.
5. Le workflow quotidien rejoue le snapshot sur le master enrichi avant CT/LT.
6. Le replay repasse par le moteur normal `apply_observations` : les règles de preuve A/B/C/D, de fraîcheur et de conflit restent donc inchangées.
7. Aucun scrape structurel réseau n'est ajouté au workflow quotidien.

## TTL fail-closed

Les données ne sont jamais rejouées indéfiniment :

- TER : 186 jours ;
- actifs fonds EUR / AUM de classe : 62 jours ;
- diversification, HHI secteur, concentration top holdings et compte observé : 14 jours.

Un timestamp absent, invalide ou futur est rejeté. Une ligne stale est rejetée. Un hash valeur/provenance incohérent est rejeté. Une clé ISIN/champ dupliquée est rejetée. Un statut de validation hors contrat est rejeté.

## Contrat de validation

Les statuts acceptés par V21.15 doivent être **exactement** ceux du moteur de merge central : `VALIDATED`, `ISIN_MATCHED`, `AUTO_MATCH`, `ATTRIBUTED`. Le state layer ne peut donc pas élargir silencieusement les identités autorisées.

## Effets attendus

- conserver au quotidien les TER/AUM structurels frais déjà vérifiés ;
- ne plus retomber artificiellement sur la baseline Yahoo pauvre après un changement de workflow ;
- permettre à V21.14 LT d'être scoré uniquement si la couverture réelle dépasse naturellement son gate 70 % ;
- ne jamais abaisser ce gate pour forcer un score ;
- ne modifier aucun poids, seuil, règle BUY/SELL ou ordre réel ;
- ne jamais étendre T1/T2 aux ETF ;
- ne pas ouvrir les holdouts.

## Fichiers

- `config/ETF_STRUCTURE_STATE_V21_15.json` ;
- `src/v182/state/etf_structure_state.py` ;
- `src/v182/reporting/etf_structure_state_replay.py` ;
- `outputs/audit/V21_15_ETF_STRUCTURE_STATE_WRITE.json` ;
- `outputs/audit/V21_15_ETF_STRUCTURE_STATE_REPLAY.json` ;
- `state/provenance/etf_structure/ETF_STRUCTURE_SNAPSHOT.csv`.

## Validation avant promotion du correctif

V21.15 doit démontrer sur un run réel :

1. collecte structurelle V21.10 réelle et écriture du state ;
2. second passage sans collecte structurelle réseau, après reconstruction d'un master courant ;
3. replay des mêmes valeurs gouvernées tant qu'elles sont fraîches ;
4. conservation des niveaux de preuve et des dates source ;
5. absence d'imputation ;
6. amélioration mesurable de la couverture CT/LT par rapport au run V21.14 ;
7. workflow temporaire de preuve supprimé avant fusion ;
8. CI final complet vert sur le head nettoyé.
