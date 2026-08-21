# Politique de cache ACTION MT V1

Le cache fournit des historiques OHLCV déjà collectés par la chaîne gouvernée. ACTION MT ne possède aucun fallback réseau caché.

## Contrat

- Racine par défaut : `data/cache/actions`.
- Clé : ISIN alphanumérique normalisé.
- Formats : Parquet prioritaire, CSV en secours.
- Fraîcheur maximale : 7 jours calendaires, configurable.
- Empreinte SHA-256 calculée sur chaque fichier lu.
- Cache manquant, périmé ou invalide : instrument non calculable, jamais imputé.
- Les erreurs de lecture sont transformées en diagnostics sans exposer de secrets.
- Le manifeste publie hits, misses, stale, invalid et hit rate.

## Colonnes et garde de clôture

L'index représente la date de séance. Colonnes minimales : `close`, `volume`; `open`, `high`, `low` restent disponibles pour les validations étendues. Avant 18:00 Europe/Paris, toute ligne datée du jour courant est retirée du calcul.

## Reproductibilité

Le rapport conserve le hash de configuration et le ledger conserve une empreinte du snapshot. Un second run identique n'ajoute pas de doublon au ledger PIT.

