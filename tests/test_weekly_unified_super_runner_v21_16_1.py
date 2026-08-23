from __future__ import annotations

from threading import Event

import pandas as pd

from v182.reporting import weekly_unified_super_runner_v21_16_1 as weekly


def test_parallel_safe_horizons_preserves_requested_order(monkeypatch):
    frame = pd.DataFrame({"isin": ["A", "B"]})
    registry = {"version": "TEST"}

    def decisions(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon]})

    def coverage(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon], "criterion": ["x"]})

    monkeypatch.setattr(weekly.committee_master_run, "decisions_from_scores", decisions)
    monkeypatch.setattr(weekly.committee_master_run, "criterion_coverage_report", coverage)

    requested = ["CT", "MT", "SHORT", "TOP_DOWN"]
    decision_parts, coverage_parts, failures = weekly._parallel_safe_horizons(
        frame, registry, "ACTION", requested
    )

    assert [part.iloc[0]["horizon"] for part in decision_parts] == requested
    assert [part.iloc[0]["horizon"] for part in coverage_parts] == requested
    assert failures == []


def test_parallel_reference_scoring_preserves_horizon_order():
    def original(_frame, _registry, asset_class, horizons):
        horizon = list(horizons)[0]
        return pd.DataFrame({"asset_class": [asset_class], "horizon": [horizon], "score": [1.0]})

    wrapped = weekly._parallel_decisions_from_scores(original)
    requested = ["CT", "MT", "SHORT", "TOP_DOWN"]
    result = wrapped(pd.DataFrame({"isin": ["A"]}), {}, "ACTION", requested)
    assert result["horizon"].tolist() == requested


def test_sector_starts_after_structure_and_before_historical_sector_join(monkeypatch, tmp_path):
    sector_started = Event()
    calls: list[str] = []

    def refresh():
        calls.append("refresh")
        return {"status": "SUCCESS"}

    def structure(root):
        calls.append("structure")
        return {"status": "SUCCESS"}

    def sector(root):
        calls.append("sector_actual")
        sector_started.set()
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base.enrichment_run, "run", refresh)
    monkeypatch.setattr(weekly.base.etf_structure_refresh, "run", structure)
    monkeypatch.setattr(weekly.base.sector_rotation_v2_shadow_run, "run", sector)

    def fake_base_run(root):
        weekly.base.enrichment_run.run()
        weekly.base.etf_structure_refresh.run(root)
        assert sector_started.wait(timeout=2.0)
        calls.append("etf_mt_remaining")
        result = weekly.base.sector_rotation_v2_shadow_run.run(root)
        calls.append("committee_after_sector_join")
        assert result["status"] == "SUCCESS"
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base, "run", fake_base_run)

    payload = weekly.run(root=tmp_path)

    assert payload["status"] == "SUCCESS"
    assert calls.index("structure") < calls.index("sector_actual")
    assert calls.index("sector_actual") < calls.index("committee_after_sector_join")
    assert calls.index("sector_actual") <= calls.index("etf_mt_remaining")
    audit = (tmp_path / "outputs/audit/WEEKLY_UNIFIED_SUPER_RUNTIME_V21_16_1.json").read_text()
    assert '"committee_waits_for_sector_rotation": true' in audit
    assert '"decision_logic_changed": false' in audit


def test_refresh_failure_does_not_start_background_sector(monkeypatch, tmp_path):
    calls: list[str] = []

    def refresh_failure():
        calls.append("refresh_failure")
        raise RuntimeError("REFRESH_FAILURE")

    def structure(root):
        calls.append("structure")
        return {"status": "SUCCESS"}

    def sector(root):
        calls.append("sector")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(weekly.base.enrichment_run, "run", refresh_failure)
    monkeypatch.setattr(weekly.base.etf_structure_refresh, "run", structure)
    monkeypatch.setattr(weekly.base.sector_rotation_v2_shadow_run, "run", sector)

    def fake_base_run(root):
        try:
            weekly.base.enrichment_run.run()
        except RuntimeError as exc:
            assert str(exc) == "REFRESH_FAILURE"
        weekly.base.etf_structure_refresh.run(root)
        # Historical unified_runner would skip sector after failed refresh.
        return {"status": "PARTIAL_SUCCESS"}

    monkeypatch.setattr(weekly.base, "run", fake_base_run)

    payload = weekly.run(root=tmp_path)
    assert payload["status"] == "PARTIAL_SUCCESS"
    assert "sector" not in calls