from __future__ import annotations

from datetime import datetime, timezone
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
    run as run_action_ct,
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


def test_full_runner_builds_shadow_outputs_and_first_pit_snapshot(tmp_path: Path):
    root = tmp_path
    (root / "config").mkdir(parents=True)
    (root / "outputs" / "daily_tct_ct").mkdir(parents=True)
    (root / "data" / "cache" / "actions").mkdir(parents=True)

    (root / "config" / "ACTION_CT_V22_0_0_SHADOW.json").write_text(
        json.dumps(_cfg(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    master = pd.DataFrame([
        {
            "isin": "FR0000000001",
            "name": "Synthetic Action",
            "yahoo_ticker": "SYN.PA",
            "relative_strength": 80.0,
            "sector_rotation_score": 82.0,
            "action_catchup_score": 75.0,
            "market_high_regime_score": 70.0,
            "valuation_discount_score": 20.0,
            "consensus_score_100_v21": 78.0,
            "target_upside_pct_v21": 15.0,
            "consensus_delta_4w": 2.0,
            "net_upgrades_30d_v21": 2.0,
            "news_catalyst_score": 70.0,
            "earnings_catalyst_score": 65.0,
            "days_to_earnings": 10.0,
        }
    ])
    master.to_csv(
        root / "outputs" / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )

    baseline = pd.DataFrame([
        {
            "asset_class": "ACTION",
            "horizon": "CT",
            "isin": "FR0000000001",
            "score": 78.0,
            "coverage_pct": 90.0,
            "status": "OK",
            "decision": "BUY_CANDIDATE",
        }
    ])
    baseline.to_csv(
        root / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )

    idx = pd.bdate_range(end="2026-08-21", periods=180)
    close = np.linspace(100.0, 150.0, len(idx)) + np.sin(np.linspace(0, 10, len(idx)))
    volume = np.full(len(idx), 1_000_000.0)
    volume[-5:] = 1_350_000.0
    history = pd.DataFrame(
        {
            "open": close * 0.996,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": volume,
            "yahoo_ticker": "SYN.PA",
        },
        index=idx,
    )
    history.to_parquet(root / "data" / "cache" / "actions" / "history_synthetic.parquet")

    payload = run_action_ct(root=root, now=datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc))

    assert payload["status"] == "SUCCESS_SHADOW"
    assert payload["rows"] == 1
    assert payload["daily_histories_found"] == 1
    assert payload["pit_snapshots_added"] == 1
    assert payload["pit_ledger_rows"] == 1
    assert payload["pit_validation_status"] == "IMMATURE_SHADOW"
    assert payload["baseline_unchanged"] is True
    assert payload["t1_t2_used"] is False
    assert payload["fixed_take_profit_enabled"] is False
    assert payload["fixed_stop_loss_enabled"] is False
    assert payload["real_orders_enabled"] is False

    shadow_path = root / "outputs" / "daily_tct_ct" / "ACTION_CT_V22_0_0_SHADOW.csv"
    ledger_path = root / "state" / "action_ct" / "ACTION_CT_V22_0_0_PIT_LEDGER.csv"
    audit_path = root / "outputs" / "audit" / "ACTION_CT_V22_0_0_AUDIT.json"
    mobile_path = root / "outputs" / "mobile" / "ANDROID_ACTION_CT_V22_SHADOW.md"
    assert shadow_path.exists()
    assert ledger_path.exists()
    assert audit_path.exists()
    assert mobile_path.exists()

    shadow = pd.read_csv(shadow_path, sep=";", encoding="utf-8-sig")
    ledger = pd.read_csv(ledger_path, sep=";", encoding="utf-8-sig")
    assert len(shadow) == 1
    assert len(ledger) == 1
    assert shadow.iloc[0]["baseline_ct_decision"] == "BUY_CANDIDATE"
    assert bool(shadow.iloc[0]["real_orders_enabled"]) is False
    assert isinstance(ledger.iloc[0]["snapshot_fingerprint"], str)
    assert len(ledger.iloc[0]["snapshot_fingerprint"]) == 64
