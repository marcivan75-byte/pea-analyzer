from pathlib import Path
import json

import pandas as pd

from v182.features.tct_catalyst_context_v24_4_1 import _scheduled_event_proximity, score_candidate
from v182.reporting.tct_v24_4_1_pit_lineage import prediction_fingerprint_v2
from v182.sources.global_market_snapshot import GlobalMarketSnapshot
from v182.sources.tct_catalyst_news_v24_4_1 import CatalystNews, classify_headline


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_1_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def test_v2431_zero_structure_score_is_not_replaced_by_40():
    source = (ROOT / "src" / "v182" / "features" / "tct_daily_trader_v24_3_1.py").read_text(encoding="utf-8")
    assert 'structure_score = _finite(entry_components.get("structure_breakout_retest")) or 40.0' not in source
    assert "if structure_score is None:" in source


def test_event_proximity_no_longer_double_counts_existing_news():
    cfg = _cfg()
    row = pd.Series({"days_to_earnings": None, "news_catalyst_score": 100.0, "funnel_instrument_news_score": 100.0})
    assert _scheduled_event_proximity(row, cfg) is None
    row["days_to_earnings"] = 0
    assert _scheduled_event_proximity(row, cfg) == 100.0


def test_missing_news_source_fails_closed_for_movement_and_direction():
    cfg = _cfg()
    row = pd.Series(
        {
            "entry_score": 95.0,
            "exit_risk_score": 10.0,
            "atr14_pct": 0.04,
            "range_expansion": 1.5,
            "entry_confirmation_count": 4,
            "entry_state": "ENTRY_STRONG_SHADOW",
            "exit_state": "HOLD_SUPPORTIVE_SHADOW",
            "days_to_earnings": None,
        }
    )
    failed_news = CatalystNews(
        None, None, 0.0, 0, 0, (), (),
        "2026-08-20T15:30:00+00:00", "2026-08-21T06:40:00+00:00", "GDELT_WINDOWED", "HTTPError"
    )
    market = GlobalMarketSnapshot(55.0, 40.0, {}, {}, "SUCCESS", ())
    result = score_candidate(row, failed_news, market, phase="PREOPEN", cfg=cfg)
    assert result["movement_potential_coverage"] < cfg["thresholds"]["minimum_movement_coverage_for_scored_alert"]
    assert result["movement_potential_score"] is None
    assert result["direction_bias_score"] is None
    assert result["catalyst_state"] == "DATA_DEGRADED_SHADOW"


def test_zero_news_is_observed_evidence_not_a_missing_source():
    cfg = _cfg()
    row = pd.Series(
        {
            "entry_score": 75.0,
            "exit_risk_score": 20.0,
            "atr14_pct": 0.03,
            "range_expansion": 1.1,
            "entry_confirmation_count": 2,
            "entry_state": "WAIT_SHADOW",
            "exit_state": "HOLD_SUPPORTIVE_SHADOW",
            "days_to_earnings": 4,
        }
    )
    no_news = CatalystNews(
        None, None, 0.0, 0, 0, (), (),
        "2026-08-20T15:30:00+00:00", "2026-08-21T06:40:00+00:00", "GDELT_WINDOWED", None
    )
    market = GlobalMarketSnapshot(50.0, 20.0, {}, {}, "SUCCESS", ())
    result = score_candidate(row, no_news, market, phase="PREOPEN", cfg=cfg)
    assert result["movement_potential_coverage"] >= 0.70
    assert result["movement_potential_score"] is not None


def test_generic_investigation_is_not_forced_into_fraud_event():
    cfg = _cfg()
    weights = cfg["news"]["event_weights"]
    event, _, _ = classify_headline("Company launches internal investigation into delivery delays", weights)
    assert event == "OTHER_NEWS"
    event, magnitude, direction = classify_headline("Regulator opens fraud investigation into Company accounts", weights)
    assert event == "FRAUD_INVESTIGATION"
    assert magnitude == 95.0
    assert direction == -90.0


def test_generic_ma_has_no_automatic_directional_bias():
    cfg = _cfg()
    assert cfg["news"]["event_weights"]["MA_ACQUISITION"]["magnitude"] == 90
    assert cfg["news"]["event_weights"]["MA_ACQUISITION"]["direction"] == 0


def test_canonical_fingerprint_ignores_unrelated_schema_additions():
    row = pd.Series(
        {
            "version": "TCT_V24.4.1_NEXT_SESSION_CATALYST_CYCLE_SHADOW",
            "phase": "PREOPEN",
            "isin": "FR0000000001",
            "movement_potential_score": 72.5,
            "direction_bias_score": 31.0,
            "snapshot_key": "2026-08-21|PREOPEN|FR0000000001",
        }
    )
    enriched = row.copy()
    enriched["future_unrelated_column"] = "must_not_change_old_hash"
    assert prediction_fingerprint_v2(row) == prediction_fingerprint_v2(enriched)


def test_v2441_isolated_pit_epoch_and_weights_are_coherent():
    cfg = _cfg()
    gates = json.loads((ROOT / "config" / "TCT_V24_4_1_VALIDATION_GATES.json").read_text(encoding="utf-8"))
    assert cfg["state"]["catalyst_ledger_path"].endswith("TCT_V24_4_1_CATALYST_LEDGER.csv")
    assert gates["validation_epoch"] == "V24.4.1_ONLY_NO_MIX_WITH_V24.4.0"
    assert abs(sum(cfg["movement_potential_weights"].values()) - 1.0) < 1e-12
    assert abs(sum(cfg["direction_weights"].values()) - 1.0) < 1e-12
    assert cfg["governance"]["decision_influence"] == 0.0
    assert cfg["governance"]["promotion_authority"] is False


def test_workflows_activate_v2442_without_intraday_runtime_and_keep_v2441_historical():
    next_session = (ROOT / ".github" / "workflows" / "tct_next_session_context.yml").read_text(encoding="utf-8")
    daily = (ROOT / ".github" / "workflows" / "committee_tct_ct_daily.yml").read_text(encoding="utf-8")
    postmarket = (ROOT / "src" / "v182" / "reporting" / "tct_postmarket_bundle_run.py").read_text(encoding="utf-8")
    assert "tct_next_session_catalyst_run_v24_4_2" in next_session
    assert "tct_v24_4_2_pit_lineage" in next_session
    assert "tct_v24_4_2_pit_validator" in next_session
    assert "TCT_V24_4_2_CATALYST_LEDGER.csv" in next_session
    assert "python -m v182.reporting.tct_postmarket_bundle_run" in daily
    assert "ohlc_ledger.run(root=root)" in postmarket
    assert 'catalyst.run(root=root, phase="POSTMARKET")' in postmarket
    assert "lineage.run(root=root)" in postmarket
    assert "validator.run(root=root)" in postmarket
    assert (ROOT / "src" / "v182" / "reporting" / "tct_next_session_catalyst_run_v24_4_1.py").exists()
    assert "actions_intraday_5m" not in next_session
    assert "5m" not in next_session.lower()
