import pandas as pd

from v182.decision.tct_baseline_v24_1_7 import (
    WEIGHTS_V24_1_2,
    MAX_SCORE_WITH_SETUP_DISABLED,
    MAX_COVERAGE_WITH_SETUP_DISABLED,
    build_tct_baseline,
)


def _cfg():
    return {"scope":{"baseline_top_n":20,"baseline_min_coverage":0.60}}


def _full_rows(n=25):
    return pd.DataFrame({
        "isin":[f"FR{i:010d}" for i in range(n)],
        "name":[f"A{i}" for i in range(n)],
        "asset_class":"ACTION",
        "pea_eligible":True,
        "score_squeeze":[100-i for i in range(n)],
        "score_earnings_proximity":80.0,
        "score_t1_tech":75.0,
        "score_bayes":70.0,
        "score_cata":65.0,
        "score_regime":60.0,
        "score_rs":55.0,
        "score_news":50.0,
        "score_valo":45.0,
        # Deliberately extreme timing/setup fields: baseline must ignore them.
        "setup":"T2_CONFIRMATION",
        "bonus":999.0,
        "t1_component_compression":100.0,
        "t2_component_bandwidth_expansion":100.0,
    })


def test_weights_are_frozen_and_setup_disabled_caps_score_and_coverage():
    assert abs(sum(WEIGHTS_V24_1_2.values())-1.0) < 1e-12
    assert WEIGHTS_V24_1_2["setup"] == 0.16
    assert MAX_SCORE_WITH_SETUP_DISABLED == 84.0
    assert MAX_COVERAGE_WITH_SETUP_DISABLED == 0.84
    frame=_full_rows(1)
    for field in ["score_squeeze","score_earnings_proximity","score_t1_tech","score_bayes","score_cata","score_regime","score_rs","score_news","score_valo"]:
        frame[field]=100.0
    out,audit=build_tct_baseline(frame,_cfg())
    row=out.iloc[0]
    assert row["tct_baseline_score"] == 84.0
    assert row["tct_baseline_coverage"] == 0.84
    assert row["tct_baseline_setup_active"] == False
    assert row["tct_baseline_t1_t2_influence"] == 0.0
    assert audit.max_score == 84.0


def test_missing_pillars_are_zero_fixed_weight_not_renormalized():
    frame=pd.DataFrame([{
        "isin":"FR0000000001","name":"Only squeeze","asset_class":"ACTION","pea_eligible":True,
        "score_squeeze":100.0,
    }])
    out,_=build_tct_baseline(frame,_cfg())
    row=out.iloc[0]
    assert row["tct_baseline_score"] == 18.0
    assert row["tct_baseline_coverage"] == 0.18
    assert row["tct_baseline_status"] == "BLOCK_BASELINE_COVERAGE"
    assert pd.isna(row["tct_baseline_rank"])


def test_setup_t1_t2_fields_have_zero_effect_on_baseline():
    a=_full_rows(1)
    b=a.copy()
    a.loc[0,["setup","bonus","t1_component_compression","t2_component_bandwidth_expansion"]]=["T2_CONFIRMATION",999,100,100]
    b.loc[0,["setup","bonus","t1_component_compression","t2_component_bandwidth_expansion"]]=[None,-999,0,0]
    out_a,_=build_tct_baseline(a,_cfg())
    out_b,_=build_tct_baseline(b,_cfg())
    assert out_a.loc[0,"tct_baseline_score"] == out_b.loc[0,"tct_baseline_score"]
    assert out_a.loc[0,"tct_baseline_coverage"] == out_b.loc[0,"tct_baseline_coverage"]


def test_only_verified_pea_actions_with_minimum_coverage_are_ranked_top20():
    frame=_full_rows(25)
    frame.loc[0,"pea_eligible"]="false"
    out,audit=build_tct_baseline(frame,_cfg())
    assert pd.isna(out.loc[0,"tct_baseline_rank"])
    assert out.loc[0,"tct_baseline_status"] == "EXCLUDED_PEA_GATE"
    assert int(out["tct_baseline_top20"].sum()) == 20
    assert audit.top20_rows == 20
    ranked=out[out["tct_baseline_rank"].notna()]
    assert ranked["tct_baseline_rank"].min() == 1
    assert ranked["tct_baseline_rank"].max() == 24


def test_rank_is_driven_by_baseline_score_only():
    frame=_full_rows(2)
    frame.loc[0,"score_squeeze"]=20.0
    frame.loc[1,"score_squeeze"]=90.0
    # Give the weaker baseline row stronger T1/T2 timing fields: must not help.
    frame.loc[0,"bonus"]=1000
    frame.loc[0,"t1_component_compression"]=100
    frame.loc[1,"bonus"]=-1000
    frame.loc[1,"t1_component_compression"]=0
    out,_=build_tct_baseline(frame,_cfg())
    assert out.loc[1,"tct_baseline_rank"] == 1
    assert out.loc[0,"tct_baseline_rank"] == 2
