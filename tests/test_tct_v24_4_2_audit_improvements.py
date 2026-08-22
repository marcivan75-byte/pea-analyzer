from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.features import tct_catalyst_context_v24_4_2 as feature
from v182.reporting import tct_next_session_catalyst_run as legacy_runner
from v182.reporting import tct_next_session_catalyst_run_v24_4_1 as runner_41
from v182.reporting.tct_pit_ohlc_ledger_v24_4_2 import build_ohlc_observations
from v182.reporting.tct_v24_4_2_pit_lineage import apply_lineage
from v182.reporting.tct_v24_4_2_pit_validator import validate_ledger
from v182.sources.global_market_snapshot import GlobalMarketSnapshot, _risk_on_from_completed
from v182.sources.tct_catalyst_news_v24_4_2 import classify_headline


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def _gates() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_2_VALIDATION_GATES.json").read_text(encoding="utf-8"))


def test_v241_import_does_not_mutate_v240_runner_globals():
    assert runner_41.VERSION == "TCT_V24.4.1_NEXT_SESSION_CATALYST_CYCLE_SHADOW"
    assert legacy_runner.CONFIG == "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json"
    assert legacy_runner.VERSION == "TCT_V24.4.0_NEXT_SESSION_CATALYST_CYCLE_SHADOW"


def test_v242_governance_weights_and_europe_risk_on():
    cfg = _cfg()
    assert sum(cfg["movement_potential_weights"].values()) == 1.0
    assert sum(cfg["direction_weights"].values()) == 1.0
    assert sum(cfg["candidate_selection"]["priority_weights"].values()) == 1.0
    risk = cfg["global_market"]["risk_on_weights"]
    assert abs(sum(risk.values()) - 1.0) < 1e-9
    assert risk["EUROSTOXX50"] + risk["CAC40"] + risk["DAX"] >= 0.15
    assert cfg["governance"]["decision_influence"] == 0.0
    assert cfg["governance"]["score_influence"] == 0.0
    assert cfg["governance"]["holdout_locked"] is True
    assert cfg["data_policy"]["intraday_bars_forbidden"] is True
    assert cfg["data_policy"]["five_minute_forbidden"] is True


def test_config_driven_news_negation_and_pea_events():
    cfg = _cfg()
    assert classify_headline("Company denies fraud investigation rumors", cfg)[0] == "OTHER_NEWS"
    assert classify_headline("Company faces accounting investigation over revenue", cfg)[0] == "FRAUD_INVESTIGATION"
    assert classify_headline("Public tender offer launched for remaining shares", cfg)[0] == "TENDER_OFFER"
    assert classify_headline("Trading suspended pending announcement", cfg)[0] == "TRADING_SUSPENSION"
    assert classify_headline("Placement privé accéléré auprès des investisseurs", cfg)[0] == "PRIVATE_PLACEMENT"
    assert classify_headline("Company added to index next month", cfg)[0] == "INDEX_INCLUSION"


def test_candidate_selection_publishes_rank_reason_and_is_bounded():
    cfg = _cfg()
    rows = []
    for i in range(80):
        rows.append(
            {
                "isin": f"FR{i:010d}",
                "entry_score": 85 if i < 20 else 40 + i % 20,
                "entry_state": "ENTRY_STRONG_SHADOW" if i < 20 else "WAIT_SHADOW",
                "exit_risk_score": 75 if 20 <= i < 30 else 25,
                "news_catalyst_score": 80 if 30 <= i < 45 else 10,
                "days_to_earnings": i % 12,
                "atr14_pct": 0.06 if 35 <= i < 40 else 0.02,
                "source_t1_quality": 70 + i % 20,
                "source_t2_quality": 65 + i % 15,
                "preopen_scope_eligible": i < 40,
                "preselection_horizons": "TCT" if i < 20 else "CT" if i < 40 else "",
            }
        )
    selected = feature.select_catalyst_candidates(pd.DataFrame(rows), cfg)
    assert len(selected) == 40
    assert selected["candidate_rank"].tolist() == list(range(1, 41))
    assert selected["preopen_scope_eligible"].astype(bool).all()
    assert selected["candidate_rank_reason"].notna().all()
    reasons = "|".join(selected["candidate_rank_reason"].astype(str))
    assert "ENTRY_READY_OR_STRONG" in reasons
    assert "EARNINGS_WITHIN_7D" in reasons
    assert "EXISTING_NEWS_HIGH" in reasons
    assert "HIGH_ATR" in reasons


def test_candidate_selection_fails_closed_without_preselection_marker():
    cfg = _cfg()
    seed = pd.DataFrame([{"isin": "FR0000000001", "entry_score": 90}])
    assert feature.select_catalyst_candidates(seed, cfg).empty


def test_global_risk_on_supports_european_components():
    weights = {"SP500": 0.1, "EUROSTOXX50": 0.4, "CAC40": 0.2, "DAX": 0.1, "VIX_INVERSE": 0.2}
    completed = {"SP500": 0.0, "EUROSTOXX50": 2.0, "CAC40": 2.0, "DAX": 2.0, "VIX": -2.0}
    value = _risk_on_from_completed(completed, weights)
    assert value is not None
    assert value > 75.0


def test_scored_candidate_persists_europe_and_vix_context():
    cfg = _cfg()
    market = GlobalMarketSnapshot(65.0, 40.0, {"VIX": -1.2, "EUROSTOXX50": 1.1, "CAC40": 0.9, "DAX": 1.0}, {}, "TEST", ())
    row = pd.Series({"entry_score": 80, "exit_risk_score": 20, "atr14_pct": 0.03, "range_expansion": 1.2, "entry_confirmation_count": 3, "days_to_earnings": 3})
    scored = feature.score_candidate(row, None, market, phase="PREOPEN", cfg=cfg)
    assert scored["global_vix_return_pct"] == -1.2
    assert scored["global_eurostoxx50_return_pct"] == 1.1


def test_ohlc_ledger_and_lineage_use_first_next_session_and_multilabels():
    cfg = _cfg()
    dates = pd.to_datetime(["2026-08-18", "2026-08-19", "2026-08-20"])
    history = pd.DataFrame(
        {
            "Open": [98.0, 100.0, 102.0],
            "High": [101.0, 102.0, 105.0],
            "Low": [97.0, 99.0, 99.0],
            "Close": [100.0, 100.0, 104.0],
            "Volume": [1000, 1200, 1500],
        },
        index=dates,
    )
    cfg31 = json.loads((ROOT / "config" / "TCT_V24_3_1_DAILY_TRADER_SHADOW.json").read_text(encoding="utf-8"))
    mapping = pd.DataFrame([{"isin": "FRTEST", "yahoo_ticker": "TEST.PA"}])
    ohlc = build_ohlc_observations(mapping, {"TEST.PA": history}, cfg31, observed_at_utc="2026-08-20T20:00:00+00:00", recent_bars=10)
    assert {"session_open", "session_high", "session_low", "session_close"}.issubset(ohlc.columns)

    prediction = pd.DataFrame(
        [
            {
                "version": "TCT_V24.4.2_NEXT_SESSION_CATALYST_CYCLE_SHADOW",
                "phase": "PREOPEN",
                "isin": "FRTEST",
                "yahoo_ticker": "TEST.PA",
                "as_of_date": "2026-08-19",
                "reference_close": 100.0,
                "atr14_pct": 0.02,
                "movement_potential_score": 80.0,
                "technical_impulse_score": 60.0,
                "direction_bias_score": 40.0,
                "technical_direction_score": 30.0,
                "snapshot_generated_at_utc": "2026-08-20T06:40:00+00:00",
                "snapshot_key": "2026-08-20|PREOPEN|FRTEST",
            }
        ]
    )
    enriched, stats = apply_lineage(prediction, ohlc, minimum_snapshot_coverage=0.8, labeled_at_utc="2026-08-20T20:00:00+00:00", cfg=cfg)
    row = enriched.iloc[0]
    assert row["outcome_as_of_date"] == "2026-08-20"
    assert round(float(row["realized_open_gap_pct"]), 6) == 2.0
    assert round(float(row["realized_session_range_pct"]), 6) == 6.0
    assert round(float(row["realized_session_abs_extreme_pct"]), 6) == 5.0
    assert round(float(row["significant_move_threshold_pct"]), 6) == 2.5
    assert float(row["significant_session_move_flag"]) == 1.0
    assert stats["qualified_snapshots"] == 1


def test_validator_uses_session_extreme_as_primary_amplitude():
    gates = _gates()
    rows = []
    for i in range(12):
        rows.append(
            {
                "version": "TCT_V24.4.2_NEXT_SESSION_CATALYST_CYCLE_SHADOW",
                "phase": "PREOPEN",
                "isin": f"FR{i}",
                "snapshot_generated_at_utc": "2026-08-21T06:40:00+00:00",
                "movement_potential_score": 90 - i,
                "technical_impulse_score": 40 + i,
                "direction_bias_score": 30.0,
                "technical_direction_score": 30.0,
                "realized_session_abs_extreme_pct": 12 - i,
                "realized_close_to_close_return_pct": 1.0 if i < 6 else -1.0,
                "realized_open_gap_pct": 0.5,
                "realized_session_range_pct": 2.0,
                "realized_max_adverse_excursion_pct": -0.5,
                "significant_session_move_flag": 1.0 if i < 5 else 0.0,
                "sector_yf": "Technology" if i < 6 else "Industrials",
                "global_risk_on_score": 65.0,
                "global_vix_return_pct": float(i - 6),
                "candidate_rank_reason": "COMPOSITE_FILL",
            }
        )
    payload, slices, _ = validate_ledger(pd.DataFrame(rows), gates)
    assert payload["amplitude_label"] == "realized_session_abs_extreme_pct"
    assert payload["primary_metrics"]["top10_absolute_mover_recall"] is not None
    assert "significant_move_precision" in payload["primary_metrics"]
    assert isinstance(slices, pd.DataFrame)


def test_v242_significant_move_definition_is_explicit():
    cfg = _cfg()
    spec = cfg["significant_move"]
    assert spec["absolute_floor_pct"] == 2.0
    assert spec["atr_multiple"] == 1.25
    assert spec["primary_amplitude_label"] == "realized_session_abs_extreme_pct"
