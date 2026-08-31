from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unsupported input format: {suffix}")


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame.to_parquet(path, index=False)
        return
    if suffix == ".csv":
        frame.to_csv(path, index=False)
        return
    raise ValueError(f"unsupported output format: {suffix}")


def filter_valid_atr(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    """Keep only rows with a finite positive PIT ATR value.

    ATR challenger rows with missing, non-finite or non-positive ATR are blocked
    rather than imputed. This prevents a few bad observations from aborting the
    entire OOS challenger while preserving fail-closed governance.
    """
    if "atr_14_pct" not in frame.columns:
        raise ValueError("BLOCK_DATA_ATR: atr_14_pct column missing")

    atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce")
    valid = atr.notna() & np.isfinite(atr) & (atr > 0)
    kept = frame.loc[valid].copy()
    blocked = frame.loc[~valid].copy()

    if kept.empty:
        raise ValueError("BLOCK_DATA_ATR: no valid ATR rows")

    report: dict[str, object] = {
        "status": "READY_ATR_CHALLENGER",
        "rows_input": int(len(frame)),
        "rows_valid_atr": int(valid.sum()),
        "rows_blocked_invalid_atr": int((~valid).sum()),
        "valid_atr_coverage": float(valid.mean()) if len(frame) else 0.0,
        "governance": {
            "invalid_atr_imputed": False,
            "invalid_atr_rows_blocked": True,
            "rule": "finite atr_14_pct > 0",
        },
    }

    if "as_of_date" in frame.columns:
        dates = pd.to_datetime(frame["as_of_date"], errors="coerce")
        years = dates.dt.year
        by_year: dict[str, object] = {}
        for year in sorted(y for y in years.dropna().unique()):
            mask = years.eq(year)
            count = int(mask.sum())
            good = int((mask & valid).sum())
            by_year[str(int(year))] = {
                "rows": count,
                "valid_atr_rows": good,
                "coverage": float(good / count) if count else 0.0,
            }
        report["by_year"] = by_year

    if not blocked.empty:
        report["blocked_examples"] = [
            {
                "ticker": str(row.get("ticker", "")),
                "as_of_date": str(row.get("as_of_date", "")),
                "atr_14_pct": None if pd.isna(row.get("atr_14_pct")) else row.get("atr_14_pct"),
            }
            for _, row in blocked.head(10).iterrows()
        ]

    return kept, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"BLOCK_DATA_ATR: input missing: {args.input}")

    try:
        frame = _read_frame(args.input)
        filtered, report = filter_valid_atr(frame)
        _write_frame(filtered, args.output)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
