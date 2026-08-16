from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class GovernedValidationResult:
    snapshot_metrics: pd.DataFrame
    summary: dict[str, Any]


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _prepare_observations(observations: pd.DataFrame, protocol: dict[str, Any]) -> pd.DataFrame:
    horizon = int(protocol["primary_horizon_days"])
    required = {
        "sector",
        "as_of",
        "RARS",
        "RLS",
        "AVCR",
        "DQS",
        "v1_sector_rotation_score",
        f"forward_return_pct_{horizon}d",
        f"mae_pct_{horizon}d",
    }
    missing = required - set(observations.columns)
    if missing:
        raise ValueError(f"MISSING_VALIDATION_COLUMNS:{sorted(missing)}")

    out = observations.copy()
    out["as_of"] = pd.to_datetime(out["as_of"], errors="coerce", utc=True)
    out = out.dropna(subset=["sector", "as_of"]).copy()
    for name in (
        "RARS",
        "RLS",
        "AVCR",
        "DQS",
        "v1_sector_rotation_score",
        f"forward_return_pct_{horizon}d",
        f"mae_pct_{horizon}d",
    ):
        out[name] = pd.to_numeric(out[name], errors="coerce")
    if "promising_but_overvalued" not in out.columns:
        warnings = out.get("warnings", pd.Series("", index=out.index, dtype=object)).astype(str)
        out["promising_but_overvalued"] = warnings.str.contains("PROMISING_BUT_OVERVALUED", regex=False)
    else:
        out["promising_but_overvalued"] = out["promising_but_overvalued"].fillna(False).astype(bool)

    duplicate_keys = ["sector", "as_of", "model_version"] if "model_version" in out.columns else ["sector", "as_of"]
    if out.duplicated(duplicate_keys).any():
        raise ValueError("DUPLICATE_VALIDATION_OBSERVATION")
    return out.sort_values(["as_of", "sector"]).reset_index(drop=True)


def _period_name(timestamp: pd.Timestamp, protocol: dict[str, Any]) -> str:
    periods = protocol["periods"]
    final_holdout = pd.Timestamp(periods["final_holdout_start"], tz="UTC")
    if timestamp >= final_holdout:
        return "FINAL_HOLDOUT_LOCKED"
    for name in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"):
        spec = periods[name]
        start = pd.Timestamp(spec["start"], tz="UTC")
        end = pd.Timestamp(spec["end"], tz="UTC")
        if start <= timestamp <= end:
            return name
    return "OUTSIDE_PROTOCOL"


def _select_spaced_dates(dates: list[pd.Timestamp], minimum_spacing_days: int) -> list[pd.Timestamp]:
    selected: list[pd.Timestamp] = []
    for timestamp in sorted(dates):
        if not selected or (timestamp - selected[-1]).days >= int(minimum_spacing_days):
            selected.append(timestamp)
    return selected


def _portfolio_snapshot(group: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any] | None:
    horizon = int(protocol["primary_horizon_days"])
    return_col = f"forward_return_pct_{horizon}d"
    mae_col = f"mae_pct_{horizon}d"
    minimum_dqs = float(protocol["eligibility"]["minimum_dqs"])
    minimum_sectors = int(protocol["eligibility"]["minimum_sectors_per_snapshot"])
    top_k = int(protocol["ranking"]["top_k"])

    eligible = group.loc[
        group["DQS"].ge(minimum_dqs)
        & group["RARS"].notna()
        & group["v1_sector_rotation_score"].notna()
        & group[return_col].notna()
        & group[mae_col].notna()
    ].copy()
    if len(eligible) < max(minimum_sectors, top_k):
        return None

    v2 = eligible.nlargest(top_k, "RARS")
    v1 = eligible.nlargest(top_k, "v1_sector_rotation_score")
    neutral = eligible

    def _mean(frame: pd.DataFrame, column: str) -> float:
        return float(pd.to_numeric(frame[column], errors="coerce").mean())

    return {
        "as_of": eligible["as_of"].iloc[0],
        "sector_count": int(len(eligible)),
        "v2_return_pct": _mean(v2, return_col),
        "v1_return_pct": _mean(v1, return_col),
        "neutral_return_pct": _mean(neutral, return_col),
        "v2_mae_pct": _mean(v2, mae_col),
        "v1_mae_pct": _mean(v1, mae_col),
        "neutral_mae_pct": _mean(neutral, mae_col),
        "v1_coverage": float(group["v1_sector_rotation_score"].notna().mean()),
        "forward_coverage": float(group[return_col].notna().mean()),
    }


def _safe_quantile(series: pd.Series, q: float) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.quantile(q))


def _warning_metrics(frame: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    horizon = int(protocol["primary_horizon_days"])
    return_col = f"forward_return_pct_{horizon}d"
    mae_col = f"mae_pct_{horizon}d"
    leader_min = float(protocol["warnings"]["leader_rls_min"])
    minimum_dqs = float(protocol["eligibility"]["minimum_dqs"])

    leaders = frame.loc[
        frame["RLS"].ge(leader_min)
        & frame["DQS"].ge(minimum_dqs)
        & frame[return_col].notna()
        & frame[mae_col].notna()
    ].copy()
    flagged = leaders.loc[leaders["promising_but_overvalued"]]
    unflagged = leaders.loc[~leaders["promising_but_overvalued"]]

    def _mean(subset: pd.DataFrame, column: str) -> float | None:
        values = pd.to_numeric(subset[column], errors="coerce").dropna()
        return None if values.empty else float(values.mean())

    flagged_return = _mean(flagged, return_col)
    unflagged_return = _mean(unflagged, return_col)
    flagged_mae = _mean(flagged, mae_col)
    unflagged_mae = _mean(unflagged, mae_col)
    return {
        "leader_n": int(len(leaders)),
        "flagged_n": int(len(flagged)),
        "unflagged_n": int(len(unflagged)),
        "flagged_mean_return_pct": flagged_return,
        "unflagged_mean_return_pct": unflagged_return,
        "flagged_minus_unflagged_return_pp": (
            None if flagged_return is None or unflagged_return is None else flagged_return - unflagged_return
        ),
        "flagged_mean_mae_pct": flagged_mae,
        "unflagged_mean_mae_pct": unflagged_mae,
        "flagged_minus_unflagged_mae_pp": (
            None if flagged_mae is None or unflagged_mae is None else flagged_mae - unflagged_mae
        ),
    }


def _period_summary(snapshot_metrics: pd.DataFrame, protocol: dict[str, Any]) -> dict[str, Any]:
    gates = protocol["promotion_gates"]
    if snapshot_metrics.empty:
        return {"status": "INSUFFICIENT_HISTORY", "pass": False, "gates": {"snapshot_count": False}}

    v2_return = pd.to_numeric(snapshot_metrics["v2_return_pct"], errors="coerce")
    v1_return = pd.to_numeric(snapshot_metrics["v1_return_pct"], errors="coerce")
    neutral_return = pd.to_numeric(snapshot_metrics["neutral_return_pct"], errors="coerce")
    v2_mae = pd.to_numeric(snapshot_metrics["v2_mae_pct"], errors="coerce")
    v1_mae = pd.to_numeric(snapshot_metrics["v1_mae_pct"], errors="coerce")

    metrics = {
        "snapshot_count": int(len(snapshot_metrics)),
        "mean_sector_count": float(snapshot_metrics["sector_count"].mean()),
        "mean_v1_coverage": float(snapshot_metrics["v1_coverage"].mean()),
        "mean_forward_coverage": float(snapshot_metrics["forward_coverage"].mean()),
        "v2_mean_return_pct": float(v2_return.mean()),
        "v1_mean_return_pct": float(v1_return.mean()),
        "neutral_mean_return_pct": float(neutral_return.mean()),
        "v2_minus_v1_mean_return_pp": float(v2_return.mean() - v1_return.mean()),
        "v2_minus_neutral_mean_return_pp": float(v2_return.mean() - neutral_return.mean()),
        "v2_positive_snapshot_rate_pct": float((v2_return > 0).mean() * 100.0),
        "v1_positive_snapshot_rate_pct": float((v1_return > 0).mean() * 100.0),
        "v2_mean_mae_pct": float(v2_mae.mean()),
        "v1_mean_mae_pct": float(v1_mae.mean()),
        "v2_minus_v1_mean_mae_pp": float(v2_mae.mean() - v1_mae.mean()),
        "v2_p10_return_pct": _safe_quantile(v2_return, 0.10),
        "v1_p10_return_pct": _safe_quantile(v1_return, 0.10),
    }
    metrics["v2_minus_v1_p10_return_pp"] = (
        None
        if metrics["v2_p10_return_pct"] is None or metrics["v1_p10_return_pct"] is None
        else metrics["v2_p10_return_pct"] - metrics["v1_p10_return_pct"]
    )

    gate_results = {
        "snapshot_count": metrics["snapshot_count"] >= int(gates["minimum_independent_snapshots_each_period"]),
        "v1_coverage": metrics["mean_v1_coverage"] >= float(protocol["eligibility"]["minimum_v1_coverage"]),
        "forward_coverage": metrics["mean_forward_coverage"] >= float(protocol["eligibility"]["minimum_forward_return_coverage"]),
        "v2_vs_v1_return": metrics["v2_minus_v1_mean_return_pp"] >= float(gates["minimum_v2_vs_v1_mean_return_pp"]),
        "v2_vs_neutral_return": metrics["v2_minus_neutral_mean_return_pp"] >= float(gates["minimum_v2_vs_neutral_mean_return_pp"]),
        "positive_rate_not_worse": (
            metrics["v2_positive_snapshot_rate_pct"]
            >= metrics["v1_positive_snapshot_rate_pct"] - float(gates["maximum_positive_rate_degradation_pp"])
        ),
        "mae_not_worse": metrics["v2_minus_v1_mean_mae_pp"] >= -float(gates["maximum_mean_mae_degradation_pp"]),
        "p10_not_worse": (
            metrics["v2_minus_v1_p10_return_pp"] is not None
            and metrics["v2_minus_v1_p10_return_pp"] >= -float(gates["maximum_p10_return_degradation_pp"])
        ),
    }
    return {
        "status": "OK",
        "pass": bool(all(gate_results.values())),
        "metrics": metrics,
        "gates": gate_results,
    }


def _warning_gate(warning: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    spec = protocol["warnings"]
    flagged_n = int(warning.get("flagged_n", 0))
    unflagged_n = int(warning.get("unflagged_n", 0))
    return_gap = _as_float(warning.get("flagged_minus_unflagged_return_pp"))
    mae_gap = _as_float(warning.get("flagged_minus_unflagged_mae_pp"))
    sample_ok = (
        flagged_n >= int(spec["minimum_flagged_leaders_total"])
        and unflagged_n >= int(spec["minimum_unflagged_leaders_total"])
    )
    separation_ok = bool(
        (return_gap is not None and return_gap <= -float(spec["minimum_return_risk_separation_pp"]))
        or (mae_gap is not None and mae_gap <= -float(spec["minimum_mae_risk_separation_pp"]))
    )
    return {
        "pass": bool(sample_ok and separation_ok),
        **warning,
        "sample_ok": sample_ok,
        "risk_separation_ok": separation_ok,
    }


def evaluate_governed_validation(observations: pd.DataFrame, protocol: dict[str, Any]) -> GovernedValidationResult:
    prepared = _prepare_observations(observations, protocol)
    prepared["period"] = prepared["as_of"].apply(lambda value: _period_name(value, protocol))
    holdout_rows = int(prepared["period"].eq("FINAL_HOLDOUT_LOCKED").sum())
    evaluation = prepared.loc[prepared["period"].isin(["VALIDATION_OOS", "DIAGNOSTIC_OOS"])].copy()

    all_snapshot_rows: list[dict[str, Any]] = []
    period_summaries: dict[str, dict[str, Any]] = {}
    spacing_days = int(protocol["eligibility"]["minimum_snapshot_spacing_days"])
    for period in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"):
        source = evaluation.loc[evaluation["period"].eq(period)].copy()
        available_dates = sorted(source["as_of"].drop_duplicates().tolist())
        selected_dates = _select_spaced_dates(available_dates, spacing_days)
        rows = []
        for timestamp in selected_dates:
            snapshot = _portfolio_snapshot(source.loc[source["as_of"].eq(timestamp)], protocol)
            if snapshot is not None:
                snapshot["period"] = period
                rows.append(snapshot)
                all_snapshot_rows.append(snapshot)
        metrics_frame = pd.DataFrame(rows)
        period_summaries[period] = _period_summary(metrics_frame, protocol)

    snapshot_metrics = pd.DataFrame(all_snapshot_rows)
    warning_metrics = _warning_metrics(evaluation, protocol)
    warning_gate = _warning_gate(warning_metrics, protocol)
    periods_pass = all(summary.get("pass", False) for summary in period_summaries.values())
    pre_holdout_pass = bool(periods_pass and warning_gate["pass"])
    status = "PRE_HOLDOUT_PASSED_FINAL_HOLDOUT_LOCKED" if pre_holdout_pass else "HOLD_SHADOW_PRE_HOLDOUT_NOT_PASSED"
    if all(summary.get("status") == "INSUFFICIENT_HISTORY" for summary in period_summaries.values()):
        status = "WAIT_FOR_PIT_HISTORY"

    summary = {
        "status": status,
        "protocol_version": protocol.get("version"),
        "primary_horizon_days": int(protocol["primary_horizon_days"]),
        "holdout_locked": True,
        "holdout_rows_ignored": holdout_rows,
        "periods": period_summaries,
        "warning_gate": warning_gate,
        "pre_holdout_pass": pre_holdout_pass,
        "promotion_ready": False,
        "decision_influence": 0.0,
        "automatic_weight_change_allowed": False,
        "automatic_threshold_retuning_allowed": False,
    }
    return GovernedValidationResult(snapshot_metrics=snapshot_metrics, summary=summary)
