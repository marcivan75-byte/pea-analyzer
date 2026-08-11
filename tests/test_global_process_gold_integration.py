import json
from pathlib import Path


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_global_process_keeps_pea_contract_and_adds_gold_parallel():
    global_ref = _load("data/reference/V21.1_GLOBAL_PROCESS_REFERENCES_PUBLISHED.json")
    gold_ref = _load("data/reference/V21.1_GOLD_V1_REFERENCE_PUBLISHED.json")
    gold_cfg = _load("data/reference/V21.1_GOLD_V1_CONFIG.json")

    assert global_ref["scope"] == "GLOBAL_MARKET_PROCESS"
    assert set(global_ref["modules"]) == {"ACTIONS_PEA", "ETF_PEA", "GOLD"}
    assert global_ref["pea_universe"]["total_active_instruments"] == 1931
    assert global_ref["modules"]["ACTIONS_PEA"]["active_validated_universe"] == 1829
    assert global_ref["modules"]["ETF_PEA"]["active_validated_universe"] == 102
    assert global_ref["pea_universe"]["gold_in_pea_instrument_count"] is False

    governance = global_ref["governance"]
    assert governance["cross_module_weight_contamination_allowed"] is False
    assert governance["actions_weights_changed_by_gold_integration"] is False
    assert governance["etf_weights_changed_by_gold_integration"] is False
    assert governance["gold_automatic_execution_allowed"] is False
    assert governance["gold_t1_t2_allowed"] is False
    assert governance["actions_tct_t1_t2_policy"] == "UNCHANGED_TCT_ONLY"

    assert gold_ref["execution_mode"] == "RESEARCH_ONLY"
    assert gold_ref["governance"]["automatic_execution_allowed"] is False
    assert gold_cfg["criteria_count"] == 102
    assert len(gold_cfg["families"]) == 11
    assert gold_cfg["t1_t2_policy"] == "EXCLUDED_GOLD; RESERVED_ACTIONS_TCT_ONLY"


def test_global_process_references_exist():
    global_ref = _load("data/reference/V21.1_GLOBAL_PROCESS_REFERENCES_PUBLISHED.json")
    for module in global_ref["modules"].values():
        assert Path(module["published_reference"]).exists()
        assert Path(module["active_engine_config"]).exists()
        assert Path(module["workflow"]).exists()
    assert Path(global_ref["modules"]["GOLD"]["source_registry"]).exists()
