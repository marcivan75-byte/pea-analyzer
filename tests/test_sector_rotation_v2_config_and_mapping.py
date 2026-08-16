from pathlib import Path
import json

import pandas as pd

from v182.features.instrument_theme_v2 import build_mapping_worklist, load_instrument_theme_mapping, score_instrument_theme_exposure
from v182.reporting.sector_rotation_v2_report import write_shadow_report


ROOT = Path(__file__).resolve().parents[1]


def test_sector_rotation_config_and_reference_tables_are_governed():
    cfg = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))
    taxonomy = pd.read_csv(ROOT / "config" / "SECTOR_ROTATION_V2_THEME_TAXONOMY.csv", sep=";")
    graph = pd.read_csv(ROOT / "config" / "SECTOR_ROTATION_V2_THEME_TRANSMISSION.csv", sep=";")
    sources = pd.read_csv(ROOT / "config" / "SECTOR_ROTATION_V2_SOURCE_REGISTRY.csv", sep=";")
    mapping = pd.read_csv(ROOT / "config" / "SECTOR_ROTATION_V2_INSTRUMENT_THEME_MAPPING.csv", sep=";")
    assert cfg["mode"] == "SHADOW_ONLY"
    assert cfg["governance"]["changes_action_scores"] is False
    assert cfg["governance"]["changes_etf_scores"] is False
    assert cfg["governance"]["can_trigger_orders"] is False
    assert cfg["governance"]["can_trigger_sales"] is False
    assert cfg["governance"]["pit_required"] is True
    assert cfg["governance"]["oos_validation_required_before_activation"] is True
    for family in ("RLS", "AVCR", "RARS_OPPORTUNITY", "DQS"):
        assert abs(sum(cfg["score_weights"][family].values()) - 1.0) < 1e-9
    assert taxonomy["theme_id"].is_unique
    assert {"RESEARCH_BASELINE", "HYPOTHESIS_ONLY"}.issubset(set(graph["status"]))
    assert sources["current_status"].isin(["ACTIVE_EXISTING", "PLANNED_V2_COLLECTOR", "PLANNED_PIT_HISTORY", "PLANNED_SOURCE", "PLANNED_SECTOR_DRIVER", "PLANNED_MAPPING", "PLANNED_V2_1"]).all()
    assert set(mapping.columns) == {"universe", "isin", "theme_id", "exposure_pct", "effective_from", "effective_to", "confidence_pct", "source", "status"}


def test_effective_dated_mapping_and_confluence_are_shadow_only(tmp_path):
    path = tmp_path / "mapping.csv"
    pd.DataFrame(
        [
            {"universe": "ACTION", "isin": "A", "theme_id": "AI", "exposure_pct": 50, "effective_from": "2026-01-01", "effective_to": "", "confidence_pct": 90, "source": "TEST", "status": "VALIDATED"},
            {"universe": "ACTION", "isin": "A", "theme_id": "GRID", "exposure_pct": 30, "effective_from": "2026-01-01", "effective_to": "", "confidence_pct": 80, "source": "TEST", "status": "VALIDATED"},
            {"universe": "ETF", "isin": "E", "theme_id": "AI", "exposure_pct": 70, "effective_from": "2027-01-01", "effective_to": "", "confidence_pct": 90, "source": "TEST", "status": "FUTURE"},
        ]
    ).to_csv(path, sep=";", index=False)
    mapping = load_instrument_theme_mapping(path, as_of="2026-08-16")
    assert set(mapping["isin"]) == {"A"}
    theme_scores = pd.DataFrame(
        {
            "theme_id": ["AI", "GRID"],
            "RLS": [85.0, 80.0],
            "RARS": [75.0, 72.0],
            "AVCR": [70.0, 40.0],
        }
    )
    scored, summary = score_instrument_theme_exposure(mapping, theme_scores, as_of="2026-08-16")
    assert summary["mapped_actions"] == 1
    assert scored.iloc[0]["strong_theme_count"] == 2
    assert scored.iloc[0]["theme_confluence_score"] > 50
    assert scored.iloc[0]["decision_influence"] == 0.0


def test_mapping_worklist_does_not_invent_unmapped_exposure():
    instruments = pd.DataFrame({"isin": ["A", "B"], "name": ["Alpha", "Beta"], "sector": ["Tech", "Banks"]})
    mapping = pd.DataFrame(
        [{"universe": "ACTION", "isin": "A", "theme_id": "AI"}]
    )
    worklist = build_mapping_worklist(instruments, mapping, universe="ACTION")
    assert worklist["isin"].tolist() == ["B"]
    assert worklist.iloc[0]["status"] == "MAPPING_REQUIRED"


def test_committee_report_has_mandatory_overvaluation_block(tmp_path):
    sectors = pd.DataFrame(
        [
            {"rank": 1, "sector": "A", "RLS": 85, "RARS": 75, "DQS": 90, "SQS": 80, "CTS": 80, "STS": 50, "MCS": 82, "AVCR": 72, "valuation_state": "OVERVALUATION_WARNING", "state": "MATURE_LEADERSHIP", "warnings": ["PROMISING_BUT_OVERVALUED"], "warning_confidence": 80, "correction_alert": False, "new_position_action": "WAIT_FOR_PULLBACK", "existing_position_action": "HOLD_MONITOR", "reentry_readiness": 0, "reentry_state": "NOT_APPLICABLE", "RLS_velocity": 1, "RLS_acceleration": 0, "as_of": "2026-08-16"},
            {"rank": 2, "sector": "B", "RLS": 78, "RARS": 80, "DQS": 90, "SQS": 76, "CTS": 78, "STS": 50, "MCS": 70, "AVCR": 35, "valuation_state": "NORMAL", "state": "CONFIRMED_ROTATION", "warnings": [], "warning_confidence": 0, "correction_alert": False, "new_position_action": "PRIORITY_BUY_ZONE", "existing_position_action": "HOLD", "reentry_readiness": 0, "reentry_state": "NOT_APPLICABLE", "RLS_velocity": 4, "RLS_acceleration": 1, "as_of": "2026-08-16"},
        ]
    )
    summary = write_shadow_report(sectors, tmp_path)
    assert summary["status"] == "OK"
    assert summary["blocks"]["PROMISING_BUT_OVERVALUED"] == 1
    block = pd.read_csv(tmp_path / "PROMISING_BUT_OVERVALUED.csv", sep=";")
    assert block["sector"].tolist() == ["A"]
