from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

VARIANTS = ("FULL", "MAE_V1", "MAE_V2")


def _raw_periods(holdout: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = holdout.copy()
    x["as_of_date"] = pd.to_datetime(x["as_of_date"], errors="coerce")
    x["ret26"] = pd.to_numeric(x["forward_ret_true_26w"], errors="coerce")
    x = x.dropna(subset=["as_of_date"])
    x["quarter"] = x["as_of_date"].dt.to_period("Q").astype(str)
    x["year"] = x["as_of_date"].dt.year.astype(str)

    def agg(col: str) -> pd.DataFrame:
        out = x.groupby(col, sort=True).agg(
            raw_signal_rows=(col, "size"),
            raw_completed_26w=("ret26", "count"),
        ).reset_index().rename(columns={col: "period"})
        out["raw_maturity_ratio"] = out["raw_completed_26w"] / out["raw_signal_rows"]
        return out

    return agg("quarter"), agg("year")


def _complete_rows(table: pd.DataFrame, raw: pd.DataFrame, completeness: pd.DataFrame, kind: str) -> pd.DataFrame:
    table = table.copy()
    raw_map = raw.set_index("period").to_dict("index")
    comp = completeness.copy()
    comp["period"] = comp["period"].astype(str)

    if kind == "quarter":
        periods = list(comp["period"])
        status_map = comp.set_index("period")["status"].to_dict()
        publish_map = {p: status_map.get(p) == "COMPLETE" for p in periods}
    else:
        periods = sorted(raw["period"].astype(str).unique())
        status_map = {}
        publish_map = {}
        for p in periods:
            qs = comp[comp["period"].str.startswith(p)]
            ok = bool(len(qs) == 4 and qs["status"].eq("COMPLETE").all())
            status_map[p] = "COMPLETE" if ok else "OUTCOME_INCOMPLETE"
            publish_map[p] = ok

    # Preserve the model-eligible denominator separately from raw ledger volume.
    if "signals_total" in table.columns and "model_eligible_rows" not in table.columns:
        table["model_eligible_rows"] = table["signals_total"]
    if "retention_rate" in table.columns and "retention_rate_vs_eligible" not in table.columns:
        table["retention_rate_vs_eligible"] = table["retention_rate"]

    existing = {(str(r.period), str(r.variant)) for r in table[["period", "variant"]].itertuples(index=False)}
    additions = []
    cols = list(table.columns)
    for period in periods:
        for variant in VARIANTS:
            if (period, variant) in existing:
                continue
            row = {c: np.nan for c in cols}
            row.update({
                "period": period,
                "variant": variant,
                "status": status_map.get(period, "UNKNOWN"),
                "final_metrics_publishable": publish_map.get(period, False),
                "portfolio_net_result_status": "REQUIRES_CAPITAL_CONSTRAINED_PORTFOLIO_SIMULATION_WITH_COSTS",
            })
            additions.append(row)
    if additions:
        table = pd.concat([table, pd.DataFrame(additions)], ignore_index=True, sort=False)

    # Raw ledger counts are the authoritative period counts for reporting completeness.
    table["period"] = table["period"].astype(str)
    table["raw_signal_rows"] = table["period"].map(lambda p: raw_map.get(p, {}).get("raw_signal_rows"))
    table["raw_completed_26w"] = table["period"].map(lambda p: raw_map.get(p, {}).get("raw_completed_26w"))
    table["raw_maturity_ratio"] = table["period"].map(lambda p: raw_map.get(p, {}).get("raw_maturity_ratio"))
    table["signals_total"] = table["raw_signal_rows"]
    table["retention_rate_vs_raw"] = table["kept"] / table["raw_signal_rows"]

    table["status"] = table["period"].map(status_map).fillna(table["status"])
    table["final_metrics_publishable"] = table["period"].map(publish_map).fillna(False).astype(bool)

    # Never publish partial outcome metrics. Keep only raw/eligibility diagnostics on incomplete periods.
    metric_cols = [
        "wins", "win_rate", "losses", "loss_rate", "false_positives", "false_positive_rate",
        "avg_win", "avg_loss", "median_return", "expectancy", "gross_profit_signal_units",
        "gross_loss_signal_units", "net_signal_pnl_units", "profit_factor", "payoff_ratio",
        "return_std", "downside_std", "best_trade", "worst_trade", "stop_count", "stop_rate",
        "mae_mean", "mae_median", "mae_p10", "mfe_mean", "mfe_median", "mfe_p90",
        "portfolio_net_result",
    ]
    incomplete = ~table["final_metrics_publishable"]
    for c in metric_cols:
        if c in table.columns:
            table.loc[incomplete, c] = np.nan

    order = [
        "period", "variant", "status", "final_metrics_publishable",
        "raw_signal_rows", "raw_completed_26w", "raw_maturity_ratio",
        "model_eligible_rows", "kept", "retention_rate_vs_eligible", "retention_rate_vs_raw",
    ]
    rest = [c for c in table.columns if c not in order and c not in {"signals_total", "retention_rate"}]
    return table[[c for c in order + rest if c in table.columns]].sort_values(["period", "variant"], kind="stable")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    args = p.parse_args()

    holdout = pd.read_csv(args.input_dir / "V22_1_HOLDOUT.csv", low_memory=False)
    completeness = pd.read_csv(args.out_dir / "V22_1_QUARTER_COMPLETENESS.csv")
    quarterly = pd.read_csv(args.out_dir / "V22_1_QUARTERLY_DETAILED_COMPARISON.csv")
    annual = pd.read_csv(args.out_dir / "V22_1_ANNUAL_DETAILED_COMPARISON.csv")
    raw_q, raw_y = _raw_periods(holdout)

    quarterly = _complete_rows(quarterly, raw_q, completeness, "quarter")
    annual = _complete_rows(annual, raw_y, completeness, "year")

    quarterly.to_csv(args.out_dir / "V22_1_QUARTERLY_DETAILED_COMPARISON.csv", index=False)
    annual.to_csv(args.out_dir / "V22_1_ANNUAL_DETAILED_COMPARISON.csv", index=False)

    q26 = quarterly[(quarterly["period"].isin(["2026Q1", "2026Q2"])) & (quarterly["variant"] == "FULL")]
    print(q26[["period", "status", "raw_signal_rows", "raw_completed_26w", "raw_maturity_ratio", "final_metrics_publishable"]].to_string(index=False))
    if set(q26["period"]) != {"2026Q1", "2026Q2"}:
        raise SystemExit("BLOCK_DATA_REPORTING: 2026Q1/Q2 missing from detailed report")
    expected = {"2026Q1": (4629, 3117), "2026Q2": (4676, 0)}
    for row in q26.itertuples(index=False):
        exp = expected[row.period]
        if (int(row.raw_signal_rows), int(row.raw_completed_26w)) != exp:
            raise SystemExit(f"BLOCK_DATA_REPORTING: unexpected {row.period} raw counts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
