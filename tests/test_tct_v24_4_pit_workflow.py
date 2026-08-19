from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_pit_validator_runs_after_snapshot_and_is_artifacted():
    workflow = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    assert "Run one next-session catalyst snapshot" in workflow
    assert "Validate accumulated V24.4 PIT ledger" in workflow
    assert "python -m v182.reporting.tct_v24_4_pit_validator_runtime" in workflow
    assert "TCT_V24_4_0_PIT_VALIDATION.json" in workflow
    assert "ANDROID_TCT_V24_4_PIT_VALIDATION.md" in workflow
    assert "TCT_V24_4_0_PIT_SLICES.csv" in workflow
    assert "TCT_V24_4_0_PREOPEN_POSTMARKET_CHANGES.csv" in workflow


def test_validation_gates_are_frozen_and_non_promoting():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_0_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    assert cfg["status"] == "PRE_REGISTERED_SHADOW_VALIDATION"
    assert cfg["maturity"]["minimum_labeled_preopen_rows"] >= 60
    assert cfg["maturity"]["minimum_observed_sessions"] >= 15
    assert cfg["acceptance"]["minimum_primary_improvement_checks_passed"] >= 2
    assert cfg["acceptance"]["movement_pass_does_not_automatically_promote"] is True
    assert cfg["governance"]["retuning_before_maturity_forbidden"] is True
    assert cfg["governance"]["holdout_locked"] is True
    assert cfg["governance"]["promotion_authority"] is False
    assert cfg["governance"]["production_influence"] == 0.0


def test_validator_has_no_production_authority():
    source = (ROOT / "src" / "v182" / "reporting" / "tct_v24_4_pit_validator.py").read_text(encoding="utf-8")
    runtime = (ROOT / "src" / "v182" / "reporting" / "tct_v24_4_pit_validator_runtime.py").read_text(encoding="utf-8")
    assert '"production_influence": 0.0' in source
    assert '"holdout_opened": False' in source
    assert '"promotion_authority": False' in source
    assert '"retuning_allowed": False' in source
    assert "MATURE_FOR_REVIEW_NOT_PROMOTION" in source
    assert "NOT_EVALUABLE_BEFORE_MATURITY" in source
    assert "Spearman rho = Pearson correlation of average ranks" in runtime
    lower = runtime.lower()
    assert "import scipy" not in lower
    assert "from scipy" not in lower
