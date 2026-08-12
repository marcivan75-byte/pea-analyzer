from __future__ import annotations

from typing import Iterable
import pandas as pd

from v182.decision.committee_master import active_criteria, resolve_field


def effective_weight_report(frame: pd.DataFrame, registry: dict, asset_class: str, horizons: Iterable[str]) -> pd.DataFrame:
    """Expose the effective per-row weights after missing-data renormalization.

    For every scorable row with at least one available criterion, the effective
    weights of available criteria sum to exactly 100%. Missing criteria receive
    0% effective weight and remain explicitly listed.
    """
    rows=[]
    for horizon in horizons:
        active=active_criteria(registry,horizon)
        if not active: continue
        resolved={}
        for name,weight,direction in active:
            values,source=resolve_field(frame,name)
            resolved[name]=(values,source,float(weight),direction)
        for idx,row in frame.iterrows():
            available=[]
            for name,(values,source,weight,direction) in resolved.items():
                ok=values is not None and idx in values.index and pd.notna(values.loc[idx])
                if ok: available.append((name,source,weight,direction))
            denom=sum(x[2] for x in available)
            for name,(values,source,weight,direction) in resolved.items():
                ok=values is not None and idx in values.index and pd.notna(values.loc[idx])
                effective=weight/denom*100.0 if ok and denom>0 else 0.0
                rows.append({
                    "asset_class":asset_class,"horizon":horizon,"isin":str(row.get("isin","") or ""),"name":str(row.get("name","") or ""),
                    "criterion":name,"raw_weight_pct":round(weight*100.0,6),"criterion_available":bool(ok),
                    "effective_weight_pct":round(effective,6),"available_raw_weight_pct":round(denom*100.0,6),
                    "normalization_policy":"AVAILABLE_CRITERIA_RENORMALIZED_TO_100","resolution":source,
                })
    return pd.DataFrame(rows)
