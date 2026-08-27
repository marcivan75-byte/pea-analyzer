from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.reporting import ci_balanced_v4


ROOT = Path(__file__).resolve().parents[1]


def test_balanced_weights_sum_to_one_and_preserve_governance():
    config = json.loads((ROOT / "config/CI_BALANCED_V4.json").read_text(encoding="utf-8"))
    assert sum(config["weights"].values()) == 1.0
    assert config["minimum_available_weight"] == 0.70
    assert config["action_minimum_potential_pct"] == 20.0
    assert config["etf_required_mt_signal"] == "STRONG_BUY"
    assert config["etf_overlap"]["maximum_per_economic_family"] == 1
    assert config["governance"]["missing_source_is_negative"] is False
    assert config["governance"]["current_committee_buy_candidate_required"] is True
    assert config["governance"]["t1_t2_score_influence"] == 0.0


def test_balanced_component_and_band_scoring_are_explicit_and_missing_safe():
    assert ci_balanced_v4._component("Strong_Buy", {"STRONG_BUY": 100.0}) == 100.0
    assert ci_balanced_v4._component(pd.NA, {"STRONG_BUY": 100.0}) is None
    bands = [{"minimum": 20.0, "score": 100.0}, {"minimum": 0.0, "score": 50.0}]
    assert ci_balanced_v4._band(25.0, bands) == 100.0
    assert ci_balanced_v4._band(5.0, bands) == 50.0
    assert ci_balanced_v4._band(pd.NA, bands) is None


def test_selection_output_exposes_requested_business_columns():
    source = (ROOT / "src/v182/reporting/ci_balanced_v4.py").read_text(encoding="utf-8")
    for column in (
        "POTENTIEL_BOURSORAMA_PCT",
        "POTENTIEL_PCT",
        "POTENTIEL_SOURCE",
        "RECOMMANDATION_BOURSORAMA",
        "NOTATION_CT",
        "NOTATION_MT",
        "NOTATION_LT",
        "MORNINGSTAR_ETOILES",
    ):
        assert column in source


def test_etf_economic_family_prefers_explicit_benchmark_then_category_geo():
    benchmark = pd.Series({"OVERLAP_OFFICIAL_BENCHMARK": "MSCI Europe", "isin": "A"})
    category = pd.Series(
        {"OVERLAP_CATEGORY": "Europe Large Cap", "OVERLAP_GEO_EXPOSURE": "Europe", "isin": "B"}
    )
    unknown = pd.Series({"isin": "C"})
    assert ci_balanced_v4._economic_family(benchmark) == "BENCHMARK:MSCI EUROPE"
    assert ci_balanced_v4._economic_family(category) == "CATEGORY_GEO:EUROPE LARGE CAP|EUROPE"
    assert ci_balanced_v4._economic_family(unknown) == "UNRESOLVED:C"
