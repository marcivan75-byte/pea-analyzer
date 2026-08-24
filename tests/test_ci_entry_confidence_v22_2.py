from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import ci_entry_confidence_v22_2 as mod


def _cfg():
    return {
        "entry": {
            "dynamic_overextension_volatility_multiple": 2.0,
            "breakout_lookback_sessions": 20,
            "block_on_below_sma200": True,
            "block_on_negative_momentum_acceleration": True,
        },
        "stability": {"strong_observations": 3, "intermediate_observations": 2},
        "confidence_labels": {"strong_min": 80.0, "intermediate_min": 70.0},
    }


def test_tct_requires_exact_t2_confirmation():
    row = pd.Series({"asset_class": "ACTION", "horizon": "TCT", "setup": "T1"})
    state, reasons, timing = mod._entry_state(row, {"history_status": "OK"}, _cfg())
    assert state == "WAIT"
    assert reasons == ["TCT_EXACT_T2_CONFIRMATION_REQUIRED"]
    assert timing == 0.0

    row["setup"] = "T2_CONFIRMATION"
    state, reasons, timing = mod._entry_state(row, {"history_status": "OK"}, _cfg())
    assert state == "READY_FOR_REVIEW"
    assert reasons == ["TCT_EXACT_T2_CONFIRMED"]
    assert timing == 100.0


def test_action_ct_requires_concrete_event_and_momentum():
    row = pd.Series({"asset_class": "ACTION", "horizon": "CT"})
    tech = {
        "history_status": "OK", "dist_sma200": 0.10, "momentum_acceleration": 0.02,
        "overextension_dynamic": False, "breakout_20d": True, "reclaim_sma20": False,
        "macd_hist": 0.1, "macd_hist_accelerating": True, "volume_ratio_20d": 1.2,
    }
    state, reasons, timing = mod._entry_state(row, tech, _cfg())
    assert state == "READY_FOR_REVIEW"
    assert "BREAKOUT_20D_CONFIRMED" in reasons
    assert timing == 100.0

    tech["momentum_acceleration"] = -0.01
    state, reasons, timing = mod._entry_state(row, tech, _cfg())
    assert state == "WAIT"
    assert "MOMENTUM_DECELERATION" in reasons
    assert timing == 45.0


def test_etf_mt_requires_close_trend_and_positive_momentum():
    row = pd.Series({"asset_class": "ETF", "horizon": "MT"})
    tech = {
        "history_status": "OK", "close": 120.0, "sma50": 110.0, "sma200": 100.0,
        "dist_sma200": 0.20, "momentum_acceleration": 0.01, "overextension_dynamic": False,
        "ret20": 0.04, "macd_hist": 0.5,
    }
    state, reasons, timing = mod._entry_state(row, tech, _cfg())
    assert state == "READY_FOR_REVIEW"
    assert "MT_CLOSE_CONFIRMATION" in reasons
    assert timing == 100.0

    tech["sma50"] = 95.0
    state, reasons, timing = mod._entry_state(row, tech, _cfg())
    assert state == "WAIT"
    assert "MT_CLOSE_TRIGGER_NOT_YET_CONFIRMED" in reasons


def test_stability_same_day_rerun_does_not_increment():
    candidates = pd.DataFrame([
        {"asset_class": "ETF", "horizon": "MT", "isin": "FR001", "v22_2_entry_state": "WAIT"}
    ])
    state = pd.DataFrame([
        {"asset_class": "ETF", "horizon": "MT", "isin": "FR001", "last_observed_date": "2026-08-24", "consecutive_observations": 2, "last_entry_state": "WAIT"}
    ])
    counts, next_state = mod._stability_update(candidates, state, "2026-08-24")
    assert counts == [2]
    assert int(next_state.iloc[0]["consecutive_observations"]) == 2

    counts, _ = mod._stability_update(candidates, state, "2026-08-25")
    assert counts == [3]


def test_confidence_label_requires_entry_proof_even_if_score_high():
    cfg = _cfg()
    assert mod._confidence_label(95.0, cfg, "WAIT") == "INSUFFICIENT_ENTRY_PROOF"
    assert mod._confidence_label(85.0, cfg, "READY_FOR_REVIEW") == "STRONG"
    assert mod._confidence_label(75.0, cfg, "READY_FOR_REVIEW") == "INTERMEDIATE"


def test_end_to_end_does_not_mutate_committee_selection(monkeypatch, tmp_path: Path):
    (tmp_path / "config").mkdir(parents=True)
    cfg = {
        "version": "TEST", "candidate_decisions": ["BUY_CANDIDATE"], "monitored_horizons": ["MT"],
        "confidence_weights": {
            "selection_coverage": .30, "provenance_quality": .20, "entry_timing": .20,
            "trend_momentum": .10, "market_sector_context": .10, "temporal_stability": .10,
        },
        "confidence_labels": {"strong_min": 80.0, "intermediate_min": 70.0},
        "entry": {
            "breakout_lookback_sessions": 20, "dynamic_overextension_volatility_multiple": 2.0,
            "block_on_below_sma200": True, "block_on_negative_momentum_acceleration": True,
        },
        "stability": {"strong_observations": 3, "intermediate_observations": 2},
    }
    (tmp_path / mod.CONFIG).write_text(json.dumps(cfg), encoding="utf-8")
    out = tmp_path / "outputs/committee_master"; out.mkdir(parents=True)
    decisions = pd.DataFrame([{
        "asset_class": "ETF", "horizon": "MT", "isin": "FR001", "name": "ETF Test",
        "score": 88.0, "coverage_pct": 100.0, "decision": "BUY_CANDIDATE", "risk_verdict": "GREEN",
    }])
    decisions.to_csv(out / "COMMITTEE_DECISIONS.csv", sep=";", index=False, encoding="utf-8-sig")
    inp = tmp_path / "inputs"; inp.mkdir()
    pd.DataFrame([{"isin": "FR001", "name": "ETF Test", "yahoo_ticker": "ETF.PA", "provider": "TEST"}]).to_csv(inp / "V18.2_PEA_ETF_MASTER.csv", sep=";", index=False, encoding="utf-8-sig")

    monkeypatch.setattr(mod, "_load_candidate_histories", lambda root, candidates, meta_map: {"ETF.PA": pd.DataFrame({"close": range(1, 251)})})
    monkeypatch.setattr(mod, "_technical_snapshot", lambda history, cfg: {
        "history_status": "OK", "close": 120.0, "sma20": 115.0, "sma50": 110.0, "sma200": 100.0,
        "dist_sma50": .09, "dist_sma200": .20, "ret20": .04, "momentum_acceleration": .01,
        "volatility_20d": .02, "macd_hist": .5, "macd_hist_accelerating": True,
        "volume_ratio_20d": 1.1, "breakout_20d": False, "reclaim_sma20": False, "overextension_dynamic": False,
    })
    result = mod.run(tmp_path)
    assert result["status"] == "SUCCESS"
    original = pd.read_csv(out / "COMMITTEE_DECISIONS.csv", sep=";", encoding="utf-8-sig")
    assert float(original.iloc[0]["score"]) == 88.0
    assert original.iloc[0]["decision"] == "BUY_CANDIDATE"
    generated = pd.read_csv(tmp_path / mod.OUTPUT, sep=";", encoding="utf-8-sig")
    assert "CI_CONFIDENCE_SCORE_0_100" in generated.columns
    assert generated.iloc[0]["v22_2_entry_state"] == "READY_FOR_REVIEW"
    assert bool(generated.iloc[0]["v22_2_real_order"]) is False
