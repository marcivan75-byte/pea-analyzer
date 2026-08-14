from __future__ import annotations
import math
import re
import pandas as pd


def _number(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            v=float(value)
            return v if math.isfinite(v) else None
        except Exception:
            return None
    text=str(value).strip().lower()
    if not text or text in {"nan","none","n/a","na","unrated","nr","unknown"}:
        return None
    match=re.search(r"-?\d+(?:[.,]\d+)?",text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",","."))
    except ValueError:
        return None


def morningstar_stars(value) -> int | None:
    n=_number(value)
    if n is None:
        return None
    n=int(round(n))
    return n if 1 <= n <= 5 else None


def risk_level_7(value) -> int | None:
    n=_number(value)
    if n is None:
        return None
    n=int(round(n))
    return n if 1 <= n <= 7 else None


def _long_decision(score: float, cfg: dict) -> str:
    if score >= float(cfg.get("buy_threshold", cfg.get("selection_threshold", 77))):
        return "BUY_CANDIDATE"
    if score >= float(cfg.get("watch_threshold", 70)):
        return "WATCH"
    if score >= float(cfg.get("review_threshold", 60)):
        return "REVIEW"
    return "REJECT"


def _short_decision(score: float, cfg: dict) -> str:
    if score >= float(cfg.get("short_candidate_threshold",77)):
        return "SHORT_RISK_CANDIDATE"
    if score >= float(cfg.get("watch_threshold",70)):
        return "WATCH_SHORT_RISK"
    return "NO_SHORT_RISK"


_LONG_ORDER={"REJECT":0,"REVIEW":1,"WATCH":2,"BUY_CANDIDATE":3}
_SHORT_ORDER={"NO_SHORT_RISK":0,"WATCH_SHORT_RISK":1,"SHORT_RISK_CANDIDATE":2}


def _normalize_long_base(decision: str, score: float, cfg: dict) -> str:
    text=str(decision or "").upper()
    if "BUY" in text or "SELECT" in text:
        return "BUY_CANDIDATE"
    if "WATCH" in text:
        return "WATCH"
    if "REVIEW" in text:
        return "REVIEW"
    if "REJECT" in text:
        return "REJECT"
    return _long_decision(score,cfg)


def _rank_confirmation(score: float | None, trend: float | None) -> str:
    if score is None:
        return "MISSING"
    if score >= 75 and (trend is None or trend >= 0):
        return "FAVORABLE"
    if score <= 25 or (trend is not None and trend <= -15):
        return "UNFAVORABLE"
    if trend is not None and abs(trend) >= 10:
        return "IMPROVING" if trend > 0 else "DETERIORATING"
    return "NEUTRAL"


def apply_etf_structural_overlay(decisions: pd.DataFrame, etf_master: pd.DataFrame, registry: dict) -> pd.DataFrame:
    """Apply validated ETF bonus/malus and expose Boursorama rank in shadow mode.

    Morningstar/risk keep their existing Committee overlay rules. Boursorama
    category rank and rank evolution are copied to the Committee only as
    confirmation fields: decision influence is exactly zero, and the exact
    V20.8.1 38-PIT MT selection remains the source of historical attribution.
    """
    out=decisions.copy()
    for col,default in (
        ("base_score",pd.NA),("morningstar_bonus",0.0),("risk_malus",0.0),
        ("structural_adjustment",0.0),("committee_score",pd.NA),
        ("base_decision",pd.NA),("structural_overlay_status","NOT_APPLICABLE"),
        ("boursorama_category_rank_score_shadow",pd.NA),("boursorama_category_rank_trend_shadow",pd.NA),
        ("boursorama_category_rank_confirmation","NOT_APPLICABLE"),("boursorama_rank_decision_influence",0.0),
    ):
        if col not in out.columns:
            out[col]=default

    if etf_master.empty or "isin" not in etf_master.columns:
        return out
    spec=registry.get("bonus_malus",{})
    star_scale={str(k):float(v) for k,v in spec.get("morningstar_bonus",{}).items() if str(k).isdigit()}
    risk_scale={str(k):float(v) for k,v in spec.get("risk_malus",{}).items() if str(k).isdigit()}
    master=etf_master.drop_duplicates("isin").set_index("isin",drop=False)

    for idx,row in out.iterrows():
        if str(row.get("asset_class","")).upper() != "ETF":
            continue
        horizon=str(row.get("horizon","")).upper()
        if horizon == "TOP_DOWN":
            continue
        isin=str(row.get("isin","") or "")
        if not isin or isin not in master.index:
            out.at[idx,"structural_overlay_status"]="ETF_REFERENCE_NOT_MATCHED"
            continue
        mrow=master.loc[isin]
        if isinstance(mrow,pd.DataFrame):
            mrow=mrow.iloc[0]
        rank_score=_number(mrow.get("boursorama_category_rank_score_shadow"))
        rank_trend=_number(mrow.get("boursorama_category_rank_trend_shadow"))
        if rank_score is not None:
            out.at[idx,"boursorama_category_rank_score_shadow"]=round(rank_score,4)
        if rank_trend is not None:
            out.at[idx,"boursorama_category_rank_trend_shadow"]=round(rank_trend,4)
        out.at[idx,"boursorama_category_rank_confirmation"]=_rank_confirmation(rank_score,rank_trend)
        out.at[idx,"boursorama_rank_decision_influence"]=0.0

        try:
            base=float(row.get("score"))
        except (TypeError,ValueError):
            out.at[idx,"structural_overlay_status"]="BASE_SCORE_MISSING"
            continue
        if not math.isfinite(base):
            out.at[idx,"structural_overlay_status"]="BASE_SCORE_MISSING"
            continue

        stars=morningstar_stars(mrow.get("morningstar_rating"))
        risk=risk_level_7(mrow.get("risk_indicator"))
        bonus=star_scale.get(str(stars),0.0) if stars is not None else 0.0
        malus=risk_scale.get(str(risk),0.0) if risk is not None else 0.0
        adjustment=(-bonus - malus) if horizon=="SHORT" else (bonus + malus)
        committee=max(0.0,min(100.0,base+adjustment))

        base_decision=str(row.get("decision","") or "")
        final_decision=base_decision
        cfg=registry.get("horizons",{}).get(horizon,{})
        if horizon=="SHORT":
            final_decision=_short_decision(committee,cfg)
        elif horizon in {"CT","LT"}:
            normalized=_normalize_long_base(base_decision,base,cfg)
            candidate=_long_decision(committee,cfg)
            if _LONG_ORDER[candidate] < _LONG_ORDER[normalized]:
                final_decision=candidate
            else:
                final_decision=normalized
        elif horizon=="MT":
            normalized=_normalize_long_base(base_decision,base,cfg)
            candidate=_long_decision(committee,cfg)
            if _LONG_ORDER[candidate] < _LONG_ORDER[normalized]:
                final_decision=candidate
            else:
                final_decision=normalized

        out.at[idx,"base_score"]=round(base,4)
        out.at[idx,"morningstar_bonus"]=round(bonus,4)
        out.at[idx,"risk_malus"]=round(malus,4)
        out.at[idx,"structural_adjustment"]=round(adjustment,4)
        out.at[idx,"committee_score"]=round(committee,4)
        out.at[idx,"base_decision"]=base_decision
        out.at[idx,"decision"]=final_decision
        out.at[idx,"score"]=round(committee,4)
        data_bits=[]
        if stars is not None: data_bits.append(f"MORNINGSTAR_{stars}STAR")
        if risk is not None: data_bits.append(f"RISK_{risk}_OF_7")
        if rank_score is not None: data_bits.append("BOURSORAMA_RANK_SHADOW")
        out.at[idx,"structural_overlay_status"]="APPLIED:"+",".join(data_bits) if data_bits else "NO_RATING_OR_RISK_DATA"
        note=str(out.at[idx,"notes"] if pd.notna(out.at[idx,"notes"]) else "")
        suffix="ETF Committee overlay applied; Morningstar/risk retain existing rules; Boursorama category rank is shadow-only with zero decision influence; MT core selection unchanged."
        out.at[idx,"notes"]=(note+" | "+suffix).strip(" |")
    return out
