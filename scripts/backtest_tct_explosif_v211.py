from __future__ import annotations

import argparse
import json
from pathlib import Path
import pandas as pd

from v182.backtest_optimizer.tct_explosif import (
    TCTLabelConfig,
    make_forward_labels,
    evaluate_scores,
    chronological_calibration_holdout,
)


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, sep=";", low_memory=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Point-in-time TCT Explosif V21.1 backtest")
    ap.add_argument("--scores", required=True, type=Path,
                    help="Historical point-in-time scores: snapshot_date,instrument_id|isin,score|tct_score")
    ap.add_argument("--ohlc", required=True, type=Path,
                    help="Historical daily OHLC with date,instrument_id|isin,open,high,low,close")
    ap.add_argument("--out", type=Path, default=Path("outputs/backtest/V21.1_TCT_EXPLOSIF_BACKTEST.json"))
    args = ap.parse_args()

    scores = _read(args.scores)
    if "instrument_id" not in scores and "isin" in scores:
        scores = scores.rename(columns={"isin": "instrument_id"})
    if "score" not in scores and "tct_score" in scores:
        scores = scores.rename(columns={"tct_score": "score"})
    scores["snapshot_date"] = pd.to_datetime(scores["snapshot_date"], errors="coerce")

    labels = make_forward_labels(_read(args.ohlc), TCTLabelConfig())
    merged = scores.merge(labels, on=["snapshot_date", "instrument_id"], how="inner")
    if merged.empty:
        raise RuntimeError("No point-in-time score/label matches")

    end = merged["snapshot_date"].max()
    report: dict[str, object] = {
        "version": "V21.1_TCT_EXPLOSIF",
        "entry_rule": "NEXT_SESSION_OPEN",
        "target": "+15% within 20 sessions",
        "risk_control": "MAE to first hit >= -12%",
        "windows": {},
    }
    for months in (12, 18, 36):
        start = end - pd.DateOffset(months=months)
        w = merged[merged["snapshot_date"] >= start].copy()
        metrics = evaluate_scores(w, decision_threshold=72.0, k=20)
        calibration = chronological_calibration_holdout(
            w, test_fraction=0.30, bins=10, min_positive_events=75, k=20
        )
        report["windows"][f"{months}m"] = {
            "start": str(start.date()),
            "end": str(end.date()),
            "rows": int(len(w)),
            "metrics": metrics,
            "calibration": calibration,
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
