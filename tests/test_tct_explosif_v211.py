from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from v182.decision.tct_explosif_v211 import compute_scores, apply_decisions
from v182.backtest_optimizer.tct_explosif import TCTLabelConfig, make_forward_labels, MonotonicBinCalibrator
from v182.decision.tct_explosif_enrichment_v211 import technical_features
from v182.sources.tct_catalysts_v211 import score_headlines


def _cfg():
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "data/reference/V21.1_TCT_EXPLOSIF_CONFIG.json").read_text(encoding="utf-8"))


def test_high_confluence_beats_weak_case():
    df = pd.DataFrame([
        {
            "isin":"HIGH","pea_confidence":"HIGH","v182_ticker_validation_confidence_pct":99,
            "breakout_20d_flag":True,"positive_reversal_flag":True,"macd_hist":2.0,"rsi14":58,
            "stoch_bull_cross_flag":True,"relative_strength":90,"gap_up_pct_v211":3,
            "earnings_catalyst_score":90,"guidance_revision_score":85,"major_contract_score":90,
            "news_catalyst_score":88,"sector_news_score":80,"sector_perf_5d_pct":4,
            "valuation_discount_score":85,"per_forward_v21":12,"earnings_growth_v21_pct":25,
            "fcf_yield_v21":9,"enterprise_value_v21":800,"ebitda_v21":100,
            "rvol20":3.0,"short_percent_float_pct":18,"short_ratio":7,
            "analyst_momentum_score":85,"consensus_delta_4w":12,"net_upgrades_30d_v21":5,
            "broker_weighted_revision_30d":8,"liquidity_percentile":0.8,"max_drawdown_1y":-18,
            "volatility_20d":35,"insider_net_buy_90d":20000,"insider_buyers_90d":4,
            "insider_sellers_90d":0,"buyback_signal":True,"action_topdown_score":75,
            "sentiment_regime_score":70,"sector_v21":"Tech"
        },
        {
            "isin":"LOW","pea_confidence":"HIGH","v182_ticker_validation_confidence_pct":99,
            "breakout_20d_flag":False,"positive_reversal_flag":False,"macd_hist":-2.0,"rsi14":78,
            "stoch_bull_cross_flag":False,"relative_strength":10,"gap_up_pct_v211":-3,
            "earnings_catalyst_score":20,"guidance_revision_score":10,"major_contract_score":np.nan,
            "news_catalyst_score":15,"sector_news_score":20,"sector_perf_5d_pct":-4,
            "valuation_discount_score":20,"per_forward_v21":35,"earnings_growth_v21_pct":5,
            "fcf_yield_v21":-2,"enterprise_value_v21":2500,"ebitda_v21":100,
            "rvol20":0.7,"short_percent_float_pct":2,"short_ratio":1,
            "analyst_momentum_score":20,"consensus_delta_4w":-10,"net_upgrades_30d_v21":-4,
            "broker_weighted_revision_30d":-8,"liquidity_percentile":0.3,"max_drawdown_1y":-45,
            "volatility_20d":80,"insider_net_buy_90d":-10000,"insider_buyers_90d":0,
            "insider_sellers_90d":4,"buyback_signal":False,"action_topdown_score":25,
            "sentiment_regime_score":25,"sector_v21":"Tech"
        }
    ])
    out = apply_decisions(compute_scores(df, _cfg()), _cfg())
    assert out.loc[0, "tct_score"] > out.loc[1, "tct_score"]
    assert out.loc[0, "tct_decision"] in {"COEUR_TCT_EXPLOSIF","SATELLITE_TCT_EXPLOSIF"}
    assert out.loc[1, "tct_decision"] != "COEUR_TCT_EXPLOSIF"


def test_rumor_alone_cannot_create_core():
    df = pd.DataFrame([{
        "isin":"RUMOR","pea_confidence":"HIGH","v182_ticker_validation_confidence_pct":99,
        "mna_rumor_score":100,"liquidity_percentile":0.7,"max_drawdown_1y":-20,"volatility_20d":30
    }])
    out = apply_decisions(compute_scores(df, _cfg()), _cfg())
    assert out.loc[0, "tct_catalyst_event_score"] <= _cfg()["gates"]["rumor_only_event_cap"]
    assert out.loc[0, "tct_decision"] != "COEUR_TCT_EXPLOSIF"


def test_forward_label_enters_next_open_and_detects_controlled_hit():
    dates = pd.date_range("2026-01-01", periods=25, freq="B")
    rows=[]
    for i,d in enumerate(dates):
        open_px=100.0
        high=100.0
        low=98.0
        close=100.0
        if i == 5:
            high=116.0
            close=114.0
        rows.append({"date":d,"instrument_id":"X","open":open_px,"high":high,"low":low,"close":close})
    labels=make_forward_labels(pd.DataFrame(rows), TCTLabelConfig(horizon_sessions=20,target_return_pct=15,mae_floor_pct=-12))
    first=labels.iloc[0]
    assert first["entry_date"] == dates[1]
    assert bool(first["explosion_hit_15pct_20d"])
    assert bool(first["controlled_explosion_hit"])
    assert first["sessions_to_first_hit"] == 5


def test_monotonic_calibrator_is_non_decreasing():
    x=pd.Series(np.arange(100,dtype=float))
    y=pd.Series(([0]*50)+([1]*50))
    cal=MonotonicBinCalibrator.fit(x,y,bins=10)
    assert np.all(np.diff(cal.probabilities) >= -1e-12)


def test_technical_enrichment_generates_tct_fields():
    n=220
    idx=pd.date_range("2025-01-01",periods=n,freq="B")
    close=pd.Series(np.linspace(100,150,n),index=idx)
    frame=pd.DataFrame({
        "Open":close.shift(1).fillna(close.iloc[0])*1.002,
        "High":close*1.01,
        "Low":close*0.99,
        "Close":close,
        "Volume":np.linspace(1000,2500,n),
    },index=idx)
    feat=technical_features(frame)
    assert feat["ma20"] is not None
    assert feat["ma200"] is not None
    assert feat["rvol20"] is not None
    assert "gap_up_pct_v211" in feat


def test_gdelt_discovery_headlines_are_scored_but_not_primary():
    result=score_headlines([
        "Example SA wins major contract award",
        "Example SA raises guidance after earnings beat",
    ])
    assert result["score"] > 50
    assert "CONTRACT" in result["categories"]
