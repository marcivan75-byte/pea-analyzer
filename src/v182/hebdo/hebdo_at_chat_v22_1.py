from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from v182.hebdo.hebdo_at_chat_v22 import MarketRegime, score_universe_v22


OUTPUT_REL = Path("outputs/hebdo/HEBDO_AT_CHAT_V22_1_SELECTION.csv")
DASHBOARD_REL = Path("outputs/hebdo/HEBDO_AT_CHAT_V22_1_DASHBOARD.json")


def apply_quality_filter(frame: pd.DataFrame) -> pd.DataFrame:
    """Exclude low-ROE/high-debt rows only when both PIT fundamentals are observed.

    No missing fundamental is imputed. Missing data is marked BLOCK_DATA_QUALITY.
    """
    out = frame.copy()
    required = ("roe", "debt_to_equity")
    missing_columns = [name for name in required if name not in out.columns]
    if missing_columns:
        out["quality_status"] = "BLOCK_DATA_QUALITY"
        return out
    roe = pd.to_numeric(out["roe"], errors="coerce")
    debt = pd.to_numeric(out["debt_to_equity"], errors="coerce")
    known = roe.notna() & debt.notna()
    out["quality_status"] = np.where(known, "OK", "BLOCK_DATA_QUALITY")
    out.loc[known & (roe < 0.05) & (debt > 1.5), "quality_status"] = "EXCLU_QUALITE"
    return out


def volatility_target_weights(frame: pd.DataFrame, regime: MarketRegime, gross_target: float = 0.8) -> pd.Series:
    """Inverse-ATR sizing; CRASH keeps at least 20% cash and halves TCT capacity upstream."""
    if not 0.0 < gross_target <= 1.0:
        raise ValueError("gross_target must be in (0, 1]")
    if "atr_14_pct" not in frame.columns:
        raise ValueError("atr_14_pct missing")
    atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce")
    inv = 1.0 / atr.where(atr > 0)
    inv = inv.replace([np.inf, -np.inf], np.nan)
    if inv.notna().sum() == 0:
        return pd.Series(0.0, index=frame.index)
    effective_gross = min(gross_target, 0.8 if regime.name == "CRASH" else gross_target)
    return inv.fillna(0.0) / float(inv.fillna(0.0).sum()) * effective_gross


def apply_four_week_exit(frame: pd.DataFrame) -> pd.DataFrame:
    """Cut a TCT after >=4 weeks when sector-neutral momentum has turned negative."""
    out = frame.copy()
    if "holding_days" not in out.columns or "mom_26w_sector" not in out.columns:
        out["exit_4w_signal"] = False
        return out
    holding = pd.to_numeric(out["holding_days"], errors="coerce")
    momentum = pd.to_numeric(out["mom_26w_sector"], errors="coerce")
    out["exit_4w_signal"] = (holding >= 20) & (momentum < 0)
    return out


def compute_ic_decay(frame: pd.DataFrame, score_col: str = "governed_score") -> dict[str, float | int | None]:
    """Compute Spearman IC on true forward-return columns when provided.

    Expected return columns are forward_ret_true_1w/2w/4w/13w/26w. Missing horizons
    remain null rather than being reconstructed from current prices.
    """
    result: dict[str, float | int | None] = {}
    if score_col not in frame.columns:
        return {f"ic_{h}": None for h in ("1w", "2w", "4w", "13w", "26w")}
    score = pd.to_numeric(frame[score_col], errors="coerce")
    for horizon in ("1w", "2w", "4w", "13w", "26w"):
        col = f"forward_ret_true_{horizon}"
        if col not in frame.columns:
            result[f"ic_{horizon}"] = None
            continue
        ret = pd.to_numeric(frame[col], errors="coerce")
        valid = score.notna() & ret.notna()
        if int(valid.sum()) < 30:
            result[f"ic_{horizon}"] = None
            continue
        result[f"ic_{horizon}"] = float(spearmanr(score[valid], ret[valid]).statistic)
    return result


def build_dashboard(frame: pd.DataFrame, regime: MarketRegime, turnover: float | None = None) -> dict[str, object]:
    dashboard: dict[str, object] = {
        "version": "V22.1",
        "regime": regime.name,
        "cac40_2w_return": regime.two_week_return,
        "tct_multiplier": regime.tct_multiplier,
        "rows": int(len(frame)),
        "turnover": turnover,
    }
    dashboard.update(compute_ic_decay(frame))
    for horizon in ("1w", "26w"):
        col = f"forward_ret_true_{horizon}"
        values = pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else pd.Series(dtype=float)
        dashboard[f"hit_rate_{horizon}"] = float((values.dropna() > 0).mean()) if not values.dropna().empty else None
    mae = pd.to_numeric(frame["mae"], errors="coerce") if "mae" in frame.columns else pd.Series(dtype=float)
    dashboard["mae_mean"] = float(mae.dropna().mean()) if not mae.dropna().empty else None
    if "selection_status" in frame.columns:
        dashboard["selection_status_pct"] = frame["selection_status"].value_counts(normalize=True, dropna=False).to_dict()
    if "quality_status" in frame.columns:
        dashboard["quality_status_pct"] = frame["quality_status"].value_counts(normalize=True, dropna=False).to_dict()
    return dashboard


def run_v22_1(
    features: pd.DataFrame,
    lasso_weights: dict[str, dict[str, object]],
    regime: MarketRegime,
    *,
    turnover: float | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    scored = score_universe_v22(features, lasso_weights)
    scored = apply_quality_filter(scored)
    eligible = scored[(scored["selection_status"] == "OK") & (scored["quality_status"] == "OK")].copy()
    eligible["portfolio_weight"] = volatility_target_weights(eligible, regime)
    eligible = apply_four_week_exit(eligible)
    blocked = scored.drop(index=eligible.index, errors="ignore").copy()
    if "portfolio_weight" not in blocked.columns:
        blocked["portfolio_weight"] = 0.0
    output = pd.concat([eligible, blocked], ignore_index=True, sort=False)
    dashboard = build_dashboard(output, regime, turnover=turnover)
    return output, dashboard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--regime", choices=("NORMAL", "CRASH"), required=True)
    parser.add_argument("--cac40-2w-return", type=float, required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    if not args.features.is_file() or not args.weights.is_file():
        raise SystemExit("BLOCK_DATA: PIT features or governed weights missing")
    features = pd.read_csv(args.features)
    weights = json.loads(args.weights.read_text(encoding="utf-8"))
    regime = MarketRegime(args.regime, 0.5 if args.regime == "CRASH" else 1.0, args.cac40_2w_return)
    selection, dashboard = run_v22_1(features, weights, regime)
    root = args.root.resolve()
    out_path = root / OUTPUT_REL
    dash_path = root / DASHBOARD_REL
    out_path.parent.mkdir(parents=True, exist_ok=True)
    selection.to_csv(out_path, index=False)
    dash_path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dashboard, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
