from __future__ import annotations

import json

import pandas as pd
import pytest

from v182.reporting import committee_master_run, etf_mt_v2081_run, waves
from v182.reporting import weekly_unified_super_runner_v22 as v22


def test_v22_disables_wave09_and_repairs_etf_mt_identity(monkeypatch, tmp_path):
    original_wave9 = waves.wave9_topdown
    original_attach = etf_mt_v2081_run._attach_selected_source_context
    original_overlay = committee_master_run.overlay_etf_mt

    def fake_previous_run(root):
        obs_a, obs_e, diag = waves.wave9_topdown(
            pd.DataFrame({"isin": ["FR0000000001"]}),
            pd.DataFrame({"isin": ["LU0000000001"]}),
            {},
            "unused",
        )
        assert obs_a == []
        assert obs_e == []
        assert diag["status"] == "DISABLED_BY_V22_RUNTIME_BASELINE"
        assert diag["external_calls"] == 0

        dynamic = pd.DataFrame(
            {
                "instrument_id": ["LU0000000001"],
                "dynamic_selected": [False],
            }
        )
        enriched, _ = etf_mt_v2081_run._attach_selected_source_context(
            dynamic,
            pd.DataFrame({"isin": ["LU0000000001"], "name": ["ETF Test"]}),
            root,
        )
        assert enriched.loc[0, "isin"] == "LU0000000001"

        ranking = pd.DataFrame(
            {
                "instrument_id": ["LU0000000001"],
                "score_final": [88.0],
                "coverage_pct": [100.0],
                "status": ["SCORABLE"],
                "decision": ["REFERENCE_CANDIDATE"],
            }
        )
        overlaid = committee_master_run.overlay_etf_mt(
            pd.DataFrame({"isin": ["LU0000000001"], "name": ["ETF Test"]}),
            ranking,
        )
        assert overlaid.loc[0, "isin"] == "LU0000000001"
        return {"status": "SUCCESS"}

    monkeypatch.setattr(v22.previous, "run", fake_previous_run)
    payload = v22.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    audit = json.loads(
        (tmp_path / "outputs" / "audit" / "WEEKLY_UNIFIED_SUPER_RUNTIME_V22.json").read_text()
    )
    assert audit["wave09_disabled"] is True
    assert audit["wave09_external_calls"] == 0
    assert audit["wave09_calls_intercepted"] == 1
    assert audit["etf_mt_isin_materializations"] == 1
    assert audit["committee_mt_isin_materializations"] == 1

    assert waves.wave9_topdown is original_wave9
    assert etf_mt_v2081_run._attach_selected_source_context is original_attach
    assert committee_master_run.overlay_etf_mt is original_overlay


def test_v22_restores_wrappers_after_failure(monkeypatch, tmp_path):
    original_wave9 = waves.wave9_topdown
    original_attach = etf_mt_v2081_run._attach_selected_source_context
    original_overlay = committee_master_run.overlay_etf_mt

    def fail_previous_run(root):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(v22.previous, "run", fail_previous_run)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        v22.run(tmp_path)

    assert waves.wave9_topdown is original_wave9
    assert etf_mt_v2081_run._attach_selected_source_context is original_attach
    assert committee_master_run.overlay_etf_mt is original_overlay

    audit = json.loads(
        (tmp_path / "outputs" / "audit" / "WEEKLY_UNIFIED_SUPER_RUNTIME_V22.json").read_text()
    )
    assert audit["status"] == "FAILED_EXCEPTION"
    assert "RuntimeError" in audit["error"]
