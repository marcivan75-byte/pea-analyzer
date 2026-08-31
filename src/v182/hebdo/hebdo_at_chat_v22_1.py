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
DEFAULT_CORRELATION_THRESHOLD = 0.80
DEFAULT_REPLACEMENT_MARGIN = 0.15
DEFAULT_ROUND_TRIP_COST_RATE = 0.0015


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


def apply_earnings_filter(frame: pd.DataFrame, *, days: int = 3, require_data: bool = False) -> pd.DataFrame:
    """Exclude known near-term earnings risk without inventing missing calendars."""
    out = frame.copy()
    if "days_to_earnings" not in out.columns:
        out["earnings_status"] = "BLOCK_DATA_EARNINGS" if require_data else "NOT_EVALUATED"
        if require_data:
            out.loc[out["selection_status"].eq("OK"), "selection_status"] = "BLOCK_DATA_EARNINGS"
        return out
    dte = pd.to_numeric(out["days_to_earnings"], errors="coerce")
    known = dte.notna()
    out["earnings_status"] = np.where(known, "OK", "BLOCK_DATA_EARNINGS" if require_data else "NOT_EVALUATED")
    risk = known & (dte >= 0) & (dte <= int(days))
    out.loc[risk, "earnings_status"] = "EXCLU_EARNINGS"
    out.loc[out["selection_status"].eq("OK") & risk, "selection_status"] = "EXCLU_EARNINGS"
    if require_data:
        out.loc[out["selection_status"].eq("OK") & ~known, "selection_status"] = "BLOCK_DATA_EARNINGS"
    return out


def _top2_sector_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame[(frame["selection_status"] == "OK") & (frame["quality_status"] == "OK")].copy()
    if valid.empty:
        return valid
    valid = valid.sort_values(["sector", "governed_score"], ascending=[True, False], kind="stable")
    valid["rank_sector_v221"] = valid.groupby("sector").cumcount() + 1
    return valid[valid["rank_sector_v221"] <= 2].sort_values(
        ["mom_26w_sector", "governed_score"], ascending=False, kind="stable"
    )


def _choose_tct_with_hysteresis(
    top2: pd.DataFrame,
    max_tct: int,
    previous_tct: set[str] | None,
    replacement_margin: float,
) -> list[int]:
    if max_tct <= 0 or top2.empty:
        return []
    if not previous_tct or "ticker" not in top2.columns:
        return list(top2.head(max_tct).index)

    previous = {str(x).upper() for x in previous_tct}
    incumbents = top2[top2["ticker"].astype(str).str.upper().isin(previous)].copy()
    challengers = top2[~top2.index.isin(incumbents.index)].copy()
    incumbents = incumbents.sort_values("governed_score", ascending=False, kind="stable")
    chosen = list(incumbents.head(max_tct).index)

    # Fill empty slots first. Once full, replace only when a challenger exceeds
    # the weakest incumbent by a governed margin. This reduces churn without
    # adding an arbitrary bonus to the financial score itself.
    for idx, row in challengers.iterrows():
        if len(chosen) < max_tct:
            chosen.append(idx)
            continue
        weakest = min(chosen, key=lambda i: float(pd.to_numeric(top2.loc[i, "governed_score"], errors="coerce")))
        weak_score = float(pd.to_numeric(top2.loc[weakest, "governed_score"], errors="coerce"))
        challenger_score = float(pd.to_numeric(row["governed_score"], errors="coerce"))
        if np.isfinite(challenger_score) and np.isfinite(weak_score) and challenger_score > weak_score + replacement_margin:
            chosen.remove(weakest)
            chosen.append(idx)
    return chosen


def double_sector_selection(
    frame: pd.DataFrame,
    max_tct: int,
    max_ct: int = 20,
    *,
    previous_tct: set[str] | None = None,
    replacement_margin: float = DEFAULT_REPLACEMENT_MARGIN,
) -> pd.DataFrame:
    """Top-2/sector, bounded TCT/CT, with optional turnover hysteresis."""
    if max_tct < 0 or max_ct < 0:
        raise ValueError("max_tct/max_ct must be >= 0")
    required = {"sector", "governed_score", "mom_26w_sector", "selection_status", "quality_status"}
    missing = required.difference(frame.columns)
    if missing:
        raise HebdoV221Blocked(f"BLOCK_DATA_HEBDO: missing {sorted(missing)}")

    out = frame.copy()
    out["hebdo_bucket"] = "NONE"
    top2 = _top2_sector_candidates(out)
    if top2.empty:
        return out

    tct_index = _choose_tct_with_hysteresis(top2, max_tct, previous_tct, replacement_margin)
    remaining = top2.loc[~top2.index.isin(tct_index)]
    ct_index = list(remaining.head(max_ct).index)
    out.loc[tct_index, "hebdo_bucket"] = "TCT"
    out.loc[ct_index, "hebdo_bucket"] = "CT"
    out.loc[top2.index, "rank_sector_v221"] = top2["rank_sector_v221"]
    out["continuity_status"] = "NOT_APPLICABLE"
    if previous_tct and "ticker" in out.columns:
        prior = {str(x).upper() for x in previous_tct}
        prior_mask = out["ticker"].astype(str).str.upper().isin(prior)
        out.loc[prior_mask & out["hebdo_bucket"].eq("TCT"), "continuity_status"] = "RETAINED_TCT"
        out.loc[prior_mask & ~out["hebdo_bucket"].eq("TCT"), "continuity_status"] = "DROPPED_PREVIOUS_TCT"
    return out


def apply_correlation_guard(
    frame: pd.DataFrame,
    returns_60d: pd.DataFrame | None,
    *,
    threshold: float = DEFAULT_CORRELATION_THRESHOLD,
    max_ct: int = 20,
) -> pd.DataFrame:
    """Demote highly correlated TCT positions using a date x ticker return panel."""
    out = frame.copy()
    out["correlation_status"] = "NOT_EVALUATED"
    if returns_60d is None or returns_60d.empty:
        return out
    if "ticker" not in out.columns:
        raise HebdoV221Blocked("BLOCK_DATA_CORRELATION: ticker missing")

    panel = returns_60d.copy()
    if {"date", "ticker", "return"}.issubset(panel.columns):
        panel["date"] = pd.to_datetime(panel["date"], errors="coerce")
        panel["return"] = pd.to_numeric(panel["return"], errors="coerce")
        panel = panel.pivot_table(index="date", columns="ticker", values="return", aggfunc="last")
    panel.columns = [str(c).upper() for c in panel.columns]
    corr = panel.tail(60).corr(min_periods=20)

    tct = out[out["hebdo_bucket"].eq("TCT")].sort_values("governed_score", ascending=False, kind="stable")
    kept: list[str] = []
    current_ct = int(out["hebdo_bucket"].eq("CT").sum())
    for idx, row in tct.iterrows():
        ticker = str(row["ticker"]).upper()
        if ticker not in corr.columns:
            out.loc[idx, "correlation_status"] = "BLOCK_DATA_CORRELATION"
            continue
        too_close = False
        for other in kept:
            value = corr.loc[ticker, other] if other in corr.columns else np.nan
            if pd.notna(value) and abs(float(value)) > threshold:
                too_close = True
                break
        if too_close:
            if current_ct < max_ct:
                out.loc[idx, "hebdo_bucket"] = "CT"
                current_ct += 1
                out.loc[idx, "correlation_status"] = "DEMOTED_CORR"
            else:
                out.loc[idx, "hebdo_bucket"] = "NONE"
                out.loc[idx, "correlation_status"] = "EXCLU_CORR"
        else:
            kept.append(ticker)
            out.loc[idx, "correlation_status"] = "OK"
    return out


def volatility_target_weights(frame: pd.DataFrame, regime: MarketRegime) -> pd.Series:
    """Inverse ATR on all selected positions; 20% cash in CRASH."""
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


def adaptive_atr_stop_pct(atr_14_pct: float, *, multiplier: float = 2.5, floor: float = 0.06, cap: float = 0.12) -> float:
    """ATR challenger only; the fixed -9% stop remains the benchmark."""
    atr = float(atr_14_pct)
    if not np.isfinite(atr) or atr <= 0:
        raise ValueError("atr_14_pct must be finite and > 0")
    return float(max(floor, min(cap, atr * multiplier)))


def apply_four_week_exit(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["exit_4w_signal"] = False
    out["exit_action"] = "HOLD"
    out["partial_exit_fraction"] = 0.0
    out["move_stop_to_breakeven"] = False
    if "holding_days" not in out.columns or "mom_26w_sector" not in out.columns:
        return out
    holding = pd.to_numeric(out["holding_days"], errors="coerce")
    momentum = pd.to_numeric(out["mom_26w_sector"], errors="coerce")
    pnl = pd.to_numeric(out["pnl_since_entry"], errors="coerce") if "pnl_since_entry" in out.columns else pd.Series(np.nan, index=out.index)
    eligible = out["hebdo_bucket"].eq("TCT") & (holding >= 20)
    full_exit = eligible & (momentum < 0)
    partial = eligible & ~full_exit & (pnl > 0.08)
    out.loc[full_exit, "exit_4w_signal"] = True
    out.loc[full_exit, "exit_action"] = "EXIT_FULL_MOMENTUM"
    out.loc[partial, "exit_4w_signal"] = True
    out.loc[partial, "exit_action"] = "TAKE_50_AND_BE"
    out.loc[partial, "partial_exit_fraction"] = 0.5
    out.loc[partial, "move_stop_to_breakeven"] = True
    return out


def compute_ic_decay(frame: pd.DataFrame, score_col: str = "governed_score") -> dict[str, float | int | None]:
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
    round_trip_cost_rate: float = DEFAULT_ROUND_TRIP_COST_RATE,
    gross_alpha_period: float | None = None,
) -> dict[str, object]:
    portfolio = frame[frame.get("hebdo_bucket", pd.Series(index=frame.index, dtype="object")).isin(["TCT", "CT"])].copy()
    estimated_cost = turnover * round_trip_cost_rate if turnover is not None else None
    net_alpha = gross_alpha_period - estimated_cost if gross_alpha_period is not None and estimated_cost is not None else None
    dashboard: dict[str, object] = {
        "version": "V22.1",
        "regime_cac": regime.name,
        "cac40_2w_return": regime.two_week_return,
        "tct_multiplier": regime.tct_multiplier,
        "rows_universe": int(len(frame)),
        "rows_portfolio": int(len(portfolio)),
        "turnover": turnover,
        "round_trip_cost_rate": round_trip_cost_rate,
        "estimated_transaction_cost_period": estimated_cost,
        "gross_alpha_period": gross_alpha_period,
        "net_alpha_after_estimated_cost": net_alpha,
        "hit_rate_5d_true": _rate_true(portfolio, "forward_ret_true_1w"),
        "hit_rate_26w_true": _rate_true(portfolio, "forward_ret_true_26w"),
        "mae_mean": _mean_numeric(portfolio, "mae"),
        "expectancy_26w_true": _mean_numeric(portfolio, "forward_ret_true_26w"),
        "adv20_eur_mean": _mean_numeric(portfolio, "adv_20_eur"),
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

    for column in ("selection_status", "quality_status", "earnings_status", "mae_status", "correlation_status", "continuity_status", "hebdo_bucket", "exit_action"):
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
    previous_tct: set[str] | None = None,
    returns_60d: pd.DataFrame | None = None,
    require_earnings_data: bool = False,
    replacement_margin: float = DEFAULT_REPLACEMENT_MARGIN,
    gross_alpha_period: float | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    if features.empty:
        raise HebdoV221Blocked("BLOCK_DATA_HEBDO: empty PIT feature set")
    if not lasso_weights:
        raise HebdoV221Blocked("BLOCK_DATA_HEBDO: governed Lasso weights missing/empty")

    scored = score_universe_v22(features, lasso_weights)
    scored = apply_quality_filter(scored)
    scored = apply_earnings_filter(scored, require_data=require_earnings_data)
    max_tct = 10 if regime.name == "CRASH" else 20
    selected = double_sector_selection(
        scored,
        max_tct=max_tct,
        max_ct=20,
        previous_tct=previous_tct,
        replacement_margin=replacement_margin,
    )
    selected = apply_correlation_guard(selected, returns_60d, max_ct=20)

    selected["portfolio_weight"] = 0.0
    portfolio_mask = selected["hebdo_bucket"].isin(["TCT", "CT"])
    if bool(portfolio_mask.any()):
        selected.loc[portfolio_mask, "portfolio_weight"] = volatility_target_weights(selected.loc[portfolio_mask], regime)
    selected = apply_four_week_exit(selected)
    dashboard = build_dashboard(selected, regime, turnover=turnover, gross_alpha_period=gross_alpha_period)
    return selected, dashboard


def _load_previous_tct(path: Path | None) -> set[str] | None:
    if path is None or not path.is_file():
        return None
    frame = pd.read_csv(path, sep=None, engine="python")
    ticker_col = next((c for c in ("ticker", "yahoo_ticker", "symbol") if c in frame.columns), None)
    if ticker_col is None:
        return None
    if "hebdo_bucket" in frame.columns:
        frame = frame[frame["hebdo_bucket"].astype(str).str.upper().eq("TCT")]
    return set(frame[ticker_col].dropna().astype(str).str.upper())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--regime", choices=("NORMAL", "CRASH"), required=True)
    parser.add_argument("--cac40-2w-return", type=float, required=True)
    parser.add_argument("--turnover", type=float, default=None)
    parser.add_argument("--gross-alpha-period", type=float, default=None)
    parser.add_argument("--previous-selection", type=Path, default=None)
    parser.add_argument("--returns-60d", type=Path, default=None)
    parser.add_argument("--require-earnings-data", action="store_true")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    if not args.features.is_file() or not args.weights.is_file():
        raise SystemExit("BLOCK_DATA: PIT features or governed weights missing")
    features = pd.read_csv(args.features)
    weights = json.loads(args.weights.read_text(encoding="utf-8"))
    regime = MarketRegime(args.regime, 0.5 if args.regime == "CRASH" else 1.0, args.cac40_2w_return)
    previous_tct = _load_previous_tct(args.previous_selection)
    returns_60d = pd.read_csv(args.returns_60d) if args.returns_60d is not None and args.returns_60d.is_file() else None

    try:
        selection, dashboard = run_v22_1(
            features,
            weights,
            regime,
            turnover=args.turnover,
            previous_tct=previous_tct,
            returns_60d=returns_60d,
            require_earnings_data=args.require_earnings_data,
            gross_alpha_period=args.gross_alpha_period,
        )
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
