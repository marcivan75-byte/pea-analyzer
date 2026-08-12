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
    """Apply the user's explicit 52-week-high bonus/malus after normalized score.

    Bonus requires recovery confirmation upstream in OHLCV features. Near-high
    penalties apply directly. TCT, SHORT and TOP_DOWN are untouched.
    """
    if decisions.empty or actions.empty or "isin" not in actions.columns: return decisions
    out=decisions.copy()
    master=actions.set_index("isin",drop=False)
    out["base_score"]=pd.to_numeric(out.get("score"),errors="coerce")
    out["high_52w_bonus_malus_points"]=0.0
    out["distance_high_52w_pct"]=np.nan
    out["sector_rotation_score"]=np.nan
    out["action_catchup_score"]=np.nan
    out["market_high_regime_score"]=np.nan
    for idx,row in out.iterrows():
        if str(row.get("asset_class"))!="ACTION" or str(row.get("horizon")) not in {"CT","MT","LT"}: continue
        isin=str(row.get("isin","") or "")
        if isin not in master.index: continue
        m=master.loc[isin]
        if isinstance(m,pd.DataFrame): m=m.iloc[0]
        bonus=pd.to_numeric(pd.Series([m.get("high_52w_bonus_malus_points")]),errors="coerce").iloc[0]
        distance=pd.to_numeric(pd.Series([m.get("distance_high_52w_pct")]),errors="coerce").iloc[0]
        rot=pd.to_numeric(pd.Series([m.get("sector_rotation_score")]),errors="coerce").iloc[0]
        catch=pd.to_numeric(pd.Series([m.get("action_catchup_score")]),errors="coerce").iloc[0]
        regime=pd.to_numeric(pd.Series([m.get("market_high_regime_score")]),errors="coerce").iloc[0]
        out.at[idx,"distance_high_52w_pct"]=distance
        out.at[idx,"sector_rotation_score"]=rot
        out.at[idx,"action_catchup_score"]=catch
        out.at[idx,"market_high_regime_score"]=regime
        if pd.isna(bonus) or pd.isna(out.at[idx,"base_score"]): continue
        out.at[idx,"high_52w_bonus_malus_points"]=float(bonus)
        adjusted=float(np.clip(out.at[idx,"base_score"]+float(bonus),0,100))
        out.at[idx,"score"]=round(adjusted,4)
        h=str(row.get("horizon")); cfg=registry.get("horizons",{}).get(h,{})
        out.at[idx,"decision"]=_decision(adjusted,str(row.get("status")),cfg,h)
    return out
