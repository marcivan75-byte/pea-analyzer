from __future__ import annotations

from pathlib import Path
import pandas as pd

from v182.audit.canonical_universe import filter_actions, load_compressed_isins
from v182.audit.identity_hydration import build_worklist
from v182.decision.committee_master import score_horizon
from v182.mapping.action_isin_resolver import apply_identity_overlay
from v182.mapping.identity_overlay_store import materialize_identity_overlay


def test_real_master_generates_exact_governed_residual_identity_worklist():
    root=Path(__file__).resolve().parents[1]
    legacy=pd.read_csv(root/"inputs"/"V18.2_PEA_ACTIONS_MASTER.csv",sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    result=filter_actions(legacy,root/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts")
    overlay_path=materialize_identity_overlay(root)
    assert overlay_path is not None
    governed,audit=apply_identity_overlay(result.included,overlay_path)
    worklist=build_worklist(governed)
    assert audit["fully_hydrated"]==360
    assert len(worklist)==39
    assert worklist["isin"].is_unique
    assert worklist["name"].notna().all()
    assert not worklist["scoring_eligible"].any()
    assert worklist["source_provenance_required"].all()
    if "yahoo_ticker" in worklist.columns:
        assert worklist["yahoo_ticker"].isna().all()


def test_new_skeletons_are_tagged_even_when_seed_status_column_already_exists():
    root=Path(__file__).resolve().parents[1]
    whitelist=root/"config"/"V21_3_ACTION_UNIVERSE_1829_ISINS.parts"
    isins=load_compressed_isins(whitelist)
    partial=pd.DataFrame([{"isin":isins[0],"asset_class":"ACTION","canonical_seed_status":"LEGACY_ROW"}])
    result=filter_actions(partial,whitelist)
    materialized=result.included[result.included["isin"]!=isins[0]]
    assert len(materialized)==1828
    assert materialized["canonical_seed_status"].eq("WHITELIST_ONLY_MISSING_METADATA").all()
    assert materialized["asset_class"].eq("ACTION").all()


def test_identity_only_row_cannot_become_scorable_by_dynamic_renormalization():
    root=Path(__file__).resolve().parents[1]
    registry=__import__("json").loads((root/"config"/"V21_ACTIONS_REFERENCE_V21_0.json").read_text())
    row={"isin":"ZZ0000000001","canonical_seed_status":"WHITELIST_ONLY_MISSING_METADATA"}
    frame=pd.DataFrame([row])
    for horizon in ("CT","MT","SHORT","TOP_DOWN"):
        scored=score_horizon(frame,registry,horizon).iloc[0]
        assert scored["coverage_pct"]==0.0
        assert scored["status"]=="BLOCK_DATA"
        assert scored["decision"]=="BLOCK_DATA"
        assert pd.isna(scored["score"])
