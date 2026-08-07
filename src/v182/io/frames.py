from __future__ import annotations
from pathlib import Path
import pandas as pd

MISSING_TOKEN = "NON_OBSERVE"

TECHNICAL_FIELDS = {
    "last_close", "volume", "mm20", "mm50", "mm100", "mm200", "rsi14",
    "macd", "macd_signal", "macd_hist", "atr14", "bb_mid", "bb_upper",
    "bb_lower", "rvol20", "volatility_20d", "volatility_60d",
    "max_drawdown_1y", "relative_strength", "positive_reversal_flag",
    "perf_1m_pct", "perf_3m_pct", "perf_6m_pct", "perf_1y_pct",
    "perf_3y_pct", "perf_5y_pct",
}
FUNDAMENTAL_FIELDS = {
    "per_ttm_yf", "per_forward_yf", "pb", "roe_api", "roa",
    "debt_to_equity", "free_cash_flow", "marge_ebit", "marge_nette",
}
CONSENSUS_FIELDS = {
    "consensus", "consensus_rating", "consensus_score", "n_analysts",
    "target_price", "buy_n", "hold_n", "sell_n", "strong_buy",
    "strong_sell", "consensus_status",
}
SCENARIO_FIELDS = {
    "scenario_bear_pct", "scenario_base_pct", "scenario_bull_pct",
    "asymmetry", "invalidation_level",
}


def load_master(path: str | Path) -> pd.DataFrame:
    """Charge un référentiel maître CSV séparé par ';' en UTF-8 BOM."""
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=True)


def save_master(frame: pd.DataFrame, path: str | Path) -> None:
    """Réécrit un référentiel maître dans le format de stockage V18.2."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", encoding="utf-8-sig", index=False)


def is_missing(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    text = str(value).strip().upper()
    return text in {"", "MISSING", "UNKNOWN", MISSING_TOKEN, "NOT_LOADED", "NAN", "<NA>", "N/A", "NA", "NULL"}


def _safe_cell(frame: pd.DataFrame, isin: str, column: str, default=""):
    if column not in frame.columns:
        return default
    value = frame.at[isin, column]
    return default if is_missing(value) else value


def _source_evidence(source: str, default: str = "D") -> str:
    text = str(source or "").lower()
    if any(token in text for token in ("issuer", "amf", "euronext", "official")):
        return "A"
    if any(token in text for token in ("finnhub", "marketstack", "alpha vantage")):
        return "B"
    if any(token in text for token in ("yahoo", "yfinance", "internal_from_ohlcv", "internal_shortlist")):
        return "C"
    return default


def _existing_meta(frame: pd.DataFrame, isin: str, field: str, current_value) -> dict | None:
    """Return field-aware provenance for the currently stored value.

    The master historically stores some provenance at row level, but dynamic
    fields have dedicated source/date columns. Using those prevents stale
    technical or API values from being frozen merely because the row-level
    evidence is B.
    """
    if is_missing(current_value):
        return None

    row_evidence = str(_safe_cell(frame, isin, "evidence_level", "D"))
    row_as_of = str(_safe_cell(frame, isin, "as_of_date", ""))
    evidence = row_evidence
    as_of = row_as_of

    if field in TECHNICAL_FIELDS:
        source = str(_safe_cell(frame, isin, "ta_source", ""))
        if source:
            evidence = _source_evidence(source, "C")
        else:
            # Technical fields produced by this pipeline are evidence C unless
            # an explicit higher-grade source is known.
            evidence = "C"
        as_of = str(
            _safe_cell(frame, isin, "ta_as_of", "")
            or _safe_cell(frame, isin, "perf_as_of", "")
            or row_as_of
        )
    elif field.endswith("_yf"):
        evidence = "C"
        as_of = str(
            _safe_cell(frame, isin, "yf_consensus_as_of", "")
            or _safe_cell(frame, isin, "fundamentals_as_of", "")
            or row_as_of
        )
    elif field in FUNDAMENTAL_FIELDS:
        source = str(_safe_cell(frame, isin, "fundamentals_source", ""))
        if source:
            evidence = _source_evidence(source, row_evidence)
        as_of = str(_safe_cell(frame, isin, "fundamentals_as_of", "") or row_as_of)
    elif field in CONSENSUS_FIELDS:
        source = str(_safe_cell(frame, isin, "consensus_source", ""))
        if source:
            evidence = _source_evidence(source, row_evidence)
        as_of = str(
            _safe_cell(frame, isin, "consensus_delta_as_of", "")
            or _safe_cell(frame, isin, "yf_consensus_as_of", "")
            or row_as_of
        )
    elif field in SCENARIO_FIELDS:
        evidence = "C"
        as_of = str(_safe_cell(frame, isin, "ta_as_of", "") or row_as_of)

    return {"value": current_value, "evidence_level": evidence, "as_of": as_of}


def _update_companion_provenance(frame: pd.DataFrame, isin: str, field: str, obs: dict) -> None:
    source = str(obs.get("source") or "")
    collected_at = str(obs.get("collected_at") or obs.get("as_of") or "")
    as_of = str(obs.get("as_of") or collected_at)

    if field in TECHNICAL_FIELDS:
        if "ta_source" in frame.columns:
            frame.at[isin, "ta_source"] = source
        if "ta_as_of" in frame.columns:
            frame.at[isin, "ta_as_of"] = collected_at or as_of
        if field.startswith("perf_") and "perf_as_of" in frame.columns:
            frame.at[isin, "perf_as_of"] = as_of
        if "perf_data_status" in frame.columns and field.startswith("perf_"):
            frame.at[isin, "perf_data_status"] = "OK"

    if field.endswith("_yf"):
        if "yf_consensus_as_of" in frame.columns:
            frame.at[isin, "yf_consensus_as_of"] = collected_at or as_of

    if field in FUNDAMENTAL_FIELDS:
        if "fundamentals_source" in frame.columns:
            frame.at[isin, "fundamentals_source"] = source
        if "fundamentals_as_of" in frame.columns:
            frame.at[isin, "fundamentals_as_of"] = collected_at or as_of
        if "fundamentals_status" in frame.columns:
            frame.at[isin, "fundamentals_status"] = "OK"

    if field in CONSENSUS_FIELDS and "consensus_source" in frame.columns:
        frame.at[isin, "consensus_source"] = source


def apply_observations(frame: pd.DataFrame, observations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Apply validated observations without replacing observed values by missing.

    Merge decisions use field-aware provenance where the schema already exposes
    it. This makes cumulative daily runs refreshable without lowering the
    protection afforded to official/higher-evidence values.
    """
    from v182.core.merge import decide

    frame = frame.set_index("isin", drop=False)
    quarantined: list[dict] = []

    for obs in observations:
        isin = obs.get("isin")
        field = obs.get("field")
        if isin is None or field is None or isin not in frame.index:
            continue
        if field not in frame.columns:
            frame[field] = pd.NA

        current_value = frame.at[isin, field]
        existing = _existing_meta(frame, isin, field, current_value)
        decision = decide(existing, obs)

        if decision.action in {"INSERT", "REPLACE"}:
            value = obs.get("value")
            frame.at[isin, field] = "" if value is None else str(value)
            _update_companion_provenance(frame, isin, field, obs)
        elif decision.action == "QUARANTINE":
            quarantined.append({**obs, "reason": decision.reason})
        # KEEP -> rien à faire

    return frame.reset_index(drop=True), quarantined
