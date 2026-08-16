from __future__ import annotations

from pathlib import Path
from typing import Any
import json

import numpy as np
import pandas as pd

from v182.backtest.sector_rotation_v2_pit_oos import evaluate_governed_validation


ROOT = Path(__file__).resolve().parents[3]


def load_protocol(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_close_cache(cache_dir: str | Path) -> dict[str, pd.Series]:
    close_by_ticker: dict[str, pd.Series] = {}
    for parquet_file in sorted(Path(cache_dir).glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(parquet_file)
        except Exception:
            continue
        if frame.empty or not hasattr(frame.columns, "levels"):
            continue
        for ticker in frame.columns.get_level_values(0).unique():
            try:
                sub = frame[ticker]
            except (KeyError, ValueError):
                continue
            if "Close" not in sub.columns:
                continue
            close = pd.to_numeric(sub["Close"], errors="coerce").dropna().sort_index()
            if close.empty:
                continue
            close.index = pd.to_datetime(close.index, errors="coerce", utc=True)
            close = close.loc[~close.index.isna()]
            previous = close_by_ticker.get(str(ticker))
            if previous is None or len(close) > len(previous):
                close_by_ticker[str(ticker)] = close
    return close_by_ticker


def _basket_metrics(
    tickers: list[str],
    as_of: pd.Timestamp,
    close_by_ticker: dict[str, pd.Series],
    protocol: dict[str, Any],
    *,
    expected_constituents: int | None = None,
) -> dict[str, Any] | None:
    horizon = int(protocol["primary_horizon_days"])
    outcome_cfg = protocol["outcomes"]
    unique_tickers = sorted({str(ticker).strip() for ticker in tickers if str(ticker).strip() and str(ticker) != "nan"})
    paths: list[np.ndarray] = []
    used: list[str] = []
    for ticker in unique_tickers:
        close = close_by_ticker.get(ticker)
        if close is None or close.empty:
            continue
        start_idx = int(close.index.searchsorted(as_of, side="right"))
        end_idx = start_idx + horizon
        if start_idx >= len(close) or end_idx >= len(close):
            continue
        path = close.iloc[start_idx : end_idx + 1].astype(float).to_numpy()
        start_price = float(path[0])
        if len(path) != horizon + 1 or not np.isfinite(start_price) or start_price <= 0:
            continue
        normalized = path / start_price
        if not np.isfinite(normalized).all():
            continue
        paths.append(normalized)
        used.append(ticker)

    expected = int(expected_constituents) if expected_constituents is not None else len(unique_tickers)
    total = max(len(unique_tickers), expected)
    coverage = float(len(used) / total) if total else 0.0
    if len(used) < int(outcome_cfg["minimum_constituents"]):
        return None
    if coverage < float(outcome_cfg["minimum_constituent_price_coverage"]):
        return None

    basket = np.mean(np.vstack(paths), axis=0)
    path_returns = (basket[1:] - 1.0) * 100.0
    return {
        "forward_return_pct": float((basket[-1] - 1.0) * 100.0),
        "mae_pct": float(np.min(path_returns)),
        "mfe_pct": float(np.max(path_returns)),
        "constituent_price_coverage": coverage,
        "constituents_total": total,
        "constituents_used": len(used),
    }


def build_outcome_observations(
    signal_history: pd.DataFrame,
    membership_history: pd.DataFrame,
    close_by_ticker: dict[str, pd.Series],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    horizon = int(protocol["primary_horizon_days"])
    if signal_history.empty or membership_history.empty:
        return pd.DataFrame(), {"status": "NO_FROZEN_HISTORY", "holdout_signals_locked": 0}

    signals = signal_history.copy()
    members = membership_history.copy()
    signals["as_of"] = pd.to_datetime(signals["as_of"], errors="coerce", utc=True)
    members["as_of"] = pd.to_datetime(members["as_of"], errors="coerce", utc=True)
    signals = signals.dropna(subset=["sector", "as_of", "model_version"])
    members = members.dropna(subset=["sector", "as_of", "model_version"])

    model_version_lock = protocol.get("model_version_lock")
    signals_excluded_version_mismatch = 0
    members_excluded_version_mismatch = 0
    if model_version_lock:
        locked = str(model_version_lock)
        signal_match = signals["model_version"].astype(str).eq(locked)
        member_match = members["model_version"].astype(str).eq(locked)
        signals_excluded_version_mismatch = int((~signal_match).sum())
        members_excluded_version_mismatch = int((~member_match).sum())
        signals = signals.loc[signal_match].copy()
        members = members.loc[member_match].copy()

    final_holdout = pd.Timestamp(protocol["periods"]["final_holdout_start"], tz="UTC")
    holdout_signals_locked = int(signals["as_of"].ge(final_holdout).sum())
    signals = signals.loc[signals["as_of"].lt(final_holdout)].copy()

    period_bounds = []
    for name in ("VALIDATION_OOS", "DIAGNOSTIC_OOS"):
        spec = protocol["periods"][name]
        period_bounds.append(
            (
                pd.Timestamp(spec["start"], tz="UTC"),
                pd.Timestamp(spec["end"], tz="UTC"),
            )
        )
    in_protocol = pd.Series(False, index=signals.index)
    for start, end in period_bounds:
        in_protocol |= signals["as_of"].between(start, end, inclusive="both")
    signals = signals.loc[in_protocol].copy()

    member_groups = {
        (str(sector), timestamp, str(version)): group
        for (sector, timestamp, version), group in members.groupby(["sector", "as_of", "model_version"], sort=False)
    }
    rows: list[dict[str, Any]] = []
    missing_membership = 0
    immature_or_low_coverage = 0
    mature_observations = 0
    for _, signal in signals.iterrows():
        row = signal.to_dict()
        row[f"forward_return_pct_{horizon}d"] = np.nan
        row[f"mae_pct_{horizon}d"] = np.nan
        row[f"mfe_pct_{horizon}d"] = np.nan
        row["constituent_price_coverage"] = 0.0
        row["constituents_total"] = 0
        row["constituents_used"] = 0

        key = (str(signal["sector"]), signal["as_of"], str(signal["model_version"]))
        group = member_groups.get(key)
        if group is None or group.empty:
            missing_membership += 1
            row["outcome_status"] = "MISSING_FROZEN_MEMBERSHIP"
            rows.append(row)
            continue
        tickers = group.get("yahoo_ticker", pd.Series(dtype=object)).dropna().astype(str).tolist()
        metrics = _basket_metrics(
            tickers,
            signal["as_of"],
            close_by_ticker,
            protocol,
            expected_constituents=int(len(group)),
        )
        if metrics is None:
            immature_or_low_coverage += 1
            row["outcome_status"] = "IMMATURE_OR_LOW_PRICE_COVERAGE"
            rows.append(row)
            continue
        row[f"forward_return_pct_{horizon}d"] = metrics["forward_return_pct"]
        row[f"mae_pct_{horizon}d"] = metrics["mae_pct"]
        row[f"mfe_pct_{horizon}d"] = metrics["mfe_pct"]
        row["constituent_price_coverage"] = metrics["constituent_price_coverage"]
        row["constituents_total"] = metrics["constituents_total"]
        row["constituents_used"] = metrics["constituents_used"]
        row["outcome_status"] = "MATURE"
        mature_observations += 1
        rows.append(row)

    observations = pd.DataFrame(rows)
    diagnostic = {
        "status": "OK" if mature_observations else "NO_MATURE_OUTCOMES",
        "model_version_lock": model_version_lock,
        "signals_excluded_version_mismatch": signals_excluded_version_mismatch,
        "members_excluded_version_mismatch": members_excluded_version_mismatch,
        "signals_in_protocol_pre_holdout": int(len(signals)),
        "mature_observations": mature_observations,
        "missing_membership": missing_membership,
        "immature_or_low_coverage": immature_or_low_coverage,
        "holdout_signals_locked": holdout_signals_locked,
        "price_tickers_available": int(len(close_by_ticker)),
    }
    return observations, diagnostic


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False) if path.exists() else pd.DataFrame()


def run(root: Path = ROOT) -> dict[str, Any]:
    protocol = load_protocol(root / "config" / "SECTOR_ROTATION_V2_PIT_OOS_PROTOCOL.json")
    state_dir = root / "state" / "sector_rotation_v2"
    output_dir = root / "outputs" / "sector_rotation"
    audit_dir = root / "outputs" / "audit"
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    signals = _read_csv(state_dir / "SECTOR_ROTATION_V2_HISTORY.csv")
    members = _read_csv(state_dir / "SECTOR_ROTATION_V2_CONSTITUENTS.csv")
    close_by_ticker = _load_close_cache(root / "data" / "cache" / "actions")
    observations, outcome_diagnostic = build_outcome_observations(signals, members, close_by_ticker, protocol)

    result = evaluate_governed_validation(observations, protocol)
    observations_path = output_dir / "V2_PIT_OOS_OBSERVATIONS.csv"
    metrics_path = output_dir / "V2_PIT_OOS_SNAPSHOT_METRICS.csv"
    status_path = audit_dir / "V2_SECTOR_ROTATION_PIT_OOS_STATUS.json"
    observations.to_csv(observations_path, sep=";", index=False, encoding="utf-8-sig")
    result.snapshot_metrics.to_csv(metrics_path, sep=";", index=False, encoding="utf-8-sig")

    summary = dict(result.summary)
    summary.update(
        {
            "outcome_diagnostic": outcome_diagnostic,
            "observations_path": str(observations_path.relative_to(root)),
            "snapshot_metrics_path": str(metrics_path.relative_to(root)),
            "status_path": str(status_path.relative_to(root)),
            "decision_influence": 0.0,
            "live_orders_enabled": False,
        }
    )
    status_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
