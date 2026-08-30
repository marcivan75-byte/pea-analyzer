"""PIT benchmark-universe contract for CDC V15.

This module qualifies the historical PEA universe only. It does not run a strategy
backtest. Each signal Friday must be associated with the PEA-eligible universe known
at that date, including delisted names when historically eligible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import pandas as pd


class BenchmarkPITError(ValueError):
    pass


REQUIRED_COLUMNS = {
    "date_signal", "isin", "pea_eligible_of_record", "knowledge_date",
    "source", "delisted", "last_trading_date"
}


def normalize_benchmark_universe(df: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise BenchmarkPITError("missing benchmark PIT columns: " + ", ".join(missing))
    z = df.copy()
    for c in ["date_signal", "knowledge_date", "last_trading_date"]:
        z[c] = pd.to_datetime(z[c], errors="coerce").dt.normalize()
    if z[["date_signal", "knowledge_date"]].isna().any().any():
        raise BenchmarkPITError("invalid benchmark PIT dates")
    z["isin"] = z["isin"].astype(str)
    if (~z["isin"].str.len().eq(12)).any():
        raise BenchmarkPITError("benchmark PIT requires 12-character ISIN pivot")
    if (z["knowledge_date"] > z["date_signal"]).any():
        raise BenchmarkPITError("look-ahead in benchmark universe")
    if z.duplicated(["date_signal", "isin"]).any():
        raise BenchmarkPITError("duplicate benchmark member for date_signal/isin")
    # A delisted security may remain in historical snapshots before its last trading date.
    bad_delisted = z["delisted"].astype(bool) & z["last_trading_date"].notna() & (z["date_signal"] > z["last_trading_date"])
    if bad_delisted.any():
        raise BenchmarkPITError("delisted security present after last_trading_date")
    return z


def snapshot(df: pd.DataFrame, date_signal) -> pd.DataFrame:
    z = normalize_benchmark_universe(df)
    d = pd.Timestamp(date_signal).normalize()
    q = z[(z["date_signal"] == d) & z["pea_eligible_of_record"].astype(bool)].copy()
    return q.sort_values("isin").reset_index(drop=True)


def qualify_benchmark(df: pd.DataFrame) -> dict:
    z = normalize_benchmark_universe(df)
    dates = sorted(z["date_signal"].dropna().unique())
    counts = {}
    for d in dates:
        q = z[(z["date_signal"] == d) & z["pea_eligible_of_record"].astype(bool)]
        counts[pd.Timestamp(d).date().isoformat()] = int(len(q))
    no_empty = bool(counts) and all(v > 0 for v in counts.values())
    delisted_preserved = bool(z["delisted"].astype(bool).any())
    return {
        "status": "BENCHMARK_PIT_READY" if no_empty else "BENCHMARK_PIT_NOT_READY",
        "snapshot_count": len(counts),
        "universe_count_by_date": counts,
        "all_snapshots_non_empty": no_empty,
        "delisted_rows_present": delisted_preserved,
        "isin_pivot": True,
        "knowledge_date_le_signal": True,
        "performance_backtest_authorized_by_this_module": False,
    }
