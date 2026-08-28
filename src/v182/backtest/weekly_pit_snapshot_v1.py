"""Append a Friday PIT snapshot from committee outputs. No invented prices."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SOURCES = (
    Path("outputs/committee_master/CI_RESULTS_CHALLENGER_V2.csv"),
    Path("outputs/committee_master/CI_SELECTION_ALL_V4.csv"),
    Path("outputs/committee_master/OR_RANKING_HEBDO_SHADOW_LATEST.csv"),
)
OUT_ACTION = Path("outputs/backtest/ACTION_MT_WEEKLY_SNAPSHOTS.csv")
OUT_ETF = Path("outputs/backtest/ETF_MT_WEEKLY_SNAPSHOTS.csv")
AUDIT = Path("outputs/audit/WEEKLY_PIT_SNAPSHOT.json")
SIGNAL_CANDIDATES = (
    "OR_COMPOSITE_SHADOW",
    "CI_WEIGHTED_SCORE",
    "score",
    "ETF_MT_SCORE",
    "ACTION_MT_SCORE",
)


def _read(root: Path, relative: Path) -> pd.DataFrame:
    path = root / relative
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", low_memory=False)


def _signal_col(frame: pd.DataFrame) -> str | None:
    for name in SIGNAL_CANDIDATES:
        if name in frame.columns:
            return name
    return None


def _append(path: Path, frame: pd.DataFrame) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        previous = pd.read_csv(path, sep=";", encoding="utf-8-sig")
        if {"isin", "as_of"} <= set(previous.columns) and {"isin", "as_of"} <= set(frame.columns):
            previous = previous[~previous.set_index(["isin", "as_of"]).index.isin(frame.set_index(["isin", "as_of"]).index)]
        out = pd.concat([previous, frame], ignore_index=True)
    else:
        out = frame
    out.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return int(len(frame))


def run(root: Path = ROOT) -> dict:
    as_of = datetime.now(timezone.utc).date().isoformat()
    frames = [_read(root, path) for path in SOURCES]
    frames = [f for f in frames if not f.empty]
    if not frames:
        payload = {"status": "SKIPPED_NO_COMMITTEE_OUTPUT", "as_of": as_of, "rows": 0}
        (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)
        (root / AUDIT).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload
    frame = frames[0]
    signal = _signal_col(frame)
    if signal is None or "isin" not in frame.columns:
        payload = {"status": "SKIPPED_MISSING_COLUMNS", "as_of": as_of, "rows": 0}
        (root / AUDIT).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload
    asset_col = "asset_class" if "asset_class" in frame.columns else None
    rows = []
    for _, row in frame.iterrows():
        asset = str(row.get(asset_col, "")).upper() if asset_col else ""
        if asset not in {"ACTION", "ETF"}:
            asset = "ETF" if "ETF" in asset else "ACTION"
        rec = {
            "isin": row.get("isin"),
            "name": row.get("name", ""),
            "asset_class": asset,
            "horizon": "MT",
            "as_of": as_of,
            "OR_COMPOSITE_SHADOW": row.get("OR_COMPOSITE_SHADOW", ""),
            "promotion_eligible": False,
            "forward_return_pct_60d": "",
            "signal_source": signal,
        }
        if asset == "ETF":
            rec["ETF_MT_SCORE"] = row.get(signal)
        else:
            rec["ACTION_MT_SCORE"] = row.get(signal)
        rows.append(rec)
    built = pd.DataFrame(rows).dropna(subset=["isin"])
    action_n = _append(root / OUT_ACTION, built[built["asset_class"].eq("ACTION")]) if not built.empty else 0
    etf_n = _append(root / OUT_ETF, built[built["asset_class"].eq("ETF")]) if not built.empty else 0
    payload = {
        "status": "SNAPSHOT_APPENDED",
        "as_of": as_of,
        "signal_column": signal,
        "action_rows": action_n,
        "etf_rows": etf_n,
        "forward_pending": True,
        "decision_influence": 0.0,
        "real_orders_enabled": False,
    }
    (root / AUDIT).parent.mkdir(parents=True, exist_ok=True)
    (root / AUDIT).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload
