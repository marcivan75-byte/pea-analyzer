from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from v182.backtest import hebdo_meta_consensus_gate_audit73 as base

StudyConfig = base.StudyConfig
POSITIVE_CONSENSUS = base.POSITIVE_CONSENSUS
attach_latest_pit_snapshot = base.attach_latest_pit_snapshot


def _delta(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None:
        return None
    return round(float(value) - float(reference), 4)


def evaluate_variants(joined: pd.DataFrame, config: StudyConfig = StudyConfig()) -> list[dict]:
    """Evaluate staged filters on a like-for-like PIT-covered cohort.

    V3 correctly separated missing PIT observations from explicit filter rejection,
    but its filtered headline metrics were still visually compared with the full J1
    baseline. V4 adds a PIT-covered J1 baseline and explicit deltas versus that
    comparable cohort so missing historical coverage cannot masquerade as alpha.
    """
    frame = joined.copy()
    has = frame["pit_snapshot_available"].fillna(False).astype(bool)
    target = has & frame["pit_target_upside_pct"].gt(config.target_upside_threshold_pct)
    positive = target & frame["pit_consensus"].isin(POSITIVE_CONSENSUS)
    revision_available = has & frame["pit_consensus_delta_4w"].notna()
    improving = positive & revision_available & frame["pit_consensus_delta_4w"].gt(config.consensus_delta_threshold)

    variants: list[tuple[str, pd.Series, bool]] = [
        ("J1_BASELINE", pd.Series(True, index=frame.index), False),
        ("J1_PIT_COVERED_BASELINE", has, True),
        ("J1_TARGET_GT_20", target, True),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS", positive, True),
        ("J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING", improving, True),
    ]
    for threshold in config.analyst_thresholds:
        variants.append((
            f"J1_TARGET_GT_20_POSITIVE_CONSENSUS_IMPROVING_ANALYSTS_GE_{threshold}",
            improving & frame["pit_n_analysts"].ge(threshold),
            True,
        ))

    total_real = base._endpoint_filtered(frame)
    pit_real = total_real[total_real["pit_snapshot_available"]]
    missing_real = total_real[~total_real["pit_snapshot_available"]]
    missing_winners = missing_real["return_pct"] > 0

    covered_metrics = base._metrics(
        frame.loc[has].copy(),
        frame.loc[has].copy(),
        nominal_eur=config.nominal_eur,
        initial_capital_eur=config.initial_capital_eur,
    )

    results = []
    for name, mask, comparable_to_covered in variants:
        selected = frame.loc[mask].copy()
        comparison_baseline = frame.loc[has].copy() if comparable_to_covered else frame
        metrics = base._metrics(
            selected,
            comparison_baseline,
            nominal_eur=config.nominal_eur,
            initial_capital_eur=config.initial_capital_eur,
        )
        selected_ids = set(base._endpoint_filtered(selected)["_trade_id"].tolist())
        rejected_pit = pit_real.loc[~pit_real["_trade_id"].isin(selected_ids)]
        metrics["variant"] = name
        metrics["comparison_cohort"] = "PIT_COVERED_J1" if comparable_to_covered else "ALL_J1"
        metrics["pit_coverage_trades"] = int((mask & has).sum())
        metrics["pit_unassessable_trades_vs_j1"] = 0 if name in {"J1_BASELINE", "J1_PIT_COVERED_BASELINE"} else int(len(missing_real))
        metrics["winners_unassessable_missing_pit_vs_j1"] = 0 if name in {"J1_BASELINE", "J1_PIT_COVERED_BASELINE"} else int(missing_winners.sum())
        metrics["filter_rejections_among_pit"] = 0 if name in {"J1_BASELINE", "J1_PIT_COVERED_BASELINE"} else int(len(rejected_pit))
        metrics["winners_filtered_out_among_pit"] = 0 if name in {"J1_BASELINE", "J1_PIT_COVERED_BASELINE"} else int((rejected_pit["return_pct"] > 0).sum())
        metrics["losers_filtered_out_among_pit"] = 0 if name in {"J1_BASELINE", "J1_PIT_COVERED_BASELINE"} else int((rejected_pit["return_pct"] < 0).sum())
        metrics["delta_expectancy_pct_vs_pit_covered_j1"] = None if not comparable_to_covered else _delta(metrics.get("expectancy_pct_per_trade"), covered_metrics.get("expectancy_pct_per_trade"))
        metrics["delta_win_rate_pct_vs_pit_covered_j1"] = None if not comparable_to_covered else _delta(metrics.get("win_rate_pct"), covered_metrics.get("win_rate_pct"))
        metrics["delta_profit_factor_vs_pit_covered_j1"] = None if not comparable_to_covered else _delta(metrics.get("profit_factor"), covered_metrics.get("profit_factor"))
        metrics["delta_reward_risk_vs_pit_covered_j1"] = None if not comparable_to_covered else _delta(metrics.get("reward_risk"), covered_metrics.get("reward_risk"))
        metrics["comparable_cohort_trades"] = int(len(pit_real)) if comparable_to_covered else int(len(total_real))
        results.append(metrics)
    return results


def run_study(ledger: pd.DataFrame, observations: pd.DataFrame, config: StudyConfig = StudyConfig()) -> dict:
    joined = attach_latest_pit_snapshot(ledger, observations)
    variants = evaluate_variants(joined, config)
    real = base._endpoint_filtered(joined)
    coverage = float(real["pit_snapshot_available"].mean() * 100.0) if len(real) else 0.0
    pit_count = int(real["pit_snapshot_available"].sum()) if len(real) else 0
    revision_count = int((real["pit_snapshot_available"] & real["pit_consensus_delta_4w"].notna()).sum()) if len(real) else 0
    revision_coverage = revision_count / pit_count * 100.0 if pit_count else 0.0
    return {
        "status": "SUCCESS",
        "version": "HEBDO_META_CONSENSUS_GATE_AUDIT73_V4",
        "policy": {
            "strict_pit": True,
            "current_target_backfill_forbidden": True,
            "relative_factset_periods_backdated": False,
            "target_upside_recomputed_from_target_and_j1_price": True,
            "missing_pit_separated_from_filter_rejection": True,
            "like_for_like_pit_baseline_required": True,
            "filtered_headline_comparison_to_full_j1_forbidden": True,
            "target_threshold_pct": config.target_upside_threshold_pct,
            "positive_consensus": sorted(POSITIVE_CONSENSUS),
            "consensus_delta_threshold": config.consensus_delta_threshold,
            "analyst_threshold_grid": list(config.analyst_thresholds),
            "analyst_threshold_selection_posthoc_forbidden": True,
            "initial_capital_eur": config.initial_capital_eur,
            "nominal_eur": config.nominal_eur,
        },
        "j1_trades": int(len(real)),
        "pit_covered_j1_trades": pit_count,
        "pit_snapshot_coverage_pct": round(coverage, 3),
        "revision_coverage_pct_among_pit": round(revision_coverage, 3),
        "variants": variants,
        "limitations": [
            "BOURSORAMA_COLLECTION_START_2026_08_22",
            "RELATIVE_FACTSET_COLUMNS_NOT_EXACT_DATES",
            "HEADLINE_FILTER_EFFECTS_MUST_USE_PIT_COVERED_J1_COMPARATOR",
            "FILTERED_VARIANT_DRAWDOWN_IS_NOT_EXACT_MARK_TO_MARKET_WITHOUT_VARIANT_DAILY_NAV",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--consensus", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    ledger = pd.read_csv(args.ledger)
    observations = pd.read_csv(args.consensus)
    payload = run_study(ledger, observations)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "j1_trades": payload["j1_trades"],
        "pit_covered_j1_trades": payload["pit_covered_j1_trades"],
        "pit_coverage_pct": payload["pit_snapshot_coverage_pct"],
    }, indent=2))


if __name__ == "__main__":
    main()
