from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace

import pandas as pd
import numpy as np
import pytest

from v182.reporting import collection_audit
from v182.reporting import etf_mt_v2081_run as etf_mt_runner
from v182.reporting import run as enrichment_run
from v182.reporting import waves
from v182.reporting.runtime_telemetry import RuntimeTelemetry
from v182.reporting.unified_runner import _safe_step


def test_runtime_telemetry_persists_stage_wall_cpu_and_categories(tmp_path) -> None:
    telemetry=RuntimeTelemetry(tmp_path,run_id="run-1",profile="DAILY_TACTICAL")
    telemetry.transition("COLLECT","COLLECTION")
    time.sleep(0.002)
    telemetry.transition("COMPUTE","PROCESSING")
    paths=telemetry.finalize("SUCCESS",excel_exports_enabled=False)

    payload=json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    rows=pd.read_csv(paths["csv"],sep=";")
    assert payload["version"]=="PIPELINE_RUNTIME_V21_13_7"
    assert payload["status"]=="SUCCESS"
    assert payload["profile"]=="DAILY_TACTICAL"
    assert payload["active_stage"] is None
    assert payload["decision_logic_changed"] is False
    assert [stage["name"] for stage in payload["stages"]]==["COLLECT","COMPUTE"]
    assert set(payload["totals_by_category_seconds"])=={"COLLECTION","PROCESSING"}
    assert (rows["wall_seconds"]>=0).all()
    assert (rows["cpu_seconds"]>=0).all()


def test_safe_step_reports_runtime_on_success_and_failure() -> None:
    success=_safe_step("ok",lambda: {"value":1})

    def fail():
        raise ValueError("boom")

    failure=_safe_step("fail",fail)
    assert success["status"]=="SUCCESS"
    assert success["wall_seconds"]>=0
    assert success["cpu_seconds"]>=0
    assert failure["status"]=="FAILED"
    assert failure["error"]=="ValueError"
    assert failure["wall_seconds"]>=0


def test_wave3_loads_each_cache_once_and_reuses_etf_frames(monkeypatch) -> None:
    calls=[]

    def fake_frames(cache_dir: str):
        calls.append(Path(cache_dir).name)
        return []

    monkeypatch.setattr(waves,"_history_frames",fake_frames)
    result=waves.wave3_local_features(
        "/cache/actions",{},"/cache/etf",{},max_workers=2,
    )
    assert result==([],[],[])
    assert sorted(calls)==["actions","etf"]


def test_wave3_parallel_helper_preserves_sequential_observations(monkeypatch) -> None:
    dates=pd.bdate_range("2024-01-02",periods=320)

    def frame(tickers: list[str],seed: int) -> pd.DataFrame:
        rng=np.random.default_rng(seed)
        blocks={}
        for index,ticker in enumerate(tickers):
            close=100*np.exp(np.cumsum(rng.normal(0.0002+index*0.00003,0.006,320)))
            blocks[ticker]=pd.DataFrame({
                "Open":close*0.999,"High":close*1.003,"Low":close*0.997,
                "Close":close,"Volume":rng.integers(100_000,900_000,320),
            },index=dates)
        return pd.concat(blocks,axis=1)

    actions=["A1.PA","A2.PA"]; etfs=[f"E{i}.PA" for i in range(6)]
    action_frame=frame(actions,1); etf_frame=frame(etfs,2)
    action_map={ticker:f"AI{i}" for i,ticker in enumerate(actions)}
    etf_map={ticker:f"EI{i}" for i,ticker in enumerate(etfs)}
    expected=(
        waves.wave3_derived_features("actions",action_map,"ACTION",history_frames=[action_frame]),
        waves.wave3_derived_features("etf",etf_map,"ETF",history_frames=[etf_frame]),
        waves.wave3_etf_beta3y("etf",etf_map,history_frames=[etf_frame]),
    )
    monkeypatch.setattr(
        waves,"_history_frames",
        lambda cache: [action_frame] if Path(cache).name=="actions" else [etf_frame],
    )
    actual=waves.wave3_local_features("actions",action_map,"etf",etf_map,max_workers=2)

    def stable(rows: list[dict]) -> list[tuple]:
        return sorted((row["universe"],row["isin"],row["field"],row["value"],row["source"]) for row in rows)

    assert tuple(stable(rows) for rows in actual)==tuple(stable(rows) for rows in expected)


def test_compact_collection_audit_skips_intermediate_excel(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(collection_audit,"actual_sources_by_field",lambda: pd.DataFrame())
    actions=pd.DataFrame({"isin":["A1"],"name":["Action"],"last_close":[10.0]})
    etfs=pd.DataFrame({"isin":["E1"],"name":["ETF"],"last_close":[20.0]})
    path=Path(collection_audit.write_collection_audit(
        actions,etfs,"WAVE_TEST",tmp_path,write_excel=False,source_context="local",
    ))
    assert path.suffix==".csv"
    assert path.exists()
    assert not (tmp_path/"COLLECTION_AUDIT_WAVE_TEST.xlsx").exists()
    assert not (tmp_path/"COLLECTION_DATA_AVAILABILITY_LATEST.xlsx").exists()
    assert (tmp_path/"COLLECTION_AUDIT_HISTORY.csv").exists()


def test_daily_profile_skips_three_unpublished_excel_exports(monkeypatch) -> None:
    from v182.reporting import exports

    calls=[]
    monkeypatch.setattr(exports,"export_master_excel",lambda *args,**kwargs: calls.append("master"))
    monkeypatch.setattr(exports,"export_run_report",lambda *args,**kwargs: calls.append("report"))
    enabled=enrichment_run._export_excel_reports(
        pd.DataFrame(),pd.DataFrame(),{},{},[],"DAILY_TACTICAL",
    )
    assert enabled is False
    assert calls==[]
    enabled=enrichment_run._export_excel_reports(
        pd.DataFrame(),pd.DataFrame(),{},{},[],"FULL",
    )
    assert enabled is True
    assert calls==["master","master","report"]


def test_etf_mt_reuses_primary_cache_without_network_collection(tmp_path, monkeypatch) -> None:
    config=tmp_path/"config"; inputs=tmp_path/"inputs"; cache=tmp_path/"data"/"cache"/"etf"
    config.mkdir(parents=True); inputs.mkdir(parents=True); cache.mkdir(parents=True)
    (config/"V18.2_MASTER_CONFIG.json").write_text(json.dumps({"yfinance":{}}),encoding="utf-8")
    (config/"V20.8_ETF_MT_HIGH_PRECISION.json").write_text("{}",encoding="utf-8")
    (config/"V20.8.2_ETF_MT_DYNAMIC.json").write_text("{}",encoding="utf-8")
    (cache/"history_000.parquet").write_bytes(b"cache-present")
    valid=pd.DataFrame({"isin":["ISIN1"],"name":["ETF"],"yahoo_ticker":["ETF.PA"]})
    monkeypatch.setattr(etf_mt_runner,"load_master",lambda path: valid.copy())
    monkeypatch.setattr(etf_mt_runner,"resolve_etf_tickers",lambda frame,path: (valid.copy(),pd.DataFrame()))
    monkeypatch.setattr(
        etf_mt_runner,
        "download_history",
        lambda **kwargs: pytest.fail("primary-cache reuse must not call Yahoo"),
    )
    histories={"ISIN1":pd.DataFrame({"Close":[1.0],"Volume":[1.0]})}
    monkeypatch.setattr(etf_mt_runner,"load_histories_from_cache",lambda path,mapping: histories)
    monkeypatch.setattr(etf_mt_runner,"sanitize_histories",lambda value: value)
    strict=pd.DataFrame({"instrument_id":["ISIN1"]})
    monkeypatch.setattr(etf_mt_runner,"score_snapshot_integrity",lambda *args: (strict,{"version":"20.8.1"}))
    monkeypatch.setattr(etf_mt_runner,"write_outputs",lambda *args: {"ranking_csv":"strict.csv","summary_json":"strict.json"})
    dynamic=pd.DataFrame({"instrument_id":["ISIN1"],"dynamic_selected":[False]})
    dynamic_summary={"scorable_etfs":1,"regime":{"allowed":False},"selected":[]}
    monkeypatch.setattr(etf_mt_runner,"apply_dynamic_weighting",lambda *args: (dynamic,dynamic_summary))

    result=etf_mt_runner.run(
        tmp_path,history_cache_dir=cache,refresh_history=False,
    )
    assert result["download"]["mode"]=="REUSED_PRIMARY_ETF_CACHE"
    assert result["download"]["network_collection_executed"] is False
    assert result["download"]["network_requests_avoided"]==1

    missing_cache=tmp_path/"data"/"cache"/"missing-etf"
    calls=[]
    monkeypatch.setattr(
        etf_mt_runner,
        "download_history",
        lambda **kwargs: calls.append(kwargs["cache_dir"]) or SimpleNamespace(
            requested=1,successful=["ETF.PA"],failed=[],
        ),
    )
    fallback=etf_mt_runner.run(
        tmp_path,
        history_cache_dir=missing_cache,
        refresh_history=False,
        refresh_if_reuse_cache_missing=True,
    )
    assert calls==[str(missing_cache)]
    assert fallback["download"]["mode"]=="PRIMARY_CACHE_MISS_INCREMENTAL_REFRESH"
    assert fallback["download"]["network_collection_executed"] is True
