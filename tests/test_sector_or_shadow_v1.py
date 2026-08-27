import json

import pandas as pd

from v182.reporting import sector_or_shadow_v1 as sector_or


def _write(root, relative, rows):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def test_sector_shadow_excludes_short_and_never_reopens_block_data(tmp_path):
    config = {
        "sector_or_shadow": {
            "top_instruments_per_sector": 5, "excluded_horizons": ["SHORT"],
            "coverage_multipliers": {"FULL_75_PLUS": 1.0, "AMBER_70_75": 0.85, "ORANGE_60_70": 0.70, "RED_BELOW_60": 0.40},
            "risk_caution_share_threshold": 0.40, "risk_caution_multiplier": 0.85,
            "rotation_action_multipliers": {"BUY": 1.0, "ACCUMULATE": 1.0, "WATCH": 0.90, "HOLD": 0.75, "HOLD_MONITOR": 0.75, "REJECT": 0.55, "BLOCK_DATA": 0.55, "MISSING": 0.70},
            "block_data_reopening_allowed": False, "score_influence": 0.0,
        }
    }
    path = tmp_path / sector_or.CONFIG
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    _write(tmp_path, sector_or.OR_INPUT, [
        {"isin": "A", "OR_COMPOSITE_SHADOW": 80, "OR_RISK_VERDICT": "GREEN"},
        {"isin": "B", "OR_COMPOSITE_SHADOW": 90, "OR_RISK_VERDICT": "ORANGE"},
        {"isin": "S", "OR_COMPOSITE_SHADOW": 99, "OR_RISK_VERDICT": "GREEN"},
        {"isin": "M", "OR_COMPOSITE_SHADOW": 95, "OR_RISK_VERDICT": "GREEN"},
    ])
    _write(tmp_path, sector_or.SECTOR_INPUT, [
        {"isin": "A", "sector": "Energy", "horizon": "CT", "coverage_pct": 76, "decision": "WATCH"},
        {"isin": "B", "sector": "Energy", "horizon": "CT", "coverage_pct": 65, "decision": "BLOCK_DATA"},
        {"isin": "S", "sector": "Energy", "horizon": "SHORT", "coverage_pct": 90, "decision": "WATCH"},
        {"isin": "M", "sector": None, "horizon": "CT", "coverage_pct": 90, "decision": "WATCH"},
    ])
    _write(tmp_path, sector_or.CHALLENGER_INPUT, [
        {"isin": "A", "score": 70, "coverage_pct": 75, "decision": "REVIEW"},
        {"isin": "B", "score": 75, "coverage_pct": 65, "decision": "BLOCK_DATA"},
    ])
    _write(tmp_path, sector_or.ROTATION_INPUT, [
        {"sector": "Energy", "new_position_action": "WATCH", "correction_alert": True, "RARS": 60},
    ])
    payload = sector_or.run(tmp_path)
    detail = pd.read_csv(tmp_path / payload["outputs"][0], sep=";")
    aggregate = pd.read_csv(tmp_path / payload["outputs"][1], sep=";")
    assert set(detail["isin"]) == {"A", "B", "M"}
    assert detail.set_index("isin").loc["B", "SECTOR_OR_ELIGIBILITY"] == "AUDIT_ONLY_FAIL_CLOSED"
    assert bool(detail.set_index("isin").loc["B", "SECTOR_OR_CAN_REOPEN_BLOCK_DATA"]) is False
    assert detail.set_index("isin").loc["M", "SECTOR_OR_GATE_REASON"] == "SECTOR_MISSING"
    assert detail.set_index("isin").loc["A", "SECTOR_OR_COMMITTEE_CONFLICT_CAP_SHADOW"] == "ATTENDRE_REPLI_SHADOW"
    assert detail.set_index("isin").loc["A", "SECTOR_OR_CONFLICT_REASON"] == "CORRECTION_ALERT"
    assert aggregate.iloc[0]["SECTOR_OR_TOP_COUNT"] == 1
    assert aggregate.iloc[0]["SECTOR_OR_AGGREGATE_SCORE"] == 72.0
    assert payload["score_influence"] == 0.0
    assert payload["real_orders_enabled"] is False
