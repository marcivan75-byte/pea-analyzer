from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import logging
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v182.features.tct_v24_1_7_exact import compute_technical_indicators

logger=logging.getLogger(__name__)
FORMULA_VERSION="T1T2_V2_2026_08"

T1_WEIGHTS={
    "compression":0.25,"volume_acceleration":0.20,"breakout_quality":0.20,
    "momentum_acceleration":0.15,"relative_strength":0.10,"risk_control":0.10,
}
T2_WEIGHTS={
    "bandwidth_expansion":0.25,"macd_confirmation":0.20,"volume_persistence":0.20,
    "breakout_hold":0.15,"relative_strength_continuation":0.10,"non_extension":0.10,
}
assert abs(sum(T1_WEIGHTS.values())-1.0)<1e-12
assert abs(sum(T2_WEIGHTS.values())-1.0)<1e-12


@dataclass(frozen=True)
class ExactTimingAudit:
    actions_rows:int
    histories_found:int
    histories_usable:int
    t1_detected_raw:int
    t1_baseline_eligible:int
    t2_confirmed:int
    active_state_records:int
    expired_state_records:int
    state_path:str


def _finite(value:Any)->float|None:
    try:
        x=float(value)
    except (TypeError,ValueError):
        return None
    return x if math.isfinite(x) else None


def _state_bool(value:Any)->bool:
    """Strict boolean parser for persisted state; string 'false' is never truthy."""
    if isinstance(value,bool):
        return value
    if value is None:
        return False
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        try:
            return bool(math.isfinite(float(value)) and float(value)>=0.5)
        except (TypeError,ValueError):
            return False
    text=str(value).strip().lower()
    if text in {"true","1","yes","y","oui","pass"}:
        return True
    if text in {"false","0","no","n","non","fail","","none","nan","na","n/a"}:
        return False
    return False


def _clip(value:float)->float:
    return float(np.clip(value,0.0,100.0))


def _num(df:pd.DataFrame,key:str)->pd.Series:
    if key not in df.columns:
        return pd.Series(np.nan,index=df.index,dtype=float)
    return pd.to_numeric(df[key],errors="coerce")


def _weighted_quality(components:Mapping[str,float|None],weights:Mapping[str,float])->tuple[float,float,int]:
    weighted=0.0; observed_weight=0.0; observed_count=0
    for name,weight in weights.items():
        value=_finite(components.get(name))
        if value is None:
            continue
        weighted += _clip(value)*float(weight)
        observed_weight += float(weight)
        observed_count += 1
    if observed_weight<=0:
        return 0.0,0.0,0
    return _clip(weighted/observed_weight),float(np.clip(observed_weight,0,1)),observed_count


def _volume_score(volume_ratio:float|None,minimum:float)->float|None:
    if volume_ratio is None:
        return None
    span=max(2.0-float(minimum),0.25)
    return _clip(50.0+(volume_ratio-float(minimum))/span*50.0)


def _breakout_score(distance_atr:float|None)->float|None:
    if distance_atr is None:
        return None
    if distance_atr<0:
        return 0.0
    if distance_atr<=0.50:
        return _clip(70.0+distance_atr*60.0)
    if distance_atr<=1.50:
        return _clip(100.0-(distance_atr-0.50)*70.0)
    return _clip(30.0-(distance_atr-1.50)*30.0)


def _relative_strength_score(rs_10d:float|None)->float|None:
    return None if rs_10d is None else _clip(50.0+rs_10d*1000.0)


def _risk_score(df:pd.DataFrame,close:pd.Series,atr:pd.Series)->float|None:
    values=[]
    c=_finite(close.iloc[-1]); a=_finite(atr.iloc[-1])
    if c and a is not None and c>0:
        atr_pct=a/c
        values.append(_clip(100.0-max(0.0,atr_pct-0.04)/0.08*100.0))
    if "open" in df.columns and len(close)>=2:
        o=_finite(_num(df,"open").iloc[-1]); pc=_finite(close.iloc[-2])
        if o is not None and pc and pc>0:
            gap=abs(o/pc-1.0)
            values.append(_clip(100.0-max(0.0,gap-0.03)/0.07*100.0))
    return float(np.mean(values)) if values else None


def _compression_metrics(bandwidth:pd.Series,percentile:float,lookback:int,window:int)->tuple[float|None,float,float|None]:
    threshold=bandwidth.shift(1).rolling(lookback,min_periods=lookback).quantile(percentile).iloc[-1]
    threshold_f=_finite(threshold)
    segment=bandwidth.iloc[-(window+1):-1]
    if threshold_f is None or threshold_f<=0 or len(segment)<window or segment.notna().sum()<window:
        return None,0.0,threshold_f
    fraction=float((segment<threshold_f).mean())
    median=_finite(segment.median())
    depth_ratio=median/threshold_f if median is not None else 1.0
    fraction_score=_clip((fraction-0.50)/0.50*100.0)
    depth_score=_clip((1.0-depth_ratio)/0.35*100.0)
    return 0.60*fraction_score+0.40*depth_score,fraction,threshold_f


def _macd_acceleration(macd_hist:pd.Series,lookback:int)->tuple[float,float]:
    recent=macd_hist.iloc[-max(int(lookback),2):].dropna().to_numpy(dtype=float)
    if len(recent)<2:
        return 0.0,0.0
    diffs=np.diff(recent)
    rising_share=float(np.mean(diffs>0))
    current=float(recent[-1])
    scale=max(float(np.max(np.abs(recent))),1e-12)
    near_zero=float(np.clip(1.0-abs(current)/scale,0,1))
    return rising_share,near_zero


def detect_exact(tech:pd.DataFrame,state:Mapping[str,Any]|None,cfg:dict)->dict:
    """Port of the exact executable detector shipped in the V24.1.7 source kit."""
    state=dict(state or {})
    sq=cfg["squeeze"]; t1=cfg["t1"]; t2=cfg["t2"]
    empty1={k:None for k in T1_WEIGHTS}; empty2={k:None for k in T2_WEIGHTS}
    minimum_rows=max(int(sq["lookback_sessions"])+1,60)
    if tech is None or len(tech)<minimum_rows:
        return {"setup":None,"reason":"HISTORY_TOO_SHORT","t1_components":empty1,"t2_components":empty2,"t1_quality":0.0,"t1_coverage":0.0,"t1_count":0,"t2_quality":0.0,"t2_coverage":0.0,"t2_count":0}
    required={"bandwidth","close","volume","bb_high","stoch_k","stoch_d","macd","macd_signal","rsi","sar","mm50"}
    if not required.issubset(tech.columns):
        return {"setup":None,"reason":"MISSING_REQUIRED_COLUMNS","t1_components":empty1,"t2_components":empty2,"t1_quality":0.0,"t1_coverage":0.0,"t1_count":0,"t2_quality":0.0,"t2_coverage":0.0,"t2_count":0}

    bandwidth=_num(tech,"bandwidth"); close=_num(tech,"close"); volume=_num(tech,"volume"); bb=_num(tech,"bb_high")
    macd=_num(tech,"macd"); signal=_num(tech,"macd_signal"); hist=macd-signal; atr=_num(tech,"atr_14")
    c_bw=_finite(bandwidth.iloc[-1]); c_close=_finite(close.iloc[-1]); c_bb=_finite(bb.iloc[-1]); c_atr=_finite(atr.iloc[-1]); c_hist=_finite(hist.iloc[-1]); c_rs=_finite(_num(tech,"rs_10d").iloc[-1])
    if None in {c_bw,c_close,c_bb,c_hist}:
        return {"setup":None,"reason":"MISSING_LATEST_VALUES","t1_components":empty1,"t2_components":empty2,"t1_quality":0.0,"t1_coverage":0.0,"t1_count":0,"t2_quality":0.0,"t2_coverage":0.0,"t2_count":0}

    comp_score,comp_fraction,squeeze_threshold=_compression_metrics(bandwidth,float(sq["percentile"]),int(sq["lookback_sessions"]),int(sq["window_sessions"]))
    pc=_finite(close.iloc[-2]); pbb=_finite(bb.iloc[-2])
    breakout_cross=bool(pc is not None and pbb is not None and pc<=pbb and c_close>c_bb)
    prev_bw=_finite(bandwidth.iloc[-2]); expanding=bool(prev_bw is not None and c_bw>prev_bw)
    vol_ma20=_finite(volume.shift(1).rolling(20,min_periods=20).mean().iloc[-1]); cv=_finite(volume.iloc[-1])
    volume_ratio=cv/vol_ma20 if cv is not None and vol_ma20 and vol_ma20>0 else None
    volume_score=_volume_score(volume_ratio,float(t1["rvol_min"]))
    distance_atr=(c_close-c_bb)/c_atr if c_atr is not None and c_atr>0 else None
    distance_pct=c_close/c_bb-1.0 if c_bb and c_bb>0 else None
    breakout_quality=_breakout_score(distance_atr)
    rising_share,near_zero=_macd_acceleration(hist,3)
    momentum=_clip(60.0*rising_share+40.0*near_zero) if c_hist<0 else 0.0
    rs_score=_relative_strength_score(c_rs); risk=_risk_score(tech,close,atr)
    t1_components={"compression":comp_score,"volume_acceleration":volume_score,"breakout_quality":breakout_quality,"momentum_acceleration":momentum,"relative_strength":rs_score,"risk_control":risk}
    t1_quality,t1_cov,t1_count=_weighted_quality(t1_components,T1_WEIGHTS)

    k=_finite(_num(tech,"stoch_k").iloc[-1]); d=_finite(_num(tech,"stoch_d").iloc[-1]); rsi=_finite(_num(tech,"rsi").iloc[-1]); sar=_finite(_num(tech,"sar").iloc[-1]); mm50=_finite(_num(tech,"mm50").iloc[-1])
    tech_gate=bool(k is not None and d is not None and rsi is not None and sar is not None and mm50 is not None and k>d and rsi<70 and k<70 and c_close>sar and c_close>mm50)
    t1_gate=all([
        comp_score is not None,
        comp_fraction>=float(sq["minimum_fraction_below_threshold"]),
        volume_ratio is not None and volume_ratio>=float(t1["rvol_min"]),
        breakout_cross,expanding,c_hist<0,rising_share>=0.50,tech_gate,
        t1_cov>=float(t1["quality_component_min_coverage"]),t1_quality>=float(t1["quality_threshold"]),
        distance_atr is not None and distance_atr<=float(t1["max_extension_atr"]),
        distance_pct is not None and distance_pct<=float(t1["max_extension_pct"]),
    ])
    if t1_gate:
        return {
            "setup":"T1","reason":None,"current_bandwidth":c_bw,"squeeze_threshold":squeeze_threshold,
            "volume_ratio":volume_ratio,"t1_quality":t1_quality,"t1_coverage":t1_cov,"t1_count":t1_count,
            "t2_quality":0.0,"t2_coverage":0.0,"t2_count":0,"t1_components":t1_components,"t2_components":empty2,
            "state_update":{"bandwidth":c_bw,"breakout_price":c_close,"atr_at_t1":c_atr,"rs_10d_at_t1":c_rs,"t1_quality_score":t1_quality},
            "overextended":False,"source_event_id":None,"age_sessions":0,
        }

    source_bw=_finite(state.get("bandwidth")); source_price=_finite(state.get("breakout_price")); source_atr=_finite(state.get("atr_at_t1")); source_rs=_finite(state.get("rs_10d_at_t1")); age=_finite(state.get("age_sessions"))
    ratio=c_bw/source_bw if source_bw and source_bw>0 else None
    expansion=None
    if ratio is not None:
        span=max(1.30-float(t2["bandwidth_expansion_ratio_min"]),0.05)
        expansion=_clip(50.0+(ratio-float(t2["bandwidth_expansion_ratio_min"]))/span*50.0)
    macd_confirmation=_clip(65.0+35.0*rising_share) if c_hist>0 else 0.0
    low_now=_finite(_num(tech,"low").iloc[-1]); floor=max(c_bb*float(t2["hold_floor_bb_factor"]),source_price*float(t2["hold_floor_t1_factor"])) if source_price is not None else c_bb*float(t2["hold_floor_bb_factor"])
    hold=0.0 if c_close<floor else 100.0 if low_now is not None and low_now<=c_bb*1.01 else 85.0
    if c_rs is None:
        rs_cont=None
    elif source_rs is None:
        rs_cont=_relative_strength_score(c_rs)
    else:
        rs_cont=_clip(70.0+(c_rs-source_rs)*1000.0)
    move_pct=c_close/source_price-1.0 if source_price and source_price>0 else None
    move_atr=(c_close-source_price)/source_atr if source_price is not None and source_atr and source_atr>0 else None
    overextended=bool((move_pct is not None and move_pct>float(t2["max_extension_pct"])) or (move_atr is not None and move_atr>float(t2["max_extension_atr"])))
    nonext_scores=[]
    if move_pct is not None:
        nonext_scores.append(_clip(100.0-max(0.0,move_pct-0.03)/max(float(t2["max_extension_pct"])-0.03,0.01)*100.0))
    if move_atr is not None:
        nonext_scores.append(_clip(100.0-max(0.0,move_atr-0.50)/max(float(t2["max_extension_atr"])-0.50,0.10)*100.0))
    nonext=float(np.mean(nonext_scores)) if nonext_scores else None
    t2_components={"bandwidth_expansion":expansion,"macd_confirmation":macd_confirmation,"volume_persistence":volume_score,"breakout_hold":hold,"relative_strength_continuation":rs_cont,"non_extension":nonext}
    t2_quality,t2_cov,t2_count=_weighted_quality(t2_components,T2_WEIGHTS)
    source_link_ok=bool(state.get("event_id")) and _state_bool(state.get("baseline_eligible_at_t1",False))
    t2_gate=all([
        source_bw is not None,source_price is not None,source_link_ok,
        age is not None and age<=int(t1["ttl_sessions"]),
        ratio is not None and ratio>=float(t2["bandwidth_expansion_ratio_min"]),
        volume_ratio is not None and volume_ratio>=float(t2["rvol_min"]),
        c_hist>0,c_close>=floor,not overextended,
        t2_cov>=float(t2["quality_component_min_coverage"]),t2_quality>=float(t2["quality_threshold"]),
    ])
    reason=None
    if source_bw is not None and not source_link_ok:
        reason="SOURCE_T1_NOT_BASELINE_ELIGIBLE"
    elif overextended:
        reason="OVEREXTENDED"
    elif source_bw is not None and t2_quality<float(t2["quality_threshold"]):
        reason="T2_QUALITY_BELOW_THRESHOLD"
    return {
        "setup":"T2_CONFIRMATION" if t2_gate else None,"reason":reason,"current_bandwidth":c_bw,"squeeze_threshold":squeeze_threshold,
        "volume_ratio":volume_ratio,"t1_quality":t1_quality,"t1_coverage":t1_cov,"t1_count":t1_count,
        "t2_quality":t2_quality,"t2_coverage":t2_cov,"t2_count":t2_count,"t1_components":t1_components,"t2_components":t2_components,
        "state_update":None,"overextended":overextended,"source_event_id":state.get("event_id"),"age_sessions":age,
        "bandwidth_ratio":ratio,"hold_floor":floor,
    }


def _event_id(isin:str,detected_at:object)->str:
    payload=f"{str(isin).strip().upper()}|{pd.Timestamp(detected_at).date().isoformat()}|T1"
    return "T1_"+sha256(payload.encode("utf-8")).hexdigest()[:20]


def _business_sessions_since(iso_date:str,as_of:date)->int:
    try:
        start=date.fromisoformat(str(iso_date)[:10])
    except Exception:
        return 10**9
    if start>=as_of:
        return 0
    return int(np.busday_count(start.isoformat(),as_of.isoformat()))


def load_state(path:Path,ttl_sessions:int,as_of:date|None=None)->tuple[dict[str,dict],int]:
    as_of=as_of or datetime.now(timezone.utc).date()
    if not path.exists():
        return {},0
    try:
        raw=json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("TCT T1 state read failed %s: %s: %s",path,type(exc).__name__,exc)
        return {},0
    if not isinstance(raw,dict):
        return {},0
    clean={}; expired=0
    for isin,record in raw.items():
        if not isinstance(record,dict):
            continue
        bw=_finite(record.get("bandwidth")); detected=str(record.get("detected_at") or "")[:10]
        if bw is None or not detected:
            continue
        age=_business_sessions_since(detected,as_of)
        if age>ttl_sessions:
            expired+=1
            continue
        clean[str(isin).upper()]={
            "bandwidth":bw,"detected_at":detected,"event_id":record.get("event_id"),"age_sessions":age,
            "breakout_price":_finite(record.get("breakout_price")),"atr_at_t1":_finite(record.get("atr_at_t1")),
            "rs_10d_at_t1":_finite(record.get("rs_10d_at_t1")),"t1_quality_score":_finite(record.get("t1_quality_score")),
            "baseline_eligible_at_t1":_state_bool(record.get("baseline_eligible_at_t1",False)),"formula_version":str(record.get("formula_version") or FORMULA_VERSION),
        }
    return clean,expired


def save_state(path:Path,state:dict[str,dict])->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={}
    for isin,record in state.items():
        bw=_finite(record.get("bandwidth"))
        if bw is None:
            continue
        payload[str(isin).upper()]={
            "bandwidth":bw,"detected_at":str(record.get("detected_at") or "")[:10],"event_id":record.get("event_id"),
            "breakout_price":_finite(record.get("breakout_price")),"atr_at_t1":_finite(record.get("atr_at_t1")),
            "rs_10d_at_t1":_finite(record.get("rs_10d_at_t1")),"t1_quality_score":_finite(record.get("t1_quality_score")),
            "baseline_eligible_at_t1":_state_bool(record.get("baseline_eligible_at_t1",False)),"formula_version":FORMULA_VERSION,
        }
    tmp=path.with_suffix(path.suffix+".tmp")
    try:
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.error("TCT T1 state write failed %s: %s: %s",path,type(exc).__name__,exc)
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            logger.debug("TCT T1 temp cleanup failed",exc_info=True)


def _extract_histories(cache_dir:Path,wanted:set[str])->dict[str,pd.DataFrame]:
    histories={}
    if not cache_dir.exists():
        return histories
    for path in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame=pd.read_parquet(path)
        except Exception as exc:
            logger.debug("TCT history read failed %s: %s",path,exc)
            continue
        if frame.empty:
            continue
        if isinstance(frame.columns,pd.MultiIndex):
            level0=set(map(str,frame.columns.get_level_values(0)))
            level1=set(map(str,frame.columns.get_level_values(1))) if frame.columns.nlevels>1 else set()
            for ticker in wanted:
                sub=None
                try:
                    if ticker in level0:
                        sub=frame[ticker]
                    elif ticker in level1:
                        sub=frame.xs(ticker,axis=1,level=1)
                except Exception:
                    sub=None
                if sub is not None and not sub.empty and (ticker not in histories or len(sub)>len(histories[ticker])):
                    histories[ticker]=sub.copy()
        elif len(wanted)==1:
            ticker=next(iter(wanted)); histories[ticker]=frame.copy()
    return histories


def _market_as_of(histories:Mapping[str,pd.DataFrame])->date:
    """Anchor state TTL to the PIT market data evaluated, not the wall clock."""
    dates=[]
    for frame in histories.values():
        if frame is None or frame.empty:
            continue
        try:
            dates.append(pd.Timestamp(frame.index[-1]).date())
        except Exception:
            continue
    return max(dates) if dates else datetime.now(timezone.utc).date()


def build_exact_timing_snapshot(actions:pd.DataFrame,cache_dir:Path,state_path:Path,cfg:dict)->tuple[pd.DataFrame,ExactTimingAudit]:
    """Evaluate exact V24.1.7 history, then enforce baseline before persisting T1.

    State TTL is anchored to the last market date present in the evaluated PIT
    histories. This keeps live runs and historical replays reproducible.
    """
    if actions is None or actions.empty:
        return pd.DataFrame(),ExactTimingAudit(0,0,0,0,0,0,0,0,str(state_path))
    work=actions.copy()
    ticker_col="yahoo_ticker" if "yahoo_ticker" in work.columns else "ticker"
    mapping={str(r[ticker_col]).strip():str(r["isin"]).strip().upper() for _,r in work.iterrows() if pd.notna(r.get(ticker_col)) and str(r.get(ticker_col)).strip() not in {"","nan","None"}}
    histories=_extract_histories(cache_dir,set(mapping))
    ttl=int(cfg["t1"]["ttl_sessions"])
    state_as_of=_market_as_of(histories)
    state,expired=load_state(state_path,ttl,state_as_of)
    rows=[]; usable=0; raw_t1=0; eligible_t1=0; t2_confirmed=0
    by_isin=work.set_index(work["isin"].astype(str).str.upper(),drop=False)
    for ticker,isin in mapping.items():
        base=by_isin.loc[isin]
        if isinstance(base,pd.DataFrame):
            base=base.iloc[0]
        rank=_finite(base.get("tct_baseline_rank")); coverage=_finite(base.get("tct_baseline_coverage"))
        baseline_ok=bool(rank is not None and rank<=int(cfg["scope"]["baseline_top_n"]) and coverage is not None and coverage>=float(cfg["scope"]["baseline_min_coverage"]))
        hist=histories.get(ticker)
        if hist is None or hist.empty:
            rows.append(_snapshot_row(base,"SHADOW_HISTORY_MISSING","NO_T1_T2",None,0.0,{},None,baseline_ok))
            continue
        try:
            tech=compute_technical_indicators(hist)
            signal_date=pd.Timestamp(tech.index[-1]).date()
            state_record=dict(state.get(isin,{}) or {})
            if state_record:
                state_record["age_sessions"]=_business_sessions_since(state_record.get("detected_at",""),signal_date)
            det=detect_exact(tech,state_record,cfg)
            usable+=1
        except Exception as exc:
            rows.append(_snapshot_row(base,"SHADOW_HISTORY_ERROR","NO_T1_T2",None,0.0,{},f"{type(exc).__name__}:{str(exc)[:160]}",baseline_ok))
            continue

        setup=det.get("setup"); timing_status="SHADOW_NO_SIGNAL"; decision="NO_T1_T2"; selected_score=None; selected_cov=0.0
        if setup=="T1":
            raw_t1+=1; selected_score=det["t1_quality"]; selected_cov=det["t1_coverage"]
            if not baseline_ok:
                timing_status="BLOCKED_BASELINE"; decision="NO_T1"
            else:
                timing_status="T1_STARTER_25_SHADOW" if selected_score>=float(cfg["t1"]["starter_threshold"]) else "T1_WATCH_SHADOW"
                decision=timing_status; eligible_t1+=1
                update=dict(det.get("state_update") or {}); event=_event_id(isin,signal_date)
                state[isin]={**update,"detected_at":signal_date.isoformat(),"event_id":event,"baseline_eligible_at_t1":True,"formula_version":FORMULA_VERSION,"age_sessions":0}
                det["source_event_id"]=event; det["age_sessions"]=0
        elif setup=="T2_CONFIRMATION":
            selected_score=det["t2_quality"]; selected_cov=det["t2_coverage"]
            if not baseline_ok:
                timing_status="BLOCKED_BASELINE"; decision="NO_T2"
            else:
                timing_status="T2_CONFIRM_75_SHADOW"; decision=timing_status; t2_confirmed+=1; state.pop(isin,None)
        elif det.get("reason"):
            timing_status="SHADOW_GATE_FAIL"; decision="NO_T1_T2"
        rows.append(_snapshot_row(base,timing_status,decision,selected_score,selected_cov,det,det.get("reason"),baseline_ok))

    observed_isins={str(r.get("isin") or "").upper() for r in rows}
    for _,base in work.iterrows():
        isin=str(base.get("isin") or "").upper()
        if isin not in observed_isins:
            rows.append(_snapshot_row(base,"SHADOW_TICKER_MISSING","NO_T1_T2",None,0.0,{},"NO_YAHOO_TICKER",False))
    save_state(state_path,state)
    snapshot=pd.DataFrame(rows)
    audit=ExactTimingAudit(len(work),len(histories),usable,raw_t1,eligible_t1,t2_confirmed,len(state),expired,str(state_path))
    return snapshot,audit


def _snapshot_row(base:pd.Series,status:str,decision:str,score:float|None,coverage:float,det:dict,reason:str|None,baseline_ok:bool)->dict:
    components={}
    for k,v in (det.get("t1_components") or {}).items():
        components[f"t1_component_{k}"]=v
    for k,v in (det.get("t2_components") or {}).items():
        components[f"t2_component_{k}"]=v
    # One timing family has six active quality criteria at a time. Never report
    # 12 available criteria merely because both families are present for audit.
    t1_count=int(det.get("t1_count",0) or 0)
    t2_count=int(det.get("t2_count",0) or 0)
    selected_family="T2" if det.get("source_event_id") else "T1"
    observed=min(6,t2_count if selected_family=="T2" else t1_count)
    return {
        "asset_class":"ACTION","horizon":"TCT","isin":str(base.get("isin") or ""),"name":str(base.get("name") or ""),
        "sector":str(base.get("sector_yf",base.get("sector_v21","NON CLASSE")) or "NON CLASSE"),
        "score":round(float(score),4) if score is not None else np.nan,"coverage_pct":round(float(coverage)*100.0,2),
        "status":status,"decision":decision,"active_criteria":6,"available_criteria":observed,
        "score_source":"V24.1.7_T1T2_V2_EXACT_OHLCV","backtest_attribution":"V2 exact source formula is SHADOW/RESEARCH_ONLY; prior T1/T2 promotion failed OOS gates.",
        "notes":"Exact V24.1.7 source-kit timing; baseline Top20 computed first; T1/T2 score influence=0; live execution forbidden.",
        "setup":det.get("setup"),"t1_t2_formula_version":FORMULA_VERSION,"tct_baseline_rank":base.get("tct_baseline_rank"),"tct_baseline_coverage":base.get("tct_baseline_coverage"),"baseline_eligible_without_t1_t2":baseline_ok,
        "t1_quality_score":det.get("t1_quality"),"t1_quality_coverage":det.get("t1_coverage"),"t2_quality_score":det.get("t2_quality"),"t2_quality_coverage":det.get("t2_coverage"),
        "t1_source_event_id":det.get("source_event_id"),"t1_age_sessions":det.get("age_sessions"),"t1_t2_overextended":bool(det.get("overextended",False)),"t1_t2_rejection_reason":reason,
        "t1_t2_score_influence":0.0,"t1_t2_live_execution_allowed":False,"setup_component_active":False,
        **components,
    }
