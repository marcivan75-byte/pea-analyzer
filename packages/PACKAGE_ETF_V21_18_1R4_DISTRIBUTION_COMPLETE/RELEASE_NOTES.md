# Notes de version V21.18.1R4

R4 applique les recommandations de l'audit R3 sans modifier les 268 critères ni activer de couche non prouvée.

- Ajout d’un README racine et d’un inventaire complet.
- Code, configurations et données de référence disponibles directement, sans extraction d’un ZIP imbriqué.
- Ajout des politiques de cache et de sécurité.
- Ajout des schémas JSON d’observation, métrique et promotion.
- Ajout d’un outil de validation autonome.
- Ajout d’empreintes SHA-256 internes pour chaque fichier du payload.
- Conservation stricte des fonctions non prouvées à l'état OFF.
- Registre validé par empreinte, enums, filtres et normalisation séparée des poids.
- Cache atomique avec quarantaine et opérations bulk.
- Seuils quantitatifs de promotion, DQS configurable et métriques JSONL p50/p95.
- Exemples JSON Schema, validation stricte et rapport JSON machine-readable.

Aucun poids, seuil, ordre, holdout ou statut live n’est modifié.
