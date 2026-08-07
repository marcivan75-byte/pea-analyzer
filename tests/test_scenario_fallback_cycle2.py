from pathlib import Path

import pandas as pd

from v182.decision.scenario_fallback import ensure_committee_scenarios


def test_scenario_fallback_uses_top_scores_when_no_explicit_committee(tmp_path: Path):
    outputs = tmp_path / "outputs"
    (outputs / "audit").mkdir(parents=True)
    frame = pd.DataFrame([
        {
            "isin": "FR1", "name": "A", "score_brut": "90", "comite_status": "RESEARCH_ONLY",
            "last_close": "100", "atr14": "5", "perf_3m_pct": "10",
        },
        {
            "isin": "FR2", "name": "B", "score_brut": "80", "comite_status": "BLOCKED",
            "last_close": "50", "atr14": "2", "perf_3m_pct": "4",
        },
    ])
    frame.to_csv(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", index=False, encoding="utf-8-sig")

    metrics = ensure_committee_scenarios(tmp_path)
    result = pd.read_csv(outputs / "V18.2_PEA_ACTIONS_MASTER_ENRICHED.csv", sep=";", encoding="utf-8-sig")

    assert metrics["selection_basis"] == "TOP_300_SCORE_BRUT_FALLBACK"
    assert metrics["scenario_isins"] == 2
    assert metrics["scenario_observations"] == 10
    assert result["scenario_bull_pct"].notna().sum() == 2
    assert metrics["execution_gate"] == "SHADOW_BLOCKED"
