from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision.source_confirmation_gate_v21_16 import apply_source_confirmation_gate, source_gate_summary

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "config" / "SOURCE_FUNCTIONAL_CONTRACT_V21_16.json").read_text(encoding="utf-8"))


def _action(horizon: str, signal: str, *, decision: str = "BUY_CANDIDATE", investing_age: float = 1.0) -> dict:
    return {
        "asset_class": "ACTION",
        "horizon": horizon,
        "isin": f"FR-{horizon}",
        "decision": decision,
        "score": 87.5,
        "boursorama_consensus": 4.2,
        "boursorama_n_analysts": 18,
        "boursorama_target_median": 123.0,
        "boursorama_dynamic_age_hours": 2.0,
        "investing_horizon_signal": signal,
        "investing_age_hours": investing_age,
    }


def test_tct_daily_strong_buy_is_fully_validated_without_score_or_decision_mutation():
    frame = pd.DataFrame([_action("TCT", "STRONG_BUY")])
    out = apply_source_confirmation_gate(frame, CONTRACT)
    assert out.loc[0, "investing_required_timeframe"] == "DAILY"
    assert out.loc[0, "source_validation_state"] == "FULLY_VALIDATED"
    assert bool(out.loc[0, "ci_source_eligible"]) is True
    assert out.loc[0, "decision"] == "BUY_CANDIDATE"
    assert out.loc[0, "score"] == 87.5
    assert out.loc[0, "source_gate_score_influence"] == 0.0


def test_each_horizon_requires_its_exact_investing_timeframe_state():
    frame = pd.DataFrame([
        _action("TCT", "BUY"),
        _action("CT", "STRONG_BUY"),
        _action("MT", "STRONG_BUY"),
    ])
    out = apply_source_confirmation_gate(frame, CONTRACT)
    assert list(out["investing_required_timeframe"]) == ["DAILY", "WEEKLY", "MONTHLY"]
    assert out.loc[0, "source_validation_state"] == "TIMING_WAIT"
    assert out.loc[1, "source_validation_state"] == "FULLY_VALIDATED"
    assert out.loc[2, "source_validation_state"] == "FULLY_VALIDATED"


def test_stale_investing_fails_closed_even_when_signal_is_strong_buy():
    out = apply_source_confirmation_gate(pd.DataFrame([_action("CT", "STRONG_BUY", investing_age=13.0)]), CONTRACT)
    assert out.loc[0, "source_validation_state"] == "TIMING_WAIT"
    assert bool(out.loc[0, "investing_source_fresh"]) is False
    assert bool(out.loc[0, "ci_source_eligible"]) is False
    assert "INVESTING_STALE_OR_MISSING" in out.loc[0, "source_validation_reasons"]


def test_watch_can_have_sources_confirmed_but_never_becomes_ci_buy_recommendation():
    out = apply_source_confirmation_gate(pd.DataFrame([_action("MT", "STRONG_BUY", decision="WATCH")]), CONTRACT)
    assert out.loc[0, "source_validation_state"] == "FULLY_VALIDATED"
    assert bool(out.loc[0, "source_fully_validated"]) is True
    assert bool(out.loc[0, "ci_source_eligible"]) is False
    assert out.loc[0, "decision"] == "WATCH"


def test_non_preselected_row_is_not_applicable_and_never_source_eligible():
    row = _action("CT", "STRONG_BUY", decision="NO_ACTION")
    out = apply_source_confirmation_gate(pd.DataFrame([row]), CONTRACT)
    assert out.loc[0, "source_validation_state"] == "NOT_APPLICABLE"
    assert bool(out.loc[0, "ci_source_eligible"]) is False


def test_etf_requires_minimum_boursorama_structural_context_and_monthly_strong_buy():
    row = {
        "asset_class": "ETF",
        "horizon": "MT",
        "isin": "ETF1",
        "decision": "BUY_CANDIDATE",
        "score": 84.0,
        "boursorama_etf_aum_eur_m": 900.0,
        "boursorama_etf_morningstar_category": "Actions Europe",
        "boursorama_etf_replication": "Synthétique",
        "boursorama_etf_dynamic_age_hours": 3.0,
        "investing_horizon_signal": "STRONG_BUY",
        "investing_age_hours": 2.0,
    }
    out = apply_source_confirmation_gate(pd.DataFrame([row]), CONTRACT)
    assert out.loc[0, "investing_required_timeframe"] == "MONTHLY"
    assert out.loc[0, "source_validation_state"] == "FULLY_VALIDATED"
    assert bool(out.loc[0, "ci_source_eligible"]) is True


def test_gate_summary_ignores_not_applicable_rows():
    frame = pd.DataFrame([_action("CT", "STRONG_BUY"), _action("CT", "STRONG_BUY", decision="NO_ACTION")])
    out = apply_source_confirmation_gate(frame, CONTRACT)
    summary = source_gate_summary(out)
    assert summary["rows"] == 2
    assert summary["applicable_rows"] == 1
    assert summary["fully_validated"] == 1
    assert summary["ci_source_eligible"] == 1
    assert summary["decision_mutation"] is False
