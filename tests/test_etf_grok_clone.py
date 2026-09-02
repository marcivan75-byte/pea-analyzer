import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v182.decision import etf_grok_high_precision as grok_decision
from v182.decision import etf_mt_high_precision as mt_decision
from v182.features import etf_grok_v2081 as grok_features
from v182.features import etf_mt_v2081 as mt_features
from v182.features.etf_grok_history_integrity import assert_grok_reference_contract


GROK_CONFIG = Path("config/V20.8_ETF_GROK_HIGH_PRECISION.json")
MT_CONFIG = Path("config/V20.8_ETF_MT_HIGH_PRECISION.json")


def _cfg(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _history(seed: int, drift: float = 0.0004, sessions: int = 820) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=sessions)
    returns = rng.normal(drift, 0.009, sessions)
    close = 100.0 * np.cumprod(1.0 + returns)
    volume = rng.integers(150_000, 600_000, sessions).astype(float)
    return pd.DataFrame({"Open": close * 0.999, "High": close * 1.005, "Low": close * 0.995, "Close": close, "Volume": volume}, index=idx)


def test_grok_config_is_independent_and_exact_clone_of_mt_parameters():
    grok = _cfg(GROK_CONFIG)
    mt = _cfg(MT_CONFIG)
    assert grok["module"] == "ETF_GROK_HIGH_PRECISION"
    assert grok["scope"]["t1_t2_enabled"] is False
    assert grok["scope"]["pit_required"] is True
    assert grok["scope"]["anti_lookahead_required"] is True
    assert grok["score"]["score_raw_weight"] == mt["score"]["score_raw_weight"] == 0.55
    assert grok["score"]["cross_section_rank_weight"] == mt["score"]["cross_section_rank_weight"] == 0.45
    assert grok["score"]["selection_threshold"] == mt["score"]["selection_threshold"] == 82.0
    assert grok["score"]["top_n"] == mt["score"]["top_n"] == 2
    assert list(grok["dynamic_criteria"]) == list(mt["dynamic_criteria"])
    assert {k: v["backtested_weight"] for k, v in grok["dynamic_criteria"].items()} == {k: v["backtested_weight"] for k, v in mt["dynamic_criteria"].items()}
    assert abs(sum(v["backtested_weight"] for v in grok["dynamic_criteria"].values()) - 1.0) < 1e-6
    assert GROK_CONFIG != MT_CONFIG
    assert_grok_reference_contract(grok)


def test_decision_core_parity_and_separate_modules():
    assert grok_decision is not mt_decision
    assert grok_decision.__name__ != mt_decision.__name__
    assert grok_decision.final_score(91.49338354580647, 100.0) == mt_decision.final_score(91.49338354580647, 100.0)
    regime_g = grok_decision.MarketRegime(0.70, 0.01, 0.08, True)
    regime_m = mt_decision.MarketRegime(0.70, 0.01, 0.08, True)
    cg = [grok_decision.Candidate("A", 95, 100, "EU"), grok_decision.Candidate("B", 92, 98, "US"), grok_decision.Candidate("C", 91, 97, "TECH")]
    cm = [mt_decision.Candidate("A", 95, 100, "EU"), mt_decision.Candidate("B", 92, 98, "US"), mt_decision.Candidate("C", 91, 97, "TECH")]
    assert [x.instrument_id for x in grok_decision.select_candidates(cg, regime_g)] == [x.instrument_id for x in mt_decision.select_candidates(cm, regime_m)] == ["A", "B"]


def test_grok_requires_all_38_criteria_and_never_uses_t1_t2():
    cfg = _cfg(GROK_CONFIG)
    weights = {name: item["backtested_weight"] for name, item in cfg["dynamic_criteria"].items()}
    scores = {name: 80.0 for name in weights}
    assert grok_decision.weighted_raw_score(scores, weights) == pytest.approx(80.0)
    scores.pop(next(iter(weights)))
    with pytest.raises(ValueError, match="missing required GROK criteria"):
        grok_decision.weighted_raw_score(scores, weights)


def test_raw_feature_engine_parity_on_same_ohlcv():
    histories = {f"ETF{i}": _history(i, drift=0.0002 + i * 0.00008) for i in range(1, 5)}
    proxy_mt = mt_features.build_equal_weight_market_proxy(histories)
    proxy_grok = grok_features.build_equal_weight_market_proxy(histories)
    assert np.allclose(proxy_mt.to_numpy(), proxy_grok.to_numpy(), rtol=0, atol=1e-12)
    for key, frame in histories.items():
        a = mt_features.compute_raw_features(frame, proxy_mt)
        b = grok_features.compute_raw_features(frame, proxy_grok)
        assert set(a) == set(b)
        for name in a:
            assert b[name] == pytest.approx(a[name], rel=0, abs=1e-12), (key, name)


def test_strict_snapshot_parity_on_same_input():
    histories = {f"ISIN{i}": _history(i + 10, drift=0.0003 + i * 0.0001) for i in range(4)}
    ref = pd.DataFrame({"isin": list(histories), "name": [f"ETF {i}" for i in range(4)], "category": ["A", "B", "C", "D"]})
    mt_snapshot, mt_summary = mt_features.score_snapshot(histories, ref, _cfg(MT_CONFIG))
    grok_snapshot, grok_summary = grok_features.score_snapshot(histories, ref, _cfg(GROK_CONFIG))
    cols = ["instrument_id", "score_raw", "score_rank_pct", "score_final", "rank_on_date", "selected", "decision"]
    left = mt_snapshot[cols].sort_values("instrument_id").reset_index(drop=True)
    right = grok_snapshot[cols].sort_values("instrument_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=0, atol=1e-12)
    assert grok_summary["module"] == "ETF_GROK_HIGH_PRECISION"
    assert mt_summary["module"] == "ETF_MT_HIGH_PRECISION"


def test_grok_output_names_and_proxy_are_isolated(tmp_path):
    sample = pd.DataFrame({"instrument_id": ["X"], "selected": [False]})
    paths = grok_features.write_outputs(sample, {"process": "ETF_GROK"}, tmp_path)
    assert Path(paths["ranking_csv"]).name == "V20.8.1_ETF_GROK_RANKING.csv"
    assert Path(paths["summary_json"]).name == "V20.8.1_ETF_GROK_SUMMARY.json"
    assert grok_features.MARKET_PROXY_ID == "PEA_ETF_GROK_EQUAL_WEIGHT_PROXY"
    assert grok_features.MARKET_PROXY_ID != mt_features.MARKET_PROXY_ID


def test_no_real_orders_and_legacy_exit_is_replay_only():
    cfg = _cfg(GROK_CONFIG)
    assert cfg["status"] == "ACTIVE_REFERENCE_SCORING_NO_REAL_ORDERS"
    assert cfg["exit_policy"]["role"] == "BACKTEST_REPLAY_ONLY"
    assert cfg["evidence"]["final_holdout_opened"] is False
