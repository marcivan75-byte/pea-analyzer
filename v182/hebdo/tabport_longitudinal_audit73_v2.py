"""Precision-safe launcher for TABPORT longitudinal Audit73.

Pandas 3 preserves source datetime units (ms/us). ``merge_asof`` requires exact
matching dtypes, so this adapter normalizes both PIT join clocks to ns UTC before
calling the already audited Audit73 join. It changes no dates, ordering, values,
filters, thresholds or portfolio rules.
"""
from __future__ import annotations

import pandas as pd

from v182.hebdo import tabport_longitudinal_audit73 as study
from src.v182.backtest import hebdo_meta_consensus_gate_audit73 as gate


def _ns_utc(values) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    # 2010-2026 is safely representable at nanosecond resolution.
    return parsed.astype("datetime64[ns, UTC]")


def attach_latest_pit_snapshot_ns(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    left = ledger.copy()
    right = observations.copy()
    if "decision_at" in left.columns:
        left["decision_at"] = _ns_utc(left["decision_at"])
    elif "date" in left.columns:
        left["date"] = _ns_utc(left["date"])
    elif "entry_date" in left.columns:
        left["entry_date"] = _ns_utc(left["entry_date"])
    if "available_at" in right.columns:
        right["available_at"] = _ns_utc(right["available_at"])
    return gate.attach_latest_pit_snapshot(left, right)


def main() -> None:
    study.attach_latest_pit_snapshot = attach_latest_pit_snapshot_ns
    study.main()


if __name__ == "__main__":
    main()
