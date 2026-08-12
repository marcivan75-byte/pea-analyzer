from pathlib import Path
import json
import os
import pandas as pd

from v182.core.merge import decide
from v182.io.frames import apply_observations
from v182.features.action_decision_enhancements import build_action_enhancement_observations
from v182.reporting.committee_performance_v21_4 import _eligible_signal_rows, _drawdown_multiplier
from v182.reporting.unified_runner import _skip_dependency


def test_attributed_morningstar_is_a_validated_merge_status():
    d=decide(None,{"validation_status":"ATTRIBUTED","value":4,"evidence_level":"B","as_of":"2026-08-12"})
    assert d.action=="INSERT"


def test_per_field_provenance_controls_evidence_not_row_level_metadata(tmp_path:Path,monkeypatch):
    ledger=tmp_path/"provenance.csv"; monkeypatch.setenv("PEA_PROVENANCE_PATH",str(ledger))
    frame=pd.DataFrame([{"isin":"A1","x":pd.NA,"evidence_level":"A","as_of_date":"2026-01-01"}])
    first={"universe":"ACTION","isin":"A1","field":"x","value":10,"source":"C_SOURCE","evidence_level":"C","as_of":"2026-08-01","validation_status":"AUTO_MATCH"}
    frame,q=apply_observations(frame,[first]); assert not q; assert float(frame.loc[0,"x"])==10
    # Row metadata still says A, but field x provenance is C; incoming B must win.
    second={"universe":"ACTION","isin":"A1","field":"x","value":20,"source":"B_SOURCE","evidence_level":"B","as_of":"2026-08-02","validation_status":"AUTO_MATCH"}
    frame,q=apply_observations(frame,[second]); assert not q; assert float(frame.loc[0,"x"])==20
    prov=pd.read_csv(ledger,sep=";"); assert set(prov["source"])=={"C_SOURCE","B_SOURCE"}; assert prov.iloc[-1]["merge_action"]=="REPLACE"


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


def test_drawdown_throttle_can_stop_new_virtual_positions():
    cfg={"drawdown_throttle":{"enabled":True,"level_1_drawdown_pct":5,"level_1_new_position_multiplier":0.75,"level_2_drawdown_pct":10,"level_2_new_position_multiplier":0.5,"level_3_drawdown_pct":15,"level_3_new_position_multiplier":0.0}}
    nav=pd.DataFrame({"nav_eur":[100000,84000]}); mult,dd=_drawdown_multiplier(nav,cfg); assert round(dd,2)==16.0; assert mult==0.0


def test_performance_step_has_explicit_dependency_skip_status():
    out=_skip_dependency("stale decisions forbidden"); assert out["status"]=="SKIPPED_DEPENDENCY"; assert "stale" in out["reason"]


def test_v21_4_registry_has_no_active_total_return_composite():
    root=Path(__file__).resolve().parents[1]; cfg=json.loads((root/"config"/"V21_ACTIONS_CRITERIA_REGISTRY.json").read_text())
    for h in ("CT","MT","LT"):
        assert "total_return_potential_score" not in cfg["weights"][h]
        assert "target_upside_gt4_score" in cfg["weights"][h]
