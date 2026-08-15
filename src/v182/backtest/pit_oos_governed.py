from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os

import pandas as pd

from v182.backtest.exceptional_pit_oos import (
    HOLDOUT_START,
    ROOT,
    _trade_metrics,
    action_overlay_backtest,
    etf_core_backtest,
)


def enforce_single_position_per_isin(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Keep at most one open trade per ISIN at any point in time.

    Signals are processed chronologically. A later signal for an ISIN is rejected
    when its entry date is on or before the exit date of the already-open trade.
    This mirrors the Committee virtual-book governance and prevents overlapping
    monthly signals from counting the same economic position twice.
    """
    if trades.empty:
        return trades.copy(), pd.DataFrame(columns=["isin", "signal_date", "entry_date", "blocked_by_exit_date", "reason"])

    work = trades.copy()
    work["_signal"] = pd.to_datetime(work["signal_date"], errors="coerce")
    work["_entry"] = pd.to_datetime(work["entry_date"], errors="coerce")
    work["_exit"] = pd.to_datetime(work["exit_date"], errors="coerce")
    work = work.sort_values(["_signal", "rank_on_date", "isin"], kind="stable")

    last_exit: dict[str, pd.Timestamp] = {}
    keep: list[int] = []
    blocked: list[dict] = []
    for idx, row in work.iterrows():
        isin = str(row.get("isin") or "")
        entry = row.get("_entry")
        exit_date = row.get("_exit")
        prior_exit = last_exit.get(isin)
        if prior_exit is not None and pd.notna(entry) and entry <= prior_exit:
            blocked.append(
                {
                    "isin": isin,
                    "signal_date": row.get("signal_date"),
                    "entry_date": row.get("entry_date"),
                    "blocked_by_exit_date": prior_exit.date().isoformat(),
                    "reason": "DUPLICATE_ISIN_WHILE_POSITION_OPEN",
                }
            )
            continue
        keep.append(idx)
        if pd.notna(exit_date):
            last_exit[isin] = pd.Timestamp(exit_date)

    filtered = work.loc[keep].drop(columns=["_signal", "_entry", "_exit"]).reset_index(drop=True)
    return filtered, pd.DataFrame(blocked)


def _loss_analysis(trades: pd.DataFrame) -> list[dict]:
    if trades.empty:
        return []
    recent = trades[trades["period"].astype(str).eq("DIAGNOSTIC_OOS")].copy()
    recent["net_return"] = pd.to_numeric(recent["net_return"], errors="coerce")
    losses = recent[recent["net_return"] < 0].sort_values("net_return")
    rows = []
    for _, row in losses.iterrows():
        rows.append(
            {
                "isin": str(row.get("isin")),
                "signal_date": str(row.get("signal_date")),
                "entry_date": str(row.get("entry_date")),
                "exit_date": str(row.get("exit_date")),
                "score_final": round(float(row.get("score_final")), 6),
                "exit_reason": str(row.get("exit_reason")),
                "holding_sessions": int(row.get("holding_sessions")),
                "net_return_pct": round(float(row.get("net_return")) * 100.0, 4),
            }
        )
    return rows


def run(root: Path = ROOT) -> dict:
    if os.environ.get("ALLOW_EXCEPTIONAL_PIT_OOS_ONCE") != "1":
        raise PermissionError("EXCEPTIONAL_PIT_OOS_DISABLED_USE_ONE_SHOT_FLAG")

    outdir = root / "outputs" / "backtest" / "exceptional_pit_oos_2026_08_14"
    outdir.mkdir(parents=True, exist_ok=True)

    action_observations, action_metrics, action_summary = action_overlay_backtest(root)
    raw_etf_trades, etf_summary = etf_core_backtest(root)
    etf_trades, duplicates = enforce_single_position_per_isin(raw_etf_trades)

    periods = ("DEVELOPMENT", "VALIDATION_OOS", "DIAGNOSTIC_OOS")
    etf_summary = dict(etf_summary)
    etf_summary["single_position_per_isin"] = True
    etf_summary["overlapping_signals_blocked"] = int(len(duplicates))
    etf_summary["raw_trade_count_before_position_guard"] = int(len(raw_etf_trades))
    etf_summary["trade_count_after_position_guard"] = int(len(etf_trades))
    etf_summary["metrics"] = [
        _trade_metrics(etf_trades[etf_trades["period"] == period] if not etf_trades.empty else pd.DataFrame(), period)
        for period in periods
    ]
    etf_summary["diagnostic_oos_losses_after_position_guard"] = _loss_analysis(etf_trades)

    action_observations.to_csv(outdir / "ACTION_52W_ROTATION_PIT_OBSERVATIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    action_metrics.to_csv(outdir / "ACTION_52W_ROTATION_PIT_METRICS.csv", sep=";", index=False, encoding="utf-8-sig")
    etf_trades.to_csv(outdir / "ETF_MT_V20_8_1_PIT_TRADES.csv", sep=";", index=False, encoding="utf-8-sig")
    raw_etf_trades.to_csv(outdir / "ETF_MT_V20_8_1_PIT_TRADES_RAW_PRE_POSITION_GUARD.csv", sep=";", index=False, encoding="utf-8-sig")
    duplicates.to_csv(outdir / "ETF_MT_V20_8_1_BLOCKED_DUPLICATE_SIGNALS.csv", sep=";", index=False, encoding="utf-8-sig")

    overall = "SUCCESS" if action_summary.get("status") == "SUCCESS" or etf_summary.get("status") == "SUCCESS" else "BLOCKED"
    payload = {
        "version": "EXCEPTIONAL_PIT_OOS_2026_08_14_GOVERNED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall,
        "one_shot": True,
        "holdout_policy": {
            "final_holdout_from": HOLDOUT_START.date().isoformat(),
            "final_holdout_opened": False,
            "all_labels_exit_before": HOLDOUT_START.date().isoformat(),
            "reason": "Preserve the final holdout because this diagnostic may be followed by corrections and cannot serve as certification.",
        },
        "actions_52w_rotation": action_summary,
        "etf_mt_38_core": etf_summary,
        "not_backtested_as_certified": {
            "actions_v21_7_full_model": "Historical PIT fundamentals/consensus are incomplete.",
            "etf_mt_43_composite": "Five structural criteria do not have complete historical PIT snapshots.",
            "boursorama_signals": "Current Boursorama observations do not have sufficient historical PIT snapshots for OOS attribution.",
        },
        "governance": {
            "single_position_per_isin": True,
            "no_parameter_optimization_inside_backtest": True,
            "no_holdout_unlock": True,
            "no_real_orders": True,
            "results_are_diagnostic_not_certification": True,
        },
    }
    (outdir / "EXCEPTIONAL_PIT_OOS_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


if __name__ == "__main__":
    run()
