from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v182.features.etf_fund_flows_v1 import (
    build_family_scores,
    build_flow_computation,
    compute_daily_flows,
)
from v182.sources.etf_fund_flows import (
    _last_market_as_of,
    _merge_same_day_observations,
    build_pea_flow_universe,
)

ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ETF_FUND_FLOW_V1_SHADOW.json").read_text(encoding="utf-8"))


def _base_row(instrument_id: str, date: str, **kwargs) -> dict:
    row = {
        "instrument_id": instrument_id, "as_of": date, "name": instrument_id, "universe": "EXTERNAL",
        "asset_class": "ETF", "economic_family": "TEST_FAMILY", "region": "US", "sector_or_theme": "TECHNOLOGY",
        "source": "issuer", "source_type": "ISSUER_OFFICIAL", "confidence": "A", "source_priority": 100,
        "aum": np.nan, "nav": np.nan, "shares_outstanding": np.nan, "market_price": np.nan,
        "distribution_per_share": 0.0, "is_pea": False, "is_inverse_or_leveraged": False, "is_synthetic": False,
        "isin": "", "ticker": "", "provider": "", "benchmark": "", "currency": "USD",
        "aum_as_of_explicit": True, "nav_as_of_explicit": True,
        "shares_as_of_explicit": True, "market_price_as_of_explicit": True,
    }
    row.update(kwargs)
    return row


def test_shares_nav_flow_isolated_from_market_performance():
    history = pd.DataFrame([
        _base_row("A", "2026-08-18", aum=1000.0, nav=10.0, shares_outstanding=100.0, market_price=10.0),
        _base_row("A", "2026-08-19", aum=1210.0, nav=11.0, shares_outstanding=110.0, market_price=11.0),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "SHARES_NAV"
    assert last["flow"] == pytest.approx(110.0)
    assert last["organic_flow_rate"] == pytest.approx(0.11)


def test_shares_nav_flow_rate_uses_implied_fund_value_when_aum_missing():
    history = pd.DataFrame([
        _base_row("A", "2026-08-18", aum=np.nan, nav=10.0, shares_outstanding=100.0),
        _base_row("A", "2026-08-19", aum=np.nan, nav=10.0, shares_outstanding=110.0),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "SHARES_NAV"
    assert last["flow"] == pytest.approx(100.0)
    assert last["organic_flow_rate"] == pytest.approx(0.10)


def test_aum_fallback_adjusts_performance_and_distribution():
    history = pd.DataFrame([
        _base_row("A", "2026-08-18", aum=1000.0, nav=10.0, market_price=10.0),
        _base_row("A", "2026-08-19", aum=1020.0, nav=10.0, market_price=10.0, distribution_per_share=0.10),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "AUM_PERFORMANCE_ADJUSTED"
    assert last["period_return"] == pytest.approx(0.01)
    assert last["flow"] == pytest.approx(10.0)


def test_undated_unchanged_aum_is_not_interpreted_as_flow():
    history = pd.DataFrame([
        _base_row(
            "A", "2026-08-18", aum=1000.0, nav=10.0, market_price=10.0,
            aum_as_of_explicit=False, nav_as_of_explicit=False, market_price_as_of_explicit=True,
        ),
        _base_row(
            "A", "2026-08-19", aum=1000.0, nav=10.0, market_price=11.0,
            aum_as_of_explicit=False, nav_as_of_explicit=False, market_price_as_of_explicit=True,
        ),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "UNSCORABLE_UNDATED_AUM_UNCHANGED"
    assert pd.isna(last["flow"])
    assert last["period_return"] == pytest.approx(0.10)


def test_undated_changed_aum_uses_dated_market_return_and_is_labeled():
    history = pd.DataFrame([
        _base_row(
            "A", "2026-08-18", aum=1000.0, nav=10.0, market_price=10.0,
            aum_as_of_explicit=False, nav_as_of_explicit=False, market_price_as_of_explicit=True,
        ),
        _base_row(
            "A", "2026-08-19", aum=1120.0, nav=10.0, market_price=11.0,
            aum_as_of_explicit=False, nav_as_of_explicit=False, market_price_as_of_explicit=True,
        ),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "AUM_PERFORMANCE_ADJUSTED_UNDATED_VENDOR_UPDATE"
    assert last["period_return"] == pytest.approx(0.10)
    assert last["flow"] == pytest.approx(20.0)


def test_market_history_date_is_used_as_yahoo_observation_date():
    history = pd.DataFrame(
        {"Close": [10.0, 10.2]},
        index=pd.to_datetime(["2026-08-18T15:30:00-04:00", "2026-08-19T15:30:00-04:00"]),
    )
    as_of, explicit = _last_market_as_of(history)
    assert as_of == "2026-08-19"
    assert explicit is True


def test_rolling_5d_flow_rate_uses_aum_before_five_intervals():
    dates = pd.bdate_range("2026-08-10", periods=6)
    rows = []
    nav = 10.0
    shares = 100.0
    for index, date in enumerate(dates):
        if index:
            shares += 10.0
        rows.append(_base_row(
            "A", date.date().isoformat(), aum=shares * nav, nav=nav,
            shares_outstanding=shares, market_price=nav,
        ))
    result = build_flow_computation(pd.DataFrame(rows), _cfg())
    last = result.observations.iloc[-1]
    assert last["flow_5d"] == pytest.approx(500.0)
    assert last["organic_flow_rate_5d"] == pytest.approx(0.5)


def test_low_confidence_is_never_scored_as_flow():
    history = pd.DataFrame([
        _base_row("A", "2026-08-18", confidence="D", aum=1000.0, nav=10.0, shares_outstanding=100.0),
        _base_row("A", "2026-08-19", confidence="D", aum=1100.0, nav=10.0, shares_outstanding=110.0),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "UNSCORABLE_LOW_CONFIDENCE"
    assert pd.isna(last["flow"])


def test_shadow_scores_never_gain_decision_influence():
    dates = pd.bdate_range("2026-05-01", periods=30)
    rows = []
    definitions = [
        ("ISIN:PEA1", "EU", True, "WORLD", "TECHNOLOGY", 1_000_000.0, 5000.0, "USD"),
        ("GLOBAL1", "US", False, "WORLD", "TECHNOLOGY", 2_000_000.0, 10000.0, "USD"),
    ]
    for instrument_id, region, is_pea, family, theme, initial_aum, daily_flow, currency in definitions:
        nav = 100.0
        shares = initial_aum / nav
        for index, date in enumerate(dates):
            if index:
                nav *= 1.001
                shares += daily_flow / nav
            rows.append(_base_row(
                instrument_id, date.date().isoformat(), universe="PEA_ETF" if is_pea else "EXTERNAL",
                economic_family=family, region=region, sector_or_theme=theme,
                asset_class="ETF",
                is_pea=is_pea, aum=shares * nav, nav=nav, shares_outstanding=shares, market_price=nav, currency=currency,
            ))
    result = build_flow_computation(pd.DataFrame(rows), _cfg())
    assert not result.instruments.empty
    assert (result.instruments["decision_influence"] == 0.0).all()
    assert (result.rotations["decision_influence"] == 0.0).all()
    pea = result.instruments[result.instruments["is_pea"]].iloc[0]
    assert pd.notna(pea["pea_flow_overlay_shadow"])
    assert pea["efs_readiness"] == "PRELIMINARY_20_59"
    assert result.diagnostics["live_orders_enabled"] is False


def test_srfs_remains_empty_before_preliminary_20_flow_observations():
    dates = pd.bdate_range("2026-08-03", periods=10)
    rows = []
    for instrument_id in ("A", "B"):
        nav = 100.0
        shares = 10_000.0
        for index, date in enumerate(dates):
            if index:
                shares += 10.0
            rows.append(_base_row(
                instrument_id, date.date().isoformat(), economic_family="WORLD",
                sector_or_theme="TECHNOLOGY", aum=shares * nav, nav=nav,
                shares_outstanding=shares, market_price=nav,
            ))
    result = build_flow_computation(pd.DataFrame(rows), _cfg())
    assert result.instruments["efs_shadow"].isna().all()
    assert result.rotations.empty
    assert result.diagnostics["srfs_scorable_sectors"] == 0


def test_family_breadth_ignores_missing_20d_rates_instead_of_counting_them_negative():
    instruments = pd.DataFrame([
        {
            "instrument_id": "A", "economic_family": "WORLD", "region": "US", "currency": "USD",
            "is_inverse_or_leveraged": False, "flow_confidence": "A", "flow_5d": 1.0, "flow_20d": 2.0,
            "flow_60d": np.nan, "organic_flow_rate_20d": 0.02, "efs_shadow": 70.0, "price_return_20d": 0.03,
        },
        {
            "instrument_id": "B", "economic_family": "WORLD", "region": "US", "currency": "USD",
            "is_inverse_or_leveraged": False, "flow_confidence": "A", "flow_5d": np.nan, "flow_20d": np.nan,
            "flow_60d": np.nan, "organic_flow_rate_20d": np.nan, "efs_shadow": np.nan, "price_return_20d": np.nan,
        },
    ])
    family = build_family_scores(instruments).iloc[0]
    assert family["valid_20d_instruments"] == 1
    assert family["breadth_positive_20d_pct"] == pytest.approx(100.0)


def test_pea_universe_uses_isin_and_economic_benchmark_for_synthetic_etf():
    master = pd.DataFrame([{
        "isin": "FR0014000001", "name": "Synthetic World PEA", "yahoo_ticker": "TEST.PA",
        "official_benchmark": "MSCI World", "category": "Actions Monde", "geo_exposure": "World",
        "region_domicile": "Europe", "provider": "Example", "pea_type": "PEA", "pea_confidence": "HIGH",
        "replication_hint": "SYNTHETIC_SWAP",
    }])
    row = build_pea_flow_universe(master).iloc[0]
    assert row["instrument_id"] == "ISIN:FR0014000001"
    assert row["economic_family"] == "WORLD"
    assert row["region"] == "World"
    assert bool(row["is_synthetic"]) is True


def test_split_like_share_change_is_quarantined_not_counted_as_flow():
    history = pd.DataFrame([
        _base_row("A", "2026-08-18", aum=1000.0, nav=10.0, shares_outstanding=100.0, market_price=10.0),
        _base_row("A", "2026-08-19", aum=1000.0, nav=5.0, shares_outstanding=200.0, market_price=5.0),
    ])
    last = compute_daily_flows(history).iloc[-1]
    assert last["flow_method"] == "QUARANTINED_SPLIT_LIKE_EVENT"
    assert last["flow_confidence"] == "QUARANTINE"
    assert pd.isna(last["flow"])


def test_same_day_official_evidence_can_be_supplemented_without_claiming_a_grade():
    snapshot = pd.DataFrame([
        {**_base_row("A", "2026-08-19", source="issuer", confidence="A", source_priority=100, aum=1000.0, shares_outstanding=100.0), "_confidence_rank": 4},
        {
            **_base_row(
                "A", "2026-08-19", source="yahoo", source_type="YFINANCE", confidence="C", source_priority=50,
                nav=10.0, nav_as_of_explicit=False,
            ),
            "_confidence_rank": 2,
        },
    ])
    row = _merge_same_day_observations(snapshot).iloc[0]
    assert row["aum"] == pytest.approx(1000.0)
    assert row["nav"] == pytest.approx(10.0)
    assert row["confidence"] == "C"
    assert row["source_components"] == "issuer|yahoo"
    assert bool(row["aum_as_of_explicit"]) is True
    assert bool(row["nav_as_of_explicit"]) is False


def test_mixed_currency_family_share_is_not_computed():
    dates = pd.bdate_range("2026-05-01", periods=30)
    rows = []
    for instrument_id, currency in (("A", "USD"), ("B", "EUR")):
        nav = 100.0
        shares = 10_000.0
        for index, date in enumerate(dates):
            if index:
                shares += 10.0
            rows.append(_base_row(
                instrument_id, date.date().isoformat(), economic_family="WORLD", currency=currency,
                aum=shares * nav, nav=nav, shares_outstanding=shares, market_price=nav,
            ))
    result = build_flow_computation(pd.DataFrame(rows), _cfg())
    assert result.instruments["flow_share_family_20d_pct"].isna().all()
    assert all(~result.families["absolute_flow_comparable"].astype(bool))


def test_config_weights_are_pre_registered_and_sum_to_one():
    cfg = _cfg()
    assert sum(cfg["score_weights"].values()) == pytest.approx(1.0)
    assert sum(cfg["sector_rotation_flow_weights"].values()) == pytest.approx(1.0)
    assert sum(cfg["pea_overlay_weights"]["mature"].values()) == pytest.approx(1.0)
    assert sum(cfg["pea_overlay_weights"]["young_history"].values()) == pytest.approx(1.0)
    assert cfg["preliminary_score_min_observations"] == 20
    assert cfg["mature_score_min_observations"] == 60
    assert cfg["anti_false_signal"]["mixed_currency_absolute_aggregation_forbidden"] is True
    assert cfg["anti_false_signal"]["undated_unchanged_aum_fallback_forbidden"] is True
    assert cfg["anti_false_signal"]["dated_market_return_preferred_over_undated_nav"] is True
    assert cfg["anti_false_signal"]["breadth_missing_values_not_negative"] is True
    assert cfg["anti_false_signal"]["srfs_requires_preliminary_instrument_readiness"] is True
    assert cfg["governance"]["decision_influence"] == 0.0
    assert cfg["governance"]["promotion_requires_dedicated_pit_oos"] is True
    assert cfg["governance"]["weights_changed_v21_16"] is False
    assert cfg["governance"]["thresholds_changed_v21_16"] is False
    assert cfg["governance"]["holdout_opened_v21_16"] is False


def test_external_flow_universe_contains_only_generic_etfs():
    universe = pd.read_csv(
        ROOT / "config" / "ETF_FUND_FLOW_EXTERNAL_UNIVERSE_V1.csv",
        sep=";",
        encoding="utf-8-sig",
    )
    assert len(universe) == 24
    assert set(universe["asset_class"].astype(str)) == {"ETF"}
