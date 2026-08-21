from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.reporting import tct_v24_4_pit_lineage as base


ROOT = Path(__file__).resolve().parents[3]
CONFIG = "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json"
VERSION = "TCT_V24.4.2_PIT_LINEAGE_V3"
FINGERPRINT_ALGORITHM = "TCT_PIT_SHA256_CANONICAL_V3"

# Prediction and validation-context fields are immutable within the V24.4.2
# epoch. Outcome columns are deliberately excluded because they are appended
# only after the first subsequent observed daily OHLC session.
PREDICTION_FIELDS = (
    "version", "phase", "isin", "yahoo_ticker", "as_of_date", "reference_close",
    "source_tct_decision", "source_tct_setup", "source_t1_quality", "source_t2_quality",
    "entry_state", "entry_score", "entry_confirmation_count", "exit_state", "exit_risk_score",
    "atr14_pct", "range_expansion", "sector_yf", "industry_yf", "country_yf", "market_cap",
    "days_to_earnings", "candidate_rank", "candidate_rank_reason", "candidate_priority_score",
    "movement_potential_score", "movement_potential_raw_score", "movement_potential_coverage",
    "direction_bias_score", "direction_bias_raw_score", "direction_coverage", "data_quality_state",
    "catalyst_state", "technical_only_actionable_flag", "news_magnitude_score", "news_direction_score",
    "news_confidence", "news_match_confidence", "news_article_count", "news_independent_sources",
    "news_event_types", "news_top_headlines", "news_source", "news_cache_hit", "news_window_start_utc",
    "news_window_end_utc", "news_error", "technical_impulse_score", "technical_direction_score",
    "known_event_proximity_score", "global_market_shock_score", "global_risk_on_score",
    "global_vix_return_pct", "global_eurostoxx50_return_pct", "global_cac40_return_pct",
    "global_dax_return_pct", "news_technical_conflict", "snapshot_generated_at_utc",
    "snapshot_window_start_utc", "snapshot_window_end_utc", "snapshot_key",
)


def prediction_fingerprint(row: pd.Series) -> str:
    payload = {key: base._normalise(row.get(key)) for key in PREDICTION_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _prepare_ohlc(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return ledger
    work = ledger.copy()
    work["isin"] = work.get("isin", pd.Series(index=work.index, dtype=object)).astype(str).str.upper()
    work["as_of_date"] = pd.to_datetime(work.get("as_of_date"), errors="coerce")
    for column in ("session_open", "session_high", "session_low", "session_close", "reference_close"):
        work[column] = pd.to_numeric(work.get(column), errors="coerce")
    work["session_close"] = work["session_close"].fillna(work["reference_close"])
    work = work.dropna(subset=["as_of_date", "session_close"])
    return work[work["session_close"] > 0].sort_values(["isin", "as_of_date"])


def first_subsequent_session(ohlc: pd.DataFrame, isin: str, source_date: object) -> dict | None:
    if ohlc.empty:
        return None
    source = pd.to_datetime(source_date, errors="coerce")
    if pd.isna(source):
        return None
    rows = ohlc[ohlc["isin"].eq(str(isin).upper()) & (ohlc["as_of_date"] > pd.Timestamp(source))]
    if rows.empty:
        return None
    first = rows.iloc[0]
    return {
        "as_of_date": pd.Timestamp(first["as_of_date"]).date().isoformat(),
        "open": first.get("session_open"),
        "high": first.get("session_high"),
        "low": first.get("session_low"),
        "close": first.get("session_close"),
    }


def _significant_threshold(row: pd.Series, cfg: dict) -> float:
    spec = cfg.get("significant_move", {})
    floor = float(spec.get("absolute_floor_pct", 2.0))
    atr_multiple = float(spec.get("atr_multiple", 1.25))
    atr = pd.to_numeric(pd.Series([row.get("atr14_pct")]), errors="coerce").iloc[0]
    atr_pct = 0.0 if pd.isna(atr) else max(0.0, float(atr) * 100.0)
    return max(floor, atr_multiple * atr_pct)


def _labels(row: pd.Series, outcome: dict, cfg: dict) -> dict | None:
    before = pd.to_numeric(pd.Series([row.get("reference_close")]), errors="coerce").iloc[0]
    close = pd.to_numeric(pd.Series([outcome.get("close")]), errors="coerce").iloc[0]
    if pd.isna(before) or float(before) <= 0 or pd.isna(close) or float(close) <= 0:
        return None
    before = float(before)
    close = float(close)
    open_ = pd.to_numeric(pd.Series([outcome.get("open")]), errors="coerce").iloc[0]
    high = pd.to_numeric(pd.Series([outcome.get("high")]), errors="coerce").iloc[0]
    low = pd.to_numeric(pd.Series([outcome.get("low")]), errors="coerce").iloc[0]
    close_ret = (close / before - 1.0) * 100.0
    open_gap = None if pd.isna(open_) or float(open_) <= 0 else (float(open_) / before - 1.0) * 100.0
    high_exc = None if pd.isna(high) or float(high) <= 0 else (float(high) / before - 1.0) * 100.0
    low_exc = None if pd.isna(low) or float(low) <= 0 else (float(low) / before - 1.0) * 100.0
    session_range = None
    if not pd.isna(high) and not pd.isna(low) and float(high) > 0 and float(low) > 0:
        session_range = (float(high) - float(low)) / before * 100.0
    extremes = [abs(x) for x in (high_exc, low_exc) if x is not None]
    session_abs_extreme = max(extremes) if extremes else abs(close_ret)
    bias = pd.to_numeric(pd.Series([row.get("direction_bias_score")]), errors="coerce").iloc[0]
    mae = None
    if not pd.isna(bias) and float(bias) > 0 and low_exc is not None:
        mae = min(0.0, low_exc)
    elif not pd.isna(bias) and float(bias) < 0 and high_exc is not None:
        mae = min(0.0, -high_exc)
    elif low_exc is not None and high_exc is not None:
        mae = min(low_exc, -high_exc, 0.0)
    threshold = _significant_threshold(row, cfg)
    return {
        "raw_realized_open_gap_pct": open_gap,
        "raw_realized_session_range_pct": session_range,
        "raw_realized_high_excursion_pct": high_exc,
        "raw_realized_low_excursion_pct": low_exc,
        "raw_realized_session_abs_extreme_pct": session_abs_extreme,
        "raw_realized_max_adverse_excursion_pct": mae,
        "raw_realized_close_to_close_return_pct": close_ret,
        "raw_realized_abs_return_pct": abs(close_ret),
        "raw_significant_move_threshold_pct": threshold,
        "raw_significant_session_move_flag": float(session_abs_extreme >= threshold),
        "raw_significant_close_move_flag": float(abs(close_ret) >= threshold),
    }


def apply_lineage(catalyst_ledger: pd.DataFrame, ohlc_ledger: pd.DataFrame, *, minimum_snapshot_coverage: float, labeled_at_utc: str, cfg: dict) -> tuple[pd.DataFrame, dict]:
    if catalyst_ledger.empty:
        return catalyst_ledger.copy(), {
            "snapshot_rows": 0, "fingerprints_created": 0, "fingerprint_mismatches": 0,
            "raw_next_session_labels": 0, "validator_labels": 0, "qualified_snapshots": 0, "pending_snapshots": 0,
        }
    out = catalyst_ledger.copy()
    prepared = _prepare_ohlc(ohlc_ledger)
    existing_hash = out.get("snapshot_payload_sha256", pd.Series(pd.NA, index=out.index, dtype=object)).copy()
    recalculated = out.apply(prediction_fingerprint, axis=1)
    existing_text = existing_hash.astype(str).str.strip()
    had_hash = existing_hash.notna() & ~existing_text.isin({"", "nan", "None"})
    mismatch = had_hash & existing_text.ne(recalculated)
    out["snapshot_payload_sha256"] = existing_hash.where(had_hash, recalculated)
    out["pit_fingerprint_valid"] = ~mismatch

    metric_names = (
        "open_gap_pct", "session_range_pct", "high_excursion_pct", "low_excursion_pct",
        "session_abs_extreme_pct", "max_adverse_excursion_pct", "close_to_close_return_pct", "abs_return_pct",
        "significant_move_threshold_pct", "significant_session_move_flag", "significant_close_move_flag",
    )
    for name in metric_names:
        for prefix in ("raw_realized_", "realized_") if name not in {"significant_move_threshold_pct", "significant_session_move_flag", "significant_close_move_flag"} else ("raw_", ""):
            column = f"{prefix}{name}"
            if column not in out.columns:
                out[column] = pd.NA
    for column in ("outcome_as_of_date", "outcome_labeled_at_utc", "outcome_label_source", "outcome_step", "outcome_snapshot_coverage", "pit_label_evaluable", "realized_direction_hit", "realized_abs_move_rank_within_snapshot"):
        if column not in out.columns:
            out[column] = pd.NA

    pre_mask = out.get("phase", pd.Series(index=out.index, dtype=object)).astype(str).eq("PREOPEN")
    raw_columns = [c for c in out.columns if c.startswith("raw_realized_") or c.startswith("raw_significant_")]
    out.loc[pre_mask, raw_columns] = pd.NA
    for idx in out.index[pre_mask]:
        row = out.loc[idx]
        outcome = first_subsequent_session(prepared, str(row.get("isin") or ""), row.get("as_of_date"))
        out.at[idx, "outcome_as_of_date"] = pd.NA
        out.at[idx, "outcome_labeled_at_utc"] = pd.NA
        out.at[idx, "outcome_label_source"] = "FIRST_SUBSEQUENT_OBSERVED_DAILY_OHLC"
        out.at[idx, "outcome_step"] = pd.NA
        if outcome is None:
            continue
        labels = _labels(row, outcome, cfg)
        if labels is None:
            continue
        for key, value in labels.items():
            out.at[idx, key] = None if value is None else round(float(value), 6)
        out.at[idx, "outcome_as_of_date"] = outcome["as_of_date"]
        out.at[idx, "outcome_labeled_at_utc"] = labeled_at_utc
        out.at[idx, "outcome_step"] = 1

    validator_columns = [
        "realized_open_gap_pct", "realized_session_range_pct", "realized_high_excursion_pct", "realized_low_excursion_pct",
        "realized_session_abs_extreme_pct", "realized_max_adverse_excursion_pct", "realized_close_to_close_return_pct",
        "realized_abs_return_pct", "significant_move_threshold_pct", "significant_session_move_flag", "significant_close_move_flag",
        "realized_direction_hit", "realized_abs_move_rank_within_snapshot",
    ]
    out.loc[pre_mask, validator_columns] = pd.NA
    out.loc[pre_mask, "pit_label_evaluable"] = False
    out.loc[pre_mask, "outcome_snapshot_coverage"] = 0.0
    qualified = pending = 0
    group_key = "snapshot_generated_at_utc" if "snapshot_generated_at_utc" in out.columns else "snapshot_key"
    for _, group in out[pre_mask].groupby(group_key, dropna=False):
        raw = pd.to_numeric(group["raw_realized_session_abs_extreme_pct"], errors="coerce")
        coverage = 0.0 if len(group) == 0 else float(raw.notna().sum() / len(group))
        out.loc[group.index, "outcome_snapshot_coverage"] = round(coverage, 6)
        if coverage < float(minimum_snapshot_coverage):
            pending += 1
            continue
        qualified += 1
        for idx in group.index:
            mapping = {
                "realized_open_gap_pct": "raw_realized_open_gap_pct",
                "realized_session_range_pct": "raw_realized_session_range_pct",
                "realized_high_excursion_pct": "raw_realized_high_excursion_pct",
                "realized_low_excursion_pct": "raw_realized_low_excursion_pct",
                "realized_session_abs_extreme_pct": "raw_realized_session_abs_extreme_pct",
                "realized_max_adverse_excursion_pct": "raw_realized_max_adverse_excursion_pct",
                "realized_close_to_close_return_pct": "raw_realized_close_to_close_return_pct",
                "realized_abs_return_pct": "raw_realized_abs_return_pct",
                "significant_move_threshold_pct": "raw_significant_move_threshold_pct",
                "significant_session_move_flag": "raw_significant_session_move_flag",
                "significant_close_move_flag": "raw_significant_close_move_flag",
            }
            if pd.isna(pd.to_numeric(pd.Series([out.at[idx, "raw_realized_session_abs_extreme_pct"]]), errors="coerce").iloc[0]):
                continue
            for target, source in mapping.items():
                out.at[idx, target] = out.at[idx, source]
            bias = pd.to_numeric(pd.Series([out.at[idx, "direction_bias_score"]]), errors="coerce").iloc[0]
            close_ret = pd.to_numeric(pd.Series([out.at[idx, "raw_realized_close_to_close_return_pct"]]), errors="coerce").iloc[0]
            if not pd.isna(bias) and abs(float(bias)) >= 25 and not pd.isna(close_ret) and float(close_ret) != 0:
                out.at[idx, "realized_direction_hit"] = 1.0 if np.sign(float(bias)) == np.sign(float(close_ret)) else 0.0
            out.at[idx, "pit_label_evaluable"] = True
        eligible = group.index[pd.to_numeric(out.loc[group.index, "realized_session_abs_extreme_pct"], errors="coerce").notna().to_numpy()]
        if len(eligible):
            ranks = pd.to_numeric(out.loc[eligible, "realized_session_abs_extreme_pct"], errors="coerce").rank(method="min", ascending=False)
            for idx, rank in ranks.items():
                out.at[idx, "realized_abs_move_rank_within_snapshot"] = float(rank)

    stats = {
        "snapshot_rows": int(len(out)),
        "fingerprints_created": int((~had_hash).sum()),
        "fingerprint_mismatches": int(mismatch.sum()),
        "raw_next_session_labels": int(pd.to_numeric(out.loc[pre_mask, "raw_realized_session_abs_extreme_pct"], errors="coerce").notna().sum()),
        "validator_labels": int(pd.to_numeric(out.loc[pre_mask, "realized_session_abs_extreme_pct"], errors="coerce").notna().sum()),
        "qualified_snapshots": int(qualified),
        "pending_snapshots": int(pending),
        "minimum_snapshot_outcome_coverage": float(minimum_snapshot_coverage),
    }
    return out, stats


def run(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config" / CONFIG).read_text(encoding="utf-8"))
    generated_at = datetime.now(timezone.utc).isoformat()
    ledger_path = root / cfg["state"]["catalyst_ledger_path"]
    ohlc_path = root / cfg["state"]["daily_close_ledger_path"]
    ledger = base._read_csv(ledger_path)
    ohlc = base._read_csv(ohlc_path)
    coverage = float(cfg.get("pit_lineage", {}).get("minimum_snapshot_outcome_coverage", 0.80))
    enriched, stats = apply_lineage(ledger, ohlc, minimum_snapshot_coverage=coverage, labeled_at_utc=generated_at, cfg=cfg)
    base._write_csv(enriched, ledger_path)
    mismatch = int(stats["fingerprint_mismatches"])
    payload = {
        "status": "FAIL_CLOSED_FINGERPRINT_MISMATCH" if mismatch else "SUCCESS_PIT_LINEAGE",
        "version": VERSION,
        "generated_at_utc": generated_at,
        "ledger_path": str(ledger_path.relative_to(root)),
        "ohlc_ledger_path": str(ohlc_path.relative_to(root)),
        **stats,
        "first_subsequent_session_only": True,
        "labels": ["open_gap", "session_range", "high_low_excursions", "close_return", "max_adverse_excursion", "significant_move"],
        "fingerprint_algorithm": FINGERPRINT_ALGORITHM,
        "fingerprint_fields": list(PREDICTION_FIELDS),
        "snapshot_fingerprint_required": True,
        "production_influence": 0.0,
        "holdout_opened": False,
        "promotion_authority": False,
    }
    audit = root / "outputs" / "audit" / "TCT_V24_4_2_PIT_LINEAGE_AUDIT.json"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if mismatch and bool(cfg.get("pit_lineage", {}).get("fail_closed_on_fingerprint_mismatch", True)):
        raise RuntimeError(f"V24.4.2 PIT fingerprint mismatch on {mismatch} stored snapshot rows")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
