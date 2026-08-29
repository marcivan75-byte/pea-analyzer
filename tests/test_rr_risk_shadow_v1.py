from pathlib import Path

import pandas as pd

from v182.reporting.rr_risk_shadow_v1 import run


def test_rr_allows_small_overshoot(tmp_path: Path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/RR_RISK_SHADOW_POLICY_V1.json").write_text(
        '{"entry_rr_min":2,"exit_rr_from_current_min":1,"overshoot_tolerance_pct":0.5,"size_soft":{"rr_lt_2":0,"rr_2_to_3":0.5,"rr_ge_3":1}}',
        encoding="utf-8",
    )
    out = tmp_path / "outputs/committee_master"
    out.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "name": "BNP",
                "SIM_CURRENT_PRICE": 202.6,
                "SIM_ENTRY_OPTIMAL": 201.96,
                "SIM_TARGET_CENTRAL": 212.82,
                "SIM_INVALIDATION": 200.84,
                "SIM_REWARD_RISK_AT_OPTIMAL_ENTRY": 9.75,
            }
        ]
    ).to_csv(out / "CI_RESULTS_CHALLENGER_V2.csv", sep=";", index=False, encoding="utf-8-sig")
    payload = run(root=tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["eligible"] == 1
    assert payload["decision_influence"] == 0.0
