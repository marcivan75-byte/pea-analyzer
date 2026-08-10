"""
Comité d'investissement TCT V24.1.4
Reprend et enrichit toutes les fonctionnalités du CDC original :
- Notes Opportunité / Risque /10
- Ratio R/O
- Délai d'entrée T1/T2 détaillé
- Verdict + Proba + Espérance
- Synthèse critères pondérés
- Dashboard par secteur (11 secteurs)
- TOP_50_OPPORTUNITE
- ULTRA_T2_EARNINGS_SQUEEZE
"""

import pandas as pd
import numpy as np
from typing import Dict, Any
from src.signals.scoring import compute_full_score, WEIGHTS_V24_1_2

# Pondération historique (CDC V24.1)
WEIGHTS_V24_1 = {
    "valo": 0.05, "t1_tech": 0.12, "setup": 0.12, "news": 0.05,
    "cata": 0.12, "squeeze": 0.12, "regime": 0.08, "rs": 0.04,
    "bayes": 0.12, "earnings": 0.10,
}


def _num(row: pd.Series, key: str, default: float) -> float:
    try:
        value = float(row.get(key, default))
        return value if np.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def _execution_block_reason(row: pd.Series) -> str | None:
    """Return a blocking reason when the row is not actionable."""
    status = str(row.get("universe_status") or "").strip().upper()
    if status in {"REJECT", "QUARANTINE"}:
        return f"UNIVERSE_{status}"
    if str(row.get("decision") or "").strip().upper() == "IGNORE":
        return str(row.get("sizing_reason") or "V24_IGNORE")
    return None

def compute_opportunity_score(row: pd.Series) -> float:
    """Note Opportunité /10 (logique CDC + bonus setups)."""
    base = _num(row, "score_final", _num(row, "score_final_v24_1", 50.0)) / 10.0

    setup = str(row.get("setup") or "")
    if setup == "T2_CONFIRMATION":
        base += 1.5
    elif setup == "T1":
        base += 0.8

    if _num(row, "score_earnings_proximity", 0.0) >= 80:
        base += 0.5

    days = _num(row, "days_to_earnings", 99.0)
    rev = _num(row, "eps_revision_3m", 0.0)
    short = _num(row, "short_interest", 0.0)
    if days <= 5 and rev >= 5 and short >= 15:
        base += 1.0

    return float(np.clip(base, 0.0, 10.0))


def compute_risk_score(row: pd.Series) -> float:
    """Note Risque /10 (logique CDC)."""
    risk = 4.0
    capi = _num(row, "market_cap_m", 1000.0)
    if capi < 100:
        risk += 2.0
    elif capi < 500:
        risk += 1.0
    elif capi > 5000:
        risk -= 1.0

    vol_ratio = _num(row, "vol_ratio", 1.0)
    if vol_ratio > 2.5:
        risk += 1.5
    elif vol_ratio > 1.8:
        risk += 0.5

    days = _num(row, "days_to_earnings", 99.0)
    if days <= 1:
        risk += 2.5
    elif days <= 5:
        risk += 1.0

    if _num(row, "short_interest", 0.0) > 15:
        risk += 1.0

    return float(np.clip(risk, 0.0, 10.0))


def compute_delai_entree(row: pd.Series) -> Dict[str, str]:
    """Délai d'entrée détaillé selon le setup (CDC)."""
    blocked = _execution_block_reason(row)
    if blocked:
        return {
            "delai_label": "NE PAS ENTRER",
            "delai_jours": "BLOQUE",
            "action_plan": f"Aucune entrée – {blocked}",
        }
    setup = str(row.get("setup") or "")
    days = _num(row, "days_to_earnings", 99.0)

    if setup == "T2_CONFIRMATION":
        return {
            "delai_label": "IMMEDIAT T2 CONFIRMATION",
            "delai_jours": "1-2j",
            "action_plan": "Entrée 50% maintenant + 50% si Vol>2.5x | SL -8% TP1 +15% TP2 +28%"
        }
    if setup == "T1":
        return {
            "delai_label": "RAPIDE T1",
            "delai_jours": "2-5j",
            "action_plan": "Entrée 50% maintenant + 50% si Vol>2.5x confirme | SL -8% TP1 +15% TP2 +28%"
        }
    if days <= 5 and _num(row, "score_earnings_proximity", 0.0) >= 80:
        return {
            "delai_label": "URGENT EARNINGS SQUEEZE",
            "delai_jours": f"J-{int(days)}j",
            "action_plan": f"Entrée avant résultats J-{int(days)}j | Attention gap si Days<=1"
        }
    if _num(row, "score_final", 0.0) >= 70:
        return {
            "delai_label": "COURT",
            "delai_jours": "2-5j",
            "action_plan": "Entrée 2-5j | Surveiller Vol Ratio >1.5x"
        }
    return {
        "delai_label": "WATCHLIST",
        "delai_jours": "Surveillance",
        "action_plan": "Surveiller volume + news sectorielles"
    }


def compute_verdict_proba_esperance(row: pd.Series) -> Dict[str, Any]:
    """Verdict + Proba + Espérance (heuristique alignée CDC)."""
    blocked = _execution_block_reason(row)
    if blocked:
        return {
            "verdict": "EVITER_BLOQUE",
            "proba_pct": 0.0,
            "esperance_pct": 0.0,
            "verdict_reason": blocked,
        }
    setup = str(row.get("setup") or "")
    days = _num(row, "days_to_earnings", 99.0)
    rev = _num(row, "eps_revision_3m", 0.0)
    short = _num(row, "short_interest", 0.0)
    score_earn = _num(row, "score_earnings_proximity", 0.0)
    meta = _num(row, "meta_proba", 0.55)

    if (setup == "T2_CONFIRMATION" and days <= 5 and rev >= 5 and short >= 15) or score_earn >= 90:
        return {
            "verdict": "COEUR_TCT_V24.1_T2_EARNINGS_SQUEEZE_ULTRA",
            "proba_pct": 82.0,
            "esperance_pct": 28.0
        }

    if setup == "T2_CONFIRMATION":
        proba = min(78.0, 55 + meta * 30)
        return {"verdict": "T2_CONFIRMATION", "proba_pct": round(proba, 1), "esperance_pct": 18.0}

    if setup == "T1":
        proba = min(68.0, 48 + meta * 25)
        return {"verdict": "T1", "proba_pct": round(proba, 1), "esperance_pct": 12.0}

    if score_earn >= 70 and days <= 5:
        return {"verdict": "EARNINGS_ATTENTION", "proba_pct": 55.0, "esperance_pct": 8.0}

    if _num(row, "score_final", 0.0) >= 60:
        # SAT = satellite de recherche: score final >= 60 sans T1/T2 confirmé.
        return {"verdict": "SAT", "proba_pct": 45.0, "esperance_pct": 5.0}

    return {"verdict": "EVITER", "proba_pct": 25.0, "esperance_pct": 0.0}


def _source_label(row: pd.Series) -> str:
    """Compact source provenance without claiming sources that were not observed."""
    vals = []
    for key in ("fundamentals_source", "ta_source", "consensus_source"):
        raw = row.get(key)
        if raw is None or pd.isna(raw):
            continue
        text = str(raw).strip()
        if not text or text.lower() in {"nan", "none", "<na>"}:
            continue
        low = text.lower()
        if "yfinance" in low or "yahoo" in low:
            label = "Yahoo Finance"
        elif "finnhub" in low:
            label = "Finnhub"
        elif "boursorama" in low:
            label = "Boursorama"
        elif "esef" in low:
            label = "ESEF"
        elif "openfigi" in low:
            label = "OpenFIGI"
        else:
            label = text[:48]
        if label not in vals:
            vals.append(label)
    return " | ".join(vals) if vals else "Free Capture repo"


def build_synthese(row: pd.Series) -> str:
    """Synthèse critères pondérés et provenance réellement observée."""
    parts = []
    parts.append(f"Opp {row.get('note_opportunite', 0):.1f} | Risque {row.get('note_risque', 0):.1f} | Ratio {row.get('ratio_ro', 0):.2f}")
    parts.append(f"Score Final {row.get('score_final', 0):.1f} | Earn {row.get('score_earnings_proximity', 0):.0f}")
    setup = row.get("setup")
    if setup is not None and not pd.isna(setup) and str(setup).strip():
        parts.append(f"Setup {setup} Bonus {row.get('bonus', 0)}")
    days = row.get("days_to_earnings")
    if pd.notna(days):
        detail = [f"Earnings J-{int(float(days))}j"]
        rev = pd.to_numeric(pd.Series([row.get("eps_revision_3m")]), errors="coerce").iloc[0]
        short = pd.to_numeric(pd.Series([row.get("short_interest")]), errors="coerce").iloc[0]
        if pd.notna(rev):
            detail.append(f"Rev {rev:+.1f}%")
        if pd.notna(short):
            detail.append(f"Short {short:.1f}%")
        parts.append(" ".join(detail))
    parts.append(f"Sources: {_source_label(row)}")
    return " – ".join(str(p) for p in parts)


def build_committee(df: pd.DataFrame, weights: Dict[str, float] = None) -> pd.DataFrame:
    """Enrichit le DataFrame avec toutes les colonnes comité du CDC + scoring V24.1.2."""
    if df is None or df.empty:
        return df

    df = df.copy()

    # Recalcule score_final avec la pondération V24.1.2 si possible
    def _recompute(row):
        try:
            return compute_full_score(row, weights if weights is not None else WEIGHTS_V24_1_2)
        except Exception:
            return {"score_base": row.get("score_final", 50), "score_final": row.get("score_final", 50), "multi": 1.0}

    scores = df.apply(_recompute, axis=1, result_type="expand")
    if "score_final" in scores.columns:
        df["score_base_v2412"] = scores.get("score_base", df.get("score_final"))
        df["score_final"] = scores["score_final"]
        df["score_multi"] = scores.get("multi", 1.0)
        if "score_coverage" in scores.columns:
            df["score_coverage_v2414"] = pd.to_numeric(scores["score_coverage"], errors="coerce")
            # Alias transitoire pour les consommateurs V24.1.3 existants.
            df["score_coverage_v2413"] = df["score_coverage_v2414"]
        if "missing_weight_policy" in scores.columns:
            df["missing_weight_policy"] = scores["missing_weight_policy"]

    df["note_opportunite"] = df.apply(compute_opportunity_score, axis=1)
    df["note_risque"] = df.apply(compute_risk_score, axis=1)
    df["ratio_ro"] = df["note_opportunite"] / (df["note_risque"] + 0.5)

    delais = df.apply(compute_delai_entree, axis=1, result_type="expand")
    df["delai_label"] = delais["delai_label"]
    df["delai_jours"] = delais["delai_jours"]
    df["action_plan"] = delais["action_plan"]

    verdicts = df.apply(compute_verdict_proba_esperance, axis=1, result_type="expand")
    df["verdict"] = verdicts["verdict"]
    df["proba_pct"] = verdicts["proba_pct"]
    df["esperance_pct"] = verdicts["esperance_pct"]
    df["verdict_reason"] = (verdicts["verdict_reason"].fillna("HEURISTIC_NON_CALIBRATED") if "verdict_reason" in verdicts.columns else "HEURISTIC_NON_CALIBRATED")
    df["proba_type"] = "HEURISTIC_NON_CALIBRATED"
    blocked_mask = df.apply(lambda r: _execution_block_reason(r) is not None, axis=1)
    df["execution_eligible"] = ~blocked_mask

    df["synthese"] = df.apply(build_synthese, axis=1)

    if "secteur" in df.columns:
        df["rank_secteur"] = (
            df.groupby("secteur")["note_opportunite"]
            .rank(ascending=False, method="min")
        )
        df = df.sort_values(["secteur", "note_opportunite"], ascending=[True, False])
    else:
        df = df.sort_values("note_opportunite", ascending=False)

    return df


def build_dashboard_secteurs(df: pd.DataFrame) -> pd.DataFrame:
    """Dashboard agrégé par secteur (11 secteurs CDC)."""
    if df is None or df.empty or "secteur" not in df.columns:
        return pd.DataFrame()

    agg = df.groupby("secteur").agg(
        n_titres=("isin", "count"),
        opp_moy=("note_opportunite", "mean"),
        risque_moy=("note_risque", "mean"),
        n_t1=("setup", lambda s: (s == "T1").sum()),
        n_t2=("setup", lambda s: (s == "T2_CONFIRMATION").sum()),
        n_earnings_5j=("days_to_earnings", lambda s: (pd.to_numeric(s, errors="coerce") <= 5).sum()),
        n_ultra=("verdict", lambda s: (s == "COEUR_TCT_V24.1_T2_EARNINGS_SQUEEZE_ULTRA").sum()),
    ).reset_index()

    agg["opp_moy"] = agg["opp_moy"].round(2)
    agg["risque_moy"] = agg["risque_moy"].round(2)
    return agg.sort_values("opp_moy", ascending=False)



def extract_top20_research(df: pd.DataFrame) -> pd.DataFrame:
    """Top 20 de recherche, indépendant de l'éligibilité d'exécution.

    Ce classement permet de remplacer le classement historique TCT sans
    transformer une absence de modèle Meta/Gap en recommandation de trading.
    Les REJECT/QUARANTINE d'univers restent exclus.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    if "universe_status" in work.columns:
        work = work[work["universe_status"].astype(str).str.upper().eq("PASS")].copy()
    if work.empty:
        return work
    cov_col = "score_coverage_v2414" if "score_coverage_v2414" in work.columns else "score_coverage_v2413"
    if cov_col in work.columns:
        cov = pd.to_numeric(work[cov_col], errors="coerce")
        work = work[cov.ge(0.60)].copy()
    if work.empty:
        return work
    rank_col = "score_final" if "score_final" in work.columns else "note_opportunite"
    work[rank_col] = pd.to_numeric(work[rank_col], errors="coerce")
    work = work[work[rank_col].notna()].copy()
    if work.empty:
        return work
    work = work.sort_values([rank_col, "note_opportunite"], ascending=[False, False], na_position="last")
    work["research_rank"] = range(1, len(work) + 1)
    work["research_only"] = ~work.get("execution_eligible", pd.Series(False, index=work.index)).fillna(False)
    cols = [c for c in [
        "research_rank", "tct_asset_class", "asset_class", "name", "pea_type",
        "isin", "ticker", "secteur", "close", "setup",
        "score_final", "score_coverage_v2414", "score_coverage_v2413", "missing_weight_policy",
        "note_opportunite", "note_risque", "ratio_ro",
        "score_earnings_proximity", "squeeze_pressure", "days_to_earnings",
        "meta_proba", "meta_model_source", "gap_model_source",
        "execution_eligible", "decision", "sizing_reason", "research_only",
        "synthese",
    ] if c in work.columns]
    return work.head(20)[cols]

def extract_top50_opportunite(df: pd.DataFrame) -> pd.DataFrame:
    """TOP 50 Opportunité exécutable, avec schéma stable même à zéro ligne."""
    expected = [
        "tct_asset_class", "asset_class", "name", "pea_type",
        "isin", "ticker", "secteur", "close", "market_cap_m", "setup",
        "note_opportunite", "note_risque", "ratio_ro", "score_final",
        "score_earnings_proximity", "days_to_earnings", "delai_label",
        "verdict", "proba_pct", "esperance_pct", "synthese"
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=expected)
    cols = [c for c in expected if c in df.columns]
    work = df.copy()
    if "execution_eligible" in work.columns:
        work = work[work["execution_eligible"].fillna(False)].copy()
    elif "decision" in work.columns:
        work = work[work["decision"].astype(str).str.upper().ne("IGNORE")].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    return work.nlargest(50, "note_opportunite")[cols]


def extract_ultra_earnings_squeeze(df: pd.DataFrame) -> pd.DataFrame:
    """ULTRA_T2_EARNINGS_SQUEEZE (type CDC 27 actions)."""
    if df is None or df.empty:
        return pd.DataFrame()
    eligible = (
        df["execution_eligible"].fillna(False)
        if "execution_eligible" in df.columns
        else pd.Series(True, index=df.index)
    )
    mask = eligible & (
        (df.get("verdict") == "COEUR_TCT_V24.1_T2_EARNINGS_SQUEEZE_ULTRA")
        | (
            (df.get("setup") == "T2_CONFIRMATION")
            & (pd.to_numeric(df.get("days_to_earnings"), errors="coerce").fillna(99) <= 5)
            & (pd.to_numeric(df.get("eps_revision_3m"), errors="coerce").fillna(0) >= 5)
            & (pd.to_numeric(df.get("short_interest"), errors="coerce").fillna(0) >= 15)
        )
    )
    return df[mask].sort_values("note_opportunite", ascending=False)
