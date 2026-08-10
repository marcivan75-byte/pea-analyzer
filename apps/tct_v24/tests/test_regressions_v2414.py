from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from main import _extract_real_outcomes
from src.data.repo_adapter import adapt_repo_free_capture
from src.gates.universe_gate import check_universe_row
from src.ml.adaptive_weights import AdaptiveWeightsEngine
from src.ml.meta_labeling import MetaLabelingModel, apply_meta_labeling
from src.pipeline.build_signals import build_signals
from src.pipeline.daily import _enforce_max_positions
from src.portfolio.position_sizing import compute_final_position_size
from src.signals.scoring import compute_score_base
from src.utils.persistence import load_last_t1, save_last_t1


def test_partial_score_is_not_double_scaled():
    score = compute_score_base({"squeeze": 50.0}, {"squeeze": 0.5, "setup": 0.5})
    assert score == pytest.approx(50.0)


def test_repo_missing_weight_is_not_redistributed():
    score = compute_score_base(
        {"squeeze": 50.0},
        {"squeeze": 0.5, "setup": 0.5},
        renormalize_missing=False,
    )
    assert score == pytest.approx(25.0)


def test_meta_model_absent_preserves_upstream_probability(tmp_path: Path):
    model = MetaLabelingModel(model_dir=str(tmp_path / "missing"), fallback_proba=0.50)
    out = apply_meta_labeling(
        pd.DataFrame({"meta_proba": [0.82, np.nan]}),
        model=model,
        fallback_proba=0.50,
        preserve_upstream=True,
    )
    assert out["meta_proba"].tolist() == pytest.approx([0.82, 0.50])
    assert out["meta_model_source"].tolist() == ["upstream", "fallback"]


def test_position_sizing_fail_closed_on_nan_and_quarantine():
    res = compute_final_position_size(
        setup={"close": 25.0, "avg_dollar_volume_20d": 5_000_000, "universe_status": "QUARANTINE"},
        meta_proba=np.nan,
        p_adverse=np.nan,
        expected_adverse_gap=np.nan,
        days_to_earnings=10,
    )
    assert res["decision"] == "IGNORE"
    assert res["position_pct"] == 0.0
    assert "UNIVERSE_QUARANTINE" in res["sizing_reason"]


def test_max_positions_cap_is_enforced():
    df = pd.DataFrame({
        "decision": ["TAKE"] * 4,
        "position_pct": [0.01] * 4,
        "shares": [10] * 4,
        "score_final": [90, 80, 70, 60],
        "meta_proba": [0.9, 0.8, 0.7, 0.6],
        "sizing_reason": ["OK"] * 4,
    })
    out = _enforce_max_positions(df, max_positions=2)
    assert out["decision"].eq("TAKE").sum() == 2
    assert out.loc[2:, "position_pct"].eq(0).all()


def test_textual_pea_proof_is_explicitly_parsed():
    no = check_universe_row(pd.Series({"isin": "FR0000120271", "ticker": "A.PA", "pea_proof_level": "no"}))
    yes = check_universe_row(pd.Series({"isin": "FR0000120271", "ticker": "A.PA", "pea_proof_level": "oui"}))
    assert no["universe_status"] == "REJECT"
    assert yes["universe_status"] == "PASS"


def test_t1_ttl_expires_stale_state_and_persists_recent(tmp_path: Path):
    p = tmp_path / "last_t1.json"
    old = (date.today() - timedelta(days=120)).isoformat()
    p.write_text(json.dumps({"FR0000120271": {"bandwidth": 0.05, "detected_at": old}}))
    assert load_last_t1(str(p), ttl_sessions=40) == {}
    save_last_t1({"FR0000120271": 0.07}, str(p), detected_at=date.today().isoformat())
    assert load_last_t1(str(p), ttl_sessions=40)["FR0000120271"] == pytest.approx(0.07)


def test_proxy_learning_is_disabled_by_default(tmp_path: Path):
    engine = AdaptiveWeightsEngine(
        weights_path=str(tmp_path / "weights.json"),
        history_path=str(tmp_path / "history.csv"),
        min_samples=2,
        allow_proxy_learning=False,
    )
    hist = pd.DataFrame({
        "setup": ["T2_CONFIRMATION"] * 5 + [None] * 5,
        "score_final": [90] * 5 + [20] * 5,
        "score_earnings_proximity": [90] * 5 + [20] * 5,
        "meta_proba": [0.9] * 5 + [0.2] * 5,
        "recorded_at": [pd.Timestamp.now(tz="UTC").isoformat()] * 10,
    })
    hist.to_csv(engine.history_path, index=False)
    before = engine.get_weights()
    assert engine.update_from_history(use_proxy_if_needed=True) == before


def test_outcomes_without_real_source_are_not_used_for_learning():
    assert _extract_real_outcomes(pd.DataFrame({"outcome": [1.0]})) is None


def test_repo_adapter_etf_provenance_and_no_fake_setup_probability():
    raw = pd.DataFrame({
        "isin": ["FR0013380607"],
        "asset_class": ["ETF"],
        "pea_type": ["PEA_LISTED"],
        "pea_confidence": ["HIGH_LISTED"],
        "yahoo_ticker": ["CAC.PA"],
        "last_close": [88.0],
        "volume": [10_000.0],
        "rvol20": [1.25],
        "rsi14": [60.0],
        "bb_upper": [90.0],
        "bb_lower": [86.0],
        "bb_mid": [88.0],
        "atr14": [1.0],
        "days_to_earnings": [-3],
        "score_ct": [99.0],
    })
    out = adapt_repo_free_capture(raw)
    assert out.loc[0, "tct_asset_class"] == "ETF"
    assert out.loc[0, "tct_adapter_source"] == "V20.7_ETF102_REFERENCE_MASTER"
    assert out.loc[0, "pea_proof_level"] == "PASS"
    assert pd.isna(out.loc[0, "days_to_earnings"])
    assert pd.isna(out.loc[0, "setup"])
    assert "meta_proba" not in out.columns


def test_build_signals_prefers_real_snapshot_without_universe_csv(tmp_path: Path):
    p = tmp_path / "free.csv"
    p.write_text(
        "isin;yahoo_ticker;pea_confidence;last_close;volume_avg_20d;days_to_earnings\n"
        "FR0000120271;TST.PA;HIGH_PEA_PME_LIST;10;100000;8\n",
        encoding="utf-8",
    )
    cfg = {
        "paths": {"free_capture": str(p), "universe": str(tmp_path / "missing_universe.csv")},
        "runtime": {"allow_network_build_signals": False},
        "t1_t2": {"network_refresh": {"enabled": False}},
    }
    out = build_signals(cfg)
    assert len(out) == 1
    assert out.loc[0, "ticker"] == "TST.PA"
    assert bool(out.loc[0, "input_contract_valid"])
