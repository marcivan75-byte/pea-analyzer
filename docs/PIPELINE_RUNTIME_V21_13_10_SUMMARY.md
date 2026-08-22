# V21.13.10 — résumé

- V22.0 et V22.1 restent inchangés et sont exécutés dans le même ordre.
- Une seule lecture physique du cache historique Actions CT est visée quand leurs demandes sont identiques.
- Chaque moteur reçoit une copie profonde indépendante des DataFrames.
- Les sorties, états, fingerprints et validations PIT restent produits par les modules d'origine.
- Aucun critère, poids, seuil, univers ou règle de décision n'est modifié.
- Les workflows quotidien et hebdomadaire appellent désormais l'orchestrateur partagé.
- Un audit runtime mesure les lectures physiques réellement évitées et le temps du bundle.
