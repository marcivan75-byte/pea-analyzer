from __future__ import annotations

"""Audit Yahoo PRE2023 raw OHLC for corporate-action discontinuities.

Diagnostic only: this script never transforms prices and is never used for model
selection. It measures changes in Yahoo's Adj Close / Close factor and identifies
large raw close jumps that coincide with factor changes, which indicates that raw
OHLC cannot be consumed naively across the event boundary.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MAX_ALLOWED_DATE = pd.Timestamp("2022-12-31")


def run(corpus_path: Path, output_dir: Path) -> dict:
    df = pd.read_parquet(corpus_path, columns=["date", "ticker", "close", "adj_close"])
    if df.empty:
        raise SystemExit("BLOCK_PRE2023_CA_EMPTY_CORPUS")
    df["date"] = pd.to_datetime(df["date"], errors="raise")
    if df["date"].max() > MAX_ALLOWED_DATE:
        raise SystemExit("BLOCK_PRE2023_CA_HOLDOUT_LEAK")
    if df.duplicated(["ticker", "date"]).any():
        raise SystemExit("BLOCK_PRE2023_CA_DUPLICATES")

    close = pd.to_numeric(df["close"], errors="coerce")
    adj = pd.to_numeric(df["adj_close"], errors="coerce")
    if (~np.isfinite(close)).any() or (~np.isfinite(adj)).any() or (close <= 0).any() or (adj <= 0).any():
        raise SystemExit("BLOCK_PRE2023_CA_INVALID_PRICE")

    work = df.sort_values(["ticker", "date"]).copy()
    work["factor"] = work["adj_close"] / work["close"]
    grp = work.groupby("ticker", sort=False)
    work["prev_close"] = grp["close"].shift(1)
    work["prev_factor"] = grp["factor"].shift(1)
    work["raw_return"] = work["close"] / work["prev_close"] - 1.0
    work["factor_change"] = work["factor"] / work["prev_factor"] - 1.0

    factor_event = work["factor_change"].abs() > 0.01
    major_factor_event = work["factor_change"].abs() > 0.05
    large_raw_jump = work["raw_return"].abs() > 0.35
    coincident = major_factor_event & large_raw_jump

    events = work.loc[factor_event, ["date", "ticker", "close", "adj_close", "factor", "raw_return", "factor_change"]].copy()
    events["major_factor_event"] = major_factor_event.loc[events.index].values
    events["large_raw_jump"] = large_raw_jump.loc[events.index].values
    events["coincident_raw_discontinuity"] = coincident.loc[events.index].values

    by_ticker = (
        work.assign(
            factor_event=factor_event,
            major_factor_event=major_factor_event,
            large_raw_jump=large_raw_jump,
            coincident_raw_discontinuity=coincident,
        )
        .groupby("ticker", as_index=False)
        .agg(
            rows=("date", "size"),
            factor_events=("factor_event", "sum"),
            major_factor_events=("major_factor_event", "sum"),
            large_raw_jumps=("large_raw_jump", "sum"),
            coincident_raw_discontinuities=("coincident_raw_discontinuity", "sum"),
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(output_dir / "PRE2023_YAHOO_ADJUSTMENT_FACTOR_EVENTS.csv", index=False)
    by_ticker.to_csv(output_dir / "PRE2023_YAHOO_CORPORATE_ACTION_BY_TICKER.csv", index=False)

    summary = {
        "purpose": "PRICE_SERIES_INTEGRITY_DIAGNOSTIC_ONLY",
        "source": "YAHOO_YFINANCE_RAW_WITH_ADJ_CLOSE_REFERENCE",
        "historical_universe_certified": False,
        "survivorship_safe": False,
        "historical_pea_eligibility_certified": False,
        "retuning": False,
        "holdout_accessed_for_prices": False,
        "price_transformation_promoted": False,
        "rows": int(len(work)),
        "tickers": int(work["ticker"].nunique()),
        "min_date": str(work["date"].min().date()),
        "max_date": str(work["date"].max().date()),
        "factor_event_rows_gt_1pct": int(factor_event.sum()),
        "major_factor_event_rows_gt_5pct": int(major_factor_event.sum()),
        "large_raw_jump_rows_gt_35pct": int(large_raw_jump.sum()),
        "coincident_raw_discontinuity_rows": int(coincident.sum()),
        "tickers_with_factor_events": int((by_ticker["factor_events"] > 0).sum()),
        "tickers_with_coincident_raw_discontinuities": int((by_ticker["coincident_raw_discontinuities"] > 0).sum()),
        "raw_ohlc_safe_to_consume_naively_across_all_dates": bool(int(coincident.sum()) == 0),
    }
    (output_dir / "PRE2023_YAHOO_CORPORATE_ACTION_SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--corpus", required=True)
    p.add_argument("--output-dir", default="outputs/pre2023_yahoo_corporate_action_audit")
    args = p.parse_args()
    run(Path(args.corpus), Path(args.output_dir))


if __name__ == "__main__":
    main()
