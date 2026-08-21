# Sécurité et gouvernance

- Ne jamais stocker de secret, token ou clé API dans le package, les caches ou les logs.
- Les erreurs 401/403 des sources doivent échouer immédiatement.
- Les logs structurés ne contiennent que des identifiants non secrets, métriques et statuts.
- Les ordres réels sont hors périmètre de ce package.
- T1/T2 restent interdits aux ETF.
- Vérifier les SHA avant toute utilisation.
