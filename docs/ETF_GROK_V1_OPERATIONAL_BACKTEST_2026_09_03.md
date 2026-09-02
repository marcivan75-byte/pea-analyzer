# ETF GROK V1 — benchmark opérationnel sur base durcie

Date: 2026-09-03

## Statut

Benchmark de recherche opérationnel, non promotion-eligible tant que l'univers historique PEA/membership n'est pas intégralement documenté.

## Boucle d'audit réalisée

Le premier replay produisait 164 trades et 87,20 % de gagnants, mais l'audit a montré 40 réouvertures/chevauchements d'un même instrument ainsi que 8 trades encore ouverts en fin de série inclus dans les statistiques. Cette version a été rejetée.

Corrections :

- capacité portefeuille limitée à 2 positions, conformément à `top_n=2` ;
- interdiction de détenir deux fois le même ISIN simultanément ;
- entrée strictement à la première séance postérieure au signal ;
- frais de 25 bp par côté appliqués multiplicativement ;
- `END_OF_DATA` séparé des trades clôturés ;
- benchmark equal-weight du même univers de recherche pour calcul d'excès de rendement ;
- audit explicite des overlaps, qui doit rester à zéro.

## Résultat corrigé R2

Période : 2013-01-01 au 2026-09-02. 165 dates de signal mensuelles. 68 ETF satisfont le profil de qualité MT close/volume et la profondeur minimale.

- trades total : 75
- trades clôturés : 73
- ouverts à fin de données : 2
- gagnants : 66
- win rate : **90,41 %**
- expectancy nette/trade : **+2,7423 %**
- profit factor : **4,5694**
- rendement médian net/trade : **+3,6687 %**
- perte maximale sur un trade : **-19,4529 %**
- excès de rendement moyen vs proxy equal-weight sur la même fenêtre de détention : **+0,4895 %**
- violations de chevauchement : **0**

Sorties : 66 `TARGET_CLOSE`, 6 `TIME_CLOSE`, 1 `STOP_CLOSE`, 2 `END_OF_DATA`.

## Lecture par régime temporel

Les années les plus faibles du replay corrigé sont 2015, 2017 et 2022. Elles doivent rester des fenêtres de stress obligatoires pour GROK 2 afin d'éviter qu'une amélioration globale masque une dégradation dans les marchés difficiles.

## Limite méthodologique restante

Les features prix/volume sont calculées strictement avec les informations disponibles à la date du signal, mais l'univers reste une reconstruction à partir de la liste contemporaine. Les dates historiques complètes d'éligibilité PEA, les entrées/sorties d'univers et les ETF disparus/fusionnés ne sont pas encore totalement documentés. Le résultat est donc un benchmark de recherche robuste pour comparer GROK V1 et GROK 2 sur une base identique, pas une preuve finale sans survivorship bias.
