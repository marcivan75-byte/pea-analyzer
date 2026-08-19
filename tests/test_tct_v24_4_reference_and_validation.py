from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v244_validation_gates_are_pre_registered_and_non_promoting():
    cfg = json.loads((ROOT / "config" / "TCT_V24_4_0_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    maturity = cfg["maturity"]
    gov = cfg["governance"]
    assert cfg["target_phase"] == "PREOPEN"
    assert maturity["minimum_labeled_preopen_rows"] >= 60
    assert maturity["minimum_distinct_isins"] >= 20
    assert maturity["minimum_high_potential_predictions"] >= 15
    assert maturity["minimum_direction_calls"] >= 20
    assert maturity["minimum_observed_sessions"] >= 15
    assert gov["retuning_before_maturity_forbidden"] is True
    assert gov["holdout_locked"] is True
    assert gov["promotion_authority"] is False
    assert gov["production_influence"] == 0.0
    assert gov["ct_transfer_forbidden_before_separate_validation"] is True


def test_v244_process_addendum_keeps_daily_scope_and_zero_authority():
    text = (ROOT / "docs" / "PROCESS_REFERENCE_V21_8_1_TCT_V24_4_ADDENDUM.md").read_text(encoding="utf-8")
    assert "V21.8.1 inchangée" in text
    assert "V24.3.1 SHADOW" in text
    assert "V24.4.0 Next-Session Catalyst Cycle SHADOW" in text
    assert "ne fait pas de day trading" in text
    assert "aucune donnée 1m/5m" in text
    assert "movement_potential_score" in text
    assert "direction_bias_score" in text
    assert "dernière vraie date daily" in text
    assert "influence de production égale à zéro" in text
    assert "CT reste gelé" in text


def test_v244_integration_doc_records_cost_and_pit_constraints():
    text = (ROOT / "docs" / "TCT_V24_4_0_NEXT_SESSION_CATALYST_CYCLE.md").read_text(encoding="utf-8")
    assert "2 snapshots maximum" in text
    assert "aucun polling" in text
    assert "aucun 1m/5m" in text
    assert "60 observations PREOPEN" in text
    assert "20 ISIN distincts" in text
    assert "premier snapshot PREOPEN" in text
    assert "ne peuvent jamais être réinjectés" in text
