"""Timezone-safe launcher for TABPORT longitudinal Audit73.

This adapter preserves the audited Audit73 PIT semantics while handling the
fully-missing-snapshot case with an explicitly UTC datetime dtype. No dates,
filters, thresholds, observations or portfolio rules are changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from v182.hebdo import tabport_longitudinal_audit73 as study
from src.v182.backtest import hebdo_meta_consensus_gate_audit73 as gate


def _ns_utc(values) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True).astype("datetime64[ns, UTC]")


def attach_latest_pit_snapshot_safe(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    left = gate._prepare_ledger(ledger)
    right = gate._prepare_consensus(observations)
    left["decision_at"] = _ns_utc(left["decision_at"])
    right["available_at"] = _ns_utc(right["available_at"])

    pieces = []
    cols = ["available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"]
    for symbol, trades in left.groupby("symbol", sort=False):
        o = right[right["symbol"].eq(symbol)]
        if o.empty:
            z = trades.copy()
            z["pit_available_at"] = pd.Series(pd.NaT, index=z.index, dtype="datetime64[ns, UTC]")
            for c in cols[1:]:
                z[f"pit_{c}"] = np.nan
            pieces.append(z)
            continue

        m = pd.merge_asof(
            trades.sort_values("decision_at"),
            o[cols].sort_values("available_at"),
            left_on="decision_at",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        m = m.rename(columns={
            "available_at": "pit_available_at",
            "target_median": "pit_target_median",
            "consensus": "pit_consensus",
            "n_analysts": "pit_n_analysts",
            "consensus_delta_4w": "pit_consensus_delta_4w",
        })
        m["pit_available_at"] = _ns_utc(m["pit_available_at"])
        pieces.append(m)

    out = pd.concat(pieces, ignore_index=True) if pieces else left.iloc[0:0].copy()
    if "pit_available_at" not in out.columns:
        out["pit_available_at"] = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]")
    else:
        out["pit_available_at"] = _ns_utc(out["pit_available_at"])
    out["decision_at"] = _ns_utc(out["decision_at"])
    out["pit_snapshot_available"] = out["pit_available_at"].notna()
    bad = out["pit_snapshot_available"] & (out["pit_available_at"] > out["decision_at"])
    if bad.any():
        raise ValueError("BLOCK_AUDIT73_LOOKAHEAD_SNAPSHOT")
    out["pit_target_upside_pct"] = np.where(
        out["pit_snapshot_available"] & pd.to_numeric(out["pit_target_median"], errors="coerce").notna(),
        (pd.to_numeric(out["pit_target_median"], errors="coerce") / out["entry_price"] - 1.0) * 100.0,
        np.nan,
    )
    return out.sort_values(["decision_at", "symbol", "_trade_id"]).reset_index(drop=True)


def main() -> None:
    study.attach_latest_pit_snapshot = attach_latest_pit_snapshot_safe
    study.main()


if __name__ == "__main__":
    main()
