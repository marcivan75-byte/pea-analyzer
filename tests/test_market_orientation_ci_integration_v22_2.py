from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import ci_entry_watch_v22_2 as watch
from v182.reporting import market_orientation_v22_2 as market


def test_market_context_is_exposed_but_shadow_only(tmp_path: Path):
    ci = tmp_path / "outputs" / "committee_master"
    ci.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "orientation_global": "CAUTION",
        "orientation_us": "NEUTRAL",
        "orientation_europe": "RISK_OFF",
        "vix": 23.5,
        "cnn_fear_greed": 48.0,
        "cnn_overheat_warning": False,
        "vstoxx": 31.0,
        "shadow_only": True,
    }]).to_csv(ci / "CI_MARKET_ORIENTATION_V22_2.csv", sep=";", index=False, encoding="utf-8-sig")
    context = watch._market_context(tmp_path)
    assert context["global"] == "CAUTION"
    assert context["us"] == "NEUTRAL"
    assert context["europe"] == "RISK_OFF"
    assert context["vix"] == 23.5
    assert context["cnn_fear_greed"] == 48.0
    assert context["vstoxx"] == 31.0


def test_vstoxx_official_symbol_and_fallback_candidates_are_locked():
    assert market.VSTOXX_OFFICIAL_SYMBOL == "V2TX"
    assert "V2TX.DE" in market.VSTOXX_YAHOO_CANDIDATES
    assert len(market.VSTOXX_YAHOO_CANDIDATES) >= 2


def test_market_orientation_governance_cannot_change_selection(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(market, "_fetch_vix_fred", lambda: {"value": 16.0})
    monkeypatch.setattr(market, "_fetch_cnn_fear_greed", lambda: {"value": 67.0})
    monkeypatch.setattr(market, "_fetch_vstoxx", lambda: {"value": 19.0})
    result = market.run(tmp_path)
    gov = result["governance"]
    assert gov["wave09_dependency"] is False
    assert gov["shadow_only"] is True
    assert gov["fred_series"] == ["VIXCLS"]
    assert gov["vstoxx_official_symbol"] == "V2TX"
    assert gov["selection_score_changed"] is False
    assert gov["selection_decision_changed"] is False
    assert gov["weights_changed"] is False
    assert gov["thresholds_changed"] is False
    assert gov["real_orders_enabled"] is False
