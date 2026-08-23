from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from v182.reporting.daily_context_baseline import (
    load_context_baseline,
    publish_context_baseline,
    publish_from_outputs,
)


def _canonical_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    actions = pd.DataFrame(
        {
            "isin": [f"FR{i:010d}" for i in range(1829)],
            "name": [f"Action {i}" for i in range(1829)],
            "last_close": [100.0] * 1829,
            "slow_consensus": [4.0] * 1829,
        }
    )
    etfs = pd.DataFrame(
        {
            "isin": [f"LU{i:010d}" for i in range(102)],
            "name": [f"ETF {i}" for i in range(102)],
            "last_close": [10.0] * 102,
            "slow_structure": ["UCITS"] * 102,
        }
    )
    return actions, etfs


def test_daily_baseline_write_never_advances_last_full_refresh_timestamp(tmp_path: Path):
    actions, etfs = _canonical_frames()
    friday = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    monday = friday + timedelta(days=3)
    first = publish_context_baseline(
        actions, etfs, tmp_path, full_refresh=True, profile="WEEKLY", now=friday
    )
    actions.loc[0, "last_close"] = 101.0
    second = publish_context_baseline(
        actions, etfs, tmp_path, full_refresh=False, profile="DAILY", now=monday
    )
    assert first["last_full_refresh_utc"] == second["last_full_refresh_utc"]
    assert second["last_snapshot_utc"] != second["last_full_refresh_utc"]
    loaded_actions, _, meta = load_context_baseline(tmp_path, now=monday)
    assert loaded_actions.loc[0, "last_close"] == 101.0
    assert meta["slow_source_freshness_extended_by_daily_write"] is False


def test_daily_baseline_stale_after_governed_max_age(tmp_path: Path):
    actions, etfs = _canonical_frames()
    friday = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)
    publish_context_baseline(actions, etfs, tmp_path, full_refresh=True, profile="WEEKLY", now=friday)
    with pytest.raises(RuntimeError, match="DAILY_FAST_BASELINE_STALE"):
        load_context_baseline(
            tmp_path,
            max_full_age_days=8,
            now=friday + timedelta(days=8, minutes=1),
        )


def test_daily_baseline_rejects_noncanonical_universe(tmp_path: Path):
    actions, etfs = _canonical_frames()
    with pytest.raises(RuntimeError, match="DAILY_FAST_ACTION_CANONICAL_COUNT_REQUIRED"):
        publish_context_baseline(
            actions.iloc[:-1], etfs, tmp_path, full_refresh=True, profile="WEEKLY"
        )


def test_weekly_publication_requires_passed_full_quality_gate(tmp_path: Path):
    actions, etfs = _canonical_frames()
    outputs = tmp_path / "outputs"
    audit = outputs / "audit"
    audit.mkdir(parents=True)
    actions.to_csv(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    etfs.to_csv(outputs / "V18.2_PEA_ETF_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")
    (audit / "V18.2_QUALITY_GATES.json").write_text(
        json.dumps({"passed": False, "expected_rows": {"ACTION": 1829, "ETF": 102}, "checks": []}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="FULL_QUALITY_GATE_NOT_PASSED"):
        publish_from_outputs(tmp_path)

    (audit / "V18.2_QUALITY_GATES.json").write_text(
        json.dumps({"passed": True, "expected_rows": {"ACTION": 1829, "ETF": 102}, "checks": [{"passed": True}]}),
        encoding="utf-8",
    )
    result = publish_from_outputs(tmp_path)
    assert result["full_quality_gate_passed"] is True
    assert result["actions_rows"] == 1829
    assert result["etf_rows"] == 102


def test_daily_workflow_uses_fast_path_and_keeps_full_screening_contract():
    workflow = Path(".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    assert "python -m v182.reporting.daily_fast_collection" in workflow
    assert "python -m v182.reporting.run\n" not in workflow
    assert "python -m v182.reporting.daily_tct_ct_runner" in workflow
    assert "python -m v182.reporting.tct_postmarket_bundle_run" in workflow
    assert "python -m v182.reporting.tactical_shadow_bundle_run" not in workflow
    assert "python -m v182.reporting.etf_structure_state_replay" not in workflow
    assert 'PEA_YF_INCREMENTAL_PERIOD: "5d"' in workflow
    assert 'PEA_DAILY_BASELINE_MAX_AGE_DAYS: "8"' in workflow
    # Weekly challenger state is transported but not recomputed Mon-Thu.
    assert "state/action_ct/" in workflow
    assert "state/action_ct_v22_1/" in workflow


def test_daily_fast_source_contains_no_slow_network_wave_calls():
    source = Path("src/v182/reporting/daily_fast_collection.py").read_text(encoding="utf-8")
    forbidden = (
        "wave4_info_actions(",
        "wave5_consensus_finnhub(",
        "wave6_etf_info(",
        "wave9_topdown(",
        "wave_public_table(",
    )
    for token in forbidden:
        assert token not in source
    assert "wave_history(actions" in source
    assert "wave3_local_features(" in source
    assert "build_rotation_observations(actions)" in source


def test_daily_postmarket_defers_pit_audit_but_not_operational_context():
    source = Path("src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert 'daily_fast = os.environ.get("PEA_RUN_PROFILE", "").strip().upper() == "DAILY_TACTICAL"' in source
    assert '("PIT_OHLC_V24.4.2"' in source
    assert '("POSTMARKET_CATALYST_V24.4.2"' in source
    assert 'deferred = ["PIT_LINEAGE_V24.4.2", "PIT_VALIDATOR_V24.4.2"]' in source
    assert '"daily_deferred_steps_have_decision_influence": False' in source
