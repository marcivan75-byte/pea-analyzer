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
    accepted, reasons, boursorama, investing, morningstar = light._evaluate(_row())
    assert accepted is True
    assert reasons == []
    assert boursorama == "BUY"
    assert investing == "BUY"
    assert morningstar is None


def test_action_boursorama_renforcer_and_investing_strong_buy_pass():
    accepted, reasons, boursorama, investing, _ = light._evaluate(
        _row(boursorama_consensus="Renforcer", investing_weekly_signal="Strong Buy")
    )
    assert accepted is True
    assert reasons == []
    assert boursorama == "RENFORCER"
    assert investing == "STRONG_BUY"


def test_investing_signal_is_horizon_specific():
    accepted, reasons, _, investing, _ = light._evaluate(
        _row(horizon="TCT", investing_daily_signal="BUY", investing_weekly_signal="SELL")
    )
    assert accepted is True
    assert reasons == []
    assert investing == "BUY"


def test_etf_three_stars_passes_without_analyst_recommendation():
    accepted, reasons, boursorama, investing, morningstar = light._evaluate(
        _row(
            asset_class="ETF",
            boursorama_consensus=None,
            morningstar_rating=3.0,
            investing_weekly_signal="STRONG_BUY",
        )
    )
    assert accepted is True
    assert reasons == []
    assert boursorama == ""
    assert investing == "STRONG_BUY"
    assert morningstar == 3.0


def test_etf_two_stars_is_excluded():
    accepted, reasons, _, _, morningstar = light._evaluate(
        _row(asset_class="ETF", boursorama_consensus=None, morningstar_rating=2.0)
    )
    assert accepted is False
    assert morningstar == 2.0
    assert "ETF_MORNINGSTAR_RATING_LT_3" in reasons


def test_etf_missing_morningstar_is_fail_closed():
    accepted, reasons, _, _, morningstar = light._evaluate(
        _row(asset_class="ETF", boursorama_consensus=None, morningstar_rating=None)
    )
    assert accepted is False
    assert morningstar is None
    assert "ETF_MORNINGSTAR_RATING_MISSING" in reasons


def test_negative_investing_blocks_light_even_with_positive_quality_gate():
    accepted, reasons, _, _, _ = light._evaluate(_row(investing_weekly_signal="NEUTRAL"))
    assert accepted is False
    assert "INVESTING_HORIZON_NOT_BUY_OR_STRONG_BUY" in reasons

    accepted, reasons, _, _, _ = light._evaluate(
        _row(asset_class="ETF", morningstar_rating=5.0, investing_weekly_signal="SELL")
    )
    assert accepted is False
    assert "INVESTING_HORIZON_NOT_BUY_OR_STRONG_BUY" in reasons


def test_attach_etf_morningstar_uses_exact_isin_master(tmp_path: Path):
    master_path = tmp_path / "outputs/V18.2_PEA_ETF_MASTER_ENRICHED.csv"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {"isin": "ETF3", "morningstar_rating": 3.0},
            {"isin": "ETF5", "morningstar_rating": 5.0},
        ]
    ).to_csv(master_path, sep=";", index=False, encoding="utf-8-sig")
    frame = pd.DataFrame(
        [
            {"isin": "ETF3", "asset_class": "ETF"},
            {"isin": "OTHER", "asset_class": "ETF"},
        ]
    )
    enriched = light._attach_etf_morningstar(frame, tmp_path)
    assert float(enriched.loc[enriched["isin"].eq("ETF3"), "morningstar_rating"].iloc[0]) == 3.0
    assert pd.isna(enriched.loc[enriched["isin"].eq("OTHER"), "morningstar_rating"].iloc[0])


def test_run_outputs_horizon_workbook_and_does_not_change_scores(tmp_path: Path):
    upstream = tmp_path / light.UPSTREAM
    upstream.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([
        _row(name="A", isin="FR1", horizon="TCT", investing_daily_signal="STRONG_BUY").to_dict(),
        _row(name="B", isin="FR2", horizon="CT", investing_weekly_signal="BUY").to_dict(),
        _row(name="C", isin="FR3", horizon="MT", investing_monthly_signal="SELL").to_dict(),
        _row(
            name="ETF3", isin="ETF3", asset_class="ETF", horizon="MT",
            boursorama_consensus=None, morningstar_rating=3.0, investing_monthly_signal="BUY"
        ).to_dict(),
        _row(
            name="ETF2", isin="ETF2", asset_class="ETF", horizon="MT",
            boursorama_consensus=None, morningstar_rating=2.0, investing_monthly_signal="BUY"
        ).to_dict(),
    ])
    frame.to_csv(upstream, sep=";", index=False, encoding="utf-8-sig")
    payload = light.run(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["selected"] == 3
    assert payload["selected_by_horizon"] == {"TCT": 1, "CT": 1, "MT": 1}
    assert payload["etf_minimum_morningstar_stars"] == 3.0
    assert payload["etf_analyst_consensus_required"] is False
    selected = pd.read_csv(tmp_path / light.OUTPUT, sep=";", encoding="utf-8-sig")
    assert selected.set_index("isin").loc["FR1", "score"] == 82.0
    assert "ETF3" in set(selected["isin"])
    assert "ETF2" not in set(selected["isin"])
    assert (tmp_path / light.EXCEL).exists()
    book = pd.ExcelFile(tmp_path / light.EXCEL)
    assert set(["ALL", "TCT", "CT", "MT"]).issubset(book.sheet_names)
