import json
from pathlib import Path

from v182.reporting.android_risk_control_center import build_markdown


def test_android_risk_control_center_shows_context_only_governance(tmp_path):
    root = Path(tmp_path)
    (root / "outputs" / "audit").mkdir(parents=True)
    (root / "outputs" / "risk").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    (root / "outputs" / "audit" / "BETA_CORRELATION_RISK_ENGINE.json").write_text(
        json.dumps({"status": "SUCCESS", "coverage_pct": 87.5, "benchmark": {"status": "OK", "label": "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2"}}),
        encoding="utf-8",
    )
    (root / "outputs" / "risk" / "PORTFOLIO_RISK_SUMMARY.json").write_text(
        json.dumps({"portfolio_beta_252d": 1.1, "portfolio_downside_beta_252d": 1.25, "diversification_warning": "AMBER", "top_engine": "AI_TECH", "top_engine_share_pct": 44.0, "systematic_stress_scenarios_pct": {"-10.0": -12.5}}),
        encoding="utf-8",
    )
    (root / "config" / "BETA_RISK_ROBUST_VALIDATION_STATUS.json").write_text(
        json.dumps({"status": "ROBUST_VALIDATED_CONTEXT_ONLY_KEEP_ALL_SIZING_SHADOW", "production_policy": {"score_influence": 0.0, "decision_influence": 0.0, "sizing_execution_influence": 0.0, "stop_loss_influence": 0.0}}),
        encoding="utf-8",
    )
    text = build_markdown(root)
    assert "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2" in text
    assert "AMBER" in text
    assert "Sizing bêta permanent: **REJETÉ**" in text
    assert "Influence sizing: 0.0" in text
