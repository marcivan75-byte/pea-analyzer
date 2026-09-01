from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

POSITIVE_CONSENSUS = {"BUY", "STRONG_BUY"}
DEFAULT_ANALYST_THRESHOLDS = (5, 10, 15, 20)
_TRUE_TOKENS = {"1", "TRUE", "T", "YES", "Y", "OUI"}
_FALSE_TOKENS = {"0", "FALSE", "F", "NO", "N", "NON", "", "NAN", "NONE", "NULL"}


@dataclass(frozen=True)
class StudyConfig:
    target_upside_threshold_pct: float = 20.0
    consensus_delta_threshold: float = 0.0
    analyst_thresholds: tuple[int, ...] = DEFAULT_ANALYST_THRESHOLDS
    nominal_eur: float = 4500.0
    initial_capital_eur: float = 65000.0


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _bool_series(series: pd.Series) -> pd.Series:
    """Parse CSV/native booleans without bool('False') becoming True."""
    def parse(value: object) -> bool:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return False
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        if isinstance(value, (int, np.integer)):
            return value != 0
        if isinstance(value, (float, np.floating)):
            return value != 0.0
        token = str(value).strip().upper()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        raise ValueError(f"BLOCK_AUDIT73_INVALID_BOOLEAN:{value!r}")
    return series.map(parse).astype(bool)


def _prepare_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    """Normalize generic, TABPORT ledger and TABPORT walk-forward schemas."""
    out = ledger.copy()
    out["_trade_id"] = np.arange(len(out), dtype=np.int64)

    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    if "symbol" not in out.columns:
        raise ValueError("BLOCK_AUDIT73_LEDGER_MISSING:['symbol']")

    if "decision_at" not in out.columns:
        if "date" in out.columns:
            out["decision_at"] = out["date"]
        elif "entry_date" in out.columns:
            out["decision_at"] = out["entry_date"]
        else:
            raise ValueError("BLOCK_AUDIT73_NO_J1_DECISION_TIME")
    out["decision_at"] = _dt(out["decision_at"])
    if out["decision_at"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_J1_DECISION_TIME")

    if "exit_date" in out.columns:
        out["exit_date"] = _dt(out["exit_date"])
    elif "outcome_end_date" in out.columns:
        out["exit_date"] = _dt(out["outcome_end_date"])

    if "entry_price" not in out.columns and "entry_outcome_price" in out.columns:
        out["entry_price"] = out["entry_outcome_price"]
    if "entry_price" not in out.columns:
        raise ValueError("BLOCK_AUDIT73_ENTRY_PRICE_REQUIRED_FOR_HISTORICAL_UPSIDE")
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    if out["entry_price"].isna().any() or (out["entry_price"] <= 0).any():
        raise ValueError("BLOCK_AUDIT73_INVALID_ENTRY_PRICE")

    if "return_pct" in out.columns:
        out["return_pct"] = pd.to_numeric(out["return_pct"], errors="coerce")
        out["return_source"] = "RETURN_PCT"
    elif "outcome_return" in out.columns:
        out["return_pct"] = pd.to_numeric(out["outcome_return"], errors="coerce") * 100.0
        out["return_source"] = "TABPORT_WF_OUTCOME_RETURN_FRACTION"
    elif "return_net" in out.columns:
        out["return_pct"] = pd.to_numeric(out["return_net"], errors="coerce") * 100.0
        out["return_source"] = "TABPORT_RETURN_NET_FRACTION"
    else:
        raise ValueError("BLOCK_AUDIT73_LEDGER_MISSING:['return_pct|outcome_return|return_net']")
    if out["return_pct"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_RETURN")

    if "pnl_net" in out.columns:
        out["pnl_net"] = pd.to_numeric(out["pnl_net"], errors="coerce")

    if "durable_false_positive" not in out.columns and "true_fp_durable" in out.columns:
        out["durable_false_positive"] = out["true_fp_durable"]
    if "stop_triggered" not in out.columns:
        if "hit_stop" in out.columns:
            out["stop_triggered"] = out["hit_stop"]
        elif "stop_declenche" in out.columns:
            out["stop_triggered"] = out["stop_declenche"]
    if "exit_category" not in out.columns and "exit_reason" in out.columns:
        out["exit_category"] = out["exit_reason"]

    for boolean_col in ("durable_false_positive", "stop_triggered", "endpoint_mark"):
        if boolean_col in out.columns:
            out[boolean_col] = _bool_series(out[boolean_col])

    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    if out["symbol"].eq("").any():
        raise ValueError("BLOCK_AUDIT73_EMPTY_SYMBOL")
    return out.sort_values(["symbol", "decision_at", "_trade_id"]).reset_index(drop=True)


def _prepare_consensus(obs: pd.DataFrame) -> pd.DataFrame:
    out = obs.copy()
    if "symbol" not in out.columns and "ticker" in out.columns:
        out["symbol"] = out["ticker"]
    required = {"symbol", "available_at", "target_median", "consensus", "n_analysts"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"BLOCK_AUDIT73_CONSENSUS_MISSING:{sorted(missing)}")
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["available_at"] = _dt(out["available_at"])
    if out["available_at"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_AVAILABLE_AT")
    if "period_kind" in out.columns:
        out = out[out["period_kind"].fillna("CURRENT").astype(str).str.upper().eq("CURRENT")].copy()
    out["target_median"] = pd.to_numeric(out["target_median"], errors="coerce")
    out["n_analysts"] = pd.to_numeric(out["n_analysts"], errors="coerce")
    if "consensus_delta_4w" not in out.columns:
        out["consensus_delta_4w"] = np.nan
    out["consensus_delta_4w"] = pd.to_numeric(out["consensus_delta_4w"], errors="coerce")
    out["consensus"] = out["consensus"].astype(str).str.upper().str.strip()
    out = out.drop_duplicates(["symbol", "available_at"], keep="last")
    return out.sort_values(["symbol", "available_at"]).reset_index(drop=True)


def attach_latest_pit_snapshot(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Attach only the latest consensus snapshot truly available at J+1."""
    left = _prepare_ledger(ledger)
    right = _prepare_consensus(observations)
    pieces = []
    cols = ["available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"]
    for symbol, trades in left.groupby("symbol", sort=False):
        o = right[right["symbol"].eq(symbol)]
        if o.empty:
            z = trades.copy()
            z["pit_available_at"] = pd.NaT
            for c in cols[1:]:
                z[f"pit_{c}"] = np.nan
            pieces.append(z)
            continue
        m = pd.merge_asof(
            trades.sort_values("decision_at"),
            o[cols].sort_values("available_at"),
            left_on="decision_at",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        pieces.append(m.rename(columns={
            "available_at": "pit_available_at",
            "target_median": "pit_target_median",
            "consensus": "pit_consensus",
            "n_analysts": "pit_n_analysts",
            "consensus_delta_4w": "pit_consensus_delta_4w",
        }))

    out = pd.concat(pieces, ignore_index=True) if pieces else left.iloc[0:0].copy()
    out["pit_snapshot_available"] = out["pit_available_at"].notna()
    bad = out["pit_snapshot_available"] & (out["pit_available_at"] > out["decision_at"])
    if bad.any():
        raise ValueError("BLOCK_AUDIT73_LOOKAHEAD_SNAPSHOT")
    out["pit_target_upside_pct"] = np.where(
        out["pit_snapshot_available"] & out["pit_target_median"].notna(),
        (pd.to_numeric(out["pit_target_median"], errors="coerce") / out["entry_price"] - 1.0) * 100.0,
        np.nan,
    )
    return out.sort_values(["decision_at", "symbol", "_trade_id"]).reset_index(drop=True)


def _endpoint_filtered(frame: pd.DataFrame) -> pd.DataFrame:
    if "endpoint_mark" not in frame.columns:
        return frame.copy()
    return frame.loc[~_bool_series(frame["endpoint_mark"])].copy()


def _max_drawdown_from_trade_returns(returns_pct: pd.Series) -> float | None:
    if returns_pct.empty:
        return None
    equity = (1.0 + returns_pct.astype(float) / 100.0).cumprod()
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def _realized_exit_drawdown(trades: pd.DataFrame, initial_capital_eur: float) -> float | None:
    """Realized-only DD; never presented as exact marked-to-market portfolio DD."""
    if (
        trades.empty or initial_capital_eur <= 0 or "exit_date" not in trades.columns
        or "pnl_net" not in trades.columns or trades["exit_date"].isna().any()
        or trades["pnl_net"].isna().any()
    ):
        return None
    ordered = trades.sort_values(["exit_date", "_trade_id"])
    equity = initial_capital_eur + ordered["pnl_net"].astype(float).cumsum()
    equity = pd.concat([pd.Series([initial_capital_eur], dtype=float), equity.reset_index(drop=True)], ignore_index=True)
    peak = equity.cummax()
    return float((equity / peak - 1.0).min() * 100.0)


def _metrics(selected: pd.DataFrame, baseline: pd.DataFrame, *, nominal_eur: float, initial_capital_eur: float) -> dict:
    real = _endpoint_filtered(selected)
    base_real = _endpoint_filtered(baseline)
    wins = real[real["return_pct"] > 0]
    losses = real[real["return_pct"] < 0]

    use_actual_pnl = "pnl_net" in real.columns and len(real) > 0 and real["pnl_net"].notna().all()
    pnl_series = real["pnl_net"].astype(float) if use_actual_pnl else real["return_pct"].astype(float) * nominal_eur / 100.0
    gross_profit = float(pnl_series[pnl_series > 0].sum())
    gross_loss = -float(pnl_series[pnl_series < 0].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else None
    rr = None
    if len(wins) and len(losses) and float(losses["return_pct"].mean()) != 0:
        rr = float(wins["return_pct"].mean() / abs(losses["return_pct"].mean()))

    if "stop_triggered" in real.columns:
        stops = int(_bool_series(real["stop_triggered"]).sum())
    else:
        exit_category = real["exit_category"].astype(str) if "exit_category" in real.columns else pd.Series("", index=real.index)
        stops = int(exit_category.str.contains("STOP", case=False, na=False).sum())
    exit_category = real["exit_category"].astype(str) if "exit_category" in real.columns else pd.Series("", index=real.index)
    early_fp = int(exit_category.eq("EARLY_FALSE_POSITIVE").sum())

    if "durable_false_positive" in real.columns:
        durable_fp = int(_bool_series(real["durable_false_positive"]).sum())
        durable_fp_definition = "TABPORT_LOCKED_TRUE_FP_DURABLE" if "true_fp_durable" in real.columns else "EXPLICIT_LEDGER_FIELD"
    else:
        durable_fp = None
        durable_fp_definition = "UNAVAILABLE_NO_LOCKED_DEFINITION"

    selected_ids = set(real["_trade_id"].tolist())
    removed = base_real.loc[~base_real["_trade_id"].isin(selected_ids)]
    winners_removed = int((removed["return_pct"] > 0).sum())

    pnl_eur = float(pnl_series.sum()) if len(real) else 0.0
    deployed = nominal_eur * len(real)
    realized_dd = _realized_exit_drawdown(real, initial_capital_eur)
    proxy_dd = None if real.empty else _max_drawdown_from_trade_returns(real["return_pct"])
    headline_dd = realized_dd if realized_dd is not None else proxy_dd
    dd_basis = "REALIZED_EXIT_PNL_NOT_MARK_TO_MARKET" if realized_dd is not None else "TRADE_SEQUENCE_COMPOUND_PROXY"

    return {
        "trades": int(len(real)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate_pct": None if real.empty else round(float((real["return_pct"] > 0).mean() * 100.0), 3),
        "stops": stops,
        "early_false_positives": early_fp,
        "durable_false_positives": durable_fp,
        "durable_false_positive_definition": durable_fp_definition,
        "winners_removed_vs_j1": winners_removed,
        "gross_profit_eur": round(gross_profit, 2),
        "gross_loss_eur": round(gross_loss, 2),
        "profit_factor": None if pf is None else round(pf, 4),
        "reward_risk": None if rr is None else round(rr, 4),
        "expectancy_pct_per_trade": None if real.empty else round(float(real["return_pct"].mean()), 4),
        "pnl_eur": round(pnl_eur, 2),
        "pnl_basis": "ACTUAL_TABPORT_PNL_NET" if use_actual_pnl else "FIXED_NOMINAL_PROXY",
        "return_on_initial_capital_pct": None if initial_capital_eur <= 0 else round(pnl_eur / initial_capital_eur * 100.0, 4),
        "return_on_deployed_nominal_pct": None if deployed <= 0 else round(pnl_eur / deployed * 100.0, 4),
        "max_drawdown_pct": None if headline_dd is None else round(headline_dd, 4),
        "max_drawdown_basis": dd_basis,
        "max_drawdown_realized_exit_curve_pct": None if realized_dd is None else round(realized_dd, 4),
        "max_drawdown_trade_sequence_proxy_pct": None if proxy_dd is None else round(proxy_dd, 4),
    }


def evaluate_variants(joined: pd.DataFrame, config: StudyConfig = StudyConfig()) -> list[dict]:
    base = joined.copy()
    has = base["pit_snapshot_available"].fillna(False).astype(bool)
    target = has & base["pit_target_upside_pct"].gt(config.target_upside_threshold_pct)
    positive = target & base["pit_consensus"].isin(POSITIVE_CONSENSUS)
    revision_available = has & base["pit_consensus_delta_4w"].notna()
    improving = positive & revision_available & base["pit_consensus_delta_4w"].gt(config.consensus_delta_threshold)

    variants: list[tuple[str, pd.Series]] = [
        ("J1_BASELINE", pd.Series(True, index=base.index)),
        ("J1_TARGET_GT_20", target),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS", positive),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING", improving),
    ]
    for threshold in config.analyst_thresholds:
        variants.append((f"J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_{threshold}", improving & base["pit_n_analysts"].ge(threshold)))

    total_real = _endpoint_filtered(base)
    pit_real = total_real[total_real["pit_snapshot_available"]]
    missing_real = total_real[~total_real["pit_snapshot_available"]]
    missing_winners = missing_real["return_pct"] > 0

    results = []
    for name, mask in variants:
        selected = base.loc[mask].copy()
        metrics = _metrics(selected, base, nominal_eur=config.nominal_eur, initial_capital_eur=config.initial_capital_eur)
        selected_ids = set(_endpoint_filtered(selected)["_trade_id"].tolist())
        rejected_pit = pit_real.loc[~pit_real["_trade_id"].isin(selected_ids)]
        metrics["variant"] = name
        metrics["pit_coverage_trades"] = int((mask & has).sum())
        metrics["pit_unassessable_trades_vs_j1"] = 0 if name == "J1_BASELINE" else int(len(missing_real))
        metrics["winners_unassessable_missing_pit_vs_j1"] = 0 if name == "J1_BASELINE" else int(missing_winners.sum())
        metrics["filter_rejections_among_pit"] = 0 if name == "J1_BASELINE" else int(len(rejected_pit))
        metrics["winners_filtered_out_among_pit"] = 0 if name == "J1_BASELINE" else int((rejected_pit["return_pct"] > 0).sum())
        metrics["losers_filtered_out_among_pit"] = 0 if name == "J1_BASELINE" else int((rejected_pit["return_pct"] < 0).sum())
        results.append(metrics)
    return results


def run_study(ledger: pd.DataFrame, observations: pd.DataFrame, config: StudyConfig = StudyConfig()) -> dict:
    joined = attach_latest_pit_snapshot(ledger, observations)
    variants = evaluate_variants(joined, config)
    real = _endpoint_filtered(joined)
    coverage = float(real["pit_snapshot_available"].mean() * 100.0) if len(real) else 0.0
    pit_count = int(real["pit_snapshot_available"].sum()) if len(real) else 0
    revision_count = int((real["pit_snapshot_available"] & real["pit_consensus_delta_4w"].notna()).sum()) if len(real) else 0
    revision_coverage = revision_count / pit_count * 100.0 if pit_count else 0.0
    return {
        "status": "SUCCESS",
        "version": "HEBDO_META_CONSENSUS_GATE_AUDIT73_V3",
        "policy": {
            "strict_pit": True,
            "current_target_backfill_forbidden": True,
            "relative_factset_periods_backdated": False,
            "target_upside_recomputed_from_target_and_j1_price": True,
            "native_tabport_fractional_returns_normalized_to_pct": True,
            "csv_boolean_tokens_parsed_strictly": True,
            "missing_pit_separated_from_filter_rejection": True,
            "target_threshold_pct": config.target_upside_threshold_pct,
            "positive_consensus": sorted(POSITIVE_CONSENSUS),
            "consensus_delta_threshold": config.consensus_delta_threshold,
            "analyst_threshold_grid": list(config.analyst_thresholds),
            "analyst_threshold_selection_posthoc_forbidden": True,
            "initial_capital_eur": config.initial_capital_eur,
            "nominal_eur": config.nominal_eur,
        },
        "j1_trades": int(len(real)),
        "pit_snapshot_coverage_pct": round(coverage, 3),
        "revision_coverage_pct_among_pit": round(revision_coverage, 3),
        "variants": variants,
        "limitations": [
            "BOURSORAMA_COLLECTION_START_2026_08_22",
            "RELATIVE_FACTSET_COLUMNS_NOT_EXACT_DATES",
            "HEADLINE_METRICS_REQUIRE_REAL_SNAPSHOTS_AVAILABLE_BY_J1",
            "FILTERED_VARIANT_DRAWDOWN_IS_NOT_EXACT_MARK_TO_MARKET_WITHOUT_VARIANT_DAILY_NAV",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", required=True)
    p.add_argument("--consensus", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    ledger = pd.read_csv(args.ledger)
    obs = pd.read_csv(args.consensus)
    payload = run_study(ledger, obs)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "j1_trades": payload["j1_trades"],
        "pit_coverage_pct": payload["pit_snapshot_coverage_pct"],
        "revision_coverage_pct_among_pit": payload["revision_coverage_pct_among_pit"],
    }, indent=2))


if __name__ == "__main__":
    main()
