import pandas as pd

from v182.reporting.boursorama_shadow_run import build_equivalence_audit


def _obs(isin: str, field: str, value):
    return {"isin": isin, "field": field, "value": value}


def test_equivalence_audit_compares_only_semantically_named_shadow_fields():
    actions = pd.DataFrame(
        [
            {"isin": "A", "consensus_score": 4.0, "consensus_delta_4w": 2.0, "n_analysts": 10, "target_upside_pct": 12.0},
            {"isin": "B", "consensus_score": 3.0, "consensus_delta_4w": -1.0, "n_analysts": 8, "target_upside_pct": 5.0},
        ]
    )
    observations = [
        _obs("A", "boursorama_consensus_score", 4.1),
        _obs("A", "boursorama_consensus_delta_4w", 2.5),
        _obs("A", "boursorama_n_analysts", 11),
        _obs("A", "boursorama_target_upside_pct", 13.0),
        _obs("B", "boursorama_consensus_score", 2.9),
        _obs("B", "boursorama_consensus_delta_4w", -1.5),
        _obs("B", "boursorama_n_analysts", 8),
        _obs("B", "boursorama_target_upside_pct", 4.0),
    ]
    audit = build_equivalence_audit(actions, observations)
    assert audit["status"] == "SHADOW_ONLY_NO_DECISION_INFLUENCE"
    assert audit["paired_master_rows"] == 2
    assert audit["comparisons"]["consensus_score_1_to_5"]["paired_rows"] == 2
    assert audit["comparisons"]["consensus_score_1_to_5"]["mae"] == 0.1
    assert "DIAGNOSTIC_ONLY" in audit["target_comparison_semantics"]
