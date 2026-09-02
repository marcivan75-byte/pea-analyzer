from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

LEGACY_EXIT_ROLE = "BACKTEST_REPLAY_ONLY"
CURRENT_ENTRY_EXIT_AUTHORITY = "V21.8_ENTRY_EXIT_BASELINE"
HISTORY_SESSION_POLICY = "OBSERVED_NUMERIC_CLOSE_ONLY"


def _real_close_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy().sort_index()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(-1)
    close_columns = [column for column in out.columns if str(column).strip().lower() == "close"]
    if not close_columns:
        return pd.DataFrame()
    close_column = close_columns[0]
    if close_column != "Close":
        out = out.rename(columns={close_column: "Close"})
    close = pd.to_numeric(out["Close"], errors="coerce")
    return out.loc[close.notna()].copy()


def sanitize_histories(histories: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    clean: dict[str, pd.DataFrame] = {}
    for instrument_id, frame in histories.items():
        observed = _real_close_frame(frame)
        if not observed.empty:
            clean[str(instrument_id)] = observed
    return clean


def assert_grok_reference_contract(config: Mapping) -> None:
    if config.get("module") != "ETF_GROK_HIGH_PRECISION":
        raise ValueError("ETF GROK config module mismatch")
    if config.get("status") != "ACTIVE_REFERENCE_SCORING_NO_REAL_ORDERS":
        raise ValueError("ETF GROK reference must remain no-real-orders")
    scope = config.get("scope") or {}
    if scope.get("t1_t2_enabled") is not False:
        raise ValueError("T1/T2 are forbidden for ETF GROK")
    if scope.get("pit_required") is not True or scope.get("anti_lookahead_required") is not True:
        raise ValueError("ETF GROK must remain PIT and anti-lookahead")

    dynamic = config.get("dynamic_criteria") or {}
    if len(dynamic) != 38:
        raise ValueError(f"ETF GROK reference requires 38 criteria, got {len(dynamic)}")
    total = sum(float(spec.get("backtested_weight", 0.0)) for spec in dynamic.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"ETF GROK reference weight total drift: {total}")

    score = config.get("score") or {}
    if float(score.get("score_raw_weight", -1)) != 0.55:
        raise ValueError("ETF GROK reference score_raw_weight drift")
    if float(score.get("cross_section_rank_weight", -1)) != 0.45:
        raise ValueError("ETF GROK reference cross_section_rank_weight drift")
    if float(score.get("selection_threshold", -1)) != 82.0:
        raise ValueError("ETF GROK reference selection_threshold drift")
    if int(score.get("top_n", -1)) != 2:
        raise ValueError("ETF GROK reference top_n drift")

    exit_policy = config.get("exit_policy") or {}
    expected_exit = {"target_return": 0.04, "hard_stop_return": -0.18, "max_holding_sessions": 168}
    for key, expected in expected_exit.items():
        if abs(float(exit_policy.get(key, 999.0)) - float(expected)) > 1e-12:
            raise ValueError(f"ETF GROK historical replay exit drift: {key}")
    if exit_policy.get("role") != LEGACY_EXIT_ROLE:
        raise ValueError("ETF GROK legacy exit policy must remain replay-only")

    gates = config.get("quality_gates") or {}
    if gates.get("require_all_backtested_dynamic_criteria") is not True:
        raise ValueError("ETF GROK complete-38 gate must remain enabled")
    if gates.get("structural_overlay_can_promote_signal") is not False:
        raise ValueError("ETF structural overlay cannot promote the GROK reference signal")


def score_snapshot_integrity(histories: Mapping[str, pd.DataFrame], etf_reference: pd.DataFrame, grok_config: Mapping) -> tuple[pd.DataFrame, dict]:
    from v182.features.etf_grok_v2081 import score_snapshot

    assert_grok_reference_contract(grok_config)
    clean = sanitize_histories(histories)
    if not clean:
        raise ValueError("no ETF GROK histories contain an observed Close")
    snapshot, summary = score_snapshot(clean, etf_reference, grok_config)
    if "selected" in snapshot.columns:
        snapshot.loc[snapshot["selected"].fillna(False).astype(bool), "decision"] = "REFERENCE_CANDIDATE"
    summary = dict(summary)
    target = float(grok_config.get("objective", {}).get("target_win_rate", 0.0))
    summary.update({
        "raw_input_histories": len(histories), "universe_histories": len(clean),
        "target_win_rate": target, "research_target_win_rate": target,
        "promotion_allowed": False, "real_orders_allowed": False,
        "history_session_policy": HISTORY_SESSION_POLICY,
        "legacy_exit_policy_role": LEGACY_EXIT_ROLE,
        "current_entry_exit_authority": CURRENT_ENTRY_EXIT_AUTHORITY,
        "clone_process": "ETF_GROK"
    })
    return snapshot, summary
