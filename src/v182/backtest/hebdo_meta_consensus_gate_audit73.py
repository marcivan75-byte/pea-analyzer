"""HEBDO AT META Audit 73 — strict PIT J+1 consensus-gate comparison.

This research module never backfills a current analyst target into the past.  A
consensus snapshot may influence a J+1 decision only when its real `available_at`
is at or before the decision timestamp.  Relative FactSet history (3m/2m/1m/7d)
is diagnostic evidence known at capture time; it is not treated as if collected
at a fabricated historical date.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd

POSITIVE_CONSENSUS = {"BUY", "STRONG_BUY"}
DEFAULT_ANALYST_THRESHOLDS = (5, 10, 15, 20)


@dataclass(frozen=True)
class StudyConfig:
    target_upside_threshold_pct: float = 20.0
    consensus_delta_threshold: float = 0.0
    analyst_thresholds: tuple[int, ...] = DEFAULT_ANALYST_THRESHOLDS
    nominal_eur: float = 4500.0


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _prepare_ledger(ledger: pd.DataFrame) -> pd.DataFrame:
    out = ledger.copy()
    required = {"symbol", "return_pct"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"BLOCK_AUDIT73_LEDGER_MISSING:{sorted(missing)}")
    if "decision_at" not in out.columns:
        if "entry_date" not in out.columns:
            raise ValueError("BLOCK_AUDIT73_NO_J1_DECISION_TIME")
        out["decision_at"] = out["entry_date"]
    out["decision_at"] = _dt(out["decision_at"])
    if out["decision_at"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_J1_DECISION_TIME")
    if "entry_price" not in out.columns:
        raise ValueError("BLOCK_AUDIT73_ENTRY_PRICE_REQUIRED_FOR_HISTORICAL_UPSIDE")
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    if out["entry_price"].isna().any() or (out["entry_price"] <= 0).any():
        raise ValueError("BLOCK_AUDIT73_INVALID_ENTRY_PRICE")
    out["return_pct"] = pd.to_numeric(out["return_pct"], errors="coerce")
    if out["return_pct"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_RETURN")
    return out.sort_values(["symbol", "decision_at"]).reset_index(drop=True)


def _prepare_consensus(obs: pd.DataFrame) -> pd.DataFrame:
    out = obs.copy()
    required = {"symbol", "available_at", "target_median", "consensus", "n_analysts"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"BLOCK_AUDIT73_CONSENSUS_MISSING:{sorted(missing)}")
    out["available_at"] = _dt(out["available_at"])
    if out["available_at"].isna().any():
        raise ValueError("BLOCK_AUDIT73_INVALID_AVAILABLE_AT")
    if "period_kind" in out.columns:
        # Relative rows are retained for diagnostics/revision but cannot masquerade
        # as a historical current-state observation.
        out = out[out["period_kind"].fillna("CURRENT").astype(str).str.upper().eq("CURRENT")].copy()
    out["target_median"] = pd.to_numeric(out["target_median"], errors="coerce")
    out["n_analysts"] = pd.to_numeric(out["n_analysts"], errors="coerce")
    if "consensus_delta_4w" not in out.columns:
        out["consensus_delta_4w"] = np.nan
    out["consensus_delta_4w"] = pd.to_numeric(out["consensus_delta_4w"], errors="coerce")
    out["consensus"] = out["consensus"].astype(str).str.upper().str.strip()
    return out.sort_values(["symbol", "available_at"]).reset_index(drop=True)


def attach_latest_pit_snapshot(ledger: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    """Attach the latest snapshot actually available by each J+1 decision."""
    left = _prepare_ledger(ledger)
    right = _prepare_consensus(observations)
    pieces = []
    for symbol, trades in left.groupby("symbol", sort=False):
        o = right[right["symbol"].eq(symbol)].copy()
        if o.empty:
            z = trades.copy()
            for c in ("available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"):
                z[f"pit_{c}"] = pd.NaT if c == "available_at" else np.nan
            pieces.append(z)
            continue
        m = pd.merge_asof(
            trades.sort_values("decision_at"),
            o[["available_at", "target_median", "consensus", "n_analysts", "consensus_delta_4w"]].sort_values("available_at"),
            left_on="decision_at",
            right_on="available_at",
            direction="backward",
            allow_exact_matches=True,
        )
        m = m.rename(columns={
            "available_at": "pit_available_at",
            "target_median": "pit_target_median",
            "consensus": "pit_consensus",
            "n_analysts": "pit_n_analysts",
            "consensus_delta_4w": "pit_consensus_delta_4w",
        })
        pieces.append(m)
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
    return out


def _max_drawdown_from_trade_returns(returns_pct: pd.Series) -> float | None:
    if returns_pct.empty:
        return None
    equity = (1.0 + returns_pct.astype(float) / 100.0).cumprod()
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min() * 100.0)


def _metrics(selected: pd.DataFrame, baseline: pd.DataFrame, *, nominal_eur: float) -> dict:
    real = selected.copy()
    if "endpoint_mark" in real.columns:
        real = real[~real["endpoint_mark"].fillna(False).astype(bool)].copy()
    base_real = baseline.copy()
    if "endpoint_mark" in base_real.columns:
        base_real = base_real[~base_real["endpoint_mark"].fillna(False).astype(bool)].copy()
    wins = real[real["return_pct"] > 0]
    losses = real[real["return_pct"] < 0]
    win_sum = float(wins["return_pct"].sum()) if len(wins) else 0.0
    loss_sum = -float(losses["return_pct"].sum()) if len(losses) else 0.0
    pf = win_sum / loss_sum if loss_sum > 0 else None
    rr = None
    if len(wins) and len(losses) and float(losses["return_pct"].mean()) != 0:
        rr = float(wins["return_pct"].mean() / abs(losses["return_pct"].mean()))
    exit_category = real["exit_category"].astype(str) if "exit_category" in real.columns else pd.Series("", index=real.index)
    stops = int(exit_category.eq("PROTECTIVE_STOP").sum())
    early_fp = int(exit_category.eq("EARLY_FALSE_POSITIVE").sum())
    if "durable_false_positive" in real.columns:
        durable_fp = int(real["durable_false_positive"].fillna(False).astype(bool).sum())
        durable_fp_definition = "EXPLICIT_LEDGER_FIELD"
    else:
        durable_fp = None
        durable_fp_definition = "UNAVAILABLE_NO_LOCKED_DEFINITION"
    selected_ids = set(real.index)
    removed = base_real.loc[~base_real.index.isin(selected_ids)]
    winners_removed = int((removed["return_pct"] > 0).sum())
    pnl_eur = float((real["return_pct"] * nominal_eur / 100.0).sum()) if len(real) else 0.0
    deployed = nominal_eur * len(real)
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
        "profit_factor": None if pf is None else round(pf, 4),
        "reward_risk": None if rr is None else round(rr, 4),
        "expectancy_pct_per_trade": None if real.empty else round(float(real["return_pct"].mean()), 4),
        "pnl_eur_nominal": round(pnl_eur, 2),
        "return_on_deployed_nominal_pct": None if deployed <= 0 else round(pnl_eur / deployed * 100.0, 4),
        "max_drawdown_trade_sequence_pct": None if real.empty else round(_max_drawdown_from_trade_returns(real["return_pct"]), 4),
    }


def evaluate_variants(joined: pd.DataFrame, config: StudyConfig = StudyConfig()) -> list[dict]:
    base = joined.copy()
    has = base["pit_snapshot_available"]
    target = has & base["pit_target_upside_pct"].gt(config.target_upside_threshold_pct)
    positive = target & base["pit_consensus"].isin(POSITIVE_CONSENSUS)
    improving = positive & base["pit_consensus_delta_4w"].gt(config.consensus_delta_threshold)
    variants: list[tuple[str, pd.Series]] = [
        ("J1_BASELINE", pd.Series(True, index=base.index)),
        ("J1_TARGET_GT_20", target),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS", positive),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING", improving),
    ]
    for threshold in config.analyst_thresholds:
        variants.append((f"J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_{threshold}", improving & base["pit_n_analysts"].ge(threshold)))
    results = []
    for name, mask in variants:
        metrics = _metrics(base.loc[mask].copy(), base, nominal_eur=config.nominal_eur)
        metrics["variant"] = name
        metrics["pit_coverage_trades"] = int((mask & has).sum())
        results.append(metrics)
    return results


def run_study(ledger: pd.DataFrame, observations: pd.DataFrame, config: StudyConfig = StudyConfig()) -> dict:
    joined = attach_latest_pit_snapshot(ledger, observations)
    variants = evaluate_variants(joined, config)
    coverage = float(joined["pit_snapshot_available"].mean() * 100.0) if len(joined) else 0.0
    return {
        "status": "SUCCESS",
        "version": "HEBDO_META_CONSENSUS_GATE_AUDIT73_V1",
        "policy": {
            "strict_pit": True,
            "current_target_backfill_forbidden": True,
            "relative_factset_periods_backdated": False,
            "target_upside_recomputed_from_target_and_j1_price": True,
            "target_threshold_pct": config.target_upside_threshold_pct,
            "positive_consensus": sorted(POSITIVE_CONSENSUS),
            "consensus_delta_threshold": config.consensus_delta_threshold,
            "analyst_threshold_grid": list(config.analyst_thresholds),
        },
        "j1_trades": int(len(joined)),
        "pit_snapshot_coverage_pct": round(coverage, 3),
        "variants": variants,
        "limitations": [
            "BOURSORAMA_COLLECTION_START_2026_08_22",
            "RELATIVE_FACTSET_COLUMNS_NOT_EXACT_DATES",
            "HEADLINE_METRICS_REQUIRE_REAL_SNAPSHOTS_AVAILABLE_BY_J1",
            "DURABLE_FALSE_POSITIVE_REQUIRES_EXPLICIT_LOCKED_LEDGER_FIELD",
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
    print(json.dumps({"status": payload["status"], "j1_trades": payload["j1_trades"], "pit_coverage_pct": payload["pit_snapshot_coverage_pct"]}, indent=2))


if __name__ == "__main__":
    main()
