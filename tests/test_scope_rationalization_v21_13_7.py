from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


REMOVED_RUNTIME_PATHS = [
    ".github/workflows/ipo_radar_v1_1_smoke.yml",
    "config/ETF_CT_LT_SHADOW_V21_14.json",
    "config/ETP_SATELLITE_V1_SHADOW.json",
    "config/GOLD_V1_1_102_CRITERIA.json",
    "config/IPO_RADAR_V1.json",
    "inputs/CRYPTO_FUND_FLOW_WEEKLY_CONTROL.csv",
    "inputs/IPO_ENRICHMENT.csv",
    "src/v182/decision/gold_v1_1.py",
    "src/v182/decision/ipo_radar_v1.py",
    "src/v182/features/etf_ct_lt_shadow_v21_14.py",
    "src/v182/features/etp_satellite_v1.py",
    "src/v182/reporting/committee_master_gold_v1_1.py",
    "src/v182/reporting/etf_ct_lt_shadow_run_v21_14.py",
    "src/v182/reporting/etp_satellite_shadow_run.py",
    "src/v182/sources/euronext_ipo_v1_3.py",
    "src/v182/sources/sec_ipo_v2.py",
]


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_retired_runtime_entrypoints_and_configs_are_absent() -> None:
    assert [relative for relative in REMOVED_RUNTIME_PATHS if (ROOT / relative).exists()] == []


def test_active_action_and_etf_registries_have_no_lt_horizon() -> None:
    for relative in (
        "config/V21_ACTIONS_CRITERIA_REGISTRY.json",
        "config/V21_ACTIONS_REFERENCE_V21_0.json",
        "config/V20_7_1_ETF_CRITERIA_REGISTRY.json",
    ):
        registry = _json(relative)
        assert "LT" not in registry.get("horizons", {})
        assert "LT" not in registry.get("weights", {})
        assert "LT" not in registry.get("directions", {})

    committee = _json("config/COMMITTEE_MASTER_V21.json")
    assert "LT" not in committee["assets"]["ACTIONS"]["horizons"]
    assert "LT" not in committee["assets"]["ETF"]["horizons"]
    assert "GOLD" not in committee["assets"]


def test_fund_flow_runtime_is_etf_only_without_gold_or_crypto_controls() -> None:
    cfg = _json("config/ETF_FUND_FLOW_V1_SHADOW.json")
    assert cfg["version"].endswith("_PEA_ONLY")
    assert "gold_composite_weights" not in cfg
    assert "crypto" not in json.dumps(cfg).lower()

    universe = pd.read_csv(ROOT / "config/ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv", sep=";", dtype=str)
    assert len(universe) == 24
    assert set(universe["asset_class"]) == {"ETF"}
    searchable = universe.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    assert not searchable.str.contains(r"gold|bitcoin|ethereum|solana|crypto", regex=True).any()


def test_preopen_scope_is_exactly_tct20_union_action_ct20() -> None:
    cfg = _json("config/TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json")
    scope = cfg["candidate_selection"]["preselection_scope"]
    assert cfg["data_policy"]["candidate_limit"] == 40
    assert cfg["data_policy"]["news_query_limit"] == 40
    assert cfg["runtime_budget"]["preopen_seconds"] == 180
    assert scope["enabled"] is True
    assert scope["asset_class"] == "ACTION"
    assert scope["tct_top_n"] == 20
    assert scope["action_ct_top_n"] == 20
    assert scope["union_max"] == 40
    assert scope["deduplicate_by"] == "isin"
    assert scope["fail_closed_if_marker_missing"] is True


def test_scheduled_workflows_have_no_retired_module_invocation() -> None:
    workflows = "\n".join(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in (
            "committee_master_daily.yml",
            "committee_tct_ct_daily.yml",
            "etf_fund_flows_daily.yml",
            "tct_next_session_context.yml",
        )
    ).lower()
    for token in (
        "committee_master_gold_v1_1",
        "etf_ct_lt_shadow",
        "etp_satellite",
        "ipo_radar",
        "crypto_fund_flow_weekly_control",
    ):
        assert token not in workflows
