from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.features.tct_catalyst_context_v24_4 import (
    catalyst_window,
    score_candidate,
    select_catalyst_candidates,
)
from v182.sources.global_market_snapshot import GlobalMarketSnapshot
from v182.sources.tct_catalyst_news import (
    CatalystNews,
    classify_headline,
    filter_articles_to_window,
    parse_article_timestamp,
    score_windowed_articles,
)


ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_0_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def test_preopen_monday_window_starts_friday_europe_close():
    cfg = _cfg()
    now = datetime(2026, 8, 24, 6, 40, tzinfo=timezone.utc)  # Monday 08:40 Paris
    start, end = catalyst_window("PREOPEN", now, cfg)
    assert start == datetime(2026, 8, 21, 15, 30, tzinfo=timezone.utc)
    assert end == now


def test_postmarket_window_starts_same_day_europe_close():
    cfg = _cfg()
    now = datetime(2026, 8, 19, 21, 15, tzinfo=timezone.utc)
    start, end = catalyst_window("POSTMARKET", now, cfg)
    assert start == datetime(2026, 8, 19, 15, 30, tzinfo=timezone.utc)
    assert end == now


def test_article_window_requires_timestamp_and_deduplicates_headlines():
    start = datetime(2026, 8, 19, 15, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 21, 15, tzinfo=timezone.utc)
    articles = [
        {"title": "Company raises guidance", "seendate": "20260819T180000Z", "domain": "a.com"},
        {"title": "Company raises guidance", "seendate": "20260819T181000Z", "domain": "b.com"},
        {"title": "Old news", "seendate": "20260819T140000Z", "domain": "c.com"},
        {"title": "No timestamp", "domain": "d.com"},
    ]
    filtered = filter_articles_to_window(articles, start, end, require_timestamp=True)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Company raises guidance"
    assert parse_article_timestamp("20260819T180000Z") == datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)


def test_multilingual_event_classification_catches_major_european_catalysts():
    weights = _cfg()["news"]["event_weights"]
    assert classify_headline("Le groupe lance un avertissement sur résultats", weights)[0] == "PROFIT_WARNING"
    assert classify_headline("La société relève ses prévisions annuelles", weights)[0] == "GUIDANCE_RAISED"
    assert classify_headline("Konzern veröffentlicht Gewinnwarnung", weights)[0] == "PROFIT_WARNING"
    assert classify_headline("La société annonce une augmentation de capital", weights)[0] == "CAPITAL_RAISE_DILUTION"


def test_high_impact_windowed_news_has_magnitude_and_direction():
    cfg = _cfg()
    start = datetime(2026, 8, 19, 15, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 21, 15, tzinfo=timezone.utc)
    articles = [
        {"title": "Company cuts guidance after weak demand", "seendate": "20260819T200000Z", "domain": "a.com"},
        {"title": "Company lowers guidance", "seendate": "20260819T201000Z", "domain": "b.com"},
        {"title": "Company outlook lowered", "seendate": "20260819T202000Z", "domain": "c.com"},
    ]
    scored = score_windowed_articles(articles, start_utc=start, end_utc=end, cfg=cfg)
    assert scored.article_count == 3
    assert scored.independent_sources == 3
    assert scored.magnitude_score is not None and scored.magnitude_score >= 90
    assert scored.direction_score is not None and scored.direction_score < -50


def test_candidate_selection_is_bounded_and_prioritises_catalysts():
    cfg = _cfg()
    rows = []
    for i in range(100):
        rows.append(
            {
                "isin": f"FR{i:010d}",
                "name": f"Company {i}",
                "entry_score": 50 + (i % 40),
                "exit_risk_score": i % 70,
                "atr14_pct": 0.01 + (i % 5) * 0.01,
                "news_catalyst_score": 90 if i == 99 else 10,
                "days_to_earnings": 1 if i == 98 else 20,
            }
        )
    selected = select_catalyst_candidates(pd.DataFrame(rows), cfg)
    assert len(selected) == int(cfg["data_policy"]["candidate_limit"])
    assert "FR0000000099" in set(selected["isin"])
    assert "FR0000000098" in set(selected["isin"])


def test_scoring_separates_movement_potential_from_direction():
    cfg = _cfg()
    row = pd.Series(
        {
            "entry_score": 82.0,
            "exit_risk_score": 10.0,
            "entry_state": "ENTRY_STRONG_SHADOW",
            "exit_state": "HOLD_SUPPORTIVE_SHADOW",
            "entry_confirmation_count": 4,
            "atr14_pct": 0.03,
            "range_expansion": 1.4,
            "days_to_earnings": 1.0,
        }
    )
    news = CatalystNews(
        95.0, -90.0, 0.9, 3, 3, ("GUIDANCE_CUT",), ("Company cuts guidance",),
        "2026-08-19T15:30:00+00:00", "2026-08-19T21:15:00+00:00", "GDELT_WINDOWED", None,
    )
    market = GlobalMarketSnapshot(60.0, 55.0, {}, {}, "TEST", ())
    scored = score_candidate(row, news, market, phase="POSTMARKET", cfg=cfg)
    assert scored["movement_potential_score"] >= 70
    assert scored["news_technical_conflict"] is True
    assert scored["catalyst_state"] == "NEWS_CONFLICT_SHADOW"
    assert scored["individual_extended_hours_quotes_used"] is False
    assert scored["intraday_bars_used"] is False


def test_v244_governance_has_zero_production_authority():
    cfg = _cfg()
    policy = cfg["data_policy"]
    gov = cfg["governance"]
    assert policy["snapshot_phases"] == ["POSTMARKET", "PREOPEN"]
    assert policy["individual_pea_extended_hours_quotes_used"] is False
    assert policy["continuous_monitoring_forbidden"] is True
    assert policy["intraday_bars_forbidden"] is True
    assert policy["five_minute_forbidden"] is True
    assert policy["quasi_realtime_forbidden"] is True
    assert gov["decision_influence"] == 0.0
    assert gov["score_influence"] == 0.0
    assert gov["sizing_influence"] == 0.0
    assert gov["stop_loss_influence"] == 0.0
    assert gov["ct_influence"] == 0.0
    assert gov["real_orders_enabled"] is False
    assert gov["holdout_locked"] is True
    assert gov["retuning_allowed"] is False
    assert gov["promotion_authority"] is False
    assert np.isclose(sum(cfg["movement_potential_weights"].values()), 1.0)
    assert np.isclose(sum(cfg["direction_weights"].values()), 1.0)
