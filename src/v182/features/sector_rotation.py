from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
import pandas as pd


def _num(frame: pd.DataFrame, field: str) -> pd.Series:
    return pd.to_numeric(frame[field],errors="coerce") if field in frame.columns else pd.Series(np.nan,index=frame.index,dtype=float)


def _bool_pct(series: pd.Series) -> float | None:
    if series.empty: return None
    text=series.astype(str).str.strip().str.lower()
    vals=pd.Series(np.nan,index=series.index,dtype=float)
    vals.loc[text.isin({"true","1","yes","oui"})]=1.0
    vals.loc[text.isin({"false","0","no","non"})]=0.0
    numeric=pd.to_numeric(series,errors="coerce")
    vals=vals.where(vals.notna(),numeric.where(numeric.isin([0,1])))
    return float(vals.mean()*100.0) if vals.notna().any() else None


def _sector_series(frame: pd.DataFrame) -> pd.Series:
    out=pd.Series("NON_CLASSE",index=frame.index,dtype=object)
    for field in ("sector_yf","sector_yahoo","sector","sector_bucket","industry_yf"):
        if field not in frame.columns: continue
        raw=frame[field].astype(str).str.strip()
        valid=~raw.str.lower().isin({"","nan","none","n/a","na","unknown"})
        out=out.where(~((out=="NON_CLASSE")&valid),raw)
    return out


def _pct_rank(series: pd.Series) -> pd.Series:
    x=pd.to_numeric(series,errors="coerce")
    return x.rank(method="average",pct=True,ascending=True)*100.0


def build_rotation_observations(actions: pd.DataFrame) -> tuple[list[dict], pd.DataFrame, dict]:
    """Detect sector catch-up/rotation without hard-coding a market regime.

    A large distance from the 52-week high helps only when short-term recovery
    evidence exists; this avoids automatically rewarding falling knives.
    """
    if actions.empty: return [],pd.DataFrame(),{"status":"EMPTY"}
    work=actions.copy(); work["_sector"]=_sector_series(work)
    dist=_num(work,"distance_high_52w_pct")
    p1=_num(work,"perf_1m_pct"); p3=_num(work,"perf_3m_pct")
    above50=work["above_mm50"] if "above_mm50" in work.columns else pd.Series(pd.NA,index=work.index)
    above200=work["above_mm200"] if "above_mm200" in work.columns else pd.Series(pd.NA,index=work.index)
    near_high_share=float((dist<=5).mean()*100.0) if dist.notna().any() else None
    breadth200=_bool_pct(above200)
    market_high_score=None
    if near_high_share is not None and breadth200 is not None:
        near_component=min(100.0,near_high_share/40.0*100.0)
        market_high_score=round(0.60*near_component+0.40*breadth200,4)

    rows=[]
    for sector,idx in work.groupby("_sector").groups.items():
        if sector=="NON_CLASSE" or len(idx)<3: continue
        sdist=dist.loc[idx]; sp1=p1.loc[idx]; sp3=p3.loc[idx]
        med_dist=float(sdist.median()) if sdist.notna().any() else np.nan
        med_p1=float(sp1.median()) if sp1.notna().any() else np.nan
        med_p3=float(sp3.median()) if sp3.notna().any() else np.nan
        accel=med_p1-med_p3/3.0 if np.isfinite(med_p1) and np.isfinite(med_p3) else np.nan
        b50=_bool_pct(above50.loc[idx]); b200=_bool_pct(above200.loc[idx])
        rows.append({"sector":sector,"n_actions":len(idx),"median_distance_high_52w_pct":med_dist,"median_perf_1m_pct":med_p1,"median_perf_3m_pct":med_p3,"momentum_acceleration":accel,"breadth_above_mm50_pct":b50,"breadth_above_mm200_pct":b200})
    sectors=pd.DataFrame(rows)
    if sectors.empty: return [],sectors,{"status":"NO_SECTORS","market_high_regime_score":market_high_score}
    sectors["catchup_gap_score"]=(pd.to_numeric(sectors["median_distance_high_52w_pct"],errors="coerce")/25.0*100.0).clip(0,100)
    sectors["momentum_rank"]=_pct_rank(sectors["median_perf_1m_pct"])
    sectors["acceleration_rank"]=_pct_rank(sectors["momentum_acceleration"])
    breadth=sectors[["breadth_above_mm50_pct","breadth_above_mm200_pct"]].mean(axis=1,skipna=True)
    market_p1=float(p1.median()) if p1.notna().any() else np.nan
    sectors["rs_inflection"]=pd.to_numeric(sectors["median_perf_1m_pct"],errors="coerce")-market_p1
    sectors["rs_rank"]=_pct_rank(sectors["rs_inflection"])
    sectors["sector_rotation_score"]=(
        0.30*sectors["catchup_gap_score"]+0.25*sectors["momentum_rank"]+0.20*sectors["acceleration_rank"]+
        0.15*breadth+0.10*sectors["rs_rank"]
    )
    recovery=(pd.to_numeric(sectors["median_perf_1m_pct"],errors="coerce")>0)&((pd.to_numeric(sectors["momentum_acceleration"],errors="coerce")>0)|(pd.to_numeric(sectors["breadth_above_mm50_pct"],errors="coerce")>=50))
    sectors.loc[~recovery,"sector_rotation_score"]=sectors.loc[~recovery,"sector_rotation_score"].clip(upper=50)
    sectors["sector_rotation_score"]=sectors["sector_rotation_score"].clip(0,100).round(4)
    sectors["recovery_gate"]=recovery
    sector_map=sectors.set_index("sector")["sector_rotation_score"].to_dict()
    gap_map=sectors.set_index("sector")["catchup_gap_score"].to_dict()
    obs=[]; now=datetime.now(timezone.utc).isoformat()
    for idx,row in work.iterrows():
        isin=str(row.get("isin","") or ""); sector=row["_sector"]
        rot=sector_map.get(sector); sgap=gap_map.get(sector)
        catch=pd.to_numeric(pd.Series([row.get("catchup_52w_score")]),errors="coerce").iloc[0]
        action_score=np.nan
        if pd.notna(catch) and rot is not None and pd.notna(rot): action_score=0.55*float(catch)+0.45*float(rot)
        elif pd.notna(catch): action_score=float(catch)
        elif rot is not None and pd.notna(rot): action_score=float(rot)
        fields={"sector_rotation_score":rot,"sector_catchup_score":sgap,"action_catchup_score":action_score,"market_high_regime_score":market_high_score}
        if action_score is not None and pd.notna(action_score) and rot is not None and market_high_score is not None:
            fields["rotation_candidate_flag"]=bool(market_high_score>=65 and float(rot)>=65 and float(action_score)>=60)
        for field,value in fields.items():
            if value is None or (isinstance(value,float) and not np.isfinite(value)): continue
            obs.append({"universe":"ACTION","isin":isin,"field":field,"value":round(float(value),4) if not isinstance(value,bool) else value,"source":"INTERNAL_PIT_SECTOR_ROTATION","collected_at":now,"as_of":now[:10],"evidence_level":"C","validation_status":"AUTO_MATCH"})
    diagnostic={"status":"OK","market_high_regime_score":market_high_score,"near_high_share_pct":near_high_share,"breadth_above_mm200_pct":breadth200,"sector_count":int(len(sectors)),"rotation_candidates_sectors":sectors.loc[sectors["sector_rotation_score"]>=65,"sector"].tolist()}
    return obs,sectors,diagnostic
