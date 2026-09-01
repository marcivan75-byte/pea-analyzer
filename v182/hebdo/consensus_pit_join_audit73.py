"""Strict, self-contained Audit73 PIT join for root HEBDO/TABPORT modules."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _dt(values) -> pd.Series:
    return pd.to_datetime(values, errors="coerce", utc=True)


def attach_latest_pit_snapshot(candidates: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Attach the latest snapshot actually available at or before decision_at.

    Required candidate columns: _signal_row, symbol, decision_at, entry_price.
    Required observation columns: symbol, available_at, target_median, consensus,
    n_analysts. consensus_delta_4w is optional. Future observations are never
    allowed and no unavailable value is imputed.
    """
    left = candidates.copy()
    required_left = {"_signal_row", "symbol", "decision_at", "entry_price"}
    missing_left = required_left - set(left.columns)
    if missing_left:
        raise ValueError(f"BLOCK_LONGITUDINAL_PIT_LEFT_MISSING:{sorted(missing_left)}")
    left["symbol"] = left["symbol"].astype(str).str.strip().str.upper()
    left["decision_at"] = _dt(left["decision_at"])
    left["entry_price"] = pd.to_numeric(left["entry_price"], errors="coerce")
    if left["decision_at"].isna().any() or left["entry_price"].isna().any() or (left["entry_price"] <= 0).any():
        raise ValueError("BLOCK_LONGITUDINAL_PIT_INVALID_CANDIDATE")

    right = observations.copy()
    required_right = {"symbol", "available_at", "target_median", "consensus", "n_analysts"}
    missing_right = required_right - set(right.columns)
    if missing_right:
        raise ValueError(f"BLOCK_LONGITUDINAL_PIT_RIGHT_MISSING:{sorted(missing_right)}")
    right["symbol"] = right["symbol"].astype(str).str.strip().str.upper()
    right["available_at"] = _dt(right["available_at"])
    if right["available_at"].isna().any():
        raise ValueError("BLOCK_LONGITUDINAL_PIT_INVALID_AVAILABLE_AT")
    if "period_kind" in right.columns:
        right = right[right["period_kind"].fillna("CURRENT").astype(str).str.upper().eq("CURRENT")].copy()
    for col in ("target_median", "n_analysts"):
        right[col] = pd.to_numeric(right[col], errors="coerce")
    if "consensus_delta_4w" not in right.columns:
        right["consensus_delta_4w"] = np.nan
    right["consensus_delta_4w"] = pd.to_numeric(right["consensus_delta_4w"], errors="coerce")
    right["consensus"] = right["consensus"].astype(str).str.upper().str.strip().replace({"NAN": np.nan, "NONE": np.nan})
    right = right.sort_values(["symbol", "available_at"]).drop_duplicates(["symbol", "available_at"], keep="last")

    pieces = []
    cols = ["available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"]
    for symbol, trades in left.groupby("symbol", sort=False):
        obs = right[right["symbol"].eq(symbol)]
        if obs.empty:
            z = trades.copy()
            z["pit_available_at"] = pd.NaT
            z["pit_target_median"] = np.nan
            z["pit_consensus"] = np.nan
            z["pit_n_analysts"] = np.nan
            z["pit_consensus_delta_4w"] = np.nan
            pieces.append(z)
            continue
        joined = pd.merge_asof(
            trades.sort_values("decision_at"),
            obs[cols].sort_values("available_at"),
            left_on="decision_at", right_on="available_at",
            direction="backward", allow_exact_matches=True,
        ).rename(columns={
            "available_at":"pit_available_at",
            "target_median":"pit_target_median",
            "consensus":"pit_consensus",
            "n_analysts":"pit_n_analysts",
            "consensus_delta_4w":"pit_consensus_delta_4w",
        })
        pieces.append(joined)
    out = pd.concat(pieces, ignore_index=True) if pieces else left.iloc[0:0].copy()
    out["pit_snapshot_available"] = out["pit_available_at"].notna()
    if (out["pit_snapshot_available"] & (out["pit_available_at"] > out["decision_at"])).any():
        raise ValueError("BLOCK_LONGITUDINAL_PIT_LOOKAHEAD")
    out["pit_target_upside_pct"] = np.where(
        out["pit_snapshot_available"] & out["pit_target_median"].notna(),
        (out["pit_target_median"] / out["entry_price"] - 1.0) * 100.0,
        np.nan,
    )
    return out.sort_values(["_signal_row"]).reset_index(drop=True)
