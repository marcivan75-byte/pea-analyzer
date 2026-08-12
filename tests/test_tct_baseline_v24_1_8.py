import pandas as pd

from v182.decision.tct_baseline_v24_1_8 import build_tct_baseline, NORMALIZATION_POLICY


def _cfg(): return {"scope":{"baseline_top_n":20,"baseline_min_coverage":0.60}}


def _full_rows(n=2):
    return pd.DataFrame({
        "isin":[f"FR{i:010d}" for i in range(n)],"name":[f"A{i}" for i in range(n)],"asset_class":"ACTION","pea_eligible":True,
        "score_squeeze":100.0,"score_earnings_proximity":100.0,"score_t1_tech":100.0,"score_bayes":100.0,"score_cata":100.0,"score_regime":100.0,"score_rs":100.0,"score_news":100.0,"score_valo":100.0,
        "setup":"T2_CONFIRMATION","bonus":999.0,"t1_component_compression":100.0,"t2_component_bandwidth_expansion":100.0,
    })


def test_all_active_pillars_are_rebased_to_100_and_setup_stays_excluded():
    out,audit=build_tct_baseline(_full_rows(1),_cfg()); row=out.iloc[0]
    assert row["tct_baseline_score"] == 100.0
    assert row["tct_baseline_coverage"] == 1.0
    assert row["tct_baseline_effective_weight_sum_pct"] == 100.0
    assert row["tct_baseline_setup_active"] == False
    assert row["tct_baseline_t1_t2_influence"] == 0.0
    assert row["tct_baseline_missing_weight_policy"] == NORMALIZATION_POLICY
    assert audit.max_score == 100.0


def test_missing_active_pillar_is_renormalized_but_coverage_gate_still_blocks():
    frame=pd.DataFrame([{"isin":"FR0000000001","name":"Only squeeze","asset_class":"ACTION","pea_eligible":True,"score_squeeze":100.0}])
    out,_=build_tct_baseline(frame,_cfg()); row=out.iloc[0]
    assert row["tct_baseline_score"] == 100.0
    assert 0 < row["tct_baseline_coverage"] < 0.60
    assert row["tct_baseline_effective_weight_sum_pct"] == 100.0
    assert row["tct_baseline_status"] == "BLOCK_BASELINE_COVERAGE"


def test_t1_t2_setup_fields_still_have_zero_baseline_effect():
    a=_full_rows(1); b=a.copy(); a.loc[0,["setup","bonus","t1_component_compression","t2_component_bandwidth_expansion"]]=["T2_CONFIRMATION",999,100,100]; b.loc[0,["setup","bonus","t1_component_compression","t2_component_bandwidth_expansion"]]=[None,-999,0,0]
    out_a,_=build_tct_baseline(a,_cfg()); out_b,_=build_tct_baseline(b,_cfg())
    assert out_a.loc[0,"tct_baseline_score"] == out_b.loc[0,"tct_baseline_score"]
    assert out_a.loc[0,"tct_baseline_coverage"] == out_b.loc[0,"tct_baseline_coverage"]
