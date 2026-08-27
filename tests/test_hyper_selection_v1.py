import json
from pathlib import Path
import pandas as pd

from v182.reporting.hyper_selection_v1 import _external_scores


def test_hyper_weights_are_twenty_named_non_redundant_and_sum_to_100():
    cfg = json.loads(Path("config/HYPER_SELECTION_V1.json").read_text(encoding="utf-8"))
    assert len(cfg["weights"]) == 20
    assert sum(cfg["weights"]) == 100
    assert [criterion["weight"] for criterion in cfg["criteria"]] == cfg["weights"]
    assert len({criterion["id"] for criterion in cfg["criteria"]}) == 20
    assert cfg["etfs"]["monthly_signal_required"] == "STRONG_BUY"
    assert any(criterion["label"] == "RSI individuel" for criterion in cfg["criteria"])


def test_missing_tradingview_reduces_reliability_instead_of_scoring_perfectly():
    cfg = json.loads(Path("config/HYPER_SELECTION_V1.json").read_text(encoding="utf-8"))
    row = pd.DataFrame([{
        "asset_class": "ETF", "H17_RELIABILITY": 100.0,
        "boursorama_etf_pea_eligible_displayed": True,
        "tradingview_daily_signal": "", "tradingview_weekly_signal": "", "tradingview_monthly_signal": "",
        "tradingview_technical_complete": None, "morningstar_rating": None,
    }])
    result = _external_scores(row, cfg).iloc[0]
    assert result["H17_RELIABILITY"] < 100.0
    assert result["HYPER_CONFIRMATION_STATE"] == "PENDING_SOURCE"
