from __future__ import annotations

from dataclasses import asdict
from typing import Mapping
import numpy as np
import pandas as pd

from v182.decision.etf_mt_high_precision import Candidate, momo_risk_on, select_candidates
from v182.features.etf_mt_v2081 import (
    _criterion_scores, _exposure_group, build_equal_weight_market_proxy, market_regime,
)


def apply_dynamic_weighting(
    strict_snapshot: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    etf_reference: pd.DataFrame,
    mt_config: Mapping,
    dynamic_config: Mapping,
) -> tuple[pd.DataFrame, dict]:
    """Build V20.8.2 challenger scores by renormalizing available criteria to 100%.

    V20.8.1 columns are preserved untouched for historical attribution. V20.8.2
    uses the same 38 PIT criteria, directions, regime and selection threshold but
    allows partial rows only when weighted coverage >= configured minimum.
    """
    snapshot=strict_snapshot.copy(); criteria_cfg=mt_config["dynamic_criteria"]; expected=list(criteria_cfg)
    if len(expected)!=38: raise ValueError(f"V20.8.2 requires 38 configured criteria, got {len(expected)}")
    weights={name:float(spec["backtested_weight"]) for name,spec in criteria_cfg.items()}; total=sum(weights.values())
    if total<=0: raise ValueError("ETF MT dynamic weights total must be positive")
    raw=snapshot.set_index("instrument_id")[expected].apply(pd.to_numeric,errors="coerce")
    criterion_scores=_criterion_scores(raw,criteria_cfg)
    weighted=pd.Series(0.0,index=raw.index,dtype=float); denom=pd.Series(0.0,index=raw.index,dtype=float)
    for name,weight in weights.items():
        values=pd.to_numeric(criterion_scores[name],errors="coerce"); ok=values.notna(); weighted+=values.fillna(0.0)*weight; denom+=ok.astype(float)*weight
    coverage=denom/total; dynamic_raw=weighted/denom.replace(0,np.nan)
    min_cov=float(dynamic_config.get("minimum_weighted_coverage",0.70)); stale=pd.to_numeric(snapshot.set_index("instrument_id")["staleness_days"],errors="coerce"); scorable=(coverage>=min_cov)&(stale<=7)&dynamic_raw.notna()
    rank=pd.Series(np.nan,index=raw.index,dtype=float); rank.loc[scorable]=dynamic_raw.loc[scorable].rank(method="average",pct=True)*100.0
    raw_weight=float(mt_config["score"]["score_raw_weight"]); rank_weight=float(mt_config["score"]["cross_section_rank_weight"]); final=raw_weight*dynamic_raw+rank_weight*rank
    proxy=build_equal_weight_market_proxy(histories); regime,regime_metrics=market_regime(raw,proxy); regime_allowed=momo_risk_on(regime)
    ref=etf_reference.drop_duplicates("isin").set_index("isin",drop=False) if "isin" in etf_reference.columns else pd.DataFrame()
    candidates=[]
    if regime_allowed:
        for instrument_id in raw.index[scorable]:
            reference_row=ref.loc[instrument_id] if not ref.empty and instrument_id in ref.index else pd.Series(dtype=object)
            if isinstance(reference_row,pd.DataFrame): reference_row=reference_row.iloc[0]
            candidates.append(Candidate(str(instrument_id),float(dynamic_raw.loc[instrument_id]),float(rank.loc[instrument_id]),_exposure_group(reference_row)))
    selected=select_candidates(candidates,regime); selected_ids={c.instrument_id for c in selected}; threshold=float(dynamic_config.get("selection_threshold",mt_config["score"]["selection_threshold"]))
    indexed=snapshot.set_index("instrument_id",drop=False); indexed["dynamic_weight_coverage_pct"]=(coverage*100.0).round(2); indexed["dynamic_available_criteria"]=(raw.notna().sum(axis=1)).astype(int); indexed["dynamic_score_raw"]=dynamic_raw.round(6); indexed["dynamic_score_rank_pct"]=rank.round(6); indexed["dynamic_score_final"]=final.round(6); indexed["dynamic_selected"]=indexed.index.astype(str).isin(selected_ids); indexed["dynamic_decision"]="BLOCK_DATA"; indexed.loc[scorable,"dynamic_decision"]="REJECT_SCORE"
    if not regime_allowed: indexed.loc[scorable,"dynamic_decision"]="ABSTAIN_REGIME"
    else:
        indexed.loc[scorable & (final>=threshold),"dynamic_decision"]="WATCH_NOT_TOP2"; indexed.loc[indexed["dynamic_selected"],"dynamic_decision"]="BUY_CANDIDATE"
    indexed["score_source"]="V20.8.2_DYNAMIC_AVAILABLE_38"; indexed["backtest_attribution"]="NONE_FOR_V20.8.2_UNTIL_DEDICATED_PIT_BACKTEST"; indexed["dynamic_missing_policy"]="AVAILABLE_CRITERIA_RENORMALIZED_TO_100_PERCENT"
    result=indexed.reset_index(drop=True).sort_values(["dynamic_selected","dynamic_score_final","instrument_id"],ascending=[False,False,True],na_position="last")
    summary={"version":dynamic_config.get("version","V20.8.2"),"status":dynamic_config.get("status","SHADOW_ACTIVE_CHALLENGER"),"minimum_weighted_coverage":min_cov,"scorable_etfs":int(scorable.sum()),"blocked_data_etfs":int((~scorable).sum()),"full_38_complete_etfs":int(snapshot["criteria_complete"].sum()),"partial_dynamic_scorable_etfs":int((scorable & ~snapshot.set_index("instrument_id")["criteria_complete"].astype(bool)).sum()),"regime":{**asdict(regime),**regime_metrics,"allowed":regime_allowed},"selected":[{"isin":c.instrument_id,"score_final":c.score_final,"exposure_group":c.exposure_group} for c in selected],"historical_performance_attribution":"NONE_FOR_V20.8.2","v2081_reference_attribution":"90.91% OOS 2021-2023 remains exact-complete-38 V20.8.1 only","live_orders_enabled":False}
    return result,summary
