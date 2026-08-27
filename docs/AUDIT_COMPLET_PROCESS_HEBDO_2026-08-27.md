# Audit complet du process HEBDO — 2026-08-27

## Périmètre

L'audit couvre le cœur CI pondéré, CI LIGHT autonome, la collecte des sources,
les gates Actions/ETF, T1/T2, l'anti-recoupement ETF, les couches Objectif/Risque,
le budget portefeuille, les publications, les validations et le packaging complet.

## Invariants préservés

- CI et CI LIGHT restent deux processus indépendants.
- Aucun critère, poids, seuil, univers ou règle PIT n'est modifié.
- Les sources externes ne peuvent pas créer un candidat CI.
- Les états incomplets restent fail-closed.
- Les calculs Objectif/Risque restent shadow et sans ordre réel.
- T1/T2 reste limité aux Actions TCT.

## Optimisations intégrées

1. Le cœur V22.2.3 produit déjà CI LIGHT de façon autonome. L'orchestrateur
   opérationnel transmet désormais ce résultat validé à l'overlay au lieu de
   relancer une seconde collecte CI LIGHT identique.
2. L'overlay V4 ne reconstruit plus l'amont CI immédiatement après son achèvement
   par le cœur. Son exécution autonome conserve néanmoins ce comportement par défaut.
3. Le ZIP et ses SHA-256 sont construits en flux par blocs de 1 Mio : les caches
   volumineux ne sont plus chargés intégralement en mémoire.
4. Le packaging reste déterministe, non-écrasant et assorti d'un manifeste SHA-256.

## Mesures et limites

La dernière baseline opérationnelle certifiée est de 1 019,423 secondes
(16 min 59 s), dont 885,021 s pour le cœur, 125,010 s pour la finalisation et
9,391 s pour l'ancien overlay. Les optimisations nouvelles retirent du chemin
critique la reconstruction amont et le second run CI LIGHT contenus dans cet overlay.

Le run de contrôle du 27 août a rencontré une erreur d'environnement dans la base
SQLite interne de yfinance (`unable to open database file`). Le collecteur a alors
tenté les symboles individuellement, ce qui mesure la défaillance du cache fournisseur
et non la durée nominale du process. Aucune relaxation métier n'a été appliquée.

## Verdict

Le process reste sous la cible gouvernée de 20 minutes sur la dernière exécution
certifiée. Les optimisations sont uniquement orchestrationnelles et mémoire : aucune
information n'est supprimée et les sorties décisionnelles restent protégées par les
mêmes gates.
