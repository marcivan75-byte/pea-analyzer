from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import ci_entry_confidence_v22_2 as core
from v182.reporting import ci_entry_watch_v22_2 as watch


def test_v22_2_config_is_shadow_and_weights_sum_to_one():
    cfg = json.loads(Path("config/CI_ENTRY_CONFIDENCE_V22_2.json").read_text(encoding="utf-8"))
    assert abs(sum(float(v) for v in cfg["confidence_weights"].values()) - 1.0) < 1e-12
    assert cfg["governance"]["selection_score_changed"] is False
    assert cfg["governance"]["selection_decision_changed"] is False
    assert cfg["governance"]["real_orders_enabled"] is False
    assert cfg["governance"]["automatic_parameter_promotion"] is False
    assert cfg["governance"]["t1_t2_scope"] == "ACTION_TCT_ONLY"
    assert cfg["governance"]["shadow_until_pit_oos_validation"] is True


def test_next_check_cadence_is_horizon_specific():
    tct = pd.Series({"asset_class": "ACTION", "horizon": "TCT", "v22_2_entry_state": "WAIT"})
    ct = pd.Series({"asset_class": "ACTION", "horizon": "CT", "v22_2_entry_state": "WAIT"})
    mt = pd.Series({"asset_class": "ETF", "horizon": "MT", "v22_2_entry_state": "WAIT"})
    assert watch._next_check(tct)[0] == "PREOPEN_THEN_INTRADAY"
    assert watch._next_check(ct)[0] == "PREOPEN_THEN_CLOSE"
    assert watch._next_check(mt)[0] == "CLOSE"


def test_missing_proof_is_fail_closed_and_actionable():
    row = pd.Series({
        "asset_class": "ACTION", "horizon": "CT", "v22_2_entry_state": "WAIT",
        "v22_2_entry_reasons": "TECHNICAL_HISTORY_MISSING",
        "v22_2_component_provenance_quality": float("nan"),
        "v22_2_component_market_sector_context": float("nan"),
        "v22_2_component_temporal_stability": 40.0,
    })
    gaps = watch._evidence_gaps(row)
    assert "ENTRY_TIMING_DATA" in gaps
    assert "PROVENANCE_QUALITY" in gaps
    assert "MARKET_SECTOR_CONTEXT" in gaps
    assert "TEMPORAL_STABILITY" in gaps
    assert "CT_PRICE_MOMENTUM_VOLUME_TRIGGER" in gaps


def test_ready_for_review_never_means_automatic_order():
    row = pd.Series({"asset_class": "ETF", "horizon": "MT", "v22_2_entry_state": "READY_FOR_REVIEW"})
    phase, when, text = watch._next_check(row)
    assert phase == "NOW"
    assert when == "CI_REVIEW_NOW"
    assert "no automatic order" in text.lower()


def test_state_cache_is_inside_existing_provenance_cache():
    # Governance paths are repository-relative and must remain portable across
    # Linux runners and Windows audit workstations.
    assert core.STATE_CACHE.as_posix().startswith("state/provenance/")


def test_watch_run_enriches_core_without_rewriting_core_file(monkeypatch, tmp_path: Path):
    out = tmp_path / core.OUTPUT
    out.parent.mkdir(parents=True)
    original = pd.DataFrame([{
        "asset_class": "ETF", "horizon": "MT", "isin": "FR001", "name": "ETF Test",
        "score": 88.0, "coverage_pct": 100.0, "decision": "BUY_CANDIDATE",
        "v22_2_entry_state": "WAIT", "v22_2_entry_reasons": "MT_CLOSE_TRIGGER_NOT_YET_CONFIRMED",
        "CI_CONFIDENCE_SCORE_0_100": 72.0, "CI_CONFIDENCE_LEVEL": "INSUFFICIENT_ENTRY_PROOF",
        "v22_2_component_provenance_quality": 80.0,
        "v22_2_component_market_sector_context": 60.0,
        "v22_2_component_temporal_stability": 40.0,
    }])
    original.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    monkeypatch.setattr(watch.core, "run", lambda root: {"status": "SUCCESS", "candidate_rows": 1})
    result = watch.run(tmp_path)
    assert result["status"] == "SUCCESS"
    enriched = pd.read_csv(tmp_path / watch.OUTPUT, sep=";", encoding="utf-8-sig")
    assert enriched.iloc[0]["CI_NEXT_CHECK_PHASE"] == "CLOSE"
    assert bool(enriched.iloc[0]["CI_AUTOMATIC_ORDER_ALLOWED"]) is False
    reloaded_core = pd.read_csv(out, sep=";", encoding="utf-8-sig")
    assert list(reloaded_core.columns) == list(original.columns)
    assert float(reloaded_core.iloc[0]["score"]) == 88.0
