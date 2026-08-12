from __future__ import annotations

import numpy as np
import pandas as pd

LONG_ORDER={"REJECT":0,"REVIEW":1,"WATCH":2,"BUY_CANDIDATE":3}


def _decision(score, status: str, cfg: dict, horizon: str) -> str:
    if status != "SCORABLE" or pd.isna(score): return status
    if horizon=="SHORT":
        if score>=float(cfg.get("short_candidate_threshold",77)): return "SHORT_RISK_CANDIDATE"
        if score>=float(cfg.get("watch_threshold",70)): return "WATCH_SHORT_RISK"
        return "NO_SHORT_RISK"
    if horizon=="TOP_DOWN": return "FAVORABLE" if score>=60 else "NEUTRAL" if score>=40 else "DEFAVORABLE"
    if score>=float(cfg.get("buy_threshold",77)): return "BUY_CANDIDATE"
    if score>=float(cfg.get("watch_threshold",70)): return "WATCH"
    if score>=float(cfg.get("review_threshold",60)): return "REVIEW"
    return "REJECT"


def _normalise_long_decision(raw: str, score: float, status: str, cfg: dict, horizon: str) -> str:
    text=str(raw or "").upper()
    if "BUY" in text or "SELECT" in text: return "BUY_CANDIDATE"
    if "WATCH" in text: return "WATCH"
    if "REVIEW" in text: return "REVIEW"
    if "REJECT" in text: return "REJECT"
    return _decision(score,status,cfg,horizon)


def apply_action_52w_overlay(decisions: pd.DataFrame, actions: pd.DataFrame, registry: dict) -> pd.DataFrame:
    """Apply 52-week catch-up as an explicit unvalidated challenger overlay.

    The numeric challenger score is retained for ranking/research. Positive
    overlay points cannot promote a base long decision (e.g. WATCH -> BUY)
    before dedicated PIT validation. Negative points may downgrade risk. This
    prevents a new, unbacktested factor from creating a production-like BUY by
    itself while preserving all information for ablation/backtest work.
    """
    if decisions.empty or actions.empty or "isin" not in actions.columns: return decisions
    out=decisions.copy(); master=actions.set_index("isin",drop=False)
    defaults={
        "base_score":np.nan,"action_pre_52w_score":np.nan,"high_52w_bonus_malus_points":0.0,
        "action_52w_challenger_score":np.nan,"action_52w_base_decision":pd.NA,
        "action_52w_challenger_decision":pd.NA,"action_52w_overlay_status":"NOT_APPLICABLE",
    }
    for col,val in defaults.items():
        if col not in out.columns: out[col]=val
    for col in ("distance_high_52w_pct","sector_rotation_score","action_catchup_score","market_high_regime_score"):
        if col not in out.columns: out[col]=np.nan
    for idx,row in out.iterrows():
        if str(row.get("asset_class"))!="ACTION" or str(row.get("horizon")) not in {"CT","MT","LT"}: continue
        isin=str(row.get("isin","") or "")
        if isin not in master.index: continue
        pre=pd.to_numeric(pd.Series([row.get("score")]),errors="coerce").iloc[0]
        out.at[idx,"action_pre_52w_score"]=pre; out.at[idx,"base_score"]=pre
        m=master.loc[isin]; m=m.iloc[0] if isinstance(m,pd.DataFrame) else m
        bonus=pd.to_numeric(pd.Series([m.get("high_52w_bonus_malus_points")]),errors="coerce").iloc[0]
        for col in ("distance_high_52w_pct","sector_rotation_score","action_catchup_score","market_high_regime_score"):
            out.at[idx,col]=pd.to_numeric(pd.Series([m.get(col)]),errors="coerce").iloc[0]
        if pd.isna(bonus) or pd.isna(pre):
            out.at[idx,"action_52w_overlay_status"]="MISSING_OVERLAY_OR_BASE_SCORE"; continue
        h=str(row.get("horizon")); cfg=registry.get("horizons",{}).get(h,{}); status=str(row.get("status"))
        base_decision=_normalise_long_decision(str(row.get("decision","")),float(pre),status,cfg,h)
        adjusted=float(np.clip(float(pre)+float(bonus),0,100)); challenger=_decision(adjusted,status,cfg,h)
        final=base_decision
        if challenger in LONG_ORDER and base_decision in LONG_ORDER and LONG_ORDER[challenger] < LONG_ORDER[base_decision]: final=challenger
        out.at[idx,"high_52w_bonus_malus_points"]=float(bonus)
        out.at[idx,"action_52w_challenger_score"]=round(adjusted,4)
        out.at[idx,"action_52w_base_decision"]=base_decision
        out.at[idx,"action_52w_challenger_decision"]=challenger
        out.at[idx,"action_52w_overlay_status"]="SHADOW_POSITIVE_NO_PROMOTION_NEGATIVE_CAN_DOWNGRADE"
        # Preserve reference score/decision; expose challenger separately.
        out.at[idx,"score"]=round(float(pre),4); out.at[idx,"decision"]=final
    return out
