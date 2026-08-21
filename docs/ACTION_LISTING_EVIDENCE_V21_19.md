# V21.19 — preuves historiques de cotation Actions

Date de preuve : 21/08/2026

## Résultat réel

La collecte V21.17 ne parcourait que la première page de l'IPO Showcase Euronext. V21.19 ajoute la pagination historique officielle et corrige le parsing des dates européennes ambiguës en mode day-first.

Preuve réseau GitHub Actions `32485729404` :

- worklist initiale : 157 Actions à historique court ;
- preuves exact-ISIN acceptées au total : 132 ;
- preuves déjà gelées V21.17 : 6 ;
- nouvelles preuves A V21.19 : 126 ;
- quarantaines : 10 ;
- non résolues : 15 ;
- pages officielles parcourues : 16 ;
- lignes Euronext inspectées : 320 ;
- candidats officiels 2023+ : 281 ;
- doublons retirés : 3 ;
- pagination : complète ;
- arrêt : première page entièrement antérieure au 01/01/2023.

Artefact : `V21_19_REAL_EURONEXT_PAGINATION_PROOF_32485729404`, SHA-256 `86a3185b10877388f3d03fb0b169271d6124db8e725d743deb56b63e1a98a2de`.

## Référentiel gelé

Les 126 nouvelles preuves sont conservées dans `config/V21_19_ACTION_LISTING_EVIDENCE_A.csv.gz`. Le CSV décompressé a pour SHA-256 `ea1f7f39c3bc94952f8e8ee6d9af907d3e2711847d96e33cd6508c4bf5b370da` ; le fichier gzip déterministe a pour SHA-256 `69b20e4b154351e88f7ef533707c900cc79c677e772370dc3e06fd87973e63bb`.

Les 10 cas ambigus restent dans `config/V21_19_ACTION_LISTING_QUARANTINE.csv`. Les 15 ISIN sans preuve Euronext suffisante restent dans `config/V21_19_ACTION_SHORT_HISTORY_UNRESOLVED.csv` pour un chantier multi-bourses ultérieur.

## Gouvernance

Une date de cotation officielle explique la brièveté d'une série mais ne crée aucune donnée synthétique et ne rend jamais une série incomplète éligible à la calibration principale. L'éligibilité reste exclusivement `PRIMARY_FULL_FROM_ANCHOR`. Le stress set 2020–2022 garde un poids de calibration nul.

Aucun poids, seuil, score, stop, Entry/Exit, sizing, holdout ou périmètre T1/T2 n'est modifié.
