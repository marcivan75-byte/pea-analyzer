"""PIT benchmark-universe contract for CDC V15.

This module qualifies the historical PEA universe only. It does not run a strategy
backtest. Each signal Friday must be associated with the PEA-eligible universe known
at that date, including delisted names when historically eligible.
"""
from __future__ import annotations

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
    bad_delisted = (
        z["delisted"].astype(bool)
        & z["last_trading_date"].notna()
        & (z["date_signal"] > z["last_trading_date"])
    )
    if bad_delisted.any():
        raise BenchmarkPITError("delisted security present after last_trading_date")
    return z


def snapshot(df: pd.DataFrame, date_signal) -> pd.DataFrame:
    z = normalize_benchmark_universe(df)
    d = pd.Timestamp(date_signal).normalize()
    q = z[(z["date_signal"] == d) & z["pea_eligible_of_record"].astype(bool)].copy()
    return q.sort_values("isin").reset_index(drop=True)


def survivorship_audit(df: pd.DataFrame) -> dict:
    """Audit historical members that are absent from the latest PIT snapshot.

    Absence from the latest universe is not automatically treated as a delisting:
    it may reflect a merger, acquisition, bankruptcy or loss of PEA eligibility.
    The critical control is that the disappearance is explicitly documented.
    """
    z = normalize_benchmark_universe(df)
    latest_date = z["date_signal"].max()
    eligible = z[z["pea_eligible_of_record"].astype(bool)].copy()

    ever = set(eligible["isin"])
    latest = set(
        eligible.loc[eligible["date_signal"].eq(latest_date), "isin"].astype(str)
    )
    absent_latest = ever - latest

    # Terminal documentation may appear on any historical row for the ISIN.
    per_isin = z.groupby("isin", dropna=False).agg(
        delisted=("delisted", "max"),
        last_trading_date=("last_trading_date", "max"),
    )
    documented = {
        isin
        for isin in absent_latest
        if isin in per_isin.index
        and (
            bool(per_isin.loc[isin, "delisted"])
            or pd.notna(per_isin.loc[isin, "last_trading_date"])
        )
    }
    unresolved = absent_latest - documented

    terminal_coverage_pct = (
        100.0 * len(documented) / len(absent_latest) if absent_latest else 100.0
    )
    return {
        "latest_snapshot_date": pd.Timestamp(latest_date).date().isoformat(),
        "historical_eligible_isin_count": len(ever),
        "latest_eligible_isin_count": len(latest),
        "absent_from_latest_count": len(absent_latest),
        "documented_terminal_or_exit_count": len(documented),
        "unresolved_disappearance_count": len(unresolved),
        "terminal_or_exit_coverage_pct": round(terminal_coverage_pct, 3),
        "absent_from_latest_isins": sorted(absent_latest),
        "unresolved_disappearance_isins": sorted(unresolved),
        "survivorship_control_ready": len(unresolved) == 0,
    }


def qualify_benchmark(df: pd.DataFrame) -> dict:
    z = normalize_benchmark_universe(df)
    dates = sorted(z["date_signal"].dropna().unique())
    counts = {}
    for d in dates:
        q = z[(z["date_signal"] == d) & z["pea_eligible_of_record"].astype(bool)]
        counts[pd.Timestamp(d).date().isoformat()] = int(len(q))
    no_empty = bool(counts) and all(v > 0 for v in counts.values())
    delisted_preserved = bool(z["delisted"].astype(bool).any())
    survivor = survivorship_audit(z)
    ready = bool(no_empty and survivor["survivorship_control_ready"])
    return {
        "status": "BENCHMARK_PIT_READY" if ready else "BENCHMARK_PIT_NOT_READY",
        "snapshot_count": len(counts),
        "universe_count_by_date": counts,
        "all_snapshots_non_empty": no_empty,
        "delisted_rows_present": delisted_preserved,
        "isin_pivot": True,
        "knowledge_date_le_signal": True,
        "survivorship_audit": survivor,
        "performance_backtest_authorized_by_this_module": False,
    }
