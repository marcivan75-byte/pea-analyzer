from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import ci_light_v22_2_3 as light


def _row(**kwargs):
    base = {
        "name": "TEST",
        "isin": "FR0000000001",
        "asset_class": "ACTION",
        "horizon": "CT",
        "score": 82.0,
        "CI_CONFIDENCE_SCORE_V22_2_1": 75.0,
        "boursorama_consensus": "BUY",
        "investing_weekly_signal": "BUY",
    }
    base.update(kwargs)
    return pd.Series(base)


def test_action_requires_both_boursorama_and_investing_positive():
    accepted, reasons, boursorama, investing = light._evaluate(_row())
    assert accepted is True
    assert reasons == []
    assert boursorama == "BUY"
    assert investing == "BUY"


def test_action_boursorama_renforcer_and_investing_strong_buy_pass():
    accepted, reasons, boursorama, investing = light._evaluate(
        _row(boursorama_consensus="Renforcer", investing_weekly_signal="Strong Buy")
    )
    assert accepted is True
    assert reasons == []
    assert boursorama == "RENFORCER"
    assert investing == "STRONG_BUY"


def test_investing_signal_is_horizon_specific():
    accepted, reasons, _, investing = light._evaluate(
        _row(horizon="TCT", investing_daily_signal="BUY", investing_weekly_signal="SELL")
    )
    assert accepted is True
    assert reasons == []
    assert investing == "BUY"


def test_etf_without_explicit_boursorama_analyst_recommendation_is_excluded():
    accepted, reasons, _, _ = light._evaluate(
        _row(asset_class="ETF", boursorama_consensus=None, investing_weekly_signal="STRONG_BUY")
    )
    assert accepted is False
    assert "ETF_BOURSORAMA_ANALYST_RECOMMENDATION_NOT_AVAILABLE" in reasons


def test_negative_investing_blocks_light_even_with_positive_boursorama():
    accepted, reasons, _, _ = light._evaluate(_row(investing_weekly_signal="NEUTRAL"))
    assert accepted is False
    assert "INVESTING_HORIZON_NOT_BUY_OR_STRONG_BUY" in reasons


def test_run_outputs_horizon_workbook_and_does_not_change_scores(tmp_path: Path):
    upstream = tmp_path / light.UPSTREAM
    upstream.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([
        _row(name="A", isin="FR1", horizon="TCT", investing_daily_signal="STRONG_BUY").to_dict(),
        _row(name="B", isin="FR2", horizon="CT", investing_weekly_signal="BUY").to_dict(),
        _row(name="C", isin="FR3", horizon="MT", investing_monthly_signal="SELL").to_dict(),
    ])
    frame.to_csv(upstream, sep=";", index=False, encoding="utf-8-sig")
    payload = light.run(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["selected"] == 2
    assert payload["selected_by_horizon"] == {"TCT": 1, "CT": 1, "MT": 0}
    selected = pd.read_csv(tmp_path / light.OUTPUT, sep=";", encoding="utf-8-sig")
    assert selected.set_index("isin").loc["FR1", "score"] == 82.0
    assert (tmp_path / light.EXCEL).exists()
    book = pd.ExcelFile(tmp_path / light.EXCEL)
    assert set(["ALL", "TCT", "CT", "MT"]).issubset(book.sheet_names)
