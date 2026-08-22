from __future__ import annotations

import pandas as pd

from v182.reporting import collection_audit


def _frames() -> tuple[pd.DataFrame,pd.DataFrame]:
    actions=pd.DataFrame({
        "isin":["FR0000000001","FR0000000002"],
        "name":["A","B"],
        "metric_a":["1","NA"],
        "metric_b":["","2"],
    })
    etfs=pd.DataFrame({
        "isin":["FR0010000001"],
        "name":["ETF"],
        "metric_a":["3"],
    })
    return actions,etfs


def test_cache_only_waves_reuse_exact_previous_inventory(tmp_path,monkeypatch) -> None:
    collection_audit._reset_audit_cache_for_tests()
    monkeypatch.setenv("GITHUB_ACTIONS","true")
    monkeypatch.setenv("PEA_SLOW_SOURCE_MODE","LIVE")
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"missing_provenance.csv"))
    actions,etfs=_frames()

    original=collection_audit._build_inventory
    builds=0

    def counted(*args,**kwargs):
        nonlocal builds
        builds+=1
        return original(*args,**kwargs)

    monkeypatch.setattr(collection_audit,"_build_inventory",counted)

    initial=collection_audit.write_collection_audit(
        actions,etfs,"WAVE_00_INITIAL_STATE",tmp_path,write_excel=False,
    )
    wave0=collection_audit.write_collection_audit(
        actions,etfs,"WAVE_00_ETF_TICKERS",tmp_path,write_excel=False,
    )
    wave1=collection_audit.write_collection_audit(
        actions,etfs,"WAVE_01_ACTION_OHLCV",tmp_path,write_excel=False,
    )
    wave2=collection_audit.write_collection_audit(
        actions,etfs,"WAVE_02_ETF_OHLCV",tmp_path,write_excel=False,
    )

    assert builds == 1
    initial_df=pd.read_csv(initial,sep=";",encoding="utf-8-sig")
    for path,wave in ((wave0,"WAVE_00_ETF_TICKERS"),(wave1,"WAVE_01_ACTION_OHLCV"),(wave2,"WAVE_02_ETF_OHLCV")):
        current=pd.read_csv(path,sep=";",encoding="utf-8-sig")
        assert current["inventory_reused"].astype(str).str.lower().eq("true").all()
        assert current["collection"].eq(wave).all()
        shared=["asset_class","field","status","available_rows","missing_rows","universe_rows","coverage_pct","source_theorique","sources_reelles","source_urls","evidence_levels","last_as_of","source_reelle_absente"]
        pd.testing.assert_frame_equal(
            current[shared].reset_index(drop=True),
            initial_df[shared].reset_index(drop=True),
            check_dtype=False,
        )


def test_mutating_wave_forces_fresh_inventory_after_reused_waves(tmp_path,monkeypatch) -> None:
    collection_audit._reset_audit_cache_for_tests()
    monkeypatch.setenv("GITHUB_ACTIONS","true")
    monkeypatch.setenv("PEA_SLOW_SOURCE_MODE","LIVE")
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"missing_provenance.csv"))
    actions,etfs=_frames()

    collection_audit.write_collection_audit(actions,etfs,"WAVE_00_INITIAL_STATE",tmp_path,write_excel=False)
    collection_audit.write_collection_audit(actions,etfs,"WAVE_01_ACTION_OHLCV",tmp_path,write_excel=False)

    changed=actions.copy()
    changed["metric_a"]=["1","2"]
    path=collection_audit.write_collection_audit(
        changed,etfs,"WAVE_03_DERIVED_OHLCV",tmp_path,write_excel=False,
    )
    current=pd.read_csv(path,sep=";",encoding="utf-8-sig")
    action_metric=current[(current["asset_class"]=="ACTION")&(current["field"]=="metric_a")].iloc[0]
    assert str(action_metric["inventory_reused"]).lower() == "false"
    assert action_metric["status"] == "AVAILABLE"
    assert int(action_metric["available_rows"]) == 2
