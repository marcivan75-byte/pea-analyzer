from pathlib import Path

import pandas as pd

from v182.reporting.entry_exit_shadow_v1 import run


def test_entry_exit_shadow_does_not_promote(tmp_path: Path):
    policy = tmp_path / "config"
    policy.mkdir()
    (policy / "ENTRY_EXIT_SHADOW_POLICY_V1.json").write_text(
        Path("config/ENTRY_EXIT_SHADOW_POLICY_V1.json").read_text(encoding="utf-8")
        if Path("config/ENTRY_EXIT_SHADOW_POLICY_V1.json").exists()
        else "{\"version\":\"T\",\"baseline_A\":{\"action_confidence_min\":66},\"challenger_B\":{\"action_confidence_min\":55,\"require_rr_min\":2},\"challenger_C\":{\"or_composite_min\":42,\"require_rr_min\":2},\"exit_hysteresis\":{\"confidence_buffer_pts\":8,\"weeks_below\":2}}",
        encoding="utf-8",
    )
    out = tmp_path / "outputs/committee_master"
    out.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "name": "TEST ETF",
                "isin": "LU1",
                "asset_class": "ETF",
                "CI_CONFIDENCE_SCORE_V22_2_1": 66.1,
                "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": 9.7,
                "OR_COMPOSITE_SHADOW": 48.0,
                "CI_SELECTION_GATE_STATUS_V4": "SELECTED",
                "CHALLENGER_ENTRY_STATE": "WAIT",
                "SIM_CURRENT_PRICE": 200.0,
                "SIM_INVALIDATION": 190.0,
            }
        ]
    ).to_csv(out / "CI_RESULTS_CHALLENGER_V2.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = run(root=tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["decision_influence"] == 0.0
    assert payload["production_gates_unchanged"] is True
