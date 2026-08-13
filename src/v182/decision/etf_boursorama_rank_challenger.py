from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import pandas as pd


RANK_FIELDS = {
    "1m": "boursorama_morningstar_rank_1m",
    "3m": "boursorama_morningstar_rank_3m",
    "6m": "boursorama_morningstar_rank_6m",
    "1y": "boursorama_morningstar_rank_1y",
    "3y": "boursorama_morningstar_rank_3y",
    "5y": "boursorama_morningstar_rank_5y",
    "10y": "boursorama_morningstar_rank_10y",
}


def _num(value) -> float | None:
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _valid_percentile(value) -> float | None:
    v = _num(value)
    return v if v is not None and 1.0 <= v <= 100.0 else None


def current_rank_bonus(rank_1y: float | None) -> float:
    """Shadow bonus/malus from Morningstar 1y percentile (1=best, 100=worst)."""
    r = _valid_percentile(rank_1y)
    if r is None:
        return 0.0
    if r <= 10:
        return 2.0
    if r <= 25:
        return 1.0
    if r <= 50:
        return 0.0
    if r <= 75:
        return -0.5
    return -1.5


def trajectory_bonus(rank_1y: float | None, rank_3y: float | None, rank_5y: float | None) -> tuple[float, str]:
    """Multi-horizon persistence/trajectory proxy, not a historical time-series claim.

    Boursorama/Morningstar ranks are current percentile ranks for each performance
    horizon. Comparing 1y/3y/5y therefore measures current relative persistence
    and recent-vs-longer-horizon direction. True rank evolution through calendar
    time is tracked separately in the persistent history file.
    """
    r1, r3, r5 = (_valid_percentile(rank_1y), _valid_percentile(rank_3y), _valid_percentile(rank_5y))
    vals = [v for v in (r1, r3, r5) if v is not None]
    if len(vals) < 2:
        return 0.0, "INSUFFICIENT_MULTI_HORIZON_RANKS"

    if len(vals) == 3 and max(vals) <= 10:
        return 1.5, "PERSISTENT_TOP10"
    if max(vals) <= 25:
        return 1.0, "PERSISTENT_TOP25"

    # Lower percentile is better. Recent 1y ranking at least 15 percentile
    # points better than both longer horizons is treated as improving.
    if r1 is not None and r3 is not None and r5 is not None:
        if r1 + 15 <= r3 and r1 + 15 <= r5:
            return 1.0, "RECENT_RANK_IMPROVEMENT"
        if r1 >= 75 and r3 <= 35 and r5 <= 35:
            return -1.5, "RECENT_STRONG_DETERIORATION"
        if r1 >= r3 + 15 and r1 >= r5 + 15:
            return -1.0, "RECENT_RANK_DETERIORATION"

    avg = sum(vals) / len(vals)
    if avg <= 25:
        return 0.5, "GOOD_MULTI_HORIZON_AVERAGE"
    if avg >= 75:
        return -1.0, "POOR_MULTI_HORIZON_AVERAGE"
    return 0.0, "MIXED_MULTI_HORIZON_RANKS"


def _persist_rank_history(root: Path, etf_master: pd.DataFrame) -> dict:
    path = root / "state" / "boursorama" / "ETF_CATEGORY_RANK_HISTORY.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    today = datetime.now(timezone.utc).date().isoformat()
    for _, row in etf_master.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin:
            continue
        ranks = {h: _valid_percentile(row.get(field)) for h, field in RANK_FIELDS.items()}
        if not any(v is not None for v in ranks.values()):
            continue
        as_of = str(row.get("boursorama_morningstar_data_date") or today).strip() or today
        rows.append({
            "as_of": as_of,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "isin": isin,
            "name": str(row.get("name") or ""),
            "morningstar_category": str(row.get("morningstar_category") or row.get("boursorama_morningstar_category") or ""),
            **{f"rank_{h}_percentile": ranks[h] for h in RANK_FIELDS},
        })
    current = pd.DataFrame(rows)
    if current.empty:
        return {"path": str(path.relative_to(root)), "rows_added": 0, "total_rows": int(len(pd.read_csv(path, sep=";"))) if path.exists() else 0}
    if path.exists():
        old = pd.read_csv(path, sep=";", dtype=str).fillna("")
        combined = pd.concat([old, current], ignore_index=True, sort=False)
    else:
        combined = current.copy()
    combined = combined.drop_duplicates(subset=["as_of", "isin"], keep="last").sort_values(["isin", "as_of"])
    combined.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return {"path": str(path.relative_to(root)), "rows_added": int(len(current)), "total_rows": int(len(combined))}


def apply_etf_boursorama_rank_challenger(
    decisions: pd.DataFrame,
    etf_master: pd.DataFrame,
    root: Path,
    *,
    cap_points: float = 3.0,
) -> tuple[pd.DataFrame, dict]:
    """Add Boursorama/Morningstar category-rank challenger columns only.

    This function never changes the official score or decision. The challenger
    is intended for dedicated PIT/OOS validation before any promotion.
    """
    out = decisions.copy()
    for col, default in (
        ("boursorama_rank_1y_percentile", pd.NA),
        ("boursorama_rank_3y_percentile", pd.NA),
        ("boursorama_rank_5y_percentile", pd.NA),
        ("boursorama_rank_current_bonus_shadow", 0.0),
        ("boursorama_rank_trajectory_bonus_shadow", 0.0),
        ("boursorama_rank_overlay_shadow", 0.0),
        ("boursorama_rank_challenger_score", pd.NA),
        ("boursorama_rank_challenger_status", "NOT_APPLICABLE"),
    ):
        if col not in out.columns:
            out[col] = default

    history = _persist_rank_history(root, etf_master)
    if etf_master.empty or "isin" not in etf_master.columns:
        return out, {"status": "NO_ETF_MASTER", "history": history}

    master = etf_master.drop_duplicates("isin").set_index("isin", drop=False)
    applied = 0
    positive = 0
    negative = 0
    for idx, row in out.iterrows():
        if str(row.get("asset_class", "")).upper() != "ETF":
            continue
        isin = str(row.get("isin") or "")
        if not isin or isin not in master.index:
            continue
        mrow = master.loc[isin]
        if isinstance(mrow, pd.DataFrame):
            mrow = mrow.iloc[0]
        r1 = _valid_percentile(mrow.get(RANK_FIELDS["1y"]))
        r3 = _valid_percentile(mrow.get(RANK_FIELDS["3y"]))
        r5 = _valid_percentile(mrow.get(RANK_FIELDS["5y"]))
        if r1 is None and r3 is None and r5 is None:
            continue
        current = current_rank_bonus(r1)
        trajectory, trajectory_status = trajectory_bonus(r1, r3, r5)
        overlay = max(-cap_points, min(cap_points, current + trajectory))
        official_score = _num(row.get("score"))
        challenger_score = None if official_score is None else max(0.0, min(100.0, official_score + overlay))

        out.at[idx, "boursorama_rank_1y_percentile"] = r1
        out.at[idx, "boursorama_rank_3y_percentile"] = r3
        out.at[idx, "boursorama_rank_5y_percentile"] = r5
        out.at[idx, "boursorama_rank_current_bonus_shadow"] = current
        out.at[idx, "boursorama_rank_trajectory_bonus_shadow"] = trajectory
        out.at[idx, "boursorama_rank_overlay_shadow"] = overlay
        out.at[idx, "boursorama_rank_challenger_score"] = challenger_score
        out.at[idx, "boursorama_rank_challenger_status"] = f"SHADOW:{trajectory_status}"
        applied += 1
        positive += int(overlay > 0)
        negative += int(overlay < 0)

    return out, {
        "status": "SHADOW_CHALLENGER",
        "rows_applied": applied,
        "positive_overlay_rows": positive,
        "negative_overlay_rows": negative,
        "overlay_cap_points": cap_points,
        "official_score_changed": False,
        "official_decision_changed": False,
        "rank_semantics": "Morningstar percentile: 1 best, 100 worst",
        "trajectory_semantics": "Current 1y/3y/5y persistence proxy; true calendar-time evolution accumulates in history",
        "history": history,
        "performance_attribution": "NONE_UNTIL_DEDICATED_PIT_OOS_BACKTEST",
    }
