import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.risk import beta_correlation_engine as engine


def _prices(returns: pd.Series, start: float = 100.0) -> pd.Series:
    return start * (1.0 + returns).cumprod()


def test_risk_config_is_context_only_and_fail_closed():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config" / "BETA_CORRELATION_RISK_ENGINE.json").read_text(encoding="utf-8"))
    validation = json.loads((root / "config" / "BETA_RISK_ROBUST_VALIDATION_STATUS.json").read_text(encoding="utf-8"))
    assert cfg["benchmark"]["fail_closed"] is True
    assert cfg["economic_validation"]["status"] == "ROBUST_VALIDATED_CONTEXT_ONLY_KEEP_ALL_SIZING_SHADOW"
    assert cfg["governance"]["decision_influence"] == 0.0
    assert cfg["governance"]["score_influence"] == 0.0
    assert cfg["governance"]["sizing_execution_influence"] == 0.0
    assert cfg["governance"]["stop_loss_influence"] == 0.0
    assert cfg["governance"]["real_orders_enabled"] is False
    assert validation["final_holdout_opened"] is False
    assert validation["production_policy"]["beta_only_position_sizing_promoted"] is False
    assert validation["production_policy"]["regime_v1_1_sizing_promoted"] is False
    assert validation["production_policy"]["regime_v1_2_sizing_promoted"] is False


def test_overlay_never_changes_score_decision_or_emits_sizing_formula():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(41)
    benchmark = pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)
    decisions = pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "MT", "isin": "A", "name": "AI Semi", "decision": "BUY_CANDIDATE", "score": 86.0},
        {"asset_class": "ETF", "horizon": "MT", "isin": "E", "name": "World ETF", "decision": "WATCH", "score": 82.0},
    ])
    action_meta = {"A": {"yahoo_ticker": "AAA", "sector_yf": "Technology", "industry_yf": "Semiconductors"}}
    etf_meta = {"E": {"yahoo_ticker": "EEE", "category": "World"}}
    out, _ = engine.apply_risk_overlay(
        decisions,
        action_meta,
        etf_meta,
        {"AAA": _prices(1.25 * benchmark)},
        {"EEE": _prices(0.8 * benchmark)},
        benchmark,
    )
    assert out["score"].tolist() == decisions["score"].tolist()
    assert out["decision"].tolist() == decisions["decision"].tolist()
    assert out["risk_position_multiplier_shadow"].isna().all()
    assert set(out["risk_position_multiplier_status"]) == {"REJECTED_KEEP_SHADOW_NO_ACTIVE_FORMULA"}
    assert set(out["risk_sizing_execution_influence"]) == {0.0}
    assert set(out["risk_stop_loss_influence"]) == {0.0}
