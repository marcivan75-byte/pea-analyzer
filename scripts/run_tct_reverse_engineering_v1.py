from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from v182.backtest.tct_reverse_engineering_v1 import (
    ReverseEngineeringConfig,
    discover_patterns_discovery_only,
    quantile_factor_scan,
    run_eight_pass_audit,
)
from v182.backtest.tct_reverse_engineering_v1_1 import (
    effective_config_for_history,
    prepare_research_matrix_adaptive,
)


def _load_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"UNSUPPORTED_INPUT_FORMAT:{suffix}")


def _derive_boolean_factors(matrix: pd.DataFrame) -> list[str]:
    specs = {
        "mom_5d_gt_3pct": matrix.get("ret_5d", pd.Series(index=matrix.index, dtype=float)) > 0.03,
        "mom_10d_gt_5pct": matrix.get("ret_10d", pd.Series(index=matrix.index, dtype=float)) > 0.05,
        "rvol20_gt_1_5": matrix.get("rvol20", pd.Series(index=matrix.index, dtype=float)) > 1.5,
        "volume_accel_gt_1_25": matrix.get("volume_accel_5_20", pd.Series(index=matrix.index, dtype=float)) > 1.25,
        "macd_hist_positive": matrix.get("macd_hist", pd.Series(index=matrix.index, dtype=float)) > 0,
        "rsi14_55_75": matrix.get("rsi14", pd.Series(index=matrix.index, dtype=float)).between(55, 75),
        "close_gt_ma20": matrix.get("close_over_ma_20", pd.Series(index=matrix.index, dtype=float)) > 0,
        "close_gt_ma50": matrix.get("close_over_ma_50", pd.Series(index=matrix.index, dtype=float)) > 0,
        "breakout_20d_flag": matrix.get("breakout_20d", pd.Series(index=matrix.index, dtype=float)).fillna(0).astype(bool),
        "breakout_50d_flag": matrix.get("breakout_50d", pd.Series(index=matrix.index, dtype=float)).fillna(0).astype(bool),
    }
    for name, values in specs.items():
        matrix[name] = values.astype(bool)
    return list(specs)


def main() -> int:
    parser = argparse.ArgumentParser(description="TCT reverse-engineering research runner")
    parser.add_argument("--ohlcv", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--exogenous", type=Path)
    parser.add_argument("--min-support", type=int, default=30)
    parser.add_argument("--max-pattern-size", type=int, default=3)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    ohlcv = _load_table(args.ohlcv)
    exogenous = _load_table(args.exogenous) if args.exogenous else None
    base_cfg = ReverseEngineeringConfig(min_support=args.min_support, max_pattern_size=args.max_pattern_size)
    cfg = effective_config_for_history(ohlcv, base_cfg)

    matrix, numeric_features = prepare_research_matrix_adaptive(
        ohlcv,
        exogenous_history=exogenous,
        cfg=cfg,
    )
    boolean_factors = _derive_boolean_factors(matrix)
    label = "label_hit_25_h20"

    discovery = matrix[matrix["research_split"] == "DISCOVERY"].copy()
    quantiles = quantile_factor_scan(discovery, numeric_features, label, min_support=cfg.min_support)
    patterns = discover_patterns_discovery_only(matrix, boolean_factors, label, cfg=cfg)
    audit = run_eight_pass_audit(matrix, boolean_factors, patterns, cfg)

    matrix.to_parquet(args.out / "research_matrix.parquet", index=False)
    quantiles.to_csv(args.out / "factor_quantiles_discovery.csv", index=False)
    patterns.to_csv(args.out / "boolean_patterns_discovery.csv", index=False)
    audit.to_csv(args.out / "audit_8_passes.csv", index=False)

    status = {
        "label": label,
        "rows": int(len(matrix)),
        "instruments": int(matrix["instrument_id"].nunique()),
        "date_min": str(pd.to_datetime(matrix["date"]).min().date()),
        "date_max": str(pd.to_datetime(matrix["date"]).max().date()),
        "split_protocol": matrix["research_split_protocol"].iloc[0] if len(matrix) else None,
        "splits": matrix["research_split"].value_counts(dropna=False).to_dict(),
        "audit_passes": int((audit["status"] == "PASS").sum()),
        "audit_failures": audit.loc[audit["status"] != "PASS", "audit_pass"].tolist(),
        "promotion_allowed": bool((audit["status"] == "PASS").all()),
        "research_only": True,
    }
    (args.out / "status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")
    print(json.dumps(status, indent=2, default=str))
    return 0 if status["promotion_allowed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
