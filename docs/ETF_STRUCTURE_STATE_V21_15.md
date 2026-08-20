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

Le parseur V21.15 refuse aussi qu'une date générique future présente sur une page produit (par exemple une prochaine distribution) devienne la date `as_of` des faits structurels. Si aucune date d'observation admissible n'est trouvée, le fallback daté de la collecte est conservé.

## Contrat de validation

Les statuts acceptés par V21.15 doivent être **exactement** ceux du moteur de merge central : `VALIDATED`, `ISIN_MATCHED`, `AUTO_MATCH`, `ATTRIBUTED`. Le state layer ne peut donc pas élargir silencieusement les identités autorisées.

## Qualification réelle initiale — run 32397462820

La preuve focused réelle du 20/08/2026 est partie du master ETF réel V21.14 produit par le run `32381008688`, sans state V21.15 ni ledger de provenance préexistant. Elle a exécuté la collecte structurelle réseau, écrit le snapshot, scoré CT/LT, restauré le master pré-structure, puis rejoué le snapshot **sans nouvelle collecte structurelle réseau**.

Résultats :

- univers canonique : 102 ETF / 102 ISIN ;
- snapshot : 629 lignes sur 102 ISIN ;
- preuves : 94 A, 109 B, 426 C ;
- actifs fonds EUR : 102/102 persistés et rejoués ;
- diversification directe / HHI : 90/102, soit 88,24 % ;
- top holdings / concentration : 52/102, soit 50,98 % ;
- TER réseau : 102/102 ; snapshot initial : 100/102, soit 98,04 % ;
- replay : 629/629 lignes éligibles, 0 rejet, 0 quarantaine ;
- comparaison state réseau vs replay : 0 mismatch ;
- CT scorable : 99/102 après réseau et 99/102 après replay ;
- LT scorable : **83/102** après réseau et **83/102** après replay, contre 0/102 dans V21.14 ;
- gate LT maintenu à 70 % ; aucun poids ni seuil modifié ;
- aucun ordre réel, aucun élargissement T1/T2, aucun holdout ouvert.

Le snapshot initial a volontairement écarté deux TER de preuve A State Street (`IE00B910VR50` et `IE00B5M1WJ87`) car la page produit contenait une date générique future `30/11/2026`, prise à tort comme `as_of`. Le contrôle fail-closed a donc fonctionné comme prévu. Le parseur a ensuite été durci pour ignorer toute date générique postérieure au fallback de collecte (+1 jour), avec tests de régression dédiés. Cette correction ne change ni valeur TER, ni poids, ni seuil ; elle corrige uniquement la date de provenance.

Artefact de qualification : `V21_15_FOCUSED_REAL_STRUCTURE_STATE_32397462820`, digest GitHub `sha256:6932db569dfe4eecf9bec74aa42911a0a11b3e7e3ba77ab08d0ed309e9764e76`.

## Effets validés

- conserver au quotidien les TER/AUM structurels frais déjà vérifiés ;
- ne plus retomber artificiellement sur la baseline Yahoo pauvre après un changement de workflow ;
- permettre à V21.14 LT d'être scoré uniquement si la couverture réelle dépasse naturellement son gate 70 % ;
- ne jamais abaisser ce gate pour forcer un score ;
- ne modifier aucun poids, seuil, règle BUY/SELL ou ordre réel ;
- ne jamais étendre T1/T2 aux ETF ;
- ne pas ouvrir les holdouts.

## Fichiers permanents

- `config/ETF_STRUCTURE_STATE_V21_15.json` ;
- `src/v182/state/etf_structure_state.py` ;
- `src/v182/reporting/etf_structure_state_replay.py` ;
- `src/v182/reporting/etf_structure_refresh.py` ;
- `src/v182/sources/etf_structural_data.py` ;
- tests V21.15 state/date guard ;
- workflow quotidien existant `committee_tct_ct_daily.yml` avec replay state avant CT/LT.

Les workflows temporaires de preuve réelle ne font pas partie de la version à fusionner.

## Gates de promotion

Avant fusion, le head nettoyé doit conserver :

1. aucun workflow temporaire de preuve ;
2. compilation complète verte ;
3. Ruff vert ;
4. audit statique vert ;
5. intégrité master et gouvernance référentielle vertes ;
6. suite pytest complète verte ;
7. workflow identité vert ;
8. PR toujours fail-closed, sans changement de poids/seuil/holdout.
