# Audit ETP Satellite V1

- Base : `main` V21.10 (`bda392a7`).
- Branche WIP=1 : `agent/etp-satellite-gold-crypto-v1`.
- Tests locaux dédiés : 9/9 PASS avant push.
- Le cron Fund Flows existant est conservé ; aucun nouveau schedule n'est créé.
- Aucun poids, seuil, holdout, score PEA ou règle T1/T2 n'est modifié.
- Gold/Crypto restent hors PEA et sans influence décisionnelle.
- Crypto : aucun moteur alpha déclaré sans validation PIT/OOS.
- Produits short/inverses : voie séparée uniquement.
