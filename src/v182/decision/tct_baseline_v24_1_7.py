from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd

from v182.decision.committee_master import resolve_field
from v182.decision.tct_v24_1_7 import universe_gate

# Frozen V24.1.2 pillar weights recovered from the historical TCT source package.
# V24.1.7 explicitly disables SETUP/T1/T2 in the baseline: its 16% fixed weight
# remains zero and is never redistributed.
WEIGHTS_V24_1_2 = {
    "squeeze": 0.18,
    "setup": 0.16,
    "earnings": 0.14,
    "t1_tech": 0.14,
    "bayes": 0.10,
    "cata": 0.08,
    "regime": 0.07,
    "rs": 0.05,
    "news": 0.04,
    "valo": 0.04,
}
assert abs(sum(WEIGHTS_V24_1_2.values()) - 1.0) < 1e-12

BASELINE_COMPONENTS = tuple(WEIGHTS_V24_1_2)
SETUP_COMPONENT = "setup"
MAX_SCORE_WITH_SETUP_DISABLED = 100.0 * (1.0 - WEIGHTS_V24_1_2[SETUP_COMPONENT])
MAX_COVERAGE_WITH_SETUP_DISABLED = 1.0 - WEIGHTS_V24_1_2[SETUP_COMPONENT]


@dataclass(frozen=True)
class BaselineAudit:
    universe_rows: int
    pea_gate_pass_rows: int
    coverage_pass_rows: int
    ranked_rows: int
    top20_rows: int
    max_score: float | None
    max_coverage: float | None


def _numeric(frame: pd.DataFrame, *names: str) -> pd.Series:
    out = pd.Series(np.nan, index=frame.index, dtype=float)
    for name in names:
        if name not in frame.columns:
            continue
        values = pd.to_numeric(frame[name], errors="coerce")
        out = out.where(out.notna(), values)
    return out


def _truth(frame: pd.DataFrame, *names: str) -> pd.Series:
    raw = pd.Series(pd.NA, index=frame.index, dtype="object")
    for name in names:
        if name not in frame.columns:
            continue
        candidate = frame[name]
        raw = raw.where(raw.notna(), candidate)
    out = pd.Series(np.nan, index=frame.index, dtype=float)
    if raw.empty:
        return out
    text = raw.astype(str).str.strip().str.lower()
    out.loc[text.isin({"true","1","yes","oui","pass"})] = 1.0
    out.loc[text.isin({"false","0","no","non","fail"})] = 0.0
    numeric = pd.to_numeric(raw, errors="coerce")
    out = out.where(out.notna(), numeric.where(numeric.isin([0,1])))
    return out


def _clip_score(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").clip(0.0, 100.0)


def _rank_score(series: pd.Series) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    return x.rank(method="average", pct=True, ascending=True) * 100.0


def _technical_proxy(frame: pd.DataFrame) -> pd.Series:
    """Exact transparent proxy recovered from the audited V24.1.4 repo adapter."""
    parts: list[pd.Series] = []
    rsi = _numeric(frame, "rsi", "rsi14")
    parts.append((100.0 - (rsi - 60.0).abs() * 2.5).clip(0,100))

    breakout = _truth(frame, "breakout_20d_flag")
    if breakout.notna().any():
        parts.append(breakout.map({1.0:100.0, 0.0:30.0}))

    macd = _numeric(frame, "macd")
    macd_signal = _numeric(frame, "macd_signal")
    macd_score = pd.Series(np.nan, index=frame.index, dtype=float)
    observed = macd.notna() & macd_signal.notna()
    macd_score.loc[observed] = np.where(macd.loc[observed] > macd_signal.loc[observed], 75.0, 40.0)
    parts.append(macd_score)

    above20 = _truth(frame, "above_mm20")
    if not above20.notna().any():
        close = _numeric(frame,"last_close","close")
        mm20 = _numeric(frame,"mm20")
        mask = close.notna() & mm20.notna()
        above20.loc[mask] = (close.loc[mask] > mm20.loc[mask]).astype(float)
    above50 = _truth(frame, "above_mm50")
    if not above50.notna().any():
        close = _numeric(frame,"last_close","close")
        mm50 = _numeric(frame,"mm50")
        mask = close.notna() & mm50.notna()
        above50.loc[mask] = (close.loc[mask] > mm50.loc[mask]).astype(float)
    trend_frame = pd.concat([above20,above50],axis=1)
    trend = trend_frame.mean(axis=1,skipna=True)*100.0
    trend = trend.where(trend_frame.notna().any(axis=1))
    parts.append(trend)

    rvol = _numeric(frame,"vol_ratio","rvol20")
    parts.append((rvol/2.5*100.0).clip(0,100))
    combined = pd.concat(parts,axis=1)
    return combined.mean(axis=1,skipna=True).where(combined.notna().any(axis=1))


def _earnings_score(days, eps_rev=np.nan, beat=np.nan, short=np.nan) -> float | None:
    """Historical V24.1.1 transform, but only when days_to_earnings is observed."""
    try:
        days=float(days)
    except (TypeError,ValueError):
        return None
    if not math.isfinite(days) or days < 0:
        return None
    def clean(value, default):
        try:
            x=float(value)
            return x if math.isfinite(x) else default
        except (TypeError,ValueError):
            return default
    eps=clean(eps_rev,0.0); beat_rate=clean(beat,50.0); short_pct=clean(short,0.0)
    if days <= 5:
        if eps >= 5 and short_pct >= 15: score=90.0
        elif eps >= 3: score=75.0
        elif eps >= 0: score=60.0
        else: score=45.0
    elif days <= 10: score=65.0 if eps >= 5 else 50.0
    elif days <= 20: score=55.0 if eps >= 5 else 40.0
    else: score=30.0
    if eps >= 10: score += 15.0
    if beat_rate >= 70: score += 10.0
    if short_pct >= 15 and days <= 5: score += 10.0
    if days <= 1: score -= 30.0
    return float(np.clip(score,0,100))


def _earnings_series(frame: pd.DataFrame) -> pd.Series:
    direct = _numeric(frame,"score_earnings_proximity")
    days = _numeric(frame,"days_to_earnings")
    eps = _numeric(frame,"eps_revision_3m")
    beat = _numeric(frame,"beat_rate")
    short = _numeric(frame,"short_interest","short_percent_float_pct","public_short_pct")
    derived = pd.Series([
        _earnings_score(d,e,b,s) for d,e,b,s in zip(days,eps,beat,short)
    ],index=frame.index,dtype=float)
    return direct.where(direct.notna(),derived)


def _catalyst_series(frame: pd.DataFrame) -> pd.Series:
    direct = _numeric(frame,"score_cata")
    parts=[]
    for name in ("guidance_revision_score","regulatory_catalyst_score","major_contract_score"):
        if name in frame.columns:
            parts.append(_clip_score(frame[name]))
    if "mna_rumor_score" in frame.columns:
        parts.append(pd.to_numeric(frame["mna_rumor_score"],errors="coerce").clip(0,65))
    if parts:
        derived=pd.concat(parts,axis=1).max(axis=1,skipna=True)
        derived=derived.where(pd.concat(parts,axis=1).notna().any(axis=1))
        return direct.where(direct.notna(),derived)
    return direct


def build_baseline_components(frame: pd.DataFrame) -> pd.DataFrame:
    """Build baseline pillars without reading setup/T1/T2 fields."""
    out=pd.DataFrame(index=frame.index)

    squeeze=_numeric(frame,"score_squeeze","squeeze_pressure")
    bandwidth=_numeric(frame,"bandwidth","bb_bandwidth")
    squeeze_derived=(100.0-bandwidth*800.0).clip(10,95)
    out["squeeze"]=squeeze.where(squeeze.notna(),squeeze_derived)

    # Non-negotiable V24.1.7 rule: setup/T1/T2 is absent from baseline scoring.
    out["setup"]=np.nan
    out["earnings"]=_earnings_series(frame)

    t1tech=_numeric(frame,"score_t1_tech")
    out["t1_tech"]=t1tech.where(t1tech.notna(),_technical_proxy(frame))

    bayes=_numeric(frame,"score_bayes")
    meta=_numeric(frame,"meta_proba")
    valid_meta=meta.where(meta.between(0,1))*100.0
    if "meta_model_source" in frame.columns:
        source=frame["meta_model_source"].astype(str).str.lower()
        valid_meta=valid_meta.mask(source.eq("fallback"))
    out["bayes"]=bayes.where(bayes.notna(),valid_meta)

    out["cata"]=_catalyst_series(frame)
    out["regime"]=_numeric(frame,"score_regime","action_topdown_score")
    direct_rs=_numeric(frame,"score_rs")
    raw_rs=_numeric(frame,"relative_strength","perf_1m_pct")
    out["rs"]=direct_rs.where(direct_rs.notna(),_rank_score(raw_rs))
    out["news"]=_numeric(frame,"score_news","news_catalyst_score","funnel_instrument_news_score")

    direct_valo=_numeric(frame,"score_valo","valuation_discount_score")
    if direct_valo.notna().any():
        out["valo"]=direct_valo
    else:
        derived,_source=resolve_field(frame,"valuation_discount_score")
        out["valo"]=_clip_score(derived) if derived is not None else np.nan

    for col in BASELINE_COMPONENTS:
        out[col]=_clip_score(out[col]) if col != "setup" else np.nan
    return out


def build_tct_baseline(actions: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, BaselineAudit]:
    """Compute the V24.1.7 Action TCT baseline and Top20 before T1/T2 timing.

    Missing pillars have fixed zero contribution; their weights are not
    redistributed. The disabled setup weight stays in the denominator, so the
    maximum score and coverage are 84%. Only verified PEA Actions meeting the
    configured baseline coverage threshold are ranked.
    """
    if actions.empty:
        empty=actions.copy()
        for col in ("tct_baseline_score","tct_baseline_coverage","tct_baseline_rank","tct_baseline_top20","tct_baseline_status"):
            empty[col]=pd.Series(dtype=float if col not in {"tct_baseline_top20","tct_baseline_status"} else object)
        return empty,BaselineAudit(0,0,0,0,0,None,None)

    out=actions.copy()
    components=build_baseline_components(out)
    total=pd.Series(0.0,index=out.index,dtype=float)
    observed_weight=pd.Series(0.0,index=out.index,dtype=float)
    for name,weight in WEIGHTS_V24_1_2.items():
        if name == SETUP_COMPONENT:
            continue
        values=pd.to_numeric(components[name],errors="coerce")
        observed=values.notna()
        total += values.fillna(0.0)*float(weight)
        observed_weight += observed.astype(float)*float(weight)
        out[f"tct_baseline_component_{name}"]=values
        out[f"tct_baseline_component_{name}_observed"]=observed
    out["tct_baseline_component_setup"]=np.nan
    out["tct_baseline_component_setup_observed"]=False
    out["tct_baseline_score"]=total.clip(0,MAX_SCORE_WITH_SETUP_DISABLED).round(4)
    out["tct_baseline_coverage"]=observed_weight.clip(0,MAX_COVERAGE_WITH_SETUP_DISABLED).round(4)

    pea_pass=pd.Series([universe_gate(row).passed for _,row in out.iterrows()],index=out.index,dtype=bool)
    min_coverage=float(cfg.get("scope",{}).get("baseline_min_coverage",0.60))
    coverage_pass=out["tct_baseline_coverage"] >= min_coverage
    rankable=pea_pass & coverage_pass
    rank=pd.Series(pd.NA,index=out.index,dtype="Int64")
    if rankable.any():
        ordered=out.loc[rankable,"tct_baseline_score"].rank(method="first",ascending=False).astype("Int64")
        rank.loc[rankable]=ordered
    out["tct_baseline_rank"]=rank
    top_n=int(cfg.get("scope",{}).get("baseline_top_n",20))
    out["tct_baseline_top20"]=(rank.notna() & (rank<=top_n))
    status=pd.Series("EXCLUDED_PEA_GATE",index=out.index,dtype=object)
    status.loc[pea_pass & ~coverage_pass]="BLOCK_BASELINE_COVERAGE"
    status.loc[rankable]="BASELINE_RANKED"
    status.loc[out["tct_baseline_top20"]]="BASELINE_TOP20"
    out["tct_baseline_status"]=status
    out["tct_baseline_missing_weight_policy"]="ZERO_FIXED_WEIGHT_NO_REDISTRIBUTION"
    out["tct_baseline_setup_active"]=False
    out["tct_baseline_t1_t2_influence"]=0.0

    audit=BaselineAudit(
        universe_rows=int(len(out)),
        pea_gate_pass_rows=int(pea_pass.sum()),
        coverage_pass_rows=int((pea_pass & coverage_pass).sum()),
        ranked_rows=int(rank.notna().sum()),
        top20_rows=int(out["tct_baseline_top20"].sum()),
        max_score=float(out["tct_baseline_score"].max()) if len(out) else None,
        max_coverage=float(out["tct_baseline_coverage"].max()) if len(out) else None,
    )
    return out,audit
