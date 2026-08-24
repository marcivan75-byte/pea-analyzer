from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from v182.reporting import market_orientation_v22_2 as mod


def test_regime_classification_and_vote():
    assert mod._volatility_regime(14.0, europe=False) == "RISK_ON"
    assert mod._volatility_regime(21.0, europe=False) == "NEUTRAL"
    assert mod._volatility_regime(31.0, europe=False) == "RISK_OFF"
    assert mod._volatility_regime(19.0, europe=True) == "RISK_ON"
    assert mod._volatility_regime(25.0, europe=True) == "NEUTRAL"
    assert mod._volatility_regime(31.0, europe=True) == "RISK_OFF"
    assert mod._sentiment_regime(40.0) == "RISK_OFF"
    assert mod._sentiment_regime(50.0) == "NEUTRAL"
    assert mod._sentiment_regime(70.0) == "RISK_ON"
    assert mod._vote(["RISK_ON", "RISK_ON", "RISK_OFF"]) == "RISK_ON"


def test_fresh_cache_avoids_network(monkeypatch, tmp_path: Path):
    cache = {
        "version": mod.VERSION,
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "vix": {"value": 16.0, "as_of": "2026-08-24"},
            "cnn_fear_greed": {"value": 65.0, "as_of": "2026-08-24"},
            "vstoxx": {"value": 19.0, "as_of": "2026-08-24"},
        },
    }
    mod._write_cache(tmp_path, cache)

    def forbidden():
        raise AssertionError("network should not be called on fresh cache")

    monkeypatch.setattr(mod, "_fetch_vix_fred", forbidden)
    monkeypatch.setattr(mod, "_fetch_cnn_fear_greed", forbidden)
    monkeypatch.setattr(mod, "_fetch_vstoxx", forbidden)
    result = mod.run(tmp_path)
    assert result["cache"]["hit"] is True
    assert result["orientation"]["us"] == "RISK_ON"
    assert result["orientation"]["europe"] == "RISK_ON"
    assert result["orientation"]["global"] == "RISK_ON"


def test_live_collection_writes_ci_output(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mod, "_fetch_vix_fred", lambda: {"value": 28.0, "as_of": "2026-08-24", "source_status": "LIVE"})
    monkeypatch.setattr(mod, "_fetch_cnn_fear_greed", lambda: {"value": 20.0, "as_of": "2026-08-24", "source_status": "LIVE"})
    monkeypatch.setattr(mod, "_fetch_vstoxx", lambda: {"value": 32.0, "as_of": "2026-08-24", "source_status": "LIVE"})
    result = mod.run(tmp_path)
    assert result["orientation"]["global"] == "RISK_OFF"
    assert result["orientation"]["cnn_extreme_fear_warning"] is True
    assert (tmp_path / "outputs" / "audit" / "MARKET_ORIENTATION_V22_2.json").exists()
    ci = tmp_path / "outputs" / "committee_master" / "CI_MARKET_ORIENTATION_V22_2.csv"
    assert ci.exists()
    frame = pd.read_csv(ci, sep=";")
    assert frame.iloc[0]["orientation_global"] == "RISK_OFF"
    assert bool(frame.iloc[0]["shadow_only"]) is True


def test_governance_is_shadow_only(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mod, "_fetch_vix_fred", lambda: {"value": 18.0})
    monkeypatch.setattr(mod, "_fetch_cnn_fear_greed", lambda: {"value": 55.0})
    monkeypatch.setattr(mod, "_fetch_vstoxx", lambda: {"value": 22.0})
    result = mod.run(tmp_path)
    gov = result["governance"]
    assert gov["wave09_dependency"] is False
    assert gov["shadow_only"] is True
    assert gov["selection_score_changed"] is False
    assert gov["selection_decision_changed"] is False
    assert gov["criteria_changed"] is False
    assert gov["weights_changed"] is False
    assert gov["thresholds_changed"] is False
    assert gov["real_orders_enabled"] is False


def test_overheat_warning_is_not_a_sell_signal(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mod, "_fetch_vix_fred", lambda: {"value": 14.0})
    monkeypatch.setattr(mod, "_fetch_cnn_fear_greed", lambda: {"value": 82.0})
    monkeypatch.setattr(mod, "_fetch_vstoxx", lambda: {"value": 18.0})
    result = mod.run(tmp_path)
    assert result["orientation"]["global"] == "RISK_ON"
    assert result["orientation"]["cnn_extreme_greed_overheat_warning"] is True
    assert result["governance"]["selection_decision_changed"] is False
