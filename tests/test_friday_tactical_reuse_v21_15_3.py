from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from v182.reporting import friday_tactical_reuse_runner as friday


ROOT = Path(__file__).resolve().parents[1]


def _decisions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"asset_class": "ACTION", "horizon": "TCT", "isin": "FR0001", "decision": "WATCH", "score": 80},
            {"asset_class": "ACTION", "horizon": "CT", "isin": "FR0002", "decision": "BUY_CANDIDATE", "score": 84},
            {"asset_class": "ACTION", "horizon": "MT", "isin": "FR0003", "decision": "WATCH", "score": 82},
            {"asset_class": "ETF", "horizon": "CT", "isin": "FR0010", "decision": "WATCH", "score": 75},
            {"asset_class": "ETF", "horizon": "MT", "isin": "FR0011", "decision": "WATCH", "score": 79},
        ]
    )


def _governed() -> pd.DataFrame:
    frame = _decisions().copy()
    frame["v21_8_entry_state"] = "WAIT"
    frame["v21_8_position_state"] = "PROTECT"
    frame["v21_8_entry_reasons"] = "TEST"
    frame["v21_8_position_reasons"] = "TEST"
    return frame


def test_scope_reuses_only_action_tct_action_ct_and_etf_ct() -> None:
    scoped = friday._scope(_decisions())
    keys = set(zip(scoped["asset_class"], scoped["horizon"], scoped["isin"]))
    assert keys == {
        ("ACTION", "TCT", "FR0001"),
        ("ACTION", "CT", "FR0002"),
        ("ETF", "CT", "FR0010"),
    }


def test_scope_fails_closed_on_duplicate_decision_key() -> None:
    frame = pd.concat([_decisions(), _decisions().iloc[[0]]], ignore_index=True)
    with pytest.raises(RuntimeError, match="FRIDAY_TACTICAL_REUSE_DUPLICATE_DECISION_KEYS"):
        friday._scope(frame)


def test_existing_governance_is_joined_without_reclassification() -> None:
    scoped = friday._scope(_decisions())
    governed = friday._attach_existing_governance(scoped, _governed())
    assert len(governed) == 3
    assert governed["v21_8_entry_state"].eq("WAIT").all()
    assert governed["v21_8_position_state"].eq("PROTECT").all()
    assert governed["decision"].tolist() == scoped["decision"].tolist()
    assert governed["score"].tolist() == scoped["score"].tolist()


def test_existing_governance_fails_closed_when_one_tactical_key_is_missing() -> None:
    scoped = friday._scope(_decisions())
    governed = _governed().query("isin != 'FR0002'").copy()
    with pytest.raises(RuntimeError, match="FRIDAY_TACTICAL_REUSE_GOVERNANCE_JOIN_MISSING"):
        friday._attach_existing_governance(scoped, governed)


def test_current_snapshot_requires_every_row_to_be_current_utc_day() -> None:
    now = datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc)
    current = pd.DataFrame({"generated_at_utc": ["2026-08-21T20:00:00+00:00", "2026-08-21T20:05:00+00:00"]})
    friday._assert_current_snapshot(current, "TEST", now)

    mixed = pd.DataFrame({"generated_at_utc": ["2026-08-21T20:00:00+00:00", "2026-08-20T20:05:00+00:00"]})
    with pytest.raises(RuntimeError, match="FRIDAY_TACTICAL_REUSE_NOT_CURRENT_DAY"):
        friday._assert_current_snapshot(mixed, "TEST", now)

    invalid = pd.DataFrame({"generated_at_utc": ["2026-08-21T20:00:00+00:00", "invalid"]})
    with pytest.raises(RuntimeError, match="FRIDAY_TACTICAL_REUSE_INVALID_TIMESTAMP"):
        friday._assert_current_snapshot(invalid, "TEST", now)


def test_friday_runner_has_no_duplicate_score_tct_or_v21_8_execution() -> None:
    source = (ROOT / "src" / "v182" / "reporting" / "friday_tactical_reuse_runner.py").read_text(encoding="utf-8")
    assert "build_tct_baseline(" not in source
    assert "build_exact_timing_snapshot(" not in source
    assert "decisions_from_scores(" not in source
    assert "apply_governance(" not in source
    assert "_persist_temporal_state(" not in source
    assert "enrich_selected_rows(" in source
    assert '"v21_8_reapply_skipped": True' in source
    assert '"temporal_state_advanced_second_time": False' in source


def test_weekly_and_daily_workflows_keep_distinct_tactical_paths() -> None:
    weekly = (ROOT / ".github" / "workflows" / "committee_master_daily.yml").read_text(encoding="utf-8")
    daily = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "python -m v182.reporting.friday_tactical_reuse_runner" in weekly
    assert "python -m v182.reporting.daily_tct_ct_runner" not in weekly
    assert "python -m v182.reporting.daily_tct_ct_runner" in daily
    assert "python -m v182.reporting.friday_tactical_reuse_runner" not in daily
