from __future__ import annotations

from pathlib import Path
import json

import pytest

from v182.reporting import tct_postmarket_bundle_run as bundle


ROOT = Path(__file__).resolve().parents[1]


def test_postmarket_bundle_preserves_exact_step_order_and_phase(monkeypatch, tmp_path):
    calls: list[str] = []

    def ohlc(*, root):
        calls.append("OHLC")
        return {"status": "SUCCESS_PIT_OHLC_LEDGER", "version": "ohlc"}

    def catalyst(*, root, phase=None, now=None):
        del now
        assert phase == "POSTMARKET"
        calls.append("CATALYST")
        return {"status": "SUCCESS_SHADOW", "version": "catalyst"}

    def lineage(*, root):
        calls.append("LINEAGE")
        return {"status": "SUCCESS_PIT_LINEAGE", "version": "lineage"}

    def validator(*, root):
        calls.append("VALIDATOR")
        return {"status": "SUCCESS_PIT_VALIDATION", "version": "validator"}

    monkeypatch.setattr(bundle.ohlc_ledger, "run", ohlc)
    monkeypatch.setattr(bundle.catalyst, "run", catalyst)
    monkeypatch.setattr(bundle.lineage, "run", lineage)
    monkeypatch.setattr(bundle.validator, "run", validator)

    payload = bundle.run(tmp_path)

    assert calls == ["OHLC", "CATALYST", "LINEAGE", "VALIDATOR"]
    assert payload["status"] == "SUCCESS_POSTMARKET_SINGLE_PROCESS"
    assert payload["postmarket_phase_explicit"] is True
    assert payload["previous_python_processes"] == 4
    assert payload["current_python_processes"] == 1
    assert payload["interpreter_startups_avoided"] == 3


def test_postmarket_bundle_attempts_later_steps_after_prior_failure(monkeypatch, tmp_path):
    calls: list[str] = []

    def broken_ohlc(*, root):
        calls.append("OHLC")
        raise RuntimeError("synthetic OHLC failure")

    def catalyst(*, root, phase=None, now=None):
        del now
        assert phase == "POSTMARKET"
        calls.append("CATALYST")
        return {"status": "SUCCESS_SHADOW", "version": "catalyst"}

    def lineage(*, root):
        calls.append("LINEAGE")
        return {"status": "SUCCESS_PIT_LINEAGE", "version": "lineage"}

    def validator(*, root):
        calls.append("VALIDATOR")
        return {"status": "SUCCESS_PIT_VALIDATION", "version": "validator"}

    monkeypatch.setattr(bundle.ohlc_ledger, "run", broken_ohlc)
    monkeypatch.setattr(bundle.catalyst, "run", catalyst)
    monkeypatch.setattr(bundle.lineage, "run", lineage)
    monkeypatch.setattr(bundle.validator, "run", validator)

    with pytest.raises(RuntimeError, match="PIT_OHLC_V24.4.2"):
        bundle.run(tmp_path)

    assert calls == ["OHLC", "CATALYST", "LINEAGE", "VALIDATOR"]
    audit = json.loads((tmp_path / "outputs/audit/POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json").read_text(encoding="utf-8"))
    assert audit["status"] == "POSTMARKET_SINGLE_PROCESS_WITH_STEP_ERRORS"
    assert audit["all_steps_attempted"] is True
    assert [item["step"] for item in audit["errors"]] == ["PIT_OHLC_V24.4.2"]


def test_daily_and_weekly_use_single_postmarket_process_but_preopen_stays_autonomous():
    daily = (ROOT / ".github/workflows/committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    weekly = (ROOT / ".github/workflows/committee_master_daily.yml").read_text(encoding="utf-8")
    preopen = (ROOT / ".github/workflows/tct_next_session_context.yml").read_text(encoding="utf-8")

    direct = (
        "python -m v182.reporting.tct_pit_ohlc_ledger_v24_4_2",
        "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_2",
        "python -m v182.reporting.tct_v24_4_2_pit_lineage",
        "python -m v182.reporting.tct_v24_4_2_pit_validator",
    )
    for workflow in (daily, weekly):
        assert workflow.count("python -m v182.reporting.tct_postmarket_bundle_run") == 1
        for command in direct:
            assert command not in workflow
        assert "POSTMARKET_BUNDLE_RUNTIME_V21_13_12.json" in workflow

    assert 'cron: "40 6 * * 1-5"' in preopen
    assert "TCT_CATALYST_PHASE: ${{ github.event.inputs.phase }}" in preopen
    assert "python -m v182.reporting.tct_next_session_catalyst_run_v24_4_2" in preopen
    assert "python -m v182.reporting.tct_postmarket_bundle_run" not in preopen


def test_bundle_changes_runtime_only_not_financial_or_pit_contract():
    source = (ROOT / "src/v182/reporting/tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    for invariant in (
        '"decision_logic_changed": False',
        '"criteria_changed": False',
        '"weights_changed": False',
        '"thresholds_changed": False',
        '"candidate_scope_changed": False',
        '"news_query_policy_changed": False',
        '"pit_logic_changed": False',
        '"fingerprint_logic_changed": False',
        '"holdout_opened": False',
        '"real_orders_enabled": False',
    ):
        assert invariant in source
    assert 'catalyst.run(root=root, phase="POSTMARKET")' in source
    assert source.index("ohlc_ledger.run(root=root)") < source.index('catalyst.run(root=root, phase="POSTMARKET")')
    assert source.index('catalyst.run(root=root, phase="POSTMARKET")') < source.index("lineage.run(root=root)")
    assert source.index("lineage.run(root=root)") < source.index("validator.run(root=root)")
