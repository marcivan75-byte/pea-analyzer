from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd

from v182.reporting.action_ct_shadow_run_v22_0 import (
    _append_first_snapshots,
    _label_outcomes,
    _merge_exit_state,
    _temporal_exit_confirmation,
    _validation_payload,
)


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ACTION_CT_V22_0_0_SHADOW.json").read_text(encoding="utf-8"))


def _snapshot(**overrides) -> dict:
    row = {
        "version": "ACTION_CT_V22.0.0_DAILY_WEEKLY_CONFLUENCE_SHADOW",
        "snapshot_date": "2026-08-21",
        "isin": "FR0000000001",
        "reference_close": 100.0,
        "baseline_ct_score": 78.0,
        "baseline_ct_decision": "BUY_CANDIDATE",
        "entry_score": 82.0,
        "entry_state": "ENTRY_READY_SHADOW",
        "entry_confirmation_count": 4,
        "exit_risk_score": 20.0,
        "exit_state": "HOLD_SUPPORTIVE_SHADOW",
        "trend_score": 85.0,
        "momentum_score": 80.0,
        "weekly_score": 75.0,
        "sector_context_score": 72.0,
        "volume_score": 70.0,
        "catalyst_score": 65.0,
        "warnings": "",
    }
    row.update(overrides)
    return row


def test_pit_first_snapshot_is_immutable_and_mutation_fails_closed():
    first = pd.DataFrame([_snapshot()])
    ledger, added, mismatches = _append_first_snapshots(pd.DataFrame(), first)
    assert added == 1
    assert mismatches == []
    same, added2, mismatches2 = _append_first_snapshots(ledger, first)
    assert added2 == 0
    assert mismatches2 == []
    assert len(same) == 1
    mutated = pd.DataFrame([_snapshot(entry_score=91.0)])
    final, added3, mismatches3 = _append_first_snapshots(ledger, mutated)
    assert added3 == 0
    assert mismatches3 == ["2026-08-21:FR0000000001"]
    assert float(final.iloc[0]["entry_score"]) == 82.0


def test_same_day_rerun_does_not_confirm_exit_but_prior_session_does():
    output = pd.DataFrame([
        {"isin": "FR1", "snapshot_date": "2026-08-21", "exit_state_raw": "EXIT_RISK_HIGH_CANDIDATE_SHADOW", "status": "SUCCESS_SHADOW"}
    ])
    same_day = pd.DataFrame([{"isin": "FR1", "snapshot_date": "2026-08-21", "exit_state": "EXIT_WATCH_SHADOW"}])
    out_same = _temporal_exit_confirmation(output, same_day)
    assert out_same.iloc[0]["exit_state"] == "EXIT_WATCH_SHADOW"
    assert out_same.iloc[0]["exit_temporal_reason"] == "AWAIT_PRIOR_SESSION_CONFIRMATION"

    prior_day = pd.DataFrame([{"isin": "FR1", "snapshot_date": "2026-08-20", "exit_state": "EXIT_WATCH_SHADOW"}])
    out_prior = _temporal_exit_confirmation(output, prior_day)
    assert out_prior.iloc[0]["exit_state"] == "EXIT_RISK_HIGH_SHADOW"
    assert "PRIOR_SESSION" in out_prior.iloc[0]["exit_temporal_reason"]


def test_exit_state_merge_keeps_latest_market_date_only():
    previous = pd.DataFrame([{"isin": "FR1", "snapshot_date": "2026-08-20", "exit_state": "EXIT_WATCH_SHADOW"}])
    current = pd.DataFrame([
        {"isin": "FR1", "snapshot_date": "2026-08-21", "exit_state": "HOLD_SUPPORTIVE_SHADOW", "status": "SUCCESS_SHADOW"},
        {"isin": "FR2", "snapshot_date": np.nan, "exit_state": "DATA_INSUFFICIENT", "status": "DATA_INSUFFICIENT"},
    ])
    state = _merge_exit_state(previous, current)
    assert len(state) == 1
    assert state.iloc[0]["snapshot_date"] == "2026-08-21"
    assert state.iloc[0]["exit_state"] == "HOLD_SUPPORTIVE_SHADOW"


def test_outcomes_are_labeled_from_next_session_open_without_lookahead_at_snapshot():
    idx = pd.bdate_range("2026-08-24", periods=45)
    close = np.linspace(101.0, 145.0, len(idx))
    history = pd.DataFrame(
        {
            "open": close - 1.0,
            "high": close + 1.5,
            "low": close - 2.0,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=idx,
    )
    ledger = pd.DataFrame([_snapshot(snapshot_date="2026-08-21")])
    labeled = _label_outcomes(ledger, {"FR0000000001": history}, [10, 20, 40])
    assert labeled.iloc[0]["entry_date"] == "2026-08-24"
    assert float(labeled.iloc[0]["entry_open"]) == 100.0
    assert pd.notna(labeled.iloc[0]["return_10d_pct"])
    assert pd.notna(labeled.iloc[0]["return_20d_pct"])
    assert pd.notna(labeled.iloc[0]["return_40d_pct"])
    assert float(labeled.iloc[0]["mfe_20d_pct"]) > 0
    assert float(labeled.iloc[0]["mae_20d_pct"]) >= -2.0


def test_validator_stays_immature_before_preregistered_sample_size():
    ledger = pd.DataFrame([
        {
            **_snapshot(),
            "return_20d_pct": 5.0,
            "mae_20d_pct": -2.0,
        }
    ])
    validation = _validation_payload(ledger, _cfg())
    assert validation["status"] == "IMMATURE_SHADOW"
    assert validation["promotion_authority"] is False
