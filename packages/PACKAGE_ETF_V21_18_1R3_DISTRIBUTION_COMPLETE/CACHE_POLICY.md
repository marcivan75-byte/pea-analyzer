# Politique de cache

## Clé obligatoire

`namespace + instrument_id + as_of_date + source_hash`

## Règles

- timestamps UTC avec fuseau obligatoire ;
- observation future rejetée ;
- TTL dépassé : cache miss ;
- changement de hash : cache miss ;
- conflit de provenance : quarantaine ;
- aucune imputation neutre ;
- aucune donnée d’exemple utilisée en décision ;
- suppression/reconstruction autorisée uniquement à partir des sources réelles.

TTL configurés par la production : structure, OHLCV, flows, rotation et benchmark doivent rester séparés.
