from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.risk import entry_exit_governance_v21_8 as governance


ROOT = Path(__file__).resolve().parents[1]


def _multifactor_row(**overrides) -> pd.Series:
    values = {
        "asset_class": "ACTION",
        "horizon": "CT",
        "isin": "FR0001",
        "score": 85.0,
        "decision": "BUY_CANDIDATE",
        "dist_sma50": -0.02,
        "dist_sma200": -0.01,
        "ret_21d": -0.03,
        "previous_v21_8_position_state": "PROTECT",
        "generated_at_utc": "2026-08-21T21:00:00+00:00",
        "previous_v21_8_observed_at_utc": "2026-08-21T20:00:00+00:00",
    }
    values.update(overrides)
    return pd.Series(values)


def test_same_day_protect_rerun_cannot_confirm_exit() -> None:
    state, reasons = governance.classify_position(_multifactor_row(), {})
    assert state == "PROTECT"
    assert "SAME_DAY_RERUN_NOT_TEMPORAL_CONFIRMATION" in reasons


def test_later_day_protect_can_confirm_exit() -> None:
    row = _multifactor_row(
        generated_at_utc="2026-08-22T21:00:00+00:00",
        previous_v21_8_observed_at_utc="2026-08-21T21:00:00+00:00",
    )
    state, reasons = governance.classify_position(row, {})
    assert state == "EXIT"
    assert "MULTIFACTOR_DETERIORATION_CONFIRMED_AFTER_PROTECT" in reasons


def test_explicit_deterioration_confirmation_still_allows_exit_same_day() -> None:
    row = _multifactor_row(deterioration_confirmed=True)
    state, _reasons = governance.classify_position(row, {})
    assert state == "EXIT"


def test_existing_exit_remains_confirmed_without_waiting_another_day() -> None:
    row = _multifactor_row(previous_v21_8_position_state="EXIT")
    state, _reasons = governance.classify_position(row, {})
    assert state == "EXIT"


def test_legacy_direct_previous_protect_without_timestamp_keeps_backward_contract() -> None:
    row = _multifactor_row().drop(labels=["previous_v21_8_observed_at_utc"])
    state, _reasons = governance.classify_position(row, {})
    assert state == "EXIT"


def test_operational_attachment_of_legacy_state_without_date_fails_closed_to_protect() -> None:
    decisions = pd.DataFrame([_multifactor_row().drop(labels=["previous_v21_8_position_state", "previous_v21_8_observed_at_utc"]).to_dict()])
    key = ("ACTION", "CT", "FR0001")
    attached = governance._attach_temporal_state(decisions, {key: "PROTECT"}, {})
    assert "previous_v21_8_observed_at_utc" in attached.columns
    assert pd.isna(attached.loc[0, "previous_v21_8_observed_at_utc"])
    state, _reasons = governance.classify_position(attached.iloc[0], {})
    assert state == "PROTECT"


def test_temporal_state_round_trip_persists_observed_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "state.csv"
    governed = pd.DataFrame(
        [
            {
                "asset_class": "ACTION",
                "horizon": "CT",
                "isin": "FR0001",
                "v21_8_position_state": "PROTECT",
                "generated_at_utc": "2026-08-21T21:00:00+00:00",
            }
        ]
    )
    assert governance._persist_temporal_state(governed, path) == 1
    assert governance._load_temporal_state(path)[('ACTION', 'CT', 'FR0001')] == "PROTECT"
    observed = governance._load_temporal_state_observed_at(path)
    assert observed[('ACTION', 'CT', 'FR0001')].startswith("2026-08-21T21:00:00")


def test_daily_operational_runner_loads_state_timestamp_before_governance() -> None:
    source = (ROOT / "src" / "v182" / "reporting" / "daily_tct_ct_runner.py").read_text(encoding="utf-8")
    assert "_load_temporal_state_observed_at" in source
    assert "_attach_temporal_state(decisions, previous, previous_observed_at)" in source
    assert '"same_day_rerun_can_confirm_exit": False' in source


def test_governance_audit_declares_same_day_exit_confirmation_forbidden() -> None:
    source = (ROOT / "src" / "v182" / "risk" / "entry_exit_governance_v21_8.py").read_text(encoding="utf-8")
    assert '"same_day_rerun_can_confirm_exit": False' in source
    assert 'STATE_OBSERVED_AT_FIELD = "v21_8_observed_at_utc"' in source
