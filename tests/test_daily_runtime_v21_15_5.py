from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import daily_consolidated_runner_v21_15_5 as consolidated
from v182.reporting import daily_tactical_super_runner_v21_15_5 as tactical


def test_daily_tct_scope_is_current_baseline_top_n_only():
    frame = pd.DataFrame({
        "isin": ["A", "B", "C", "D"],
        "tct_baseline_rank": [1, 2, 3, 4],
        "tct_baseline_coverage": [0.80, 0.70, 0.90, 0.40],
    })
    cfg = {"scope": {"baseline_top_n": 2, "baseline_min_coverage": 0.60}}
    scoped = tactical._daily_tct_scope(frame, cfg)
    assert scoped["isin"].tolist() == ["A", "B"]


def test_selected_action_ct_scope_uses_configured_top_n(tmp_path: Path):
    (tmp_path / "outputs" / "daily_tct_ct").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    cfg = {"candidate_selection": {"preselection_scope": {"action_ct_top_n": 2}}}
    (tmp_path / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").write_text(json.dumps(cfg), encoding="utf-8")
    rows = pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "CT", "isin": "A", "score": 90, "decision": "BUY_CANDIDATE", "status": "SCORABLE"},
        {"asset_class": "ACTION", "horizon": "CT", "isin": "B", "score": 80, "decision": "WATCH", "status": "SCORABLE"},
        {"asset_class": "ACTION", "horizon": "CT", "isin": "C", "score": 70, "decision": "REVIEW", "status": "SCORABLE"},
        {"asset_class": "ETF", "horizon": "CT", "isin": "E", "score": 99, "decision": "BUY_CANDIDATE", "status": "SCORABLE"},
    ])
    rows.to_csv(tmp_path / "outputs" / "daily_tct_ct" / "DAILY_TCT_CT_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    assert tactical._selected_action_ct_isins(tmp_path) == {"A", "B"}


def test_investing_daily_retry_is_bounded_and_original_restored(monkeypatch, tmp_path: Path):
    seen = {}

    def collector(*args, **kwargs):
        seen.update(kwargs)
        return "COLLECTED"

    monkeypatch.setattr(tactical.selected_source, "collect_technical_context_cached", collector)
    original_reference = tactical.selected_source.collect_technical_context_cached

    def fake_enrich(rows, root, profile):
        result = tactical.selected_source.collect_technical_context_cached(
            rows,
            root / "cache.json",
            root / "map.json",
            refresh_budget=40,
            ttl_hours=6,
            request_start_interval_seconds=1,
            max_workers=4,
        )
        assert result == "COLLECTED"
        return rows.copy(), {"status": "SUCCESS"}

    monkeypatch.setattr(tactical.selected_source, "enrich_selected_rows", fake_enrich)
    rows = pd.DataFrame({"isin": ["A"]})
    _, payload = tactical._enrich_selected_daily(rows, tmp_path)
    assert seen["refresh_budget"] == tactical.INVESTING_DAILY_RETRY_BUDGET
    assert seen["timeout_seconds"] == tactical.INVESTING_DAILY_TIMEOUT_SECONDS
    assert payload["investing_full_refresh_weekly_preserved"] is True
    assert tactical.selected_source.collect_technical_context_cached is original_reference


def test_postmarket_lineage_bool_dtype_fix_is_scoped_and_restored(monkeypatch, tmp_path: Path):
    lineage = tactical.base.postmarket.lineage

    def fake_apply(catalyst_ledger, ohlc_ledger, **kwargs):
        assert catalyst_ledger["pit_label_evaluable"].dtype == object
        catalyst_ledger.loc[:, "pit_label_evaluable"] = False
        return catalyst_ledger, {"ok": True}

    monkeypatch.setattr(lineage, "apply_lineage", fake_apply)
    original_reference = lineage.apply_lineage

    def fake_run(root):
        frame = pd.DataFrame({"pit_label_evaluable": [float("nan")]})
        output, _ = lineage.apply_lineage(frame, pd.DataFrame(), minimum_snapshot_coverage=0.5, labeled_at_utc="x", cfg={})
        assert output["pit_label_evaluable"].iloc[0] is False or output["pit_label_evaluable"].iloc[0] == False
        return {"status": "SUCCESS"}

    payload = tactical._run_postmarket_dtype_safe(fake_run, tmp_path)
    assert payload["status"] == "SUCCESS"
    assert lineage.apply_lineage is original_reference


def test_collection_code_contract_ignores_unrelated_files_but_tracks_collection_code(tmp_path: Path):
    for relative in consolidated.CACHE_CONTRACT_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    first = consolidated._collection_code_contract(tmp_path)
    unrelated = tmp_path / ".github" / "workflows" / "daily.yml"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("name: changed", encoding="utf-8")
    assert consolidated._collection_code_contract(tmp_path) == first
    tracked = tmp_path / consolidated.CACHE_CONTRACT_FILES[0]
    tracked.write_text("semantic collection change", encoding="utf-8")
    assert consolidated._collection_code_contract(tmp_path) != first
