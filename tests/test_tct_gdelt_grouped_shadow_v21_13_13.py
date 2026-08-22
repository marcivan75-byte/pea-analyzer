from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

from v182.sources import tct_catalyst_news_grouped_shadow_v21_13_13 as shadow
from v182.sources.tct_catalyst_news_v24_4_2 import NewsBatch, score_windowed_articles


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 8, 22, 5, 0, tzinfo=timezone.utc)
END = datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)


def _cfg() -> dict:
    return json.loads((ROOT / "config/TCT_V24_4_2_CATALYST_CONTEXT_SHADOW.json").read_text(encoding="utf-8"))


def _article(title: str, minute: int = 0, domain: str = "source.example") -> dict:
    return {
        "title": title,
        "seendate": f"20260822T06{minute:02d}00Z",
        "domain": domain,
        "url": f"https://{domain}/{minute}",
    }


def test_group_builder_reduces_40_individual_queries_to_8_groups_of_5():
    cfg = _cfg()
    candidates = [
        {"isin": f"FR{i:010d}", "name": f"Company {i:02d}"}
        for i in range(40)
    ]

    groups, rejected = shadow.build_grouped_queries(candidates, cfg=cfg, group_size=5)

    assert rejected == []
    assert len(groups) == 8
    assert all(len(group.candidates) == 5 for group in groups)
    assert all(group.max_records == 125 for group in groups)
    assert groups[0].query == '("Company 00" OR "Company 01" OR "Company 02" OR "Company 03" OR "Company 04")'


def test_group_builder_fails_closed_if_future_query_suffix_changes_semantics():
    cfg = _cfg()
    cfg["news"]["candidate_query_suffix"] = "markets"

    groups, rejected = shadow.build_grouped_queries(
        [{"isin": "FR1", "name": "Alpha SA"}, {"isin": "FR2", "name": "Beta SE"}],
        cfg=cfg,
    )

    assert groups == []
    assert rejected == [{"reason": "NON_EMPTY_QUERY_SUFFIX_UNSUPPORTED_IN_SHADOW", "suffix": "markets"}]


def test_strict_title_attribution_drops_ambiguous_and_unattributed_articles():
    cfg = _cfg()
    groups, _ = shadow.build_grouped_queries(
        [
            {"isin": "FR1", "name": "Alpha Energie"},
            {"isin": "FR2", "name": "Béta Industrie"},
        ],
        cfg=cfg,
        group_size=2,
    )
    articles = [
        _article("Alpha Energie raises guidance", 1),
        _article("Beta Industrie wins major contract", 2),
        _article("Alpha Energie and Beta Industrie announce partnership", 3),
        _article("European industrial shares rise", 4),
    ]

    by_isin, metrics = shadow.attribute_group_articles(
        groups[0], articles, start_utc=START, end_utc=END, cfg=cfg
    )

    assert [row["title"] for row in by_isin["FR1"]] == ["Alpha Energie raises guidance"]
    assert [row["title"] for row in by_isin["FR2"]] == ["Beta Industrie wins major contract"]
    assert metrics.windowed_articles == 4
    assert metrics.attributed_articles == 2
    assert metrics.ambiguous_articles == 1
    assert metrics.unattributed_articles == 1


def test_grouped_shadow_never_converts_unattributable_group_news_to_zero_evidence(monkeypatch):
    cfg = _cfg()
    candidates = [
        {"isin": "FR1", "name": "Alpha SA"},
        {"isin": "FR2", "name": "Beta SE"},
    ]

    def fake_fetch(query, **kwargs):
        assert "Alpha SA" in query and "Beta SE" in query
        return [_article("European equities react to earnings season", 1)], None

    monkeypatch.setattr(shadow, "fetch_articles", fake_fetch)
    batch = shadow.fetch_candidate_news_grouped_shadow(
        candidates,
        start_utc=START,
        end_utc=END,
        phase="PREOPEN",
        cfg=cfg,
        group_size=2,
    )

    assert batch["FR1"].magnitude_score is None
    assert batch["FR2"].magnitude_score is None
    assert batch["FR1"].error == "GROUPED_SHADOW_NO_STRICT_TITLE_ATTRIBUTION"
    assert batch["FR2"].error == "GROUPED_SHADOW_NO_STRICT_TITLE_ATTRIBUTION"
    assert batch.metrics["unattributed_articles_dropped"] == 1
    assert batch.metrics["production_path_changed"] is False
    assert batch.metrics["scheduled_workflow_wired"] is False
    assert batch.metrics["promotion_authority"] is False


def test_ab_comparator_requires_exact_output_or_counts_candidate_as_fallback():
    cfg = _cfg()
    alpha_articles = [_article("Alpha SA raises guidance", 1)]
    beta_articles = [_article("Beta SE profit warning", 2)]
    individual = {
        "FR1": score_windowed_articles(alpha_articles, start_utc=START, end_utc=END, cfg=cfg),
        "FR2": score_windowed_articles(beta_articles, start_utc=START, end_utc=END, cfg=cfg),
    }
    grouped = NewsBatch(
        {
            "FR1": score_windowed_articles(alpha_articles, start_utc=START, end_utc=END, cfg=cfg),
            "FR2": score_windowed_articles([], start_utc=START, end_utc=END, cfg=cfg, error="GROUPED_SHADOW_NO_STRICT_TITLE_ATTRIBUTION"),
        },
        metrics={"grouped_request_count": 1},
    )

    audit = shadow.compare_individual_vs_grouped(individual, grouped)

    assert audit["exact_equivalent_count"] == 1
    assert audit["fallback_isins"] == ["FR2"]
    assert audit["fallback_count"] == 1
    assert audit["baseline_request_count"] == 2
    assert audit["grouped_request_count"] == 1
    assert audit["projected_requests_with_exact_fallback"] == 2
    assert audit["projected_request_reduction_pct"] == 0.0
    assert audit["promotion_ready_exact_equivalence"] is False
    assert audit["production_activation"] is False


def test_exact_equivalence_can_quantify_request_reduction_without_authorizing_promotion():
    cfg = _cfg()
    alpha_articles = [_article("Alpha SA raises guidance", 1)]
    beta_articles = [_article("Beta SE profit warning", 2)]
    individual = {
        "FR1": score_windowed_articles(alpha_articles, start_utc=START, end_utc=END, cfg=cfg),
        "FR2": score_windowed_articles(beta_articles, start_utc=START, end_utc=END, cfg=cfg),
    }
    grouped = NewsBatch(dict(individual), metrics={"grouped_request_count": 1})

    audit = shadow.compare_individual_vs_grouped(individual, grouped)

    assert audit["exact_equivalence_rate"] == 1.0
    assert audit["fallback_count"] == 0
    assert audit["projected_request_reduction_pct"] == 50.0
    assert audit["promotion_ready_exact_equivalence"] is True
    assert audit["production_activation"] is False


def test_grouped_shadow_is_not_wired_into_any_scheduled_workflow():
    needle = "tct_catalyst_news_grouped_shadow_v21_13_13"
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        assert needle not in workflow.read_text(encoding="utf-8")

    production = (ROOT / "src/v182/sources/tct_catalyst_news_v24_4_2.py").read_text(encoding="utf-8")
    assert needle not in production
