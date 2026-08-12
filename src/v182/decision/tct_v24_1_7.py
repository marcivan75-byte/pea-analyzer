from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import math
import pandas as pd
import numpy as np


@dataclass(frozen=True)
class GateResult:
    passed: bool
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class QualityResult:
    score: float | None
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
    relative_strength_10d: float | None
    quality_score: float
    baseline_rank: int


def load_tct_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_pea_eligibility(value: Any) -> bool | None:
    """Parse PEA proof without the historical `float('false')` bug.

    Explicit negative strings are False, explicit positive strings are True,
    numeric values use the historical 0.5 proof threshold, and unknown values
    remain None. Exceptions are never silently converted to PASS.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if pd.isna(value):
            return None
        return float(value) >= 0.5
    text = str(value).strip().lower()
    if not text or text in {"nan", "none", "n/a", "na", "unknown", "nd"}:
        return None
    if text in {"true", "yes", "y", "oui", "1", "eligible", "pea", "pass"}:
        return True
    if text in {"false", "no", "n", "non", "0", "ineligible", "fail"}:
        return False
    try:
        return float(text.replace(",", ".")) >= 0.5
    except ValueError:
        return None


def universe_gate(row: pd.Series) -> GateResult:
    """TCT is ACTION-only and requires non-negative PEA evidence."""
    reasons=[]
    asset = str(row.get("asset_class", row.get("type", "ACTION")) or "ACTION").upper()
    if asset not in {"ACTION", "EQUITY", "STOCK"}:
        reasons.append("NOT_ACTION")

    proof = None
    for field in ("pea_eligible", "pea_proof_level", "pea_confidence", "pea_validation_gate"):
        if field in row and pd.notna(row.get(field)):
            parsed = parse_pea_eligibility(row.get(field))
            if parsed is False:
                reasons.append("PEA_PROOF_LOW")
                proof = False
                break
            if parsed is True:
                proof = True
    if proof is None:
        reasons.append("PEA_PROOF_MISSING")

    return GateResult(not reasons, "PASS" if not reasons else "QUARANTINE", tuple(reasons))


def weighted_quality(row: pd.Series, component_fields: dict[str, str], weights: dict[str, float], min_coverage: float) -> QualityResult:
    numer=0.0; denom=0.0; observed=0; total=len(weights); total_weight=sum(float(w) for w in weights.values())
    for component, weight in weights.items():
        field=component_fields[component]
        raw=row.get(field)
        try:
            value=float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        value=max(0.0,min(100.0,value))
        w=float(weight)
        numer += value*w
        denom += w
        observed += 1
    coverage=denom/total_weight if total_weight else 0.0
    score=(numer/denom) if denom and coverage >= min_coverage else None
    return QualityResult(round(score,4) if score is not None else None, round(coverage,4), observed, total)


def _f(row: pd.Series, field: str) -> float | None:
    try:
        value=float(row.get(field))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _b(row: pd.Series, field: str) -> bool | None:
    value=row.get(field)
    if value is None or (isinstance(value,float) and pd.isna(value)):
        return None
    if isinstance(value,bool): return value
    text=str(value).strip().lower()
    if text in {"true","1","yes","oui","pass"}: return True
    if text in {"false","0","no","non","fail"}: return False
    return None


def evaluate_t1(row: pd.Series, cfg: dict) -> dict:
    """Evaluate exact binary V24.1.7 T1 gates and externally computed quality.

    Continuous component transforms from the richer TCT package are not
    reconstructed here: if those six component fields are absent, quality is
    explicitly INPUT_REQUIRED even when binary gates pass.
    """
    gate=universe_gate(row)
    if not gate.passed:
        return {"status":"QUARANTINE","decision":"NO_T1","reasons":list(gate.reasons),"quality_score":None,"quality_coverage":0.0}

    t1=cfg["t1"]; sq=cfg["squeeze"]; reasons=[]; missing=[]
    rank=_f(row,"tct_baseline_rank"); baseline_cov=_f(row,"tct_baseline_coverage")
    if rank is None or baseline_cov is None:
        missing.append("TCT_BASELINE")
    else:
        if rank > cfg["scope"]["baseline_top_n"]: reasons.append("NOT_BASELINE_TOP20")
        if baseline_cov < cfg["scope"]["baseline_min_coverage"]: reasons.append("BASELINE_COVERAGE_LOW")

    squeeze=_f(row,"bb_squeeze_fraction_8")
    if squeeze is None: missing.append("COMPRESSION")
    elif squeeze < sq["minimum_fraction_below_threshold"]: reasons.append("COMPRESSION_FAIL")

    rvol=_f(row,"rvol20")
    if rvol is None: missing.append("RVOL20")
    elif rvol < t1["rvol_min"]: reasons.append("RVOL_FAIL")

    cross=_b(row,"bb_breakout_cross_flag"); close=_f(row,"last_close"); bb=_f(row,"bb_upper"); atr=_f(row,"atr14")
    if cross is None or close is None or bb is None or atr is None or atr <= 0 or bb <= 0:
        missing.append("BREAKOUT")
    else:
        extension=max(0.0,close-bb)
        if not cross: reasons.append("BREAKOUT_CROSS_FAIL")
        if extension/atr > t1["max_extension_atr"]: reasons.append("BREAKOUT_ATR_EXTENSION")
        if extension/bb > t1["max_extension_pct"]: reasons.append("BREAKOUT_PCT_EXTENSION")

    macd=_f(row,"macd_hist"); old=_f(row,"macd_hist_3d_ago")
    if macd is None or old is None or old == 0:
        missing.append("MACD_ACCELERATION")
    else:
        accel=(macd-old)/abs(old)
        if not (macd < 0 and old < 0 and accel >= t1["macd_hist_acceleration_min"]):
            reasons.append("MACD_NEGATIVE_ACCELERATION_FAIL")

    k=_f(row,"stoch_k"); d=_f(row,"stoch_d"); rsi=_f(row,"rsi14"); mm50=_f(row,"mm50"); sar=_f(row,"sar")
    if k is None or d is None or rsi is None or close is None or (mm50 is None and sar is None):
        missing.append("TECHNICAL_GATE")
    else:
        above_ref=(sar is not None and close > sar) or (mm50 is not None and close > mm50)
        if not (k>d and rsi<70 and k<70 and above_ref): reasons.append("TECHNICAL_GATE_FAIL")

    quality=weighted_quality(row,t1["component_fields"],t1["components"],t1["quality_component_min_coverage"])
    if quality.score is None:
        missing.append("T1_QUALITY_COMPONENTS")

    if reasons:
        return {"status":"SHADOW_GATE_FAIL","decision":"NO_T1","reasons":reasons,"missing":missing,"quality_score":quality.score,"quality_coverage":quality.coverage}
    if missing:
        return {"status":"SHADOW_INPUT_REQUIRED","decision":"T1_GATE_PASS_QUALITY_OR_INPUT_REQUIRED","reasons":[],"missing":sorted(set(missing)),"quality_score":quality.score,"quality_coverage":quality.coverage}
    if quality.score < t1["quality_threshold"]:
        return {"status":"SHADOW_QUALITY_FAIL","decision":"NO_T1","reasons":["T1_QUALITY_LT_70"],"quality_score":quality.score,"quality_coverage":quality.coverage}
    decision="T1_STARTER_25_SHADOW" if quality.score >= t1["starter_threshold"] else "T1_WATCH_SHADOW"
    return {"status":"SHADOW_T1_ELIGIBLE","decision":decision,"reasons":[],"quality_score":quality.score,"quality_coverage":quality.coverage}


def make_t1_state(row: pd.Series, evaluation: dict, signal_date: str) -> T1State | None:
    if evaluation.get("status") != "SHADOW_T1_ELIGIBLE":
        return None
    required={k:_f(row,k) for k in ("bb_bandwidth","last_close","atr14","tct_baseline_rank")}
    if any(v is None for v in required.values()):
        return None
    isin=str(row.get("isin","") or "")
    if not isin: return None
    rs=_f(row,"relative_strength_10d")
    signal_id=f"{isin}:{signal_date}:T1"
    return T1State(isin,signal_id,signal_date,float(required["bb_bandwidth"]),float(required["last_close"]),float(required["atr14"]),rs,float(evaluation["quality_score"]),int(required["tct_baseline_rank"]))


def evaluate_t2(row: pd.Series, state: T1State | None, sessions_since_t1: int | None, cfg: dict) -> dict:
    """Evaluate T2 only from the exact persisted eligible T1 state."""
    gate=universe_gate(row)
    if not gate.passed:
        return {"status":"QUARANTINE","decision":"NO_T2","reasons":list(gate.reasons)}
    if state is None or str(row.get("isin","") or "") != state.isin:
        return {"status":"SHADOW_INPUT_REQUIRED","decision":"NO_T2","reasons":["EXACT_LINKED_T1_REQUIRED"]}
    t2=cfg["t2"]; reasons=[]; missing=[]
    rank=_f(row,"tct_baseline_rank"); baseline_cov=_f(row,"tct_baseline_coverage")
    if rank is None or baseline_cov is None: missing.append("TCT_BASELINE")
    else:
        if rank > cfg["scope"]["baseline_top_n"]: reasons.append("NOT_BASELINE_TOP20")
        if baseline_cov < cfg["scope"]["baseline_min_coverage"]: reasons.append("BASELINE_COVERAGE_LOW")

    if sessions_since_t1 is None: missing.append("T1_TTL")
    elif sessions_since_t1 < 1 or sessions_since_t1 > cfg["t1"]["ttl_sessions"]: reasons.append("T1_TTL_FAIL")

    bw=_f(row,"bb_bandwidth")
    if bw is None or state.bandwidth <= 0: missing.append("BANDWIDTH_T1_LINK")
    elif bw/state.bandwidth < t2["bandwidth_expansion_ratio_min"]: reasons.append("BANDWIDTH_EXPANSION_FAIL")

    macd=_f(row,"macd_hist")
    if macd is None: missing.append("MACD")
    elif macd <= 0: reasons.append("MACD_NOT_POSITIVE")

    rvol=_f(row,"rvol20")
    if rvol is None: missing.append("RVOL20")
    elif rvol < t2["rvol_min"]: reasons.append("VOLUME_PERSISTENCE_FAIL")

    close=_f(row,"last_close"); bb=_f(row,"bb_upper"); atr=_f(row,"atr14")
    if close is None or bb is None or atr is None or atr <= 0: missing.append("T2_PRICE_RISK")
    else:
        floor=max(bb*t2["hold_floor_bb_factor"],state.price*t2["hold_floor_t1_factor"])
        if close < floor: reasons.append("BREAKOUT_HOLD_FAIL")
        move_pct=(close/state.price-1.0) if state.price else np.inf
        move_atr=(close-state.price)/atr
        if move_pct > t2["max_extension_pct"] or move_atr > t2["max_extension_atr"]:
            reasons.append("T2_EXTENSION_FAIL")

    rs=_f(row,"relative_strength_10d")
    if state.relative_strength_10d is not None:
        if rs is None: missing.append("RELATIVE_STRENGTH")
        elif rs < state.relative_strength_10d: reasons.append("RELATIVE_STRENGTH_DEGRADED")

    quality=weighted_quality(row,t2["component_fields"],t2["components"],t2["quality_component_min_coverage"])
    if quality.score is None: missing.append("T2_QUALITY_COMPONENTS")

    if reasons:
        return {"status":"SHADOW_GATE_FAIL","decision":"NO_T2","reasons":reasons,"missing":missing,"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    if missing:
        return {"status":"SHADOW_INPUT_REQUIRED","decision":"T2_GATE_PASS_QUALITY_OR_INPUT_REQUIRED","reasons":[],"missing":sorted(set(missing)),"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    if quality.score < t2["quality_threshold"]:
        return {"status":"SHADOW_QUALITY_FAIL","decision":"NO_T2","reasons":["T2_QUALITY_LT_75"],"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}
    return {"status":"SHADOW_T2_CONFIRMED","decision":"T2_CONFIRM_75_SHADOW","reasons":[],"quality_score":quality.score,"quality_coverage":quality.coverage,"t1_signal_id":state.signal_id}


def tct_shadow_snapshot(actions: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Run T1 shadow gates only when the baseline TCT rank already exists.

    T2 is stateful by design and is not inferred from a one-day snapshot.
    """
    if actions.empty or "tct_baseline_rank" not in actions.columns or "tct_baseline_coverage" not in actions.columns:
        return pd.DataFrame([{
            "asset_class":"ACTION","horizon":"TCT","isin":"","name":"ACTION TCT V24.1.7",
            "sector":"TRANSVERSAL","score":np.nan,"coverage_pct":0.0,"status":"SHADOW_BASELINE_REQUIRED",
            "decision":"SHADOW_BASELINE_REQUIRED","active_criteria":0,"available_criteria":0,
            "score_source":cfg.get("version","V24.1.7"),"backtest_attribution":"V2 NOT_YET_BACKTESTED; prior T1/T2 OOS failed promotion gates.",
            "notes":"T1/T2 timing-only. Exact TCT baseline Top20 outside T1/T2 must be computed first; live execution forbidden."
        }])
    rows=[]
    for _,row in actions.iterrows():
        evaluation=evaluate_t1(row,cfg)
        rows.append({
            "asset_class":"ACTION","horizon":"TCT","isin":str(row.get("isin","") or ""),"name":str(row.get("name","") or ""),
            "sector":str(row.get("sector_yf",row.get("sector_v21","NON CLASSE")) or "NON CLASSE"),
            "score":evaluation.get("quality_score"),"coverage_pct":round(float(evaluation.get("quality_coverage",0.0))*100.0,2),
            "status":evaluation.get("status"),"decision":evaluation.get("decision"),"active_criteria":6,"available_criteria":int(round(float(evaluation.get("quality_coverage",0.0))*6)),
            "score_source":cfg.get("version","V24.1.7"),"backtest_attribution":"V2 NOT_YET_BACKTESTED; prior T1/T2 OOS failed promotion gates.",
            "notes":";".join(evaluation.get("reasons",[])+evaluation.get("missing",[]))
        })
    return pd.DataFrame(rows)
