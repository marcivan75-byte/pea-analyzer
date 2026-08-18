# TCT V24.2.x — Audit de statut du chantier

Date : 18/08/2026

## Verdict

**TECHNIQUE SHADOW : VALIDÉ EN NON-RÉGRESSION**

**STATISTIQUE / PROMOTION : NON VALIDÉ — ACCUMULATION PIT REQUISE**

Le chantier reste ouvert au sens WIP=1. Le CT reste gelé.

## Périmètre intégré

### V24.2.0 Intraday / Scalping SHADOW

- couche ACTION TCT uniquement ;
- signaux sources T1/T2 exacts ;
- interdiction d'utiliser l'intraday de la même séance que le signal journalier ;
- première séance éligible J+1 ;
- fenêtre initiale J+1 à J+3 ;
- données 5 minutes séparées du cache journalier ;
- VWAP, RVOL par slot, volume acceleration, opening range, breakout/retest, VWAP reclaim, volatilité, turnover, momentum ;
- spread et order flow uniquement si réellement disponibles ;
- 4 setups séparés ;
- ledgers PIT persistants ;
- MFE/MAE/rendement clôture utilisés uniquement comme labels post-événement ;
- influence production nulle.

### V24.2.1 Analytics SHADOW

- expectancy brute à la clôture ;
- taux positif ;
- gain/perte moyens ;
- profit factor brut ;
- MFE/MAE ;
- segmentation setup / T1-T2 / J+1-J+3 / heure / score / état ;
- aucune expectancy nette tant que la friction observée n'est pas suffisamment couverte ;
- aucun retuning automatique ;
- aucun pouvoir de promotion.

## Audits CI

### PR #79 — V24.2.0

- full audit GitHub Actions `#32187851396` : **SUCCESS** ;
- ETF MT non-régression `#32187851393` : **SUCCESS** ;
- merge squash : `f5c1616`.

Contrôles dédiés verts :

- mutation de toutes les barres futures sans modification des features antérieures ;
- opening range non actionnable avant sa clôture ;
- signal journalier J non exploitable intraday avant J+1 ;
- aucune connexion au `daily_tct_ct_runner` canonique ;
- décision/score/sizing/stop influence = 0 ;
- aucun ordre réel.

### PR #80 — V24.2.1

- full audit GitHub Actions `#32188361478` : **SUCCESS** ;
- merge squash : `5515ac8`.

Contrôles dédiés verts :

- calcul des métriques descriptives ;
- segmentation temporelle et par setup ;
- seuils de maturité pré-enregistrés ;
- `promotion_authority = false` ;
- `retuning_allowed = false` ;
- isolation du runtime canonique.

## Seuils de maturité gelés avant résultats

- <10 entrées : `ACCUMULATING_EARLY` ;
- 10 à 29 : `ACCUMULATING_DESCRIPTIVE_ONLY` ;
- ≥30 : contrôle de diversité ;
- ≥10 ISIN distincts ;
- ≥15 entrées dans un setup avant revue de ce setup.

Atteindre ces seuils ne constitue pas une validation. Le statut maximal automatique est `READY_FOR_PRE_REGISTERED_REVIEW_NOT_PROMOTION`.

## Point restant bloquant

Aucun **run représentatif post-intégration sur données réelles V24.2.x** n'a été validé dans le cadre de ce chantier au moment du présent audit. Les tests synthétiques et CI démontrent la causalité et la non-régression du code, mais ne démontrent pas l'avantage statistique du scalping appliqué au TCT.

Il est donc interdit de conclure à une amélioration de performance à ce stade.

## Definition of Done restante

Pour clore le chantier TCT V24.2.x :

1. accumuler les observations PIT réelles ;
2. atteindre au minimum les seuils de maturité pré-enregistrés ;
3. contrôler couverture des données et frictions ;
4. auditer les résultats par setup et par régime ;
5. pré-enregistrer une hypothèse candidate sans retuning opportuniste ;
6. réaliser la validation PIT/OOS appropriée sans ouvrir le holdout final prématurément ;
7. vérifier espérance, drawdown, queue de pertes, taux positif, profit factor et rendement ajusté du risque ;
8. promouvoir uniquement si le gain marginal est robuste ; sinon rejeter le challenger ;
9. seulement ensuite évaluer séparément un éventuel transfert au CT.

## Gouvernance maintenue

- Production : V21.8.1 inchangée ;
- TCT V24.2.x : SHADOW_RESEARCH_ONLY ;
- CT : gelé pendant le chantier ;
- holdout final : fermé ;
- ordres réels : désactivés ;
- take-profit fixe : désactivé ;
- stop-loss fixe V24.2 : désactivé ;
- WIP : 1.
