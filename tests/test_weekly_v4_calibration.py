from pathlib import Path

import pandas as pd

from v182.audit.weekly_v4_calibration import run


ROOT = Path(__file__).resolve().parents[1]


def test_calibration_audit_keeps_reference_vectors_and_describes_thresholds(tmp_path):
    candidates = tmp_path / "candidates.csv"
    pd.DataFrame(
        [
            {"score": 77, "CI_CONFIDENCE_SCORE_V22_2_1": 66},
            {"score": 76, "CI_CONFIDENCE_SCORE_V22_2_1": 70},
        ]
    ).to_csv(candidates, sep=";", index=False, encoding="utf-8-sig")
    payload = run(ROOT, candidate_path=candidates, write=False)
    assert payload["status"] == "PASS"
    assert payload["decision"] == "KEEP_REFERENCE_WEIGHTS_AND_THRESHOLDS"
    assert payload["threshold_sensitivity"]["status"] == "DESCRIPTIVE_ONLY_NO_THRESHOLD_OPTIMIZATION"
    assert all(abs(item["sum"] - 1.0) < 1e-9 for item in payload["weight_concentration"].values())
