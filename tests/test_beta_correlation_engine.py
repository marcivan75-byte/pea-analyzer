import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.risk import beta_correlation_engine as engine
from v182.risk.beta_metrics import compute_beta_metrics, economic_engine_tags


def _prices(returns: pd.Series, start: float = 100.0) -> pd.Series:
    return start * (1.0 + returns).cumprod()


def test_repository_risk_config_is_strict_shadow():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config" / "BETA_CORRELATION_RISK_ENGINE.json").read_text(encoding="utf-8"))
    governance = cfg["governance"]
    validation = cfg["economic_validation"]
    assert cfg["enabled"] is True
    assert validation["status"] == "VALIDATED_KEEP_SHADOW"
    assert validation["beta_only_position_sizing"] == "REJECTED_KEEP_SHADOW"
    assert validation["workflow_run_id"] == 31939903983
    assert validation["post_result_threshold_tuning_forbidden"] is True
    assert governance["decision_influence"] == 0.0
    assert governance["score_influence"] == 0.0
    assert governance["sizing_execution_influence"] == 0.0
    assert governance["stop_loss_influence"] == 0.0
    assert governance["positive_signal_can_create_buy"] is False
    assert governance["negative_signal_can_force_sell"] is False
    assert governance["stop_loss_formula_link_forbidden"] is True
    assert governance["promotion_requirement"] == "NEW_PRE_REGISTERED_DEDICATED_PIT_OOS_MARGINAL_UPLIFT_FOR_ANY_FUTURE_HYPOTHESIS"
    assert governance["real_orders_enabled"] is False


def test_validation_status_persists_negative_promotion_decision():
    root = Path(__file__).resolve().parents[1]
    status = json.loads((root / "config" / "BETA_RISK_VALIDATION_STATUS.json").read_text(encoding="utf-8"))
    assert status["source_workflow_run_id"] == 31939903983
    assert status["final_holdout_opened"] is False
    assert status["verdict"] == "KEEP_BETA_SIZING_SHADOW"
    assert status["validation_oos"]["pass"] is False
    assert status["diagnostic_oos"]["pass"] is False
    assert status["validation_oos"]["risk_adjusted_improvement"] < 0
    assert status["validation_oos"]["expectancy_retention"] < 0.95
    assert status["diagnostic_oos"]["beta_sized"]["expectancy"] < 0
    assert status["production_policy"]["beta_only_position_sizing_promoted"] is False
    assert status["production_policy"]["decision_influence"] == 0.0
    assert status["production_policy"]["score_influence"] == 0.0
    assert status["production_policy"]["sizing_execution_influence"] == 0.0
    assert status["production_policy"]["stop_loss_influence"] == 0.0
    assert status["production_policy"]["real_orders_enabled"] is False


def test_beta_exact_linear_relationship():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(7)
    benchmark = pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx)
    metrics = compute_beta_metrics(1.5 * benchmark, benchmark)
    assert abs(metrics["beta_252d"] - 1.5) < 1e-10
    assert abs(metrics["downside_beta_252d"] - 1.5) < 1e-10
    assert abs(metrics["upside_beta_252d"] - 1.5) < 1e-10
    assert metrics["r2_252d"] > 0.999999


def test_asymmetric_downside_beta_is_detected():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(11)
    benchmark = pd.Series(rng.normal(0.0, 0.012, len(idx)), index=idx)
    asset = benchmark.where(benchmark > 0, benchmark * 2.0)
    metrics = compute_beta_metrics(asset, benchmark)
    assert metrics["downside_beta_252d"] > metrics["upside_beta_252d"] * 1.7
    assert metrics["downside_upside_beta_ratio"] > 1.7


def test_beta_nonstationarity_span():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(5)
    benchmark = pd.Series(rng.normal(0.0, 0.01, len(idx)), index=idx)
    asset = benchmark.copy()
    asset.iloc[-63:] = 1.8 * benchmark.iloc[-63:]
    metrics = compute_beta_metrics(asset, benchmark)
    assert metrics["beta_63d"] > metrics["beta_252d"]
    assert metrics["beta_stability_span"] > 0.3


def test_engine_tags_capture_shared_macro_drivers():
    tags = economic_engine_tags("Technology", "Semiconductors", "AI processors")
    assert "AI_TECH" in tags
    assert "SEMICONDUCTORS" in tags
    assert "LIQUIDITY" in tags
    assert "RATES" in tags


def test_overlay_never_mutates_score_decision_or_stop():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(1)
    benchmark = pd.Series(rng.normal(0.0003, 0.01, len(idx)), index=idx)
    action_prices = _prices(1.2 * benchmark)
    etf_prices = _prices(0.8 * benchmark + rng.normal(0, 0.002, len(idx)))
    decisions = pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "MT", "isin": "A", "name": "AI Semi", "decision": "BUY_CANDIDATE", "score": 86.0},
        {"asset_class": "ETF", "horizon": "MT", "isin": "E", "name": "World ETF", "decision": "WATCH", "score": 82.0},
    ])
    action_meta = {"A": {"yahoo_ticker": "AAA", "sector_yf": "Technology", "industry_yf": "Semiconductors"}}
    etf_meta = {
        "E": {
            "yahoo_ticker": "EEE",
            "category": "World",
            "direct_sector_hhi": 0.20,
            "direct_top_holdings_concentration_pct": 30,
        }
    }
    out, _ = engine.apply_risk_overlay(decisions, action_meta, etf_meta, {"AAA": action_prices}, {"EEE": etf_prices}, benchmark)
    assert out["decision"].tolist() == decisions["decision"].tolist()
    assert out["score"].tolist() == decisions["score"].tolist()
    assert set(out["risk_score_decision_influence"]) == {0.0}
    assert set(out["risk_sizing_execution_influence"]) == {0.0}
    assert set(out["risk_stop_loss_influence"]) == {0.0}


def test_portfolio_stress_is_systematic_component_only():
    idx = pd.date_range("2024-01-01", periods=320, freq="B")
    rng = np.random.default_rng(4)
    benchmark = pd.Series(rng.normal(0, 0.01, len(idx)), index=idx)
    rows = pd.DataFrame([
        {
            "isin": "A",
            "decision": "BUY_CANDIDATE",
            "risk_beta_252d": 1.5,
            "risk_downside_beta_252d": 1.5,
            "risk_engine_tags": "AI_TECH|GROWTH|LIQUIDITY",
        },
        {
            "isin": "B",
            "decision": "BUY_CANDIDATE",
            "risk_beta_252d": 0.5,
            "risk_downside_beta_252d": 0.5,
            "risk_engine_tags": "GOLD_PRECIOUS|GEOPOLITICS",
        },
    ])
    summary = engine.portfolio_summary(rows, {"A": 1.5 * benchmark, "B": 0.5 * benchmark}, benchmark, [-10, -20])
    assert abs(summary["portfolio_downside_beta_252d"] - 1.0) < 1e-12
    assert summary["systematic_stress_scenarios_pct"]["-10.0"] == -10.0
    assert "NOT_TOTAL_LOSS_FORECAST" in summary["stress_scenario_semantic"]


def test_run_degrades_safely_without_benchmark(tmp_path, monkeypatch):
    root = Path(tmp_path)
    (root / "config").mkdir()
    (root / "outputs" / "committee_master").mkdir(parents=True)
    config = {"version": "TEST", "benchmark": {"min_sessions": 126, "min_constituents": 20}}
    (root / "config" / "BETA_CORRELATION_RISK_ENGINE.json").write_text(json.dumps(config), encoding="utf-8")
    pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "MT", "isin": "A", "decision": "BUY_CANDIDATE", "score": 80}
    ]).to_csv(
        root / "outputs" / "committee_master" / "COMMITTEE_DECISIONS.csv",
        sep=";",
        index=False,
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(engine, "load_cached_prices", lambda path: {})
    payload = engine.run(root)
    assert payload["status"] == "DEGRADED_BENCHMARK_UNAVAILABLE"
    assert payload["decision_influence"] == 0.0
