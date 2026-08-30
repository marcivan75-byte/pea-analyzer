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
HORIZONS = ("1w", "2w", "4w", "13w", "26w")


class HebdoV221Blocked(RuntimeError):
    pass


def apply_quality_filter(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    required = ("roe", "debt_to_equity")
    if any(name not in out.columns for name in required):
        out["quality_status"] = "BLOCK_DATA_QUALITY"
        return out
    roe = pd.to_numeric(out["roe"], errors="coerce")
    debt = pd.to_numeric(out["debt_to_equity"], errors="coerce")
    known = roe.notna() & debt.notna()
    out["quality_status"] = np.where(known, "OK", "BLOCK_DATA_QUALITY")
    out.loc[known & (roe < 0.05) & (debt > 1.5), "quality_status"] = "EXCLU_QUALITE"
    return out


def double_sector_selection(frame: pd.DataFrame, max_tct: int, max_ct: int = 20) -> pd.DataFrame:
    """Top-2/secteur puis allocation bornée TCT/CT sans duplication."""
    if max_tct < 0 or max_ct < 0:
        raise ValueError("max_tct/max_ct must be >= 0")
    required = {"sector", "governed_score", "mom_26w_sector", "selection_status", "quality_status"}
    missing = required.difference(frame.columns)
    if missing:
        raise HebdoV221Blocked(f"BLOCK_DATA_HEBDO: missing {sorted(missing)}")

    out = frame.copy()
    out["hebdo_bucket"] = "NONE"
    valid = out[(out["selection_status"] == "OK") & (out["quality_status"] == "OK")].copy()
    if valid.empty:
        return out

    valid = valid.sort_values(["sector", "governed_score"], ascending=[True, False], kind="stable")
    valid["rank_sector_v221"] = valid.groupby("sector").cumcount() + 1
    top2 = valid[valid["rank_sector_v221"] <= 2].copy()
    top2 = top2.sort_values(["mom_26w_sector", "governed_score"], ascending=False, kind="stable")

    tct_index = top2.head(max_tct).index
    remaining = top2.loc[~top2.index.isin(tct_index)]
    ct_index = remaining.head(max_ct).index
    out.loc[tct_index, "hebdo_bucket"] = "TCT"
    out.loc[ct_index, "hebdo_bucket"] = "CT"
    out.loc[top2.index, "rank_sector_v221"] = top2["rank_sector_v221"]
    return out


def volatility_target_weights(frame: pd.DataFrame, regime: MarketRegime) -> pd.Series:
    """Inverse ATR sur l'ensemble des positions sélectionnées; 20% cash en CRASH."""
    if frame.empty:
        return pd.Series(dtype=float, index=frame.index)
    if "atr_14_pct" not in frame.columns:
        raise HebdoV221Blocked("BLOCK_DATA_SIZING: atr_14_pct missing")
    atr = pd.to_numeric(frame["atr_14_pct"], errors="coerce")
    valid = atr.notna() & np.isfinite(atr) & (atr > 0)
    if not bool(valid.all()):
        raise HebdoV221Blocked("BLOCK_DATA_SIZING: ATR missing/non-positive; no imputation allowed")
    inv = 1.0 / atr
    gross = 0.8 if regime.name == "CRASH" else 1.0
    denom = float(inv.sum())
    if not np.isfinite(denom) or denom <= 0:
        raise HebdoV221Blocked("BLOCK_DATA_SIZING: invalid inverse ATR denominator")
    return inv / denom * gross


def apply_four_week_exit(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "holding_days" not in out.columns or "mom_26w_sector" not in out.columns:
        out["exit_4w_signal"] = False
        return out
    holding = pd.to_numeric(out["holding_days"], errors="coerce")
    momentum = pd.to_numeric(out["mom_26w_sector"], errors="coerce")
    out["exit_4w_signal"] = out["hebdo_bucket"].eq("TCT") & (holding >= 20) & (momentum < 0)
    return out


def compute_ic_decay(frame: pd.DataFrame, score_col: str = "governed_score") -> dict[str, float | int | None]:
    """IC cross-sectionnel sur univers scoreable; jamais sur seuls gagnants/positions."""
    result: dict[str, float | int | None] = {}
    if score_col not in frame.columns:
        for horizon in HORIZONS:
            result[f"ic_{horizon}"] = None
            result[f"ic_{horizon}_n"] = 0
        return result
    score = pd.to_numeric(frame[score_col], errors="coerce")
    for horizon in HORIZONS:
        col = f"forward_ret_true_{horizon}"
        if col not in frame.columns:
            result[f"ic_{horizon}"] = None
            result[f"ic_{horizon}_n"] = 0
            continue
        ret = pd.to_numeric(frame[col], errors="coerce")
        valid = score.notna() & ret.notna()
        n = int(valid.sum())
        result[f"ic_{horizon}_n"] = n
        result[f"ic_{horizon}"] = float(spearmanr(score[valid], ret[valid]).statistic) if n >= 30 else None
    return result


def _rate_true(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float((values > 0).mean()) if not values.empty else None


def _mean_numeric(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def build_dashboard(
    frame: pd.DataFrame,
    regime: MarketRegime,
    *,
    turnover: float | None = None,
) -> dict[str, object]:
    """Dashboard: IC sur univers, P&L/MAE/stops uniquement sur portefeuille sélectionné."""
    portfolio = frame[frame.get("hebdo_bucket", pd.Series(index=frame.index, dtype="object")).isin(["TCT", "CT"])].copy()
    dashboard: dict[str, object] = {
        "version": "V22.1",
        "regime_cac": regime.name,
        "cac40_2w_return": regime.two_week_return,
        "tct_multiplier": regime.tct_multiplier,
        "rows_universe": int(len(frame)),
        "rows_portfolio": int(len(portfolio)),
        "turnover": turnover,
        "hit_rate_5d_true": _rate_true(portfolio, "forward_ret_true_1w"),
        "hit_rate_26w_true": _rate_true(portfolio, "forward_ret_true_26w"),
        "mae_mean": _mean_numeric(portfolio, "mae"),
        "expectancy_26w_true": _mean_numeric(portfolio, "forward_ret_true_26w"),
    }
    dashboard.update(compute_ic_decay(frame))

    if "hit_stop" in portfolio.columns:
        stop = portfolio["hit_stop"].dropna().astype(bool)
        dashboard["stop_rate"] = float(stop.mean()) if not stop.empty else None
    else:
        dashboard["stop_rate"] = None

    if "portfolio_weight" in portfolio.columns:
        w = pd.to_numeric(portfolio["portfolio_weight"], errors="coerce").dropna()
        dashboard["gross_exposure"] = float(w.sum()) if not w.empty else None
    else:
        dashboard["gross_exposure"] = None

    for column in ("selection_status", "quality_status", "mae_status", "hebdo_bucket"):
        if column in frame.columns:
            dashboard[f"{column}_pct"] = frame[column].value_counts(normalize=True, dropna=False).to_dict()

    dashboard["acceptance"] = {
        "ic_1w_gt_0_06": dashboard.get("ic_1w") is not None and float(dashboard["ic_1w"]) > 0.06,
        "ic_4w_gt_0_03": dashboard.get("ic_4w") is not None and float(dashboard["ic_4w"]) > 0.03,
        "hit_rate_26w_gt_0_60": dashboard["hit_rate_26w_true"] is not None and float(dashboard["hit_rate_26w_true"]) > 0.60,
        "stop_rate_lt_0_20": dashboard["stop_rate"] is not None and float(dashboard["stop_rate"]) < 0.20,
        "turnover_lt_0_35": turnover is not None and turnover < 0.35,
        "mae_target_ge_minus_0_045": dashboard["mae_mean"] is not None and float(dashboard["mae_mean"]) >= -0.045,
        "expectancy_target_ge_0_078": dashboard["expectancy_26w_true"] is not None and float(dashboard["expectancy_26w_true"]) >= 0.078,
    }
    dashboard["acceptance_all_measured_pass"] = all(dashboard["acceptance"].values())
    return dashboard


def run_v22_1(
    features: pd.DataFrame,
    lasso_weights: dict[str, dict[str, object]],
    regime: MarketRegime,
    *,
    turnover: float | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if features.empty:
        raise HebdoV221Blocked("BLOCK_DATA_HEBDO: empty PIT feature set")
    if not lasso_weights:
        raise HebdoV221Blocked("BLOCK_DATA_HEBDO: governed Lasso weights missing/empty")

    scored = score_universe_v22(features, lasso_weights)
    scored = apply_quality_filter(scored)
    max_tct = 10 if regime.name == "CRASH" else 20
    selected = double_sector_selection(scored, max_tct=max_tct, max_ct=20)

    selected["portfolio_weight"] = 0.0
    portfolio_mask = selected["hebdo_bucket"].isin(["TCT", "CT"])
    if bool(portfolio_mask.any()):
        selected.loc[portfolio_mask, "portfolio_weight"] = volatility_target_weights(selected.loc[portfolio_mask], regime)
    selected = apply_four_week_exit(selected)
    dashboard = build_dashboard(selected, regime, turnover=turnover)
    return selected, dashboard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--regime", choices=("NORMAL", "CRASH"), required=True)
    parser.add_argument("--cac40-2w-return", type=float, required=True)
    parser.add_argument("--turnover", type=float, default=None)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    if not args.features.is_file() or not args.weights.is_file():
        raise SystemExit("BLOCK_DATA: PIT features or governed weights missing")
    features = pd.read_csv(args.features)
    weights = json.loads(args.weights.read_text(encoding="utf-8"))
    regime = MarketRegime(args.regime, 0.5 if args.regime == "CRASH" else 1.0, args.cac40_2w_return)

    try:
        selection, dashboard = run_v22_1(features, weights, regime, turnover=args.turnover)
    except HebdoV221Blocked as exc:
        raise SystemExit(str(exc)) from exc

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
