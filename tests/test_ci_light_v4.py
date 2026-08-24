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


def test_etf_uses_morningstar_not_equity_analyst_contract():
    accepted, reasons, _ = light._evaluate(
        _row(
            asset_class="ETF",
            morningstar_rating=3,
            boursorama_consensus=None,
            boursorama_n_analysts=None,
            boursorama_target_upside_pct=None,
        )
    )
    assert accepted is True
    assert not any("BOURSORAMA" in reason for reason in reasons)
    assert light._evaluate(_row(asset_class="ETF", morningstar_rating=2.99))[0] is False
    assert light._evaluate(_row(asset_class="ETF", morningstar_rating=None))[0] is False


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


def test_light_reuses_selection_context_without_second_collection(monkeypatch, tmp_path):
    row = {"isin": "A", "name": "A", **_row().to_dict()}
    upstream = tmp_path / light.UPSTREAM
    prepared = tmp_path / light.SELECTION_ALL
    audit = tmp_path / light.SELECTION_AUDIT
    for path in (upstream, prepared, audit):
        path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(upstream, sep=";", index=False, encoding="utf-8-sig")
    pd.DataFrame([row]).to_csv(prepared, sep=";", index=False, encoding="utf-8-sig")
    audit.write_text('{"source_context":{"status":"SUCCESS_WITH_CONTEXT"}}', encoding="utf-8")
    monkeypatch.setattr(
        light,
        "enrich_selected_rows_v4",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("second collection")),
    )
    payload = light.run(tmp_path, reuse_selection_context=True)
    assert payload["status"] == "SUCCESS"
    assert payload["source_context_reused"] is True
    assert payload["source_collection_passes"] == 0
