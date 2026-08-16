from pathlib import Path

import pandas as pd

from v182.features.macro_sector_v2 import compute_macro_sector_scores, load_macro_sensitivity
from v182.features.theme_propagation_v2 import load_transmission_graph, propagate_theme_scores


ROOT = Path(__file__).resolve().parents[1]


def test_hypothesis_only_edges_cannot_create_propagation_candidates():
    graph = load_transmission_graph(ROOT / "config" / "SECTOR_ROTATION_V2_THEME_TRANSMISSION.csv")
    scores = pd.DataFrame(
        {
            "theme_id": ["DATA_CENTERS", "NUCLEAR", "POWER_INFRA", "GRID"],
            "RLS": [90.0, 55.0, 60.0, 58.0],
            "AVCR": [40.0, 30.0, 35.0, 40.0],
        }
    )
    result, summary = propagate_theme_scores(scores, graph, max_depth=2)
    assert summary["status"] == "OK"
    assert not result.loc[result["origin_theme"].eq("DATA_CENTERS"), "destination_theme"].eq("NUCLEAR").any()
    assert result.loc[result["origin_theme"].eq("DATA_CENTERS"), "destination_theme"].eq("POWER_INFRA").any()


def test_propagation_keeps_overvalued_destination_but_fails_risk_gate():
    graph = pd.DataFrame(
        [
            {
                "from_theme": "A",
                "to_theme": "B",
                "initial_strength": 0.8,
                "lag_min_days": 10,
                "lag_max_days": 100,
                "status": "RESEARCH_BASELINE",
            }
        ]
    )
    scores = pd.DataFrame({"theme_id": ["A", "B"], "RLS": [90.0, 75.0], "AVCR": [30.0, 85.0]})
    result, _ = propagate_theme_scores(scores, graph, maximum_destination_risk=65.0)
    assert len(result) == 1
    assert bool(result.iloc[0]["risk_gate_pass"]) is False
    assert result.iloc[0]["decision_influence"] == 0.0


def test_macro_sector_scores_are_neutral_with_neutral_factors():
    sensitivity = load_macro_sensitivity(ROOT / "config" / "SECTOR_ROTATION_V2_MACRO_SENSITIVITY.csv")
    factors = {column: 50.0 for column in sensitivity.columns if column not in {"theme_id", "status"}}
    result, summary = compute_macro_sector_scores(factors, sensitivity)
    assert summary["status"] == "OK"
    assert result["sector_macro_score"].between(49.999, 50.001).all()
    assert result["macro_evidence_sufficient"].all()


def test_macro_factor_coverage_shrinks_partial_evidence_toward_neutral():
    sensitivity = pd.DataFrame(
        [
            {"theme_id": "GROWTH", "growth": 1.0, "real_rates": -1.0, "status": "RESEARCH_PRIOR"},
        ]
    )
    full, _ = compute_macro_sector_scores({"growth": 90.0, "real_rates": 10.0}, sensitivity)
    partial, summary = compute_macro_sector_scores({"growth": 90.0}, sensitivity)
    assert full.iloc[0]["sector_macro_score"] > partial.iloc[0]["sector_macro_score"] > 50.0
    assert summary["factor_coverage_pct"] == 50.0
