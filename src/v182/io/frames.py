from __future__ import annotations
from pathlib import Path
import pandas as pd

MISSING_TOKEN = "NON_OBSERVE"
MISSING_TOKENS={"", "MISSING", "UNKNOWN", MISSING_TOKEN, "NOT_LOADED", "NAN", "<NA>", "N/A", "NA", "NULL"}

OHLCV_BOOTSTRAP_FIELDS={
    "mm20","mm50","mm100","mm200","rsi14","macd","macd_signal","macd_hist","macd_hist_3d_ago","macd_hist_change_3d",
    "atr14","atr14_pct","opening_gap_pct","bb_mid","bb_upper","bb_lower","bb_bandwidth","bb_breakout_cross_flag",
    "bb_breakout_hold_flag","bb_bandwidth_p15_100","bb_squeeze_fraction_8","bb_bandwidth_expansion_ratio","stoch_k","stoch_d",
    "stoch_bull_cross_flag","rvol20","rvol20_3d_avg","volatility_20d","volatility_60d","volatility_1y_pct","max_drawdown_1y",
    "perf_10d_pct","perf_1m_pct","perf_3m_pct","perf_6m_pct","perf_1y_pct","perf_3y_pct","perf_5y_pct","above_mm20",
    "above_mm50","above_mm200","high_52w","distance_high_52w_pct","catchup_52w_score","high_52w_bonus_malus_points",
    "distribution_policy","dividend_cagr_3y","dividend_ttm","positive_reversal_flag","last_close","volume","relative_strength",
    "relative_strength_10d",
}
YFINANCE_SELF_DESCRIBING_FIELDS={
    "per_ttm_yf","per_forward_yf","revenue_growth_yf","earnings_growth_yf","target_mean_yf","target_high_yf","target_low_yf",
    "current_price_yf","n_analysts_yf","recommendation_mean_yf","recommendation_key_yf","dividend_rate_yf","sector_yf","industry_yf",
    "country_yf","quote_type_yf","earnings_timestamp_yf","earnings_timestamp_start_yf","earnings_timestamp_end_yf",
    "next_earnings_timestamp_yf","days_to_earnings","earnings_within_7d_flag","earnings_within_30d_flag",
}
YFINANCE_GENERIC_FIELDS={
    "market_cap","pb","roe_api","roa","debt_to_equity","total_debt_yf","ebitda_yf","free_cash_flow","marge_ebit","marge_nette",
    "beta","dividend_yield_pct","payout_ratio",
}
YFINANCE_SOURCE_COLUMNS=("fundamentals_source","source","source_name","ta_source","consensus_source")
LEGACY_CONTEXT_FIELDS={
    "evidence_level","as_of_date","ta_as_of","perf_as_of","yf_consensus_as_of","fundamentals_as_of","per_fix_as_of",
    *YFINANCE_SOURCE_COLUMNS,
}


def load_master(path: str | Path) -> pd.DataFrame:
    """Charge un référentiel maître CSV ';' UTF-8 BOM, valeurs vides = NaN."""
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=True)


def save_master(frame: pd.DataFrame, path: str | Path) -> None:
    out = Path(path); out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", encoding="utf-8-sig", index=False)


def is_missing(value) -> bool:
    if value is None: return True
    # Masters are string-heavy. Strings can never be scalar NaN objects, so avoid
    # a pandas dispatch on the dominant path while keeping the exact token policy.
    if isinstance(value,str):
        return value.strip().upper() in MISSING_TOKENS
    try:
        if pd.isna(value): return True
    except (TypeError, ValueError):
        return False
    return str(value).strip().upper() in MISSING_TOKENS


def _cell(frame:pd.DataFrame,isin,field:str):
    if field not in frame.columns: return None
    return frame.at[isin,field]


def _latest_as_of(frame:pd.DataFrame,isin,candidates:tuple[str,...])->str:
    """Return the latest parseable UTC evidence timestamp from candidate columns."""
    values: list[tuple[pd.Timestamp, str]]=[]
    for field in candidates:
        value=_cell(frame,isin,field)
        if is_missing(value):
            continue
        parsed=pd.to_datetime(value,errors="coerce",utc=True)
        if pd.isna(parsed):
            continue
        values.append((parsed,str(value).strip()))
    return max(values,key=lambda item:item[0])[1] if values else ""


def _has_yfinance_legacy_marker(frame:pd.DataFrame,isin)->bool:
    for field in YFINANCE_SOURCE_COLUMNS:
        value=_cell(frame,isin,field)
        if is_missing(value): continue
        text=str(value).casefold()
        if "yfinance" in text or "yahoo" in text: return True
    return False


def _legacy_row_context(frame: pd.DataFrame, isin) -> dict:
    """Compute all legacy row metadata once for repeated field merges on one ISIN."""
    raw_evidence=_cell(frame,isin,"evidence_level")
    row_evidence="D" if is_missing(raw_evidence) else str(raw_evidence).strip().upper()
    if row_evidence not in {"A","B","C","D"}: row_evidence="D"
    return {
        "row_evidence":row_evidence,
        "row_as_of":_latest_as_of(frame,isin,("as_of_date",)),
        "ohlcv_as_of":_latest_as_of(frame,isin,("ta_as_of","perf_as_of","as_of_date")),
        "yfinance_self_as_of":_latest_as_of(frame,isin,("yf_consensus_as_of","fundamentals_as_of","per_fix_as_of","as_of_date")),
        "yfinance_generic_as_of":_latest_as_of(frame,isin,("fundamentals_as_of","per_fix_as_of","yf_consensus_as_of","as_of_date")),
        "has_yfinance_marker":_has_yfinance_legacy_marker(frame,isin),
    }


def _legacy_field_metadata(frame:pd.DataFrame,isin,field:str,incoming:dict,context:dict|None=None)->dict:
    """Resolve evidence for a legacy value that predates per-field provenance.

    ``context`` is an optional per-ISIN cache. When absent this function retains
    its historical standalone behavior by recomputing from the current frame.
    """
    ctx=context if context is not None else _legacy_row_context(frame,isin)
    row_evidence=str(ctx["row_evidence"])
    row_as_of=str(ctx["row_as_of"])
    if row_evidence=="A": return {"evidence_level":"A","as_of":row_as_of,"bootstrap":"ROW_A"}

    source=str(incoming.get("source") or "").strip().upper()
    if source=="INTERNAL_FROM_OHLCV" and field in OHLCV_BOOTSTRAP_FIELDS:
        return {"evidence_level":"C","as_of":str(ctx["ohlcv_as_of"]),"bootstrap":"LEGACY_OHLCV_C"}

    if source=="YFINANCE" and field in YFINANCE_SELF_DESCRIBING_FIELDS:
        return {"evidence_level":"C","as_of":str(ctx["yfinance_self_as_of"]),"bootstrap":"LEGACY_YFINANCE_C"}

    if source=="YFINANCE" and field in YFINANCE_GENERIC_FIELDS and bool(ctx["has_yfinance_marker"]):
        return {"evidence_level":"C","as_of":str(ctx["yfinance_generic_as_of"]),"bootstrap":"LEGACY_YFINANCE_MARKED_C"}

    return {"evidence_level":row_evidence,"as_of":row_as_of,"bootstrap":"ROW_FALLBACK"}


def _ensure_text_assignable(frame: pd.DataFrame, field: str) -> None:
    """Allow canonical string storage even when pandas inferred a numeric dtype."""
    if field in frame.columns and frame[field].dtype != object:
        frame[field] = frame[field].astype(object)


def _materialize_missing_observation_fields(frame: pd.DataFrame, observations: list[dict]) -> pd.DataFrame:
    """Add new observation columns in one block to avoid DataFrame fragmentation."""
    present=set(frame.columns); missing=[]
    for obs in observations:
        isin=obs.get("isin"); field=obs.get("field")
        if isin is None or field is None or isin not in frame.index or field in present:
            continue
        present.add(field); missing.append(field)
    if not missing:
        return frame
    additions=pd.DataFrame(pd.NA,index=frame.index,columns=missing,dtype=object)
    return pd.concat([frame,additions],axis=1)


def apply_observations(frame: pd.DataFrame, observations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Merge observations with provenance, freshness and numeric-domain gates."""
    if not observations:
        return frame.reset_index(drop=True), []

    from v182.audit.provenance import append_records, load_latest_readonly, retained_meta_matches_value, value_hash
    from v182.core.data_domain import bounds_for_field, validate_numeric_value
    from v182.core.merge import decide

    frame = frame.set_index("isin", drop=False)
    frame = _materialize_missing_observation_fields(frame, observations)
    quarantined: list[dict] = []
    base_provenance=load_latest_readonly()
    provenance_updates: dict[tuple[str,str],dict] = {}
    legacy_contexts: dict[str,dict] = {}
    provenance_records=[]

    for obs in observations:
        isin = obs.get("isin"); field = obs.get("field")
        if isin is None or field is None or isin not in frame.index:
            provenance_records.append({**obs,"merge_action":"SKIP","merge_reason":"ISIN_OR_FIELD_NOT_IN_MASTER"})
            continue

        field_text=str(field)
        if bounds_for_field(field_text) is not None:
            valid, domain_reason = validate_numeric_value(field_text, obs.get("value"))
            if not valid:
                quarantined.append({**obs,"reason":f"NUMERIC_DOMAIN:{domain_reason}"})
                provenance_records.append({**obs,"merge_action":"QUARANTINE","merge_reason":f"NUMERIC_DOMAIN:{domain_reason}"})
                continue

        current_value = frame.at[isin, field]
        isin_text=str(isin); key=(isin_text,field_text)
        meta=provenance_updates.get(key)
        if meta is None:
            meta=base_provenance.get(key)
        if is_missing(current_value):
            existing=None
        elif meta and retained_meta_matches_value(meta,current_value):
            existing={"value":current_value,"evidence_level":meta.get("evidence_level","D"),"as_of":meta.get("as_of","")}
        else:
            context=legacy_contexts.get(isin_text)
            if context is None:
                context=_legacy_row_context(frame,isin)
                legacy_contexts[isin_text]=context
            legacy=_legacy_field_metadata(frame,isin,field_text,obs,context=context)
            existing={"value":current_value,"evidence_level":legacy["evidence_level"],"as_of":legacy["as_of"]}
        decision=decide(existing,obs)
        if decision.action in {"INSERT","REPLACE"}:
            _ensure_text_assignable(frame,field_text)
            value=obs.get("value"); frame.at[isin,field_text]="" if value is None else str(value)
            provenance_updates[key]={**obs,"merge_action":decision.action,"merge_reason":decision.reason,"value_sha256":value_hash(value)}
            # Preserve historical same-batch semantics: if a field used to derive
            # legacy context changes, the next legacy lookup must see that update.
            if field_text in LEGACY_CONTEXT_FIELDS:
                legacy_contexts.pop(isin_text,None)
        elif decision.action=="QUARANTINE":
            quarantined.append({**obs,"reason":decision.reason})
        provenance_records.append({**obs,"merge_action":decision.action,"merge_reason":decision.reason})

    append_records(provenance_records)
    return frame.reset_index(drop=True), quarantined
