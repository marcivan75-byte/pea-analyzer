# Guide d’exploitation

1. Exécuter `python tools/validate_package.py`.
2. Vérifier que tous les garde-fous sont `false` dans `reference/criterion_registry.json`.
3. Alimenter les caches uniquement depuis des observations réelles et PIT.
4. Conserver le `run_id`, l’horodatage UTC, la source et le hash de valeur.
5. Produire les métriques de couverture, fraîcheur, DQS et latence.
6. Refuser tout score sous la couverture minimale du moteur.
7. Ne jamais promouvoir une couche shadow sans dossier OOS complet et revue indépendante.

Les répertoires `cache/*/data/` sont volontairement vides à la livraison.
