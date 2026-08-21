from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GateResult:
    passed: bool
    status: str
    reasons: tuple[str,...]


@dataclass(frozen=True)
class QualityResult:
    score: float|None
    coverage: float
    observed_components: int
    total_components: int


@dataclass(frozen=True)
class T1State:
    isin: str
    signal_id: str
    signal_date: str
    bandwidth: float
    price: float
    atr: float
    relative_strength_10d: float|None
    quality_score: float
    baseline_rank: int


def load_tct_config(path:str|Path)->dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_pea_eligibility(value:Any)->bool|None:
    """Fail-closed parser fixing the historical float('false') eligibility bug."""
    if value is None: return None
    if isinstance(value,bool): return value
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if pd.isna(value): return None
        return float(value)>=0.5
    text=str(value).strip().lower()
    if not text or text in {"nan","none","n/a","na","unknown","nd"}: return None
    if text in {"true","yes","y","oui","1","eligible","pea","pass"}: return True
    if text in {"false","no","n","non","0","ineligible","fail"}: return False
    try: return float(text.replace(",","."))>=0.5
    except ValueError: return None


def universe_gate(row:pd.Series)->GateResult:
    reasons=[]
    asset=str(row.get("asset_class",row.get("type","ACTION")) or "ACTION").upper()
    if asset not in {"ACTION","EQUITY","STOCK"}: reasons.append("NOT_ACTION")
    proof=None
    for field in ("pea_eligible","pea_proof_level","pea_confidence","pea_validation_gate"):
        if field in row and pd.notna(row.get(field)):
            parsed=parse_pea_eligibility(row.get(field))
            if parsed is False: reasons.append("PEA_PROOF_LOW"); proof=False; break
            if parsed is True: proof=True
    if proof is None: reasons.append("PEA_PROOF_MISSING")
    return GateResult(not reasons,"PASS" if not reasons else "QUARANTINE",tuple(reasons))


def weighted_quality(row:pd.Series,component_fields:dict[str,str],weights:dict[str,float],min_coverage:float)->QualityResult:
    numer=0.0; denom=0.0; observed=0; total_weight=sum(float(w) for w in weights.values())
    for component,weight in weights.items():
        try: value=float(row.get(component_fields[component]))
        except (TypeError,ValueError): continue
        if not math.isfinite(value): continue
        value=max(0.0,min(100.0,value)); w=float(weight); numer+=value*w; denom+=w; observed+=1
    coverage=denom/total_weight if total_weight else 0.0
    score=numer/denom if denom and coverage>=min_coverage else None
    return QualityResult(round(score,4) if score is not None else None,round(coverage,4),observed,len(weights))


def _f(row:pd.Series,field:str)->float|None:
    try:
        value=float(row.get(field)); return value if math.isfinite(value) else None
    except (TypeError,ValueError): return None


def _first_float(row:pd.Series,*fields:str)->float|None:
    for field in fields:
        value=_f(row,field)
        if value is not None: return value
    return None


def _b(row:pd.Series,field:str)->bool|None:
    value=row.get(field)
    if value is None or (isinstance(value,float) and pd.isna(value)): return None
    if isinstance(value,bool): return value
    text=str(value).strip().lower()
    if text in {"true","1","yes","oui","pass"}: return True
    if text in {"false","0","no","non","fail"}: return False
    return None


def evaluate_t1(row:pd.Series,cfg:dict)->dict:
    """Row-level fallback aligned with the executable V24.1.7 source gates."""
    gate=universe_gate(row)
    if not gate.passed:
        return {"status":"QUARANTINE","decision":"NO_T1","reasons":list(gate.reasons),"quality_score":None,"quality_coverage":0.0}
    t1=cfg["t1"]; sq=cfg["squeeze"]; reasons=[]; missing=[]
    rank=_f(row,"tct_baseline_rank"); cov=_f(row,"tct_baseline_coverage")
    if rank is None or cov is None: missing.append("TCT_BASELINE")
    else:
        if rank>cfg["scope"]["baseline_top_n"]: reasons.append("NOT_BASELINE_TOP20")
        if cov<cfg["scope"]["baseline_min_coverage"]: reasons.append("BASELINE_COVERAGE_LOW")
    squeeze=_f(row,"bb_squeeze_fraction_8")
    if squeeze is None: missing.append("COMPRESSION")
    elif squeeze<sq["minimum_fraction_below_threshold"]: reasons.append("COMPRESSION_FAIL")
    rvol=_first_float(row,"vol_ratio_exact","vol_ratio","rvol20")
    if rvol is None: missing.append("RVOL")
    elif rvol<t1["rvol_min"]: reasons.append("RVOL_FAIL")
    cross=_b(row,"bb_breakout_cross_flag"); close=_first_float(row,"last_close","close"); bb=_first_float(row,"bb_upper","bb_high"); atr=_first_float(row,"atr14","atr_14")
    if cross is None or close is None or bb is None or atr is None or atr<=0 or bb<=0: missing.append("BREAKOUT")
    else:
        extension=max(0.0,close-bb)
        if not cross: reasons.append("BREAKOUT_CROSS_FAIL")
        if extension/atr>t1["max_extension_atr"]: reasons.append("BREAKOUT_ATR_EXTENSION")
        if extension/bb>t1["max_extension_pct"]: reasons.append("BREAKOUT_PCT_EXTENSION")
    expansion=_f(row,"bb_bandwidth_expansion_ratio")
    if expansion is None: missing.append("BANDWIDTH_EXPANSION")
    elif expansion<=1.0: reasons.append("BANDWIDTH_NOT_EXPANDING")
    hist=_f(row,"macd_hist"); rising=_f(row,"macd_hist_rising_share_3")
    if hist is None or rising is None: missing.append("MACD_ACCELERATION")
    elif not (hist<0 and rising>=t1["macd_hist_acceleration_min"]): reasons.append("MACD_NEGATIVE_ACCELERATION_FAIL")
    k=_f(row,"stoch_k"); d=_f(row,"stoch_d"); rsi=_first_float(row,"rsi14","rsi"); sar=_f(row,"sar"); mm50=_f(row,"mm50")
    if any(x is None for x in (k,d,rsi,close,sar,mm50)): missing.append("TECHNICAL_GATE")
    else:
        assert k is not None and d is not None and rsi is not None and close is not None and sar is not None and mm50 is not None
        if not (k>d and rsi<70 and k<70 and close>sar and close>mm50): reasons.append("TECHNICAL_GATE_FAIL")
    quality=weighted_quality(row,t1["component_fields"],t1["components"],t1["quality_component_min_coverage"])
    if quality.score is None: missing.append("T1_QUALITY_COMPONENTS")
    if reasons: return {"status":"SHADOW_GATE_FAIL","decision":"NO_T1","reasons":reasons,"missing":missing,"quality_score":quality.score,"quality_coverage":quality.coverage}
    if missing: return {"status":"SHADOW_INPUT_REQUIRED","decision":"T1_GATE_PASS_QUALITY_OR_INPUT_REQUIRED","reasons":[],"missing":sorted(set(missing)),"quality_score":quality.score,"quality_coverage":quality.coverage}
    if quality.score<t1["quality_threshold"]: return {"status":"SHADOW_QUALITY_FAIL","decision":"NO_T1","reasons":["T1_QUALITY_LT_70"],"quality_score":quality.score,"quality_coverage":quality.coverage}
    decision="T1_STARTER_25_SHADOW" if quality.score>=t1["starter_threshold"] else "T1_WATCH_SHADOW"
    return {"status":"SHADOW_T1_ELIGIBLE","decision":decision,"reasons":[],"quality_score":quality.score,"quality_coverage":quality.coverage}


def make_t1_state(row:pd.Series,evaluation:dict,signal_date:str)->T1State|None:
    if evaluation.get("status")!="SHADOW_T1_ELIGIBLE": return None
    bw=_first_float(row,"bb_bandwidth","bandwidth"); price=_first_float(row,"last_close","close"); atr=_first_float(row,"atr14","atr_14"); rank=_f(row,"tct_baseline_rank")
    if any(x is None for x in (bw,price,atr,rank)): return None
    assert bw is not None and price is not None and atr is not None and rank is not None
    isin=str(row.get("isin","") or "")
    if not isin: return None
    rs=_first_float(row,"relative_strength_10d","rs_10d")
    return T1State(isin,f"{isin}:{signal_date}:T1",signal_date,float(bw),float(price),float(atr),rs,float(evaluation["quality_score"]),int(rank))


def evaluate_t2(row:pd.Series,state:T1State|None,sessions_since_t1:int|None,cfg:dict)->dict:
    """Row-level T2 fallback: relative strength is a quality component, not a gate."""
    gate=universe_gate(row)
    if not gate.passed: return {"status":"QUARANTINE","decision":"NO_T2","reasons":list(gate.reasons)}
    if state is None or str(row.get("isin","") or "")!=state.isin: return {"status":"SHADOW_INPUT_REQUIRED","decision":"NO_T2","reasons":["EXACT_LINKED_T1_REQUIRED"]}
    t2=cfg["t2"]; reasons=[]; missing=[]
    rank=_f(row,"tct_baseline_rank"); cov=_f(row,"tct_baseline_coverage")
    if rank is None or cov is None: missing.append("TCT_BASELINE")
    else:
        if rank>cfg["scope"]["baseline_top_n"]: reasons.append("NOT_BASELINE_TOP20")
        if cov<cfg["scope"]["baseline_min_coverage"]: reasons.append("BASELINE_COVERAGE_LOW")
    if sessions_since_t1 is None: missing.append("T1_TTL")
    elif sessions_since_t1>cfg["t1"]["ttl_sessions"]: reasons.append("T1_TTL_FAIL")
    bw=_first_float(row,"bb_bandwidth","bandwidth")
    if bw is None or state.bandwidth<=0: missing.append("BANDWIDTH_T1_LINK")
    elif bw/state.bandwidth<t2["bandwidth_expansion_ratio_min"]: reasons.append("BANDWIDTH_EXPANSION_FAIL")
    hist=_f(row,"macd_hist")
    if hist is None: missing.append("MACD")
    elif hist<=0: reasons.append("MACD_NOT_POSITIVE")
    rvol=_first_float(row,"vol_ratio_exact","vol_ratio","rvol20")
    if rvol is None: missing.append("RVOL")
    elif rvol<t2["rvol_min"]: reasons.append("VOLUME_PERSISTENCE_FAIL")
    close=_first_float(row,"last_close","close"); bb=_first_float(row,"bb_upper","bb_high"); atr=_first_float(row,"atr14","atr_14")
    if close is None or bb is None or atr is None or atr<=0: missing.append("T2_PRICE_RISK")
    else:
        floor=max(bb*t2["hold_floor_bb_factor"],state.price*t2["hold_floor_t1_factor"])
        if close<floor: reasons.append("BREAKOUT_HOLD_FAIL")
        move_pct=(close/state.price-1.0) if state.price else np.inf; move_atr=(close-state.price)/state.atr if state.atr else np.inf
        if move_pct>t2["max_extension_pct"] or move_atr>t2["max_extension_atr"]: reasons.append("T2_EXTENSION_FAIL")
    quality=weighted_quality(row,t2["component_fields"],t2["components"],t2["quality_component_min_coverage"])
    if quality.score is None: missing.append("T2_QUALITY_COMPONENTS")
    if reasons: return {"status":"SHADOW_GATE_FAIL","decision":"NO_T2","reasons":reasons,"missing":missing,"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    if missing: return {"status":"SHADOW_INPUT_REQUIRED","decision":"T2_GATE_PASS_QUALITY_OR_INPUT_REQUIRED","reasons":[],"missing":sorted(set(missing)),"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    if quality.score<t2["quality_threshold"]: return {"status":"SHADOW_QUALITY_FAIL","decision":"NO_T2","reasons":["T2_QUALITY_LT_75"],"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    return {"status":"SHADOW_T2_CONFIRMED","decision":"T2_CONFIRM_75_SHADOW","reasons":[],"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}


def tct_shadow_snapshot(actions:pd.DataFrame,cfg:dict)->pd.DataFrame:
    """Legacy row fallback; production Committee uses exact OHLCV timing engine."""
    if actions.empty or "tct_baseline_rank" not in actions.columns or "tct_baseline_coverage" not in actions.columns:
        return pd.DataFrame([{"asset_class":"ACTION","horizon":"TCT","isin":"","name":"ACTION TCT V24.1.7","sector":"TRANSVERSAL","score":np.nan,"coverage_pct":0.0,"status":"SHADOW_BASELINE_REQUIRED","decision":"SHADOW_BASELINE_REQUIRED","active_criteria":0,"available_criteria":0,"score_source":cfg.get("version","V24.1.7"),"backtest_attribution":"V2 NOT_YET_BACKTESTED; prior T1/T2 OOS failed promotion gates.","notes":"Exact baseline must be computed before T1/T2; live execution forbidden."}])
    rows=[]
    for _,row in actions.iterrows():
        evaluation=evaluate_t1(row,cfg)
        rows.append({"asset_class":"ACTION","horizon":"TCT","isin":str(row.get("isin","") or ""),"name":str(row.get("name","") or ""),"sector":str(row.get("sector_yf",row.get("sector_v21","NON CLASSE")) or "NON CLASSE"),"score":evaluation.get("quality_score"),"coverage_pct":round(float(evaluation.get("quality_coverage",0.0))*100.0,2),"status":evaluation.get("status"),"decision":evaluation.get("decision"),"active_criteria":6,"available_criteria":0,"score_source":"V24.1.7_T1T2_ROW_FALLBACK","backtest_attribution":"SHADOW only; exact production path is history-based.","notes":"Fallback aligned with exact binary gates; no score influence; no live execution."})
    return pd.DataFrame(rows)
