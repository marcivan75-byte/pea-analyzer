from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import argparse
import json

import pandas as pd

from v182.audit.canonical_universe import filter_actions
from v182.mapping.action_yahoo_ticker import qualify_action_yahoo_tickers
from v182.backtest.calibration_windows import load_policy, resolve_primary_window

ROOT = Path(__file__).resolve().parents[3]
PRIMARY_ANCHOR_TOLERANCE_DAYS = 7
FRESHNESS_TOLERANCE_DAYS = 7
STRESS_START = pd.Timestamp("2020-01-01", tz="UTC")
STRESS_END_EXCLUSIVE = pd.Timestamp("2023-01-01", tz="UTC")


def _read_semicolon(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, low_memory=False)


def _utc(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _month_key(ts: pd.Timestamp) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def _months_between(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    left = start.tz_localize(None).to_period("M")
    right = end.tz_localize(None).to_period("M")
    return [str(period) for period in pd.period_range(left, right, freq="M")]


def _real_close_series(frame: pd.DataFrame, ticker: str) -> pd.Series:
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    out = frame
    if isinstance(out.columns, pd.MultiIndex):
        level = None
        for candidate in range(out.columns.nlevels):
            if ticker in set(map(str, out.columns.get_level_values(candidate))):
                level = candidate
                break
        if level is None:
            return pd.Series(dtype=float)
        try:
            out = out.xs(ticker, axis=1, level=level, drop_level=True)
        except (KeyError, ValueError):
            return pd.Series(dtype=float)
    close_columns = [column for column in out.columns if str(column).strip().lower() == "close"]
    if not close_columns:
        return pd.Series(dtype=float)
    close = pd.to_numeric(out[close_columns[0]], errors="coerce").dropna()
    if close.empty:
        return pd.Series(dtype=float)
    index = pd.to_datetime(close.index, errors="coerce", utc=True)
    valid = ~index.isna()
    close = pd.Series(close.to_numpy()[valid], index=index[valid], dtype=float)
    return close[~close.index.duplicated(keep="last")].sort_index()


def load_cache_series(cache_dir: Path) -> tuple[dict[str, pd.Series], list[dict[str, str]]]:
    series_by_ticker: dict[str, pd.Series] = {}
    failures: list[dict[str, str]] = []
    for parquet in sorted(cache_dir.glob("history_*.parquet")):
        try:
            frame = pd.read_parquet(parquet)
        except Exception as exc:
            failures.append({"file": parquet.name, "error": type(exc).__name__})
            continue
        if frame.empty or not isinstance(frame.columns, pd.MultiIndex):
            failures.append({"file": parquet.name, "error": "UNSUPPORTED_OR_EMPTY_CACHE_FRAME"})
            continue
        tickers = sorted({str(value) for value in frame.columns.get_level_values(0)})
        for ticker in tickers:
            close = _real_close_series(frame, ticker)
            if close.empty:
                continue
            prior = series_by_ticker.get(ticker)
            if prior is None or prior.empty:
                series_by_ticker[ticker] = close
            else:
                merged = pd.concat([prior, close]).sort_index()
                series_by_ticker[ticker] = merged[~merged.index.duplicated(keep="last")]
    return series_by_ticker, failures


def _load_universes(root: Path) -> dict[str, pd.DataFrame]:
    actions = _read_semicolon(root / "inputs" / "V18.2_PEA_ACTIONS_MASTER.csv")
    if not actions.empty:
        actions = filter_actions(
            actions,
            root / "config" / "V21_3_ACTION_UNIVERSE_1829_ISINS.parts",
        ).included.reset_index(drop=True)
        qualify_action_yahoo_tickers(actions)

    etf = _read_semicolon(root / "inputs" / "V18.2_PEA_ETF_MASTER.csv")
    mapping = _read_semicolon(root / "config" / "V18.2_ETF_TICKER_MAP.csv")
    if not etf.empty and not mapping.empty and {"isin", "yahoo_ticker"}.issubset(mapping.columns):
        lookup = mapping[["isin", "yahoo_ticker"]].drop_duplicates("isin")
        etf = etf.drop(columns=["yahoo_ticker"], errors="ignore").merge(lookup, on="isin", how="left")
    return {"ACTION": actions, "ETF": etf}


def _instrument_row(
    asset_class: str,
    isin: str,
    ticker: str,
    close: pd.Series | None,
    *,
    primary_start: pd.Timestamp,
    as_of: pd.Timestamp,
) -> dict[str, Any]:
    expected_primary_months = _months_between(primary_start, as_of)
    expected_stress_months = _months_between(STRESS_START, STRESS_END_EXCLUSIVE - pd.Timedelta(days=1))
    base: dict[str, Any] = {
        "asset_class": asset_class,
        "isin": isin,
        "ticker": ticker,
        "primary_start": primary_start.date().isoformat(),
        "primary_end": as_of.date().isoformat(),
        "expected_primary_months": len(expected_primary_months),
        "expected_stress_months": len(expected_stress_months),
        "launch_date": None,
        "launch_date_source": None,
    }
    if not ticker:
        return {**base, "primary_status": "NO_TICKER", "stress_status": "NO_TICKER"}
    if close is None or close.empty:
        return {**base, "primary_status": "NO_CACHE_HISTORY", "stress_status": "NO_CACHE_HISTORY"}

    close = close.loc[close.index <= as_of]
    if close.empty:
        return {**base, "primary_status": "NO_HISTORY_AT_OR_BEFORE_ASOF", "stress_status": "NO_HISTORY_AT_OR_BEFORE_ASOF"}

    first = close.index.min()
    last = close.index.max()
    primary = close.loc[(close.index >= primary_start) & (close.index <= as_of)]
    stress = close.loc[(close.index >= STRESS_START) & (close.index < STRESS_END_EXCLUSIVE)]

    primary_months = sorted({_month_key(ts) for ts in primary.index})
    stress_months = sorted({_month_key(ts) for ts in stress.index})
    missing_primary = sorted(set(expected_primary_months) - set(primary_months))
    missing_stress = sorted(set(expected_stress_months) - set(stress_months))

    anchor_ok = bool(not primary.empty and primary.index.min() <= primary_start + pd.Timedelta(days=PRIMARY_ANCHOR_TOLERANCE_DAYS))
    fresh_ok = bool(last >= as_of - pd.Timedelta(days=FRESHNESS_TOLERANCE_DAYS))

    if primary.empty:
        primary_status = "NO_PRIMARY_HISTORY"
    elif not anchor_ok:
        primary_status = "START_AFTER_ANCHOR_UNRESOLVED"
    elif not fresh_ok:
        primary_status = "STALE_END"
    elif missing_primary:
        primary_status = "PRIMARY_MISSING_CALENDAR_MONTHS"
    else:
        primary_status = "PRIMARY_FULL_FROM_ANCHOR"

    if not stress.empty and not missing_stress:
        stress_status = "STRESS_FULL_2020_2022"
    elif stress.empty:
        stress_status = "NO_STRESS_HISTORY"
    else:
        stress_status = "STRESS_PARTIAL"

    return {
        **base,
        "first_observed_date": first.date().isoformat(),
        "last_observed_date": last.date().isoformat(),
        "observed_sessions_total": int(len(close)),
        "observed_primary_sessions": int(len(primary)),
        "observed_primary_months": int(len(primary_months)),
        "primary_month_coverage_pct": round(100.0 * len(primary_months) / len(expected_primary_months), 4),
        "missing_primary_months": ",".join(missing_primary),
        "observed_stress_sessions": int(len(stress)),
        "observed_stress_months": int(len(stress_months)),
        "stress_month_coverage_pct": round(100.0 * len(stress_months) / len(expected_stress_months), 4),
        "missing_stress_months": ",".join(missing_stress),
        "primary_status": primary_status,
        "stress_status": stress_status,
        "short_history_reason": (
            "NEEDS_TRUSTED_LISTING_OR_INCEPTION_DATE"
            if primary_status == "START_AFTER_ANCHOR_UNRESOLVED"
            else None
        ),
    }


def run(root: Path = ROOT, as_of: Any | None = None) -> dict[str, Any]:
    resolved_as_of = _utc(as_of or datetime.now(timezone.utc).date().isoformat())
    policy = load_policy(root / "config" / "CALIBRATION_WINDOWS_V21_12.json")
    primary_window = resolve_primary_window(resolved_as_of, policy)
    if primary_window.start != pd.Timestamp("2023-01-01", tz="UTC") and resolved_as_of < pd.Timestamp("2028-01-01", tz="UTC"):
        raise ValueError("PRIMARY_WINDOW_DRIFT")

    config = json.loads((root / "config" / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    history_period = str(config.get("yfinance", {}).get("history_period", ""))
    required_history_start = str(config.get("yfinance", {}).get("required_history_start", ""))

    universes = _load_universes(root)
    rows: list[dict[str, Any]] = []
    cache_failures: list[dict[str, str]] = []
    cache_ticker_counts: dict[str, int] = {}
    for asset_class, master in universes.items():
        cache_dir = root / "data" / "cache" / ("actions" if asset_class == "ACTION" else "etf")
        series_by_ticker, failures = load_cache_series(cache_dir)
        cache_ticker_counts[asset_class] = len(series_by_ticker)
        cache_failures.extend({"asset_class": asset_class, **failure} for failure in failures)
        if master.empty:
            continue
        for _, instrument in master.iterrows():
            isin = str(instrument.get("isin") or "").strip()
            raw_ticker = instrument.get("yahoo_ticker")
            ticker = "" if pd.isna(raw_ticker) else str(raw_ticker).strip()
            rows.append(
                _instrument_row(
                    asset_class,
                    isin,
                    ticker,
                    series_by_ticker.get(ticker),
                    primary_start=primary_window.start,
                    as_of=resolved_as_of,
                )
            )

    audit = pd.DataFrame(rows)
    outdir = root / "outputs" / "audit"
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / "OHLCV_PRIMARY_HISTORY_DEPTH.csv"
    audit.to_csv(csv_path, sep=";", index=False, encoding="utf-8-sig")

    primary_counts = Counter(audit.get("primary_status", pd.Series(dtype=str)).dropna().astype(str))
    stress_counts = Counter(audit.get("stress_status", pd.Series(dtype=str)).dropna().astype(str))
    unresolved = audit[audit.get("primary_status", pd.Series(index=audit.index, dtype=str)).eq("START_AFTER_ANCHOR_UNRESOLVED")]
    summary = {
        "version": "V21.13_OHLCV_HISTORY_DEPTH_AUDIT",
        "status": "SUCCESS" if not audit.empty else "NO_AUDIT_ROWS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": resolved_as_of.date().isoformat(),
        "primary_window_start": primary_window.start.date().isoformat(),
        "primary_window_end": primary_window.end.date().isoformat(),
        "history_period_configured": history_period,
        "required_history_start": required_history_start,
        "rows": int(len(audit)),
        "rows_by_asset_class": {str(k): int(v) for k, v in audit.get("asset_class", pd.Series(dtype=str)).value_counts().to_dict().items()},
        "cache_tickers_by_asset_class": cache_ticker_counts,
        "primary_status_counts": {key: int(value) for key, value in sorted(primary_counts.items())},
        "stress_status_counts": {key: int(value) for key, value in sorted(stress_counts.items())},
        "short_history_unresolved_count": int(len(unresolved)),
        "short_history_policy": "DO_NOT_ASSUME_POST_2023_LAUNCH_WITHOUT_TRUSTED_LISTING_OR_INCEPTION_DATE",
        "cache_read_failures": cache_failures,
        "stress_calibration_weight": 0.0,
        "weight_or_threshold_changes": False,
        "output": str(csv_path.relative_to(root)),
    }
    json_path = outdir / "OHLCV_PRIMARY_HISTORY_DEPTH_SUMMARY.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit actual OHLCV depth for V21.12/V21.13 calibration governance")
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    print(json.dumps(run(as_of=args.as_of), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
