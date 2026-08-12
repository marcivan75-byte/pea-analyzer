from __future__ import annotations

import numpy as np
import pandas as pd


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


def apply_action_52w_overlay(decisions: pd.DataFrame, actions: pd.DataFrame, registry: dict) -> pd.DataFrame:
    """Apply explicit 52-week-high bonus/malus to Action CT/MT/LT only.

    The ETF structural `base_score` column, when present, is never overwritten.
    Action pre-overlay score is stored separately as `action_pre_52w_score`.
    """
    if decisions.empty or actions.empty or "isin" not in actions.columns: return decisions
    out=decisions.copy(); master=actions.set_index("isin",drop=False)
    if "action_pre_52w_score" not in out.columns: out["action_pre_52w_score"]=np.nan
    if "high_52w_bonus_malus_points" not in out.columns: out["high_52w_bonus_malus_points"]=0.0
    for col in ("distance_high_52w_pct","sector_rotation_score","action_catchup_score","market_high_regime_score"):
        if col not in out.columns: out[col]=np.nan
    for idx,row in out.iterrows():
        if str(row.get("asset_class"))!="ACTION" or str(row.get("horizon")) not in {"CT","MT","LT"}: continue
        isin=str(row.get("isin","") or "")
        if isin not in master.index: continue
        pre=pd.to_numeric(pd.Series([row.get("score")]),errors="coerce").iloc[0]; out.at[idx,"action_pre_52w_score"]=pre
        m=master.loc[isin]
        if isinstance(m,pd.DataFrame): m=m.iloc[0]
        bonus=pd.to_numeric(pd.Series([m.get("high_52w_bonus_malus_points")]),errors="coerce").iloc[0]
        distance=pd.to_numeric(pd.Series([m.get("distance_high_52w_pct")]),errors="coerce").iloc[0]
        rot=pd.to_numeric(pd.Series([m.get("sector_rotation_score")]),errors="coerce").iloc[0]
        catch=pd.to_numeric(pd.Series([m.get("action_catchup_score")]),errors="coerce").iloc[0]
        regime=pd.to_numeric(pd.Series([m.get("market_high_regime_score")]),errors="coerce").iloc[0]
        out.at[idx,"distance_high_52w_pct"]=distance; out.at[idx,"sector_rotation_score"]=rot; out.at[idx,"action_catchup_score"]=catch; out.at[idx,"market_high_regime_score"]=regime
        if pd.isna(bonus) or pd.isna(pre): continue
        out.at[idx,"high_52w_bonus_malus_points"]=float(bonus); adjusted=float(np.clip(float(pre)+float(bonus),0,100)); out.at[idx,"score"]=round(adjusted,4)
        h=str(row.get("horizon")); cfg=registry.get("horizons",{}).get(h,{}); out.at[idx,"decision"]=_decision(adjusted,str(row.get("status")),cfg,h)
    return out
