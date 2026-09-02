"""Precision-safe launcher for TABPORT longitudinal Audit73.

Pandas 3 preserves source datetime units (ms/us) and represents an all-missing
``pd.NaT`` column as timezone-naive. This adapter normalizes PIT join clocks to
ns UTC and keeps the all-missing PIT timestamp column timezone-aware. It changes
no dates, ordering, values, filters, thresholds or portfolio rules.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from v182.hebdo import tabport_longitudinal_audit73 as study
from src.v182.backtest import hebdo_meta_consensus_gate_audit73 as gate


def _ns_utc(values) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    # 2010-2026 is safely representable at nanosecond resolution.
    return parsed.astype("datetime64[ns, UTC]")


def attach_latest_pit_snapshot_ns(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Audit73 PIT join with explicit ns-UTC handling, including empty matches."""
    left = gate._prepare_ledger(ledger)
    right = gate._prepare_consensus(observations)
    left["decision_at"] = _ns_utc(left["decision_at"])
    right["available_at"] = _ns_utc(right["available_at"])

    pieces: list[pd.DataFrame] = []
    cols = ["available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"]
    for symbol, trades in left.groupby("symbol", sort=False):
        o = right[right["symbol"].eq(symbol)]
        if o.empty:
            z = trades.copy()
            # Critical Pandas-3 fix: all-missing timestamps must remain tz-aware.
            z["pit_available_at"] = pd.Series(pd.NaT, index=z.index, dtype="datetime64[ns, UTC]")
            for c in cols[1:]:
                z[f"pit_{c}"] = np.nan
            pieces.append(z)
            continue
        t = trades.sort_values("decision_at").copy()
        t["decision_at"] = _ns_utc(t["decision_at"])
        oo = o[cols].sort_values("available_at").copy()
        oo["available_at"] = _ns_utc(oo["available_at"])
        m = pd.merge_asof(
            t,
            oo,
            left_on="decision_at",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        pieces.append(m.rename(columns={
            "available_at": "pit_available_at",
            "target_median": "pit_target_median",
            "consensus": "pit_consensus",
            "n_analysts": "pit_n_analysts",
            "consensus_delta_4w": "pit_consensus_delta_4w",
        }))

    out = pd.concat(pieces, ignore_index=True) if pieces else left.iloc[0:0].copy()
    if "pit_available_at" not in out:
        out["pit_available_at"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    else:
        out["pit_available_at"] = _ns_utc(out["pit_available_at"])
    out["decision_at"] = _ns_utc(out["decision_at"])
    out["pit_snapshot_available"] = out["pit_available_at"].notna()

    bad = out["pit_snapshot_available"] & (out["pit_available_at"] > out["decision_at"])
    if bad.any():
        raise ValueError("BLOCK_AUDIT73_LOOKAHEAD_SNAPSHOT")

    out["pit_target_upside_pct"] = np.where(
        out["pit_snapshot_available"] & out["pit_target_median"].notna(),
        (pd.to_numeric(out["pit_target_median"], errors="coerce") / out["entry_price"] - 1.0) * 100.0,
        np.nan,
    )
    return out.sort_values(["decision_at", "symbol", "_trade_id"]).reset_index(drop=True)


def main() -> None:
    study.attach_latest_pit_snapshot = attach_latest_pit_snapshot_ns
    study.main()


if __name__ == "__main__":
    main()
