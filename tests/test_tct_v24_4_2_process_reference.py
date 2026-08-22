from __future__ import annotations

from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_v242_referential_is_numeric_source_of_truth_and_cdc_references_it():
    ref = (ROOT / "docs" / "TCT_REFERENTIEL_V24_4_2_FINAL.md").read_text(encoding="utf-8")
    cdc = (ROOT / "docs" / "TCT_CDC_V24_4_2_FINAL.md").read_text(encoding="utf-8")
    assert "source humaine unique des valeurs numériques" in ref
    assert "Il ne constitue pas une seconde source de valeurs numériques" in cdc
    assert "V24.4.2_ONLY_NO_MIX_WITH_PRIOR_EPOCHS" in ref
    assert "production canonique V21.8.1" in cdc


def test_v242_workflow_runs_only_new_catalyst_epoch():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    assert "tct_next_session_catalyst_run_v24_4_2" in workflow
    assert "tct_v24_4_2_pit_lineage" in workflow
    assert "tct_v24_4_2_pit_validator" in workflow
    assert "tct_next_session_catalyst_run_v24_4_1" not in workflow
    assert "tct_v24_4_1_pit_lineage" not in workflow
    assert "5m" not in workflow.lower()
    assert "1m" not in workflow.lower()


def test_daily_workflow_persists_ohlc_not_close_only_v241_and_runs_v2431_before_it():
    workflow = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    bundle = (ROOT / "src" / "v182" / "reporting" / "tactical_shadow_bundle_run.py").read_text(encoding="utf-8")
    assert "tct_pit_ohlc_ledger_v24_4_2" in workflow
    assert "TCT_DAILY_OHLC_LEDGER.csv" in workflow
    assert "tct_pit_close_ledger_v24_4_1" not in workflow
    assert "daily_tct_ct_runner" in workflow
    assert "python -m v182.reporting.tactical_shadow_bundle_run" in workflow
    assert "tct_trader.run(root=root)" in bundle
    assert workflow.index("python -m v182.reporting.tactical_shadow_bundle_run") < workflow.index("python -m v182.reporting.tct_pit_ohlc_ledger_v24_4_2")


def test_calibration_is_not_wired_into_any_workflow():
    needle = "tct_v24_4_2_weight_calibration"
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert needle not in path.read_text(encoding="utf-8")


def test_v242_config_has_fail_closed_governance_runtime_budget_and_inactive_secondary_contract():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))
    assert cfg["runtime_budget"]["preopen_seconds"] == 180
    assert cfg["runtime_budget"]["postmarket_seconds"] == 600
    assert cfg["runtime_budget"]["fail_closed_on_budget_exhaustion"] is True
    assert cfg["pit_lineage"]["minimum_snapshot_outcome_coverage"] == 0.80
    assert cfg["news"]["secondary_source_enabled"] is False
    assert cfg["news"]["secondary_activation_requires_qualification"] is True
    assert cfg["governance"]["retuning_allowed"] is False
    assert cfg["governance"]["promotion_authority"] is False
    assert cfg["governance"]["real_orders_enabled"] is False
    scope = cfg["candidate_selection"]["preselection_scope"]
    assert scope["enabled"] is True
    assert scope["tct_top_n"] == 20
    assert scope["action_ct_top_n"] == 20
    assert scope["union_max"] == 40


def test_external_audit_implementation_matrix_documents_deferred_items():
    text = (ROOT / "docs" / "TCT_AUDIT_IMPLEMENTATION_V24_4_2_2026-08-21.md").read_text(encoding="utf-8")
    for finding in ["F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08", "F09", "F10", "F11", "F12"]:
        assert finding in text
    assert "provider secondaire non activé" in text
    assert "aucun poids modifié" in text
