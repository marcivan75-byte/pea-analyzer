import pandas as pd

from v182.reporting import ci_light_v4 as light


def _row(**changes):
    row = {
        "asset_class": "ACTION",
        "horizon": "CT",
        "boursorama_consensus": "BUY",
        "boursorama_n_analysts": 11,
        "boursorama_target_upside_pct": 20.1,
        "morningstar_rating": None,
        "tradingview_daily_signal": "BUY",
        "tradingview_weekly_signal": "STRONG_BUY",
        "tradingview_monthly_signal": "BUY",
        "score": 1,
        "CI_CONFIDENCE_SCORE_V22_2_1": 1,
    }
    row.update(changes)
    return pd.Series(row)


def test_action_light_contract_is_strict_and_independent_of_full_ci_thresholds():
    assert light._evaluate(_row())[0] is True
    assert light._evaluate(_row(boursorama_n_analysts=10))[0] is False
    assert light._evaluate(_row(boursorama_target_upside_pct=20))[0] is False


def test_etf_uses_exact_boursorama_fiche_not_equity_analyst_contract():
    accepted, reasons, _ = light._evaluate(
        _row(
            asset_class="ETF",
            morningstar_rating=4,
            boursorama_consensus=None,
            boursorama_n_analysts=None,
            boursorama_target_upside_pct=None,
            boursorama_etf_pea_eligible_displayed=True,
            tradingview_monthly_signal="STRONG_BUY",
        )
    )
    assert accepted is True
    assert not any("BOURSORAMA" in reason for reason in reasons)
    assert light._evaluate(
        _row(asset_class="ETF", boursorama_etf_pea_eligible_displayed=False)
    )[0] is False


def test_etf_requires_monthly_strong_buy_but_daily_weekly_accept_buy():
    accepted, reasons, _ = light._evaluate(
        _row(
            asset_class="ETF",
            boursorama_etf_pea_eligible_displayed=True,
            tradingview_daily_signal="BUY",
            tradingview_weekly_signal="BUY",
            tradingview_monthly_signal="BUY",
        )
    )
    assert accepted is False
    assert "ETF_TRADINGVIEW_MONTHLY_NOT_STRONG_BUY" in reasons
    assert light._evaluate(
        _row(
            asset_class="ETF",
            boursorama_etf_pea_eligible_displayed=True,
            tradingview_daily_signal="BUY",
            tradingview_weekly_signal="BUY",
            tradingview_monthly_signal="STRONG_BUY",
        )
    )[0] is True


def test_etf_morningstar_fallback_only_replaces_missing_weekly_or_monthly():
    accepted, reasons, details = light._evaluate(
        _row(
            asset_class="ETF",
            morningstar_rating=4,
            boursorama_etf_pea_eligible_displayed=True,
            tradingview_weekly_signal=pd.NA,
            tradingview_monthly_signal=pd.NA,
        )
    )
    assert accepted is True
    assert reasons == []
    assert details["morningstar_fallback_used"] == ["WEEKLY", "MONTHLY"]
    assert light._evaluate(
        _row(asset_class="ETF", boursorama_etf_pea_eligible_displayed=True, morningstar_rating=3.99, tradingview_weekly_signal=pd.NA)
    )[0] is False
    assert light._evaluate(
        _row(asset_class="ETF", boursorama_etf_pea_eligible_displayed=True, morningstar_rating=5, tradingview_weekly_signal="NEUTRAL")
    )[0] is False
    assert light._evaluate(
        _row(asset_class="ACTION", morningstar_rating=5, tradingview_weekly_signal=pd.NA)
    )[0] is False


def test_all_three_tradingview_horizons_must_be_positive():
    for field in (
        "tradingview_daily_signal",
        "tradingview_weekly_signal",
        "tradingview_monthly_signal",
    ):
        assert light._evaluate(_row(**{field: "NEUTRAL"}))[0] is False
        accepted, reasons, _ = light._evaluate(_row(**{field: pd.NA}))
        assert accepted is False
        assert any("SIGNAL_MISSING" in reason for reason in reasons)


def test_urls_fail_closed_without_exact_identity():
    assert light._boursorama_url(_row(isin="X", yahoo_ticker=None))[1] == "UNRESOLVED_FAIL_CLOSED"
    assert light._tradingview_url(_row(isin="X", yahoo_ticker=None))[1] == "UNRESOLVED_FAIL_CLOSED"


def test_light_uses_dedicated_universe_without_ci_context(monkeypatch, tmp_path):
    row = {"isin": "A", "name": "A", **_row().to_dict()}
    row.pop("score")
    row.pop("CI_CONFIDENCE_SCORE_V22_2_1")
    upstream = tmp_path / light.UPSTREAM
    upstream.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(upstream, sep=";", index=False, encoding="utf-8-sig")
    monkeypatch.setattr(light, "_master_frames", lambda root: (pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(
        light, "collect_ci_light_context", lambda frame, root: (frame, {"status": "SUCCESS_WITH_CONTEXT"})
    )
    payload = light.run(tmp_path)
    assert payload["status"] == "SUCCESS"
    assert payload["ci_output_dependency"] is False
    assert payload["ci_selection_used"] is False
    assert payload["source_context_reused"] is False
    assert payload["source_collection_passes"] == 1


def test_etf_overlap_gate_removes_highly_correlated_lower_priority_etf():
    dates = pd.date_range("2025-01-01", periods=130, freq="B")
    base = pd.Series(range(100, 230), index=dates, dtype=float)
    selected = pd.DataFrame([
        {"isin": "BEST", "name": "Best", "asset_class": "ETF", "yahoo_ticker": "BEST.PA", "CI_LIGHT_TRADINGVIEW_DAILY": "BUY", "CI_LIGHT_TRADINGVIEW_WEEKLY": "STRONG_BUY", "CI_LIGHT_TRADINGVIEW_MONTHLY": "BUY", "CI_LIGHT_MORNINGSTAR_RATING": 4, "CI_LIGHT_INCLUDED": True, "CI_LIGHT_REASON": "PASS_ALL_LIGHT_GATES"},
        {"isin": "OTHER", "name": "Other", "asset_class": "ETF", "yahoo_ticker": "OTHER.PA", "CI_LIGHT_TRADINGVIEW_DAILY": "BUY", "CI_LIGHT_TRADINGVIEW_WEEKLY": "BUY", "CI_LIGHT_TRADINGVIEW_MONTHLY": "BUY", "CI_LIGHT_MORNINGSTAR_RATING": 5, "CI_LIGHT_INCLUDED": True, "CI_LIGHT_REASON": "PASS_ALL_LIGHT_GATES"},
    ])
    kept, removed, decisions = light._apply_etf_overlap_gate(
        selected, {"BEST.PA": base, "OTHER.PA": base * 2}, minimum_observations=60
    )
    assert kept["isin"].tolist() == ["BEST"]
    assert removed["isin"].tolist() == ["OTHER"]
    assert decisions[0]["method"] == "RETURN_CORRELATION_PROXY"
