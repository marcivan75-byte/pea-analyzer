# V21.13.13 — GDELT grouped-query SHADOW

## Statut

Expérience de réduction du nombre de requêtes réseau. **Non branchée sur les workflows planifiés, sans autorité de promotion et sans influence sur les décisions.**

La production V24.4.2 continue d'utiliser les requêtes GDELT individuelles exactes par société.

## Pourquoi cette expérience

Le fournisseur GDELT est actuellement protégé par un limiteur global conservateur d'un démarrage de requête par seconde. Avec une union TCT Top 20 + Action CT Top 20, le PREMARKET/POSTMARKET peut donc demander jusqu'à 40 requêtes individuelles et subir un plancher de latence important.

L'API DOC 2.0 supporte les blocs booléens OR, par exemple :

`("Company A" OR "Company B" OR "Company C")`

L'Article List accepte jusqu'à 250 résultats. Le prototype retient par défaut des groupes de 5 sociétés et demande au maximum 25 × 5 = 125 articles par groupe. Pour 40 candidats, le nombre théorique de démarrages réseau passe ainsi de 40 à 8 avant tout fallback.

## Garde-fous

### 1. Production inchangée

Le module expérimental `tct_catalyst_news_grouped_shadow_v21_13_13.py` n'est importé par aucun runner de production et n'est référencé par aucun workflow planifié.

### 2. Suffixe de requête

Le prototype n'accepte le batching que lorsque `candidate_query_suffix` est vide, comme dans la configuration V24.4.2 actuelle. Si un opérateur ou suffixe est ajouté ultérieurement, l'expérience échoue fermée au lieu de supposer une équivalence de syntaxe GDELT.

### 3. Attribution article → ISIN

Après filtrage PIT strict sur la fenêtre temporelle :

- le titre est normalisé de façon déterministe ;
- un article est attribué uniquement si le titre contient exactement un nom complet de société du groupe ;
- zéro correspondance = article non attribué et rejeté ;
- plusieurs correspondances = article ambigu et rejeté ;
- aucune heuristique ou attribution probabiliste n'est utilisée.

### 4. Absence de news

Si un groupe renvoie des articles mais qu'aucun ne peut être attribué à une société, cette société reçoit une erreur `GROUPED_SHADOW_NO_STRICT_TITLE_ATTRIBUTION`. Elle ne reçoit pas artificiellement un score « zéro news ».

### 5. Comparaison A/B fail-closed

Le comparateur individuel vs groupé exige l'égalité exacte de :

- erreur fournisseur ;
- nombre d'articles ;
- score de magnitude ;
- score directionnel ;
- types d'événements ;
- principaux titres.

Toute divergence classe l'ISIN en **fallback individuel**.

Le rapport calcule ensuite :

- taux d'équivalence exacte ;
- rappel de présence de news ;
- rappel des événements critiques ;
- nombre d'ISIN nécessitant un fallback ;
- nombre projeté de requêtes après fallback ;
- réduction nette projetée des requêtes.

`promotion_ready_exact_equivalence` est uniquement un indicateur de recherche. `production_activation` reste toujours `false` dans V21.13.13.

## Exemple de potentiel théorique

Pour 40 candidats :

- baseline : 40 requêtes ;
- groupes de 5 : 8 requêtes ;
- réduction brute : 80 %.

Ce gain n'est **pas** considéré réel tant qu'un A/B sur des snapshots représentatifs PREOPEN et POSTMARKET n'a pas mesuré les fallbacks nécessaires. Si, par exemple, 12 ISIN divergent, le coût projeté devient 8 + 12 = 20 requêtes, soit 50 % de réduction au lieu de 80 %.

## Critère de décision futur

Aucune promotion ne doit être faite sur la seule base du temps gagné. Il faudra au minimum démontrer sur plusieurs fenêtres représentatives que l'approche groupée conserve les événements et scores individuels, ou que le mécanisme de fallback restaure exactement les sorties individuelles avec une réduction nette de requêtes significative.

En cas de doute, la méthode individuelle actuelle reste la référence.
