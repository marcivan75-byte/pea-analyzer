"""
Scoring pondéré TCT V24.1.2
Réajustement orienté performance horizon 1-5 jours.
"""

from typing import Dict, Any
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Pondération V24.1.2 (réajustée)
# ---------------------------------------------------------------------------
# Logique :
# - Concentrer le poids sur les facteurs à plus fort edge court terme
#   (Squeeze, Setup T1/T2, Earnings, Technique)
# - Réduire Valo / News / Cata (bruit ou horizon inadapté)
# - Conserver un peu de Bayes / Régime / RS pour le contexte
# ---------------------------------------------------------------------------

WEIGHTS_V24_1_2 = {
    "squeeze":  0.18,   # +6 pts – cœur du système
    "setup":    0.16,   # +4 pts – signal T1/T2
    "earnings": 0.14,   # +4 pts – fort près des résultats
    "t1_tech":  0.14,   # +2 pts – confirmation technique
    "bayes":    0.10,   # -2 pts – déjà partiellement couvert par meta
    "cata":     0.08,   # -4 pts – bruité en TCT
    "regime":   0.07,   # -1 pt
    "rs":       0.05,   # +1 pt  – momentum relatif utile
    "news":     0.04,   # -1 pt
    "valo":     0.04,   # -1 pt  – peu pertinent en 1-5j
}
# Total = 1.00

assert abs(sum(WEIGHTS_V24_1_2.values()) - 1.0) < 1e-9, "Weights must sum to 1.0"

def get_active_weights() -> dict:
    """Charge les poids adaptatifs si disponibles, sinon V24.1.2."""
    try:
        from src.ml.adaptive_weights import AdaptiveWeightsEngine
        engine = AdaptiveWeightsEngine()
        return engine.get_weights()
    except Exception:
        return WEIGHTS_V24_1_2


# Ancienne pondération (référence)
WEIGHTS_V24_1 = {
    "valo": 0.05, "t1_tech": 0.12, "setup": 0.12, "news": 0.05,
    "cata": 0.12, "squeeze": 0.12, "regime": 0.08, "rs": 0.04,
    "bayes": 0.12, "earnings": 0.10,
}


def compute_score_base(
    scores: Dict[str, float],
    weights: Dict[str, float] = None,
    renormalize_missing: bool = True,
) -> float:
    """Calcule le score pondéré sur 0-100.

    ``renormalize_missing=True`` conserve la logique native de l'application.
    Pour l'intégration au Free Capture du repo, ``False`` applique la gouvernance
    historique du projet : un pilier absent contribue 0 et son poids n'est pas
    redistribué.
    """
    if weights is None:
        weights = WEIGHTS_V24_1_2

    total = 0.0
    w_sum = 0.0
    for k, w in weights.items():
        v = scores.get(k)
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(fv):
            continue
        total += fv * w
        w_sum += w

    if w_sum <= 0:
        return 50.0 if renormalize_missing else 0.0
    if renormalize_missing and w_sum < 0.99:
        return float(np.clip(total / w_sum, 0, 100))
    return float(np.clip(total, 0, 100))


def apply_bonus_and_multi(
    score_base: float,
    setup: str = None,
    bonus: float = 0,
    multi_t1: float = 1.2,
    multi_t2: float = 1.4
) -> Dict[str, float]:
    """
    Applique bonus T1/T2 + multiplicateur (logique CDC).
    T2 écrase T1.
    """
    score_with_bonus = score_base + float(bonus or 0)

    multi = 1.0
    if setup == "T2_CONFIRMATION":
        multi = multi_t2
    elif setup == "T1":
        multi = multi_t1

    score_final = min(100.0, score_with_bonus * multi)
    return {
        "score_base": round(score_base, 2),
        "score_with_bonus": round(score_with_bonus, 2),
        "multi": multi,
        "score_final": round(score_final, 2),
    }


def build_component_scores(row: pd.Series, strict_missing: bool = False) -> Dict[str, float]:
    """Construit les sous-scores 0-100.

    En mode repo (`strict_missing=True`), une donnée non observée reste NaN au
    lieu d'être imputée à 50. Cela évite l'inflation de score sur les 732
    colonnes Free Capture lorsque certains piliers ne sont pas renseignés.
    """
    missing_default = np.nan if strict_missing else 50.0

    def g(key, default=None):
        if default is None:
            default = missing_default
        v = row.get(key)
        try:
            fv = float(v)
            return fv if np.isfinite(fv) else default
        except (TypeError, ValueError):
            return default

    squeeze = g("score_squeeze", np.nan)
    if pd.isna(squeeze):
        squeeze = g("squeeze_pressure", np.nan)
    if pd.isna(squeeze):
        bw = g("bandwidth", np.nan if strict_missing else 0.08)
        if not pd.isna(bw):
            squeeze = float(np.clip(100 - bw * 800, 10, 95))

    setup_raw = row.get("setup")
    setup = "" if pd.isna(setup_raw) else str(setup_raw)
    setup_source_raw = row.get("setup_source")
    setup_source = "" if pd.isna(setup_source_raw) else str(setup_source_raw)
    if setup == "T2_CONFIRMATION":
        setup_score = 90.0
    elif setup == "T1":
        setup_score = 75.0
    elif strict_missing and setup_source.startswith("UNCONFIRMED"):
        setup_score = np.nan
    else:
        setup_score = 30.0

    t1_tech = g("score_t1_tech", np.nan)
    if pd.isna(t1_tech) and not strict_missing:
        t1_tech = g("rsi", 50.0)

    rs = g("score_rs", np.nan)
    if pd.isna(rs):
        rs10 = g("rs_10d", np.nan)
        if not pd.isna(rs10):
            rs = float(np.clip(50.0 + rs10 * 100.0, 0, 100))
        elif not strict_missing:
            rs = 50.0

    meta_source = str(row.get("meta_model_source") or "").lower()
    if strict_missing and meta_source == "fallback":
        bayes = np.nan
    else:
        bayes = g("score_bayes", np.nan)
        if pd.isna(bayes):
            meta = g("meta_proba", np.nan if strict_missing else 0.55)
            bayes = meta * 100.0 if not pd.isna(meta) else np.nan

    return {
        "valo":     g("score_valo", missing_default),
        "t1_tech":  t1_tech,
        "setup":    setup_score,
        "news":     g("score_news", missing_default),
        "cata":     g("score_cata", missing_default),
        "squeeze":  squeeze,
        "regime":   g("score_regime", 55.0 if not strict_missing else np.nan),
        "rs":       rs,
        "bayes":    bayes,
        "earnings": g("score_earnings_proximity", 40.0 if not strict_missing else np.nan),
    }

def compute_full_score(row: pd.Series, weights: Dict[str, float] = None) -> Dict[str, float]:
    """Pipeline composants → base → bonus/multiplicateur.

    Les lignes adaptées depuis le repo utilisent automatiquement la politique
    de poids fixes sans redistribution des critères manquants.
    """
    adapter_raw = row.get("tct_adapter_source")
    strict = False if pd.isna(adapter_raw) else bool(str(adapter_raw).strip())
    components = build_component_scores(row, strict_missing=strict)
    w = weights if weights is not None else get_active_weights()
    observed_weight = 0.0
    for key, weight in w.items():
        try:
            val = float(components.get(key))
            if np.isfinite(val):
                observed_weight += float(weight)
        except (TypeError, ValueError):
            pass
    base = compute_score_base(components, w, renormalize_missing=not strict)
    bonus = gbonus = row.get("bonus", 0)
    try:
        bonus = float(gbonus) if not pd.isna(gbonus) else 0.0
    except (TypeError, ValueError):
        bonus = 0.0
    setup_raw = row.get("setup")
    setup = "" if pd.isna(setup_raw) else str(setup_raw)
    result = apply_bonus_and_multi(base, setup=setup, bonus=bonus)
    result["score_coverage"] = round(float(np.clip(observed_weight / max(sum(w.values()), 1e-12), 0, 1)), 3)
    result["missing_weight_policy"] = "ZERO_FIXED_WEIGHT" if strict else "RENORMALIZE_NATIVE"
    return result
