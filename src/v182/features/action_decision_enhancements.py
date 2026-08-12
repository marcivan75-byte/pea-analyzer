from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
import pandas as pd


def _num(value):
    try:
        x=float(str(value).replace(",",".").replace("%","")); return x if np.isfinite(x) else None
    except (TypeError,ValueError): return None


def _morningstar_score(rating) -> float | None:
    x=_num(rating)
    if x is None or x<1 or x>5: return None
    return {1:0.0,2:25.0,3:55.0,4:80.0,5:100.0}.get(int(round(x)))


def _dividend_gt4_score(yield_pct) -> float | None:
    y=_num(yield_pct)
    if y is None or y<0: return None
    if y<4.0: return max(0.0,y/4.0*35.0)
    if y<6.0: return 60.0+(y-4.0)*10.0
    return min(100.0,80.0+(y-6.0)*5.0)


def _target_growth_score(upside_pct) -> float | None:
    u=_num(upside_pct)
    if u is None: return None
    if u<=0: return max(0.0,20.0+u)
    if u<10: return 20.0+3.0*u
    if u<20: return 50.0+2.5*(u-10.0)
    return min(100.0,75.0+1.25*(u-20.0))


def build_action_enhancement_observations(actions: pd.DataFrame) -> list[dict]:
    """Build bounded 0-100 decision factors from observed values only."""
    now=datetime.now(timezone.utc).isoformat(); out=[]
    for _,row in actions.iterrows():
        isin=str(row.get("isin","") or "")
        morning=_morningstar_score(row.get("morningstar_rating"))
        div=_dividend_gt4_score(row.get("dividend_yield_pct") if row.get("dividend_yield_pct") is not None else row.get("dividend_yield_v21_pct"))
        target_raw=row.get("upside_pct_yf") if row.get("upside_pct_yf") not in (None,"") else row.get("upside_pct")
        if target_raw in (None,""): target_raw=row.get("target_upside_pct_v21")
        target=_target_growth_score(target_raw)
        total_parts=[]; total_weights=[]
        if target is not None: total_parts.append(target*0.75); total_weights.append(0.75)
        if div is not None: total_parts.append(div*0.25); total_weights.append(0.25)
        total=sum(total_parts)/sum(total_weights) if total_weights else None
        values={"morningstar_action_score":morning,"dividend_gt4_score":div,"target_upside_growth_score":target,"total_return_potential_score":total}
        for field,value in values.items():
            if value is None: continue
            source="DERIVED_MORNINGSTAR_STOCK_RATING" if field=="morningstar_action_score" else "DERIVED_TARGET_DIVIDEND_OBSERVED"
            out.append({"universe":"ACTION","isin":isin,"field":field,"value":round(float(value),4),"source":source,"collected_at":now,"as_of":now[:10],"evidence_level":"C" if field!="morningstar_action_score" else "B","validation_status":"DERIVED_NO_IMPUTATION"})
    return out
