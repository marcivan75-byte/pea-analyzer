from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .data import attach_forward_returns, first_column, infer_snapshot_date, normalise_id

ACTION_PATTERNS = (
    "*V21.0_ACTIONS_PEA_1429_COMMITTEE*.csv",
    "*ACTIONS_3609_DECISIONS*.csv",
    "*ACTIONS*3609*DECISIONS*.csv",
)
ETF_PATTERNS = ("*V20.7_ETF102_COMMITTEE*.csv", "*ETF102*COMMITTEE*.csv")

ACTION_IDS = ("isin", "canonical_isin", "ISIN", "canonical_name", "Nom société", "name")
ETF_IDS = ("isin", "ISIN", "ticker_yahoo_final", "ticker_primary", "name")
ACTION_PRICES = ("last_close", "canonical_last_close", "Cours €", "price", "close")
ETF_PRICES = ("last_close", "price", "close", "nav", "daily_last_close")


def _discover(root: Path, patterns: Iterable[str]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.rglob(pattern):
            found[str(path.resolve())] = path
    return sorted(found.values())


def _restore_v21_component_scores(raw: pd.DataFrame, out: pd.DataFrame) -> int:
    """Recover the point-in-time 0..100 component score from archived V21 contribution/effective-weight evidence.

    This deliberately uses values written by the historical committee run rather than recalculating an old
    snapshot with today's scoring code. Rounding error is small and preferable to model-version look-ahead.
    """
    restored = 0
    for contrib_col in raw.columns:
        if not contrib_col.startswith(("contrib_ct_", "contrib_mt_", "contrib_lt_")):
            continue
        suffix = contrib_col[len("contrib_"):]
        effective_col = f"effective_weight_{suffix}"
        if effective_col not in raw.columns:
            continue
        contrib = pd.to_numeric(raw[contrib_col], errors="coerce")
        effective = pd.to_numeric(raw[effective_col], errors="coerce")
        score = contrib.div(effective.replace(0.0, np.nan)).where(effective.gt(0.0)).clip(0.0, 100.0)
        out[f"component_score_{suffix}"] = score
        restored += 1
    return restored


def _snapshot(path: Path, asset_class: str) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    except Exception:
        return None
    ids = ACTION_IDS if asset_class == "ACTION" else ETF_IDS
    prices = ACTION_PRICES if asset_class == "ACTION" else ETF_PRICES
    id_col = first_column(df, ids)
    price_col = first_column(df, prices)
    date = infer_snapshot_date(path)
    if id_col is None or price_col is None or date is None:
        return None
    out = df.copy()
    for col in list(out.columns):
        out[col] = out[col].astype("string")
    if asset_class == "ACTION":
        _restore_v21_component_scores(df, out)
    out["__instrument_id"] = normalise_id(df[id_col])
    out["__snapshot_date"] = pd.Timestamp(date).normalize()
    out["__price"] = pd.to_numeric(df[price_col], errors="coerce")
    out["__asset_class"] = asset_class
    out["__source_file"] = str(path)
    out = out.dropna(subset=["__instrument_id", "__snapshot_date", "__price"])
    out = out[out["__price"] > 0]
    if out.empty:
        return None
    return out


def _load_previous(previous_root: Path | None, asset_class: str) -> pd.DataFrame:
    if previous_root is None or not previous_root.exists():
        return pd.DataFrame()
    name = f"{asset_class}_HISTORY.parquet"
    candidates = sorted(previous_root.rglob(name))
    if not candidates:
        return pd.DataFrame()
    try:
        return pd.read_parquet(candidates[-1])
    except Exception:
        return pd.DataFrame()


def _merge_history(previous: pd.DataFrame, current: list[pd.DataFrame]) -> pd.DataFrame:
    frames = ([previous] if not previous.empty else []) + current
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True, sort=False)
    df["__snapshot_date"] = pd.to_datetime(df["__snapshot_date"], errors="coerce").dt.normalize()
    df["__instrument_id"] = normalise_id(df["__instrument_id"])
    df["__price"] = pd.to_numeric(df["__price"], errors="coerce")
    df = df.dropna(subset=["__instrument_id", "__snapshot_date", "__price"])
    df = df[df["__price"] > 0]
    source = df.get("__source_file", pd.Series("", index=df.index)).astype(str)
    source_priority = source.str.contains("V21.0_ACTIONS_PEA_1429_COMMITTEE", regex=False).astype(int)
    df = df.assign(__source_priority=source_priority, __source_sort=source).sort_values(
        ["__snapshot_date", "__instrument_id", "__source_priority", "__source_sort"]
    )
    df = df.drop_duplicates(["__snapshot_date", "__instrument_id"], keep="last").drop(
        columns=["__source_priority", "__source_sort"]
    )
    return df.reset_index(drop=True)


def _add_outcomes(df: pd.DataFrame, horizon_specs: list[tuple[int, int]]) -> pd.DataFrame:
    out = df.copy()
    for horizon, tolerance in sorted(set(horizon_specs)):
        enriched = attach_forward_returns(out, horizon, tolerance)
        out[f"__forward_return_{horizon}d"] = pd.to_numeric(enriched["__forward_return"], errors="coerce")
        out[f"__realized_horizon_{horizon}d"] = pd.to_numeric(enriched["__realized_horizon_days"], errors="coerce")
    return out


def build_rolling_history(raw_root: Path, previous_root: Path | None, output_root: Path, config_path: Path) -> dict[str, object]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    elements = cfg["elements"]
    output_root.mkdir(parents=True, exist_ok=True)
    audit: dict[str, object] = {
        "version": "POINT_IN_TIME_HISTORY_V1",
        "lookahead_policy": "features preserved from archived committee snapshots; outcomes use later archived prices only",
        "component_reconstruction_policy": "V21 metric scores recovered from archived contribution/effective-weight evidence, not recalculated with future code",
        "production_weights_modified": False,
        "asset_classes": {},
    }
    for asset_class, patterns in (("ETF", ETF_PATTERNS), ("ACTION", ACTION_PATTERNS)):
        current_frames = [x for p in _discover(raw_root, patterns) if (x := _snapshot(p, asset_class)) is not None]
        previous = _load_previous(previous_root, asset_class)
        history = _merge_history(previous, current_frames)
        specs = [
            (int(e["horizon_days"]), int(e["horizon_tolerance_days"]))
            for e in elements if e["asset_class"] == asset_class
        ]
        if not history.empty:
            history = _add_outcomes(history, specs)
            history.to_parquet(output_root / f"{asset_class}_HISTORY.parquet", index=False, compression="zstd")
        dates = sorted(pd.to_datetime(history.get("__snapshot_date", pd.Series(dtype="datetime64[ns]")).dropna().unique()))
        horizon_audit = {}
        for horizon, _ in sorted(set(specs)):
            col = f"__forward_return_{horizon}d"
            labeled = history[history[col].notna()] if (not history.empty and col in history.columns) else pd.DataFrame()
            labeled_dates = int(labeled["__snapshot_date"].nunique()) if not labeled.empty else 0
            horizon_audit[str(horizon)] = {
                "labeled_rows": int(len(labeled)),
                "labeled_snapshots": labeled_dates,
                "label_coverage_pct": round(100.0 * len(labeled) / max(1, len(history)), 2),
            }
        component_cols = [c for c in history.columns if c.startswith("component_score_")] if not history.empty else []
        audit["asset_classes"][asset_class] = {
            "rows": int(len(history)),
            "columns": int(len(history.columns)) if not history.empty else 0,
            "snapshots": len(dates),
            "first_snapshot": str(dates[0].date()) if dates else None,
            "last_snapshot": str(dates[-1].date()) if dates else None,
            "new_snapshot_files": len(current_frames),
            "previous_rows_restored": int(len(previous)),
            "restored_component_score_columns": len(component_cols),
            "outcomes": horizon_audit,
        }
    (output_root / "HISTORY_AUDIT.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Build rolling point-in-time history for ten-element backtests")
    parser.add_argument("--raw", required=True)
    parser.add_argument("--previous", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    audit = build_rolling_history(
        Path(args.raw), Path(args.previous) if args.previous else None, Path(args.output), Path(args.config)
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
