import json
from pathlib import Path

from docx import Document

from v182.reporting.android_risk_control_center import build_markdown, run


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_ci_risk_center_labels_recommendation_basket_not_held_portfolio(tmp_path: Path):
    _write_json(
        tmp_path / "outputs/audit/BETA_CORRELATION_RISK_ENGINE.json",
        {"status": "SUCCESS", "coverage_pct": 94.0, "benchmark": {"label": "PEA", "status": "OK"}},
    )
    _write_json(
        tmp_path / "outputs/risk/PORTFOLIO_RISK_SUMMARY.json",
        {
            "status": "OK",
            "analysis_universe": "UNIQUE_ACTIVE_COMMITTEE_ISINS_NOT_HELD_PORTFOLIO",
            "is_real_portfolio": False,
            "real_portfolio_fit_status": "NOT_AVAILABLE_NO_PORTFOLIO_INPUT",
            "unique_isins": 4,
            "duplicate_horizon_rows_removed": 3,
            "weight_method": "EQUAL_WEIGHT_UNIQUE_ISIN_DIAGNOSTIC",
            "portfolio_beta_252d": 1.1,
            "portfolio_downside_beta_252d": 1.2,
            "mean_pair_correlation_252d": 0.55,
            "mean_stress_pair_correlation": 0.7,
            "diversification_warning": "AMBER",
            "top_engine": "TECH",
            "top_engine_share_pct": 50.0,
            "systematic_stress_scenarios_pct": {"-10.0": -12.0},
        },
    )
    _write_json(
        tmp_path / "config/BETA_RISK_ROBUST_VALIDATION_STATUS.json",
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

    text = build_markdown(tmp_path)
    assert "Panier des recommandations — distinct du portefeuille détenu" in text
    assert "Lignes multi-horizons neutralisées: 3" in text
    assert "Portefeuille réellement détenu : **NON RENSEIGNÉ**" in text

    payload = run(tmp_path)
    assert payload["is_real_portfolio"] is False
    assert payload["real_portfolio_fit_status"] == "NOT_AVAILABLE_NO_PORTFOLIO_INPUT"
    word_path = tmp_path / "outputs/committee_master/CI_RISQUE_PANIER_RECOMMANDATIONS.docx"
    assert word_path.exists()
    word_text = "\n".join(p.text for p in Document(word_path).paragraphs)
    assert "Risque du panier de recommandations" in word_text
    assert "Portefeuille réellement détenu" in word_text
    assert "Aucun portefeuille équipondéré fictif" in word_text
