from __future__ import annotations

import threading

from v182.reporting import unified_runner


def _install_common_stubs(monkeypatch, *, etf_mt_stub, structure_stub, post_barrier=None):
    monkeypatch.setattr(unified_runner.enrichment_run,"run",lambda: {"status":"SUCCESS"})
    monkeypatch.setattr(unified_runner.etf_structure_refresh,"run",structure_stub)
    monkeypatch.setattr(unified_runner.etf_mt_v2081_run,"run",etf_mt_stub)
    monkeypatch.setattr(
        unified_runner.sector_rotation_v2_shadow_run,
        "run",
        lambda root: {
            "status":"SUCCESS",
            "pit_oos_validation":{
                "status":"WAIT_FOR_PIT_HISTORY",
                "promotion_ready":False,
                "decision_influence":0.0,
            },
        },
    )
    monkeypatch.setattr(unified_runner.committee_master_v21_4,"run",lambda root: {"status":"SUCCESS"})
    monkeypatch.setattr(unified_runner.sector_rotation_v2_decision_context,"run",lambda root: {"status":"SUCCESS"})
    monkeypatch.setattr(unified_runner.beta_correlation_engine,"run",lambda root: {"status":"SUCCESS"})

    def risk_control(root):
        if post_barrier is not None:
            post_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    def ci(root):
        if post_barrier is not None:
            post_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    monkeypatch.setattr(unified_runner.android_risk_control_center,"run",risk_control)
    monkeypatch.setattr(unified_runner.committee_ci_explainability,"run",ci)
    monkeypatch.setattr(unified_runner,"write_step_runtime",lambda *args,**kwargs: {})


def test_cache_only_etf_branches_and_post_risk_outputs_overlap(tmp_path,monkeypatch):
    cache=tmp_path/"data"/"cache"/"etf"
    cache.mkdir(parents=True)
    (cache/"history_test.parquet").write_bytes(b"cache-present")

    etf_barrier=threading.Barrier(2)
    post_barrier=threading.Barrier(2)
    mt_kwargs={}

    def structure(root):
        etf_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    def etf_mt(root,**kwargs):
        mt_kwargs.update(kwargs)
        etf_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    _install_common_stubs(
        monkeypatch,
        etf_mt_stub=etf_mt,
        structure_stub=structure,
        post_barrier=post_barrier,
    )

    payload=unified_runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert mt_kwargs["history_cache_dir"] == cache
    assert mt_kwargs["refresh_history"] is False
    assert mt_kwargs["refresh_if_reuse_cache_missing"] is True
    assert payload["steps"]["etf_structure"]["status"] == "SUCCESS"
    assert payload["steps"]["etf_mt"]["status"] == "SUCCESS"
    assert payload["steps"]["risk_control_center"]["status"] == "SUCCESS"
    assert payload["steps"]["ci_explainability"]["status"] == "SUCCESS"


def test_missing_primary_etf_cache_preserves_sequential_fallback(tmp_path,monkeypatch):
    structure_done=threading.Event()
    order=[]
    mt_kwargs={}

    def structure(root):
        order.append("structure")
        structure_done.set()
        return {"status":"SUCCESS"}

    def etf_mt(root,**kwargs):
        assert structure_done.is_set(), "ETF MT fallback must not overlap structure when cache is absent"
        order.append("etf_mt")
        mt_kwargs.update(kwargs)
        return {"status":"SUCCESS"}

    _install_common_stubs(
        monkeypatch,
        etf_mt_stub=etf_mt,
        structure_stub=structure,
    )

    payload=unified_runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert order[:2] == ["structure","etf_mt"]
    assert mt_kwargs["refresh_history"] is False
    assert mt_kwargs["refresh_if_reuse_cache_missing"] is True


def test_risk_context_remains_before_parallel_output_builders(tmp_path,monkeypatch):
    cache=tmp_path/"data"/"cache"/"etf"
    cache.mkdir(parents=True)
    (cache/"history_test.parquet").write_bytes(b"cache-present")
    risk_done=threading.Event()
    post_barrier=threading.Barrier(2)

    def structure(root):
        return {"status":"SUCCESS"}

    def etf_mt(root,**kwargs):
        return {"status":"SUCCESS"}

    _install_common_stubs(
        monkeypatch,
        etf_mt_stub=etf_mt,
        structure_stub=structure,
    )

    def risk(root):
        risk_done.set()
        return {"status":"SUCCESS"}

    def control(root):
        assert risk_done.is_set()
        post_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    def ci(root):
        assert risk_done.is_set()
        post_barrier.wait(timeout=5)
        return {"status":"SUCCESS"}

    monkeypatch.setattr(unified_runner.beta_correlation_engine,"run",risk)
    monkeypatch.setattr(unified_runner.android_risk_control_center,"run",control)
    monkeypatch.setattr(unified_runner.committee_ci_explainability,"run",ci)

    payload=unified_runner.run(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["steps"]["risk_context"]["status"] == "SUCCESS"
