"""Harnais PIT/OOS commun ETF MT et Actions MT — research only."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
PROTOCOLS = {
    "ETF_MT": Path("config/ETF_MT_PIT_OOS_PROTOCOL.json"),
    "ACTION_MT": Path("config/ACTION_MT_PIT_OOS_PROTOCOL.json"),
}
OBSERVATIONS = {
    "ETF_MT": Path("state/backtest/ETF_MT_PIT_OBSERVATIONS.csv"),
    "ACTION_MT": Path("state/backtest/ACTION_MT_PIT_OBSERVATIONS.csv"),
}


def load_protocol(root: Path, scope: str) -> dict:
    path = root / PROTOCOLS[scope]
    return json.loads(path.read_text(encoding="utf-8"))


def _period_name(timestamp: pd.Timestamp, protocol: dict) -> str:
    periods = protocol["periods"]
    holdout = pd.Timestamp(periods["final_holdout_start"], tz="UTC")
    if timestamp >= holdout:
        return "FINAL_HOLDOUT_LOCKED"
    for name in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"):
        spec = periods[name]
        start = pd.Timestamp(spec["start"], tz="UTC")
        end = pd.Timestamp(spec["end"], tz="UTC")
        if start <= timestamp <= end:
            return name
    return "OUTSIDE_PROTOCOL"


def _empty_summary(protocol: dict, scope: str, reason: str) -> dict:
    return {
        "status": "WAIT_FOR_PIT_HISTORY",
        "reason": reason,
        "scope": scope,
        "protocol_version": protocol.get("version"),
        "primary_horizon_days": protocol.get("primary_horizon_days"),
        "holdout_locked": True,
        "pre_holdout_pass": False,
        "promotion_ready": False,
        "decision_influence": 0.0,
        "automatic_weight_change_allowed": False,
        "automatic_threshold_retuning_allowed": False,
        "real_orders_enabled": False,
        "periods": {
            "VALIDATION_OOS": {"status": "INSUFFICIENT_HISTORY", "pass": False},
            "DIAGNOSTIC_OOS": {"status": "INSUFFICIENT_HISTORY", "pass": False},
        },
    }


def evaluate(observations: pd.DataFrame, protocol: dict, scope: str) -> dict:
    if observations is None or observations.empty:
        return _empty_summary(protocol, scope, "NO_OBSERVATIONS")
    horizon = int(protocol["primary_horizon_days"])
    signal = protocol["ranking"]["signal_column"]
    fallback = protocol["ranking"]["fallback_signal_column"]
    return_col = f"forward_return_pct_{horizon}d"
    needed = {"isin", "as_of", return_col}
    if needed - set(observations.columns):
        return _empty_summary(protocol, scope, f"MISSING_COLUMNS:{sorted(needed - set(observations.columns))}")
    frame = observations.copy()
    frame["as_of"] = pd.to_datetime(frame["as_of"], errors="coerce", utc=True)
    frame[return_col] = pd.to_numeric(frame[return_col], errors="coerce")
    if signal in frame.columns:
        frame["_signal"] = pd.to_numeric(frame[signal], errors="coerce")
    elif fallback in frame.columns:
        frame["_signal"] = pd.to_numeric(frame[fallback], errors="coerce")
    else:
        return _empty_summary(protocol, scope, "MISSING_SIGNAL_COLUMN")
    frame = frame.dropna(subset=["isin", "as_of", "_signal", return_col])
    if frame.empty:
        return _empty_summary(protocol, scope, "NO_VALID_ROWS")
    frame["period"] = frame["as_of"].map(lambda value: _period_name(value, protocol))
    holdout_rows = int(frame["period"].eq("FINAL_HOLDOUT_LOCKED").sum())
    top_k = int(protocol["ranking"]["top_k"])
    min_n = int(protocol["eligibility"]["minimum_instruments_per_snapshot"])
    spacing = int(protocol["eligibility"]["minimum_snapshot_spacing_days"])
    period_summaries = {}
    for period in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"):
        source = frame[frame["period"].eq(period)]
        dates = sorted(source["as_of"].drop_duplicates().tolist())
        selected = []
        for ts in dates:
            if not selected or (ts - selected[-1]).days >= spacing:
                selected.append(ts)
        gaps = []
        for ts in selected:
            snap = source[source["as_of"].eq(ts)]
            if len(snap) < max(min_n, top_k):
                continue
            ranked = snap.nlargest(top_k, "_signal")
            gaps.append(
                {
                    "as_of": ts.date().isoformat(),
                    "n": int(len(snap)),
                    "signal_return_pct": float(ranked[return_col].mean()),
                    "equal_weight_return_pct": float(snap[return_col].mean()),
                }
            )
        if not gaps:
            period_summaries[period] = {"status": "INSUFFICIENT_HISTORY", "pass": False, "snapshot_count": 0}
            continue
        metrics = pd.DataFrame(gaps)
        edge = float(metrics["signal_return_pct"].mean() - metrics["equal_weight_return_pct"].mean())
        pos_signal = float((metrics["signal_return_pct"] > 0).mean() * 100.0)
        pos_base = float((metrics["equal_weight_return_pct"] > 0).mean() * 100.0)
        p10_edge = float(metrics["signal_return_pct"].quantile(0.10) - metrics["equal_weight_return_pct"].quantile(0.10))
        gates = protocol["promotion_gates"]
        gate_results = {
            "snapshot_count": len(metrics) >= int(gates["minimum_independent_snapshots_each_period"]),
            "edge_vs_equal_weight": edge >= float(gates["minimum_signal_vs_equal_weight_mean_return_pp"]),
            "positive_rate_not_worse": pos_signal >= pos_base - float(gates["maximum_positive_rate_degradation_pp"]),
            "p10_not_worse": p10_edge >= -float(gates["maximum_p10_return_degradation_pp"]),
        }
        period_summaries[period] = {
            "status": "OK",
            "pass": bool(all(gate_results.values())),
            "snapshot_count": int(len(metrics)),
            "mean_signal_return_pct": float(metrics["signal_return_pct"].mean()),
            "mean_equal_weight_return_pct": float(metrics["equal_weight_return_pct"].mean()),
            "signal_minus_equal_weight_pp": edge,
            "gates": gate_results,
        }
    pre_pass = all(item.get("pass") for item in period_summaries.values())
    status = "PRE_HOLDOUT_PASSED_FINAL_HOLDOUT_LOCKED" if pre_pass else "HOLD_SHADOW_PRE_HOLDOUT_NOT_PASSED"
    if all(item.get("status") == "INSUFFICIENT_HISTORY" for item in period_summaries.values()):
        status = "WAIT_FOR_PIT_HISTORY"
    return {
        "status": status,
        "scope": scope,
        "protocol_version": protocol.get("version"),
        "primary_horizon_days": horizon,
        "holdout_locked": True,
        "holdout_rows_ignored": holdout_rows,
        "periods": period_summaries,
        "pre_holdout_pass": bool(pre_pass and status != "WAIT_FOR_PIT_HISTORY"),
        "promotion_ready": False,
        "decision_influence": 0.0,
        "automatic_weight_change_allowed": False,
        "automatic_threshold_retuning_allowed": False,
        "real_orders_enabled": False,
    }


def run(root: Path = ROOT, scope: str = "ETF_MT") -> dict:
    protocol = load_protocol(root, scope)
    path = root / OBSERVATIONS[scope]
    if path.exists() and path.stat().st_size:
        observations = pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)
    else:
        observations = pd.DataFrame()
    summary = evaluate(observations, protocol, scope)
    summary["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["observations_path"] = str(OBSERVATIONS[scope])
    summary["observations_rows"] = int(len(observations))
    out = root / f"outputs/audit/{scope}_PIT_OOS_STATUS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def run_all(root: Path = ROOT) -> dict:
    return {scope: run(root=root, scope=scope) for scope in PROTOCOLS}


if __name__ == "__main__":
    print(json.dumps(run_all(), ensure_ascii=False, indent=2))
