from __future__ import annotations

import argparse
from pathlib import Path
import json

import pandas as pd

from .config import OptimizerConfig
from .data import attach_forward_returns, load_snapshot_files
from .engine import BacktestOptimizer

ROOT = Path(__file__).resolve().parents[3]


def _markdown(result) -> str:
    lines = [
        "# BACKTEST_OPTIMIZER_V1",
        "",
        f"**Status:** `{result.status}`",
        "",
        "## Guardrails",
        "- Point-in-time snapshots only; no reconstruction from future fundamentals/consensus.",
        "- Out-of-sample holdout is mandatory before a new weight set can be recommended.",
        "- Production weights are never modified by this module.",
        "",
    ]
    if result.audit.get("reason"):
        lines += ["## Result", str(result.audit["reason"]), ""]
    if result.baseline_weights:
        lines += ["## Weights", "| Feature | Current | Recommended |", "|---|---:|---:|"]
        for name, current in result.baseline_weights.items():
            proposed = result.recommended_weights.get(name, current)
            lines.append(f"| {name} | {current:.2%} | {proposed:.2%} |")
        lines.append("")
    if result.baseline_metrics:
        lines += ["## Out-of-sample metrics", "| Metric | Current | Recommended |", "|---|---:|---:|"]
        for key in ["mean_return", "annualized_return", "annualized_vol", "max_drawdown", "hit_rate", "turnover"]:
            a = result.baseline_metrics.get(key, float("nan"))
            b = result.recommended_metrics.get(key, float("nan"))
            lines.append(f"| {key} | {a:.4f} | {b:.4f} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-safe weight optimiser for archived PEA committee snapshots")
    parser.add_argument("--input", required=True, help="Snapshot file or directory")
    parser.add_argument("--config", default=str(ROOT / "config" / "BACKTEST_OPTIMIZER_V1.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "outputs" / "backtest_optimizer"))
    args = parser.parse_args()

    cfg = OptimizerConfig.from_json(args.config)
    raw = load_snapshot_files(args.input)
    if raw.empty:
        raise SystemExit("No valid point-in-time snapshots found")
    data = attach_forward_returns(raw, cfg.horizon_days, cfg.horizon_tolerance_days)
    result = BacktestOptimizer(cfg).optimize(data)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"feature": k, "current_weight": v, "recommended_weight": result.recommended_weights.get(k, v)}
        for k, v in result.baseline_weights.items()
    ]).to_csv(out / "WEIGHTS.csv", index=False)
    result.sensitivity.to_csv(out / "SENSITIVITY.csv", index=False)
    result.leaderboard.head(250).to_csv(out / "LEADERBOARD_TOP250.csv", index=False)
    (out / "AUDIT.json").write_text(json.dumps(result.audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "SUMMARY.md").write_text(_markdown(result), encoding="utf-8")
    print(result.status)
    print(json.dumps(result.audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
