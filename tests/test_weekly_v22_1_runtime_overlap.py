from __future__ import annotations

from pathlib import Path
from threading import Event

import pandas as pd

from v182.audit import quality as quality_module
from v182.reporting import run as pipeline
from v182.reporting import weekly_unified_super_runner_v22_1 as runner
from v182.sources import morningstar_actions


def test_v22_1_reuses_prefetched_morningstar_at_original_application_point(monkeypatch, tmp_path: Path) -> None:
    calls = {"morningstar": 0, "collect56": 0, "exports": 0}
    expected = ([{"isin": "FR0000000001", "field": "morningstar_action_score", "value": 80.0}], [])

    def fake_morningstar(actions_df, snapshot_path, worklist_path):
        calls["morningstar"] += 1
        assert list(actions_df["isin"]) == ["FR0000000001"]
        return expected

    def fake_collect56(actions_df, etf_df, cfg, finnhub_key, *, run_wave5, run_wave6):
        calls["collect56"] += 1
        return ([], []), ([], [])

    def fake_parallel_exports(*args, **kwargs):
        calls["exports"] += 1
        return True

    monkeypatch.setattr(morningstar_actions, "load_authorized_snapshot", fake_morningstar)
    monkeypatch.setattr(pipeline, "_collect_wave5_wave6_parallel", fake_collect56)
    monkeypatch.setattr(runner, "_parallel_excel_exports", fake_parallel_exports)

    observed = {}

    def fake_previous_run(root):
        actions = pd.DataFrame({"isin": ["FR0000000001"]})
        etfs = pd.DataFrame({"isin": ["FR0010000001"]})
        cfg = {
            "morningstar_actions": {
                "snapshot_path": "inputs/ms.csv",
                "worklist_path": "outputs/gaps/ms.csv",
            }
        }
        pipeline._collect_wave5_wave6_parallel(
            actions,
            etfs,
            cfg,
            "key",
            run_wave5=True,
            run_wave6=True,
        )
        observed["morningstar"] = morningstar_actions.load_authorized_snapshot(
            actions,
            root / "inputs/ms.csv",
            root / "outputs/gaps/ms.csv",
        )
        observed["exports"] = pipeline._export_excel_reports(
            actions, etfs, {}, {}, [], "FULL"
        )
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert observed["morningstar"] == expected
    assert observed["exports"] is True
    assert calls == {"morningstar": 1, "collect56": 1, "exports": 1}
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json").read_text(encoding="utf-8")
    assert '"morningstar_prefetch_started": true' in audit
    assert '"morningstar_prefetch_fallback": false' in audit
    assert '"morningstar_application_position_changed": false' in audit


def test_final_audit_overlaps_quality_but_quality_waits_for_audit(monkeypatch, tmp_path: Path) -> None:
    audit_started = Event()
    release_audit = Event()
    audit_finished = Event()
    calls = {"audit": 0, "quality": 0}

    def fake_audit(actions, etfs, wave_id, *, failures=None, source_context=""):
        assert wave_id == "WAVE_99_FINAL"
        calls["audit"] += 1
        audit_started.set()
        assert release_audit.wait(timeout=2.0)
        audit_finished.set()

    class FakeQuality:
        passed = True
        checks = []

    def fake_quality(*args, **kwargs):
        calls["quality"] += 1
        assert audit_started.wait(timeout=2.0)
        release_audit.set()
        return FakeQuality()

    monkeypatch.setattr(pipeline, "_audit", fake_audit)
    monkeypatch.setattr(quality_module, "run_quality_gates", fake_quality)

    def fake_previous_run(root):
        frame = pd.DataFrame({"isin": ["FR0000000001"]})
        pipeline._audit(frame, frame, "WAVE_99_FINAL", failures=[], source_context="final")
        result = quality_module.run_quality_gates(frame, frame, {}, {}, {}, {})
        assert result.passed is True
        assert audit_finished.is_set()
        return {"status": "SUCCESS"}

    monkeypatch.setattr(runner.previous, "run", fake_previous_run)
    payload = runner.run(tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls == {"audit": 1, "quality": 1}
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V22_1.json").read_text(encoding="utf-8")
    assert '"final_audit_quality_overlap_started": true' in audit
    assert '"quality_waits_for_final_audit": true' in audit


def test_parallel_excel_exports_keep_exact_outputs(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "outputs"
    output.mkdir()
    monkeypatch.setattr(pipeline, "OUTPUTS", output)
    seen = []

    from v182.reporting import exports

    def fake_master(frame, path, title):
        seen.append(("master", Path(path).name, title, len(frame)))
        Path(path).write_text(title, encoding="utf-8")

    def fake_report(before, after, checks, path):
        seen.append(("report", Path(path).name, len(checks)))
        Path(path).write_text("report", encoding="utf-8")

    monkeypatch.setattr(exports, "export_master_excel", fake_master)
    monkeypatch.setattr(exports, "export_run_report", fake_report)

    result = runner._parallel_excel_exports(
        pd.DataFrame({"isin": ["A"]}),
        pd.DataFrame({"isin": ["E"]}),
        {"ACTION": {}},
        {"ACTION": {}},
        [{"passed": True}],
        "FULL",
    )

    assert result is True
    assert (output / "V18.2_PEA_ACTIONS_ACTUALISE.xlsx").exists()
    assert (output / "V18.2_PEA_ETF_ACTUALISE.xlsx").exists()
    assert (output / "V18.2_RUN_REPORT.xlsx").exists()
    assert {item[1] for item in seen} == {
        "V18.2_PEA_ACTIONS_ACTUALISE.xlsx",
        "V18.2_PEA_ETF_ACTUALISE.xlsx",
        "V18.2_RUN_REPORT.xlsx",
    }


def test_parallel_excel_exports_remain_disabled_for_daily(monkeypatch) -> None:
    from v182.reporting import exports

    def fail(*args, **kwargs):
        raise AssertionError("Daily must not create Weekly Excel exports")

    monkeypatch.setattr(exports, "export_master_excel", fail)
    monkeypatch.setattr(exports, "export_run_report", fail)
    assert runner._parallel_excel_exports(
        pd.DataFrame(), pd.DataFrame(), {}, {}, [], "DAILY_TACTICAL"
    ) is False
