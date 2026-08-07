from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.io.frames import is_missing, load_master

ROOT = Path(__file__).resolve().parents[3]

VISIBLE_COLUMNS = [
    "isin", "name", "yahoo_ticker", "country", "comite_status", "score_brut",
]


def _worklist(frame: pd.DataFrame, universe: str) -> pd.DataFrame:
    if "last_close" not in frame.columns:
        gaps = frame.copy()
    else:
        gaps = frame[frame["last_close"].apply(is_missing)].copy()
    columns = [column for column in VISIBLE_COLUMNS if column in gaps.columns]
    gaps = gaps[columns].copy()
    gaps["universe"] = universe
    gaps["gap_reason"] = "NO_USABLE_OHLCV_AFTER_FALLBACK_CHAIN"
    if "score_brut" in gaps.columns:
        gaps["_score"] = pd.to_numeric(gaps["score_brut"], errors="coerce")
        gaps = gaps.sort_values("_score", ascending=False, na_position="last").drop(columns=["_score"])
    return gaps.reset_index(drop=True)


def write_ohlcv_gap_audit(root: Path | None = None) -> dict:
    root = root or ROOT
    outputs = root / "outputs"
    gaps_dir = outputs / "gaps"
    audit_dir = outputs / "audit"
    gaps_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    actions = load_master(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv")
    etf = load_master(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv")
    action_gaps = _worklist(actions, "ACTION")
    etf_gaps = _worklist(etf, "ETF")

    action_path = gaps_dir / "V18.2_OHLCV_ACTION_GAPS.csv"
    etf_path = gaps_dir / "V18.2_OHLCV_ETF_GAPS.csv"
    action_gaps.to_csv(action_path, sep=";", index=False, encoding="utf-8-sig")
    etf_gaps.to_csv(etf_path, sep=";", index=False, encoding="utf-8-sig")

    source_metrics_path = audit_dir / "V18.2_SOURCE_FALLBACK_METRICS.json"
    source_metrics = (
        json.loads(source_metrics_path.read_text(encoding="utf-8"))
        if source_metrics_path.exists()
        else {}
    )
    summary = {
        "actions_total": len(actions),
        "actions_without_last_close": len(action_gaps),
        "actions_last_close_coverage_pct": round((len(actions) - len(action_gaps)) / max(1, len(actions)) * 100.0, 2),
        "etf_total": len(etf),
        "etf_without_last_close": len(etf_gaps),
        "etf_last_close_coverage_pct": round((len(etf) - len(etf_gaps)) / max(1, len(etf)) * 100.0, 2),
        "fallback_chain": ["YFINANCE", "OPENFIGI_YFINANCE_REPAIR", "MARKETSTACK", "ALPHA_VANTAGE"],
        "wave01_failed": int((source_metrics.get("wave01_actions") or {}).get("failed", 0) or 0),
        "wave02_failed": int((source_metrics.get("wave02_etf") or {}).get("failed", 0) or 0),
        "status": "ACTIONABLE_RESIDUAL_GAPS",
    }
    (audit_dir / "V18.2_OHLCV_GAP_METRICS.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    metrics = write_ohlcv_gap_audit()
    print(
        "OHLCV_GAP_AUDIT — "
        f"Actions={metrics['actions_without_last_close']}/{metrics['actions_total']} | "
        f"ETF={metrics['etf_without_last_close']}/{metrics['etf_total']}"
    )


if __name__ == "__main__":
    main()
