from pathlib import Path
import json
import pandas as pd
import pytest

from v182.audit.canonical_universe import filter_actions
from v182.audit.provenance import actual_sources_by_field, append_records, load_latest
from v182.core.merge import decide, is_missing_value, values_equal
from v182.io.frames import apply_observations
from v182.features.action_decision_enhancements import build_action_enhancement_observations
from v182.reporting.committee_performance_v21_4 import _eligible_signal_rows, _drawdown_multiplier, _current_buy_confirmation, _daily_turnover
from v182.reporting.unified_runner import _skip_dependency, _exit_code

ROOT=Path(__file__).resolve().parents[1]
WHITELIST=ROOT/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts"


def test_attributed_morningstar_is_a_validated_merge_status():
    d=decide(None,{"validation_status":"ATTRIBUTED","value":4,"evidence_level":"B","as_of":"2026-08-12"})
    assert d.action=="INSERT"


def test_per_field_provenance_controls_evidence_not_row_level_metadata(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{"isin":"A1","x":pd.NA,"evidence_level":"A","as_of_date":"2026-01-01"}])
    first={"universe":"ACTION","isin":"A1","field":"x","value":10,"source":"C_SOURCE","evidence_level":"C","as_of":"2026-08-01","validation_status":"AUTO_MATCH"}
    frame,q=apply_observations(frame,[first]); assert not q; assert float(frame.loc[0,"x"])==10
    second={"universe":"ACTION","isin":"A1","field":"x","value":20,"source":"B_SOURCE","evidence_level":"B","as_of":"2026-08-02","validation_status":"AUTO_MATCH"}
    frame,q=apply_observations(frame,[second]); assert not q; assert float(frame.loc[0,"x"])==20
    prov=pd.read_csv(ledger,sep=";"); assert set(prov["source"])=={"C_SOURCE","B_SOURCE"}; assert prov.iloc[-1]["merge_action"]=="REPLACE"


def test_keep_attempt_never_becomes_retained_provenance(tmp_path:Path):
    ledger=tmp_path/"provenance.csv"
    append_records([
        {"universe":"ACTION","isin":"A1","field":"x","value":20,"source":"B_RETAINED","evidence_level":"B","as_of":"2026-08-02","validation_status":"AUTO_MATCH","merge_action":"INSERT","merge_reason":"FIRST"},
        {"universe":"ACTION","isin":"A1","field":"x","value":19,"source":"C_REJECTED","evidence_level":"C","as_of":"2026-08-03","validation_status":"AUTO_MATCH","merge_action":"KEEP","merge_reason":"LOWER_EVIDENCE"},
    ],ledger)
    latest=load_latest(ledger)
    assert latest[("A1","x")]["source"]=="B_RETAINED"
    assert latest[("A1","x")]["evidence_level"]=="B"


def test_missing_and_numeric_merge_normalization():
    assert is_missing_value(float("nan")); assert is_missing_value(pd.NA); assert is_missing_value("N/A")
    assert values_equal("5.0",5.0); assert values_equal("  ABC ","abc")
    existing={"value":"5.0","evidence_level":"B","as_of":"2026-08-01"}
    missing=decide(existing,{"validation_status":"AUTO_MATCH","value":float("nan"),"evidence_level":"A","as_of":"2026-08-02"})
    same=decide(existing,{"validation_status":"AUTO_MATCH","value":5.0,"evidence_level":"B","as_of":"2026-08-01"})
    assert missing.action=="KEEP" and missing.reason=="NO_REGRESSION_MISSING"
    assert same.action=="KEEP" and same.reason=="NO_CHANGE"


def test_actual_sources_are_separated_by_universe(tmp_path:Path):
    ledger=tmp_path/"provenance.csv"
    append_records([
        {"universe":"ACTION","isin":"A1","field":"sector","value":"Tech","source":"ACTION_SOURCE","evidence_level":"B","as_of":"2026-08-01","validation_status":"AUTO_MATCH","merge_action":"INSERT","merge_reason":"FIRST"},
        {"universe":"ETF","isin":"E1","field":"sector","value":"Tech","source":"ETF_SOURCE","evidence_level":"C","as_of":"2026-08-01","validation_status":"AUTO_MATCH","merge_action":"INSERT","merge_reason":"FIRST"},
    ],ledger)
    out=actual_sources_by_field(ledger)
    assert out.loc[(out.universe=="ACTION")&(out.field=="sector"),"sources_reelles"].iloc[0]=="ACTION_SOURCE"
    assert out.loc[(out.universe=="ETF")&(out.field=="sector"),"sources_reelles"].iloc[0]=="ETF_SOURCE"


def test_real_legacy_master_can_materialize_exact_1829_universe():
    legacy=pd.read_csv(ROOT/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv",sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    result=filter_actions(legacy,WHITELIST)
    assert len(result.included)==1829; assert result.included["isin"].nunique()==1829
    assert result.materialized_missing_count>=0
    if result.materialized_missing_count:
        seeded=result.included[result.included["canonical_seed_status"]=="WHITELIST_ONLY_MISSING_METADATA"]
        assert len(seeded)==result.materialized_missing_count
        assert seeded["yahoo_ticker"].isna().all()
        with pytest.raises(RuntimeError,match="MISSING_CANONICAL_ISINS"):
            filter_actions(legacy,WHITELIST,materialize_missing=False)


def test_action_enhancement_observations_are_mergeable_and_gt4_explicit():
    actions=pd.DataFrame([{"isin":"A1","morningstar_rating":4,"dividend_yield_pct":3.9,"upside_pct":4.1}])
    obs=build_action_enhancement_observations(actions); by={o["field"]:o for o in obs}
    assert all(o["validation_status"]=="AUTO_MATCH" for o in obs)
    assert by["dividend_gt4_score"]["value"]==0.0
    assert by["target_upside_gt4_score"]["value"]>0.0


def test_virtual_signal_selection_consolidates_multiple_horizons_per_isin():
    cfg={"buy_decisions":["BUY_CANDIDATE"],"minimum_buy_score":77,"minimum_signal_coverage_pct":70}
    d=pd.DataFrame([
        {"decision":"BUY_CANDIDATE","score":80,"coverage_pct":90,"isin":"A1","horizon":"CT"},
        {"decision":"BUY_CANDIDATE","score":85,"coverage_pct":80,"isin":"A1","horizon":"MT"},
        {"decision":"BUY_CANDIDATE","score":82,"coverage_pct":90,"isin":"A2","horizon":"LT"},
    ])
    out=_eligible_signal_rows(d,cfg); assert len(out)==2
    a1=out[out["isin"]=="A1"].iloc[0]; assert a1["horizon"]=="MT"; assert a1["contributing_horizons"]=="CT|MT"


def test_deferred_virtual_entry_must_be_reconfirmed_on_fill_run():
    cfg={"buy_decisions":["BUY_CANDIDATE"],"minimum_buy_score":77,"minimum_signal_coverage_pct":70}
    still_buy=pd.DataFrame([{"decision":"BUY_CANDIDATE","score":82,"coverage_pct":85,"isin":"A1","horizon":"MT"}])
    confirm=_current_buy_confirmation(still_buy,cfg)
    assert confirm is not None and confirm["score"]==82.0 and confirm["horizon"]=="MT"
    invalidated=pd.DataFrame([{"decision":"WATCH","score":75,"coverage_pct":90,"isin":"A1","horizon":"MT"}])
    assert _current_buy_confirmation(invalidated,cfg) is None


def test_drawdown_throttle_can_stop_new_virtual_positions():
    cfg={"drawdown_throttle":{"enabled":True,"level_1_drawdown_pct":5,"level_1_new_position_multiplier":0.75,"level_2_drawdown_pct":10,"level_2_new_position_multiplier":0.5,"level_3_drawdown_pct":15,"level_3_new_position_multiplier":0.0}}
    nav=pd.DataFrame({"nav_eur":[100000,84000]}); mult,dd=_drawdown_multiplier(nav,cfg); assert round(dd,2)==16.0; assert mult==0.0


def test_daily_turnover_counts_buys_and_sells_and_persists_same_day():
    tx=pd.DataFrame([
        {"book_id":"B","date":"2026-08-13","type":"BUY","gross_eur":5000},
        {"book_id":"B","date":"2026-08-13","type":"SELL","gross_eur":3000},
        {"book_id":"B","date":"2026-08-12","type":"BUY","gross_eur":9000},
        {"book_id":"OTHER","date":"2026-08-13","type":"BUY","gross_eur":9000},
    ])
    assert _daily_turnover(tx,"B","2026-08-13")==8000.0


def test_partial_unified_run_has_nonzero_cli_exit_code():
    assert _exit_code({"status":"SUCCESS"})==0
    assert _exit_code({"status":"PARTIAL_SUCCESS"})==1
    assert _exit_code({"status":"FAILED"})==1


def test_performance_step_has_explicit_dependency_skip_status():
    out=_skip_dependency("stale decisions forbidden"); assert out["status"]=="SKIPPED_DEPENDENCY"; assert "stale" in out["reason"]


def test_v21_7_registry_has_no_active_derived_or_unvalidated_overlays():
    cfg=json.loads((ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json").read_text())
    for h in ("CT","MT","LT"):
        assert "total_return_potential_score" not in cfg["weights"][h]
        assert "target_upside_gt4_score" not in cfg["weights"][h]
        assert "dividend_gt4_score" not in cfg["weights"][h]
        assert "morningstar_action_score" not in cfg["weights"][h]
        assert "sector_rotation_score" not in cfg["weights"][h]
        assert "action_catchup_score" not in cfg["weights"][h]
        assert cfg["weights"][h]["target_upside_pct_v21"]>0
    for h in ("MT","LT"):
        assert cfg["weights"][h]["dividend_yield_v21_pct"]>0


def test_action_reference_and_challenger_are_separate_and_normalized():
    ref=json.loads((ROOT/"config"/"V21_ACTIONS_REFERENCE_V21_0.json").read_text())
    challenger=json.loads((ROOT/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json").read_text())
    integrity=json.loads((ROOT/"config"/"FULL_REFERENTIAL_INTEGRITY.json").read_text())
    for h in ("CT","MT","LT","SHORT","TOP_DOWN"):
        assert abs(sum(ref["weights"][h].values())-1.0)<1e-9
        assert abs(sum(challenger["weights"][h].values())-1.0)<1e-6
    assert integrity["actions"]["reference_weights"]["role"]=="FINAL_REFERENCE_UNTIL_CHALLENGER_VALIDATION"
    assert integrity["actions"]["challenger_weights"]["role"]=="SHADOW_CHALLENGER_NO_PERFORMANCE_ATTRIBUTION"
