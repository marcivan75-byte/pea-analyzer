from __future__ import annotations

from pathlib import Path
import json

import pandas as pd
import pytest

from v182.features.etp_satellite_v1 import build_satellite_context, write_satellite_outputs


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"instrument_id":"G1","asset_class":"GOLD_ETC","name":"Gold EU","is_pea":False,"is_inverse_or_leveraged":False},
        {"instrument_id":"G2","asset_class":"GOLD_MINERS_ETF","name":"Gold Miners","is_pea":False,"is_inverse_or_leveraged":False},
        {"instrument_id":"B1","asset_class":"CRYPTO_ETP","name":"Bitcoin","is_pea":False,"is_inverse_or_leveraged":False},
        {"instrument_id":"E1","asset_class":"CRYPTO_ETF","name":"Ethereum","is_pea":False,"is_inverse_or_leveraged":False},
        {"instrument_id":"S1","asset_class":"CRYPTO_SHORT_ETF","name":"Short BTC","is_pea":False,"is_inverse_or_leveraged":True},
        {"instrument_id":"X1","asset_class":"ETF","name":"Generic ETF","is_pea":False,"is_inverse_or_leveraged":False},
    ])


def _flows() -> pd.DataFrame:
    return pd.DataFrame([
        {"instrument_id":"G1","efs_shadow":70.0,"efs_readiness":"MATURE_60_PLUS","flow_price_state":"ROTATION_CONFIRMED"},
        {"instrument_id":"G2","efs_shadow":30.0,"efs_readiness":"MATURE_60_PLUS","flow_price_state":"EXIT_CONFIRMED"},
        {"instrument_id":"B1","efs_shadow":80.0,"efs_readiness":"PRELIMINARY_20_59","flow_price_state":"EARLY_ACCUMULATION"},
        {"instrument_id":"E1","efs_shadow":60.0,"efs_readiness":"MATURE_60_PLUS","flow_price_state":"NEUTRAL"},
        {"instrument_id":"S1","efs_shadow":90.0,"efs_readiness":"MATURE_60_PLUS","flow_price_state":"NEUTRAL"},
    ])


def _gold() -> dict:
    return {
        "GOLD_SCORE_CT": 72.0,
        "GOLD_SCORE_MT": 78.0,
        "QDS_OR": 81.0,
        "DATA_TRUST_OR": 88.0,
        "current_scores": {
            "TACTICAL_2_12W": {"decision": "WATCH"},
            "STRATEGIC_6_24M": {"decision": "FAVORABLE"},
        },
    }


def test_satellite_filters_generic_and_keeps_lanes_separate():
    out, summary = build_satellite_context(_universe(), _flows(), _gold(), {"version":"TEST","mode":"SHADOW_ONLY"})
    assert set(out["instrument_id"]) == {"G1","G2","B1","E1","S1"}
    assert set(out["satellite_lane"]) == {"GOLD_PHYSICAL","GOLD_MINERS","CRYPTO_LONG","CRYPTO_SHORT_CONTEXT"}
    assert summary["no_cross_lane_ranking"] is True
    assert not out["cross_lane_ranking_allowed"].any()


def test_no_pea_contamination_allowed():
    universe = _universe()
    universe.loc[universe["instrument_id"].eq("B1"), "is_pea"] = True
    with pytest.raises(RuntimeError, match="PEA_CONTAMINATION"):
        build_satellite_context(universe, _flows(), _gold(), {})


def test_missing_flow_is_not_neutral_imputation():
    flows = _flows()[_flows()["instrument_id"].ne("E1")]
    out, _ = build_satellite_context(_universe(), flows, _gold(), {})
    row = out.loc[out["instrument_id"].eq("E1")].iloc[0]
    assert pd.isna(row["flow_score_shadow"])
    assert row["flow_context_state"] == "DATA_INSUFFICIENT"


def test_crypto_has_no_fake_alpha_or_orders():
    out, summary = build_satellite_context(_universe(), _flows(), _gold(), {})
    crypto = out[out["satellite_lane"].eq("CRYPTO_LONG")]
    assert set(crypto["alpha_engine_status"]) == {"CRYPTO_PIT_OOS_ALPHA_NOT_IMPLEMENTED"}
    assert not crypto["live_orders_enabled"].any()
    assert not crypto["promotion_allowed"].any()
    assert summary["crypto_alpha_engine_status"] == "NOT_IMPLEMENTED_PIT_OOS"


def test_gold_context_is_attached_but_not_promoted():
    out, summary = build_satellite_context(_universe(), _flows(), _gold(), {})
    gold = out[out["satellite_lane"].str.startswith("GOLD")]
    assert gold["gold_score_mt"].eq(78.0).all()
    assert gold["alpha_engine_status"].eq("GOLD_V1_1_CONTEXT_PLUS_FLOWS").all()
    assert gold["decision_influence"].eq(0.0).all()
    assert summary["gold_engine_available"] is True


def test_lane_rank_resets_inside_each_lane():
    out, _ = build_satellite_context(_universe(), _flows(), _gold(), {})
    crypto = out[out["satellite_lane"].eq("CRYPTO_LONG")].sort_values("flow_score_shadow", ascending=False)
    assert list(crypto["lane_rank_shadow"].astype("Int64")) == [1,2]
    gold_physical = out[out["satellite_lane"].eq("GOLD_PHYSICAL")]
    assert int(gold_physical.iloc[0]["lane_rank_shadow"]) == 1


def test_write_outputs(tmp_path: Path):
    out, summary = build_satellite_context(_universe(), _flows(), _gold(), {})
    paths = write_satellite_outputs(out, summary, tmp_path)
    assert (tmp_path / paths["context_csv"]).exists()
    assert (tmp_path / paths["audit_json"]).exists()
    assert (tmp_path / paths["mobile_md"]).exists()


def test_empty_flow_input_remains_data_insufficient():
    out, summary = build_satellite_context(_universe(), pd.DataFrame(), _gold(), {})
    assert out["flow_score_shadow"].isna().all()
    assert set(out["flow_context_state"]) == {"DATA_INSUFFICIENT"}
    assert summary["flow_scorable_instruments"] == 0


def test_runner_with_realistic_files(tmp_path: Path):
    from v182.reporting.etp_satellite_shadow_run import run

    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "outputs" / "etf_fund_flows").mkdir(parents=True)
    (tmp_path / "outputs" / "gold_v1_1").mkdir(parents=True)
    cfg = {
        "version":"ETP_SATELLITE_V1.0_SHADOW",
        "mode":"SHADOW_ONLY",
        "inputs": {
            "external_universe":"config/ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv",
            "flow_instruments":"outputs/etf_fund_flows/ETF_FLOW_INSTRUMENTS_SHADOW.csv",
            "gold_decision_optional":"outputs/gold_v1_1/GOLD_V1_1_DECISION.json"
        },
        "governance": {"decision_influence":0.0,"real_orders_allowed":False,"t1_t2_forbidden":True}
    }
    (tmp_path / "config" / "ETP_SATELLITE_V1_SHADOW.json").write_text(json.dumps(cfg), encoding="utf-8")
    _universe().to_csv(tmp_path / "config" / "ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv", sep=";", index=False, encoding="utf-8-sig")
    _flows().to_csv(tmp_path / "outputs" / "etf_fund_flows" / "ETF_FLOW_INSTRUMENTS_SHADOW.csv", sep=";", index=False, encoding="utf-8-sig")
    (tmp_path / "outputs" / "gold_v1_1" / "GOLD_V1_1_DECISION.json").write_text(json.dumps(_gold()), encoding="utf-8")
    summary = run(tmp_path)
    assert summary["instrument_count"] == 5
    assert summary["flow_input_available"] is True
    assert summary["gold_decision_input_available"] is True
    assert (tmp_path / "outputs" / "audit" / "ETP_SATELLITE_V1_SHADOW.json").exists()
