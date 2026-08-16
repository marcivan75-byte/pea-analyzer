import json
from pathlib import Path

from v182.reporting.android_ci_control_center import build_markdown


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_android_control_center_exposes_robust_beta_risk(tmp_path):
    root = Path(tmp_path)
    _write_json(
        root / "outputs" / "unified" / "UNIFIED_SUMMARY_LATEST.json",
        {
            "status": "SUCCESS",
            "generated_at_utc": "2026-08-16T13:00:00Z",
            "steps": {
                "committee": {"status": "SUCCESS"},
                "beta_correlation_risk": {"status": "SUCCESS"},
                "stop_loss": {"status": "SUCCESS"},
            },
        },
    )
    _write_json(
        root / "outputs" / "audit" / "BETA_CORRELATION_RISK_ENGINE.json",
        {
            "status": "SUCCESS",
            "coverage_pct": 92.5,
            "benchmark": {
                "status": "OK",
                "label": "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2",
                "eligible_constituents": 1113,
                "sessions": 6841,
                "max_abs_daily_return": 0.0975,
                "p99_abs_daily_return": 0.025,
                "allowed_max_abs_daily_return": 0.15,
            },
        },
    )
    _write_json(
        root / "outputs" / "risk" / "PORTFOLIO_RISK_SUMMARY.json",
        {
            "portfolio_beta_252d": 1.12,
            "portfolio_downside_beta_252d": 1.28,
            "diversification_warning": "AMBER",
            "top_engine": "AI_TECH",
            "top_engine_share_pct": 48.0,
            "systematic_stress_scenarios_pct": {"-10.0": -12.8, "-20.0": -25.6},
        },
    )
    _write_json(
        root / "config" / "BETA_RISK_ROBUST_VALIDATION_STATUS.json",
        {
            "status": "ROBUST_VALIDATED_CONTEXT_ONLY_KEEP_ALL_SIZING_SHADOW",
            "production_policy": {
                "score_influence": 0.0,
                "decision_influence": 0.0,
                "sizing_execution_influence": 0.0,
                "stop_loss_influence": 0.0,
            },
        },
    )

    markdown = build_markdown(root)

    assert "## RISQUE BÊTA / DIVERSIFICATION" in markdown
    assert "PEA_ACTION_ROBUST_EQUAL_WEIGHT_PROXY_V2" in markdown
    assert "Diversification: AMBER" in markdown
    assert "AI_TECH" in markdown
    assert "Sizing bêta permanent: REJETÉ" in markdown
    assert "score=0.0 | décision=0.0 | sizing=0.0 | stop=0.0" in markdown
    assert "beta_correlation_risk: [OK] SUCCESS" in markdown
    assert "stop_loss: [OK] SUCCESS" in markdown
