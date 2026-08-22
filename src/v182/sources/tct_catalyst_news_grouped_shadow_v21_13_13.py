from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
import re
import unicodedata

from v182.sources import tct_catalyst_news as base
from v182.sources.gdelt_news import fetch_articles, safe_query_text
from v182.sources.rate_limit import StartRateLimiter
from v182.sources.tct_catalyst_news_v24_4_2 import CatalystNews, NewsBatch, score_windowed_articles


VERSION = "V21.13.13_GDELT_GROUPED_QUERY_SHADOW"
DEFAULT_GROUP_SIZE = 5
GDELT_ARTLIST_MAX_RECORDS = 250


@dataclass(frozen=True)
class GroupCandidate:
    isin: str
    name: str
    match_phrase: str


@dataclass(frozen=True)
class GroupedQuery:
    group_id: str
    candidates: tuple[GroupCandidate, ...]
    query: str
    max_records: int


@dataclass(frozen=True)
class AttributionMetrics:
    windowed_articles: int
    attributed_articles: int
    ambiguous_articles: int
    unattributed_articles: int


def _normalise_match(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return f" {phrase} " in f" {text} "


def _candidate(name: object, isin: object) -> GroupCandidate | None:
    clean_name = safe_query_text(name, max_len=80)
    clean_isin = str(isin or "").strip()
    match_phrase = _normalise_match(clean_name)
    if not clean_isin or not clean_name or not match_phrase:
        return None
    return GroupCandidate(clean_isin, clean_name, match_phrase)


def build_grouped_queries(
    candidates: list[dict],
    *,
    cfg: dict,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> tuple[list[GroupedQuery], list[dict]]:
    """Build deterministic GDELT OR groups without changing the production query path.

    The current V24.4.2 suffix is empty. This shadow engine deliberately fails
    closed when a suffix is configured, because nesting or redistributing future
    query operators could change semantics. Production continues to use the
    individual-query implementation regardless of this function's result.
    """

    suffix = str(cfg.get("news", {}).get("candidate_query_suffix") or "").strip()
    if suffix:
        return [], [{"reason": "NON_EMPTY_QUERY_SUFFIX_UNSUPPORTED_IN_SHADOW", "suffix": suffix}]

    limit = min(
        int(cfg.get("data_policy", {}).get("news_query_limit", 40)),
        int(cfg.get("data_policy", {}).get("candidate_limit", 40)),
    )
    size = max(2, min(int(group_size), 8))
    seen_isins: set[str] = set()
    selected: list[GroupCandidate] = []
    rejected: list[dict] = []
    for row in candidates[:limit]:
        item = _candidate(row.get("name"), row.get("isin"))
        if item is None:
            rejected.append({"isin": str(row.get("isin") or ""), "reason": "MISSING_ISIN_OR_COMPANY_NAME"})
            continue
        if item.isin in seen_isins:
            rejected.append({"isin": item.isin, "reason": "DUPLICATE_ISIN"})
            continue
        seen_isins.add(item.isin)
        selected.append(item)

    per_candidate = max(1, int(cfg.get("data_policy", {}).get("news_max_records_per_candidate", 25)))
    groups: list[GroupedQuery] = []
    for offset in range(0, len(selected), size):
        members = tuple(selected[offset : offset + size])
        if not members:
            continue
        terms = " OR ".join(f'"{member.name}"' for member in members)
        max_records = min(GDELT_ARTLIST_MAX_RECORDS, per_candidate * len(members))
        groups.append(
            GroupedQuery(
                group_id=f"G{len(groups) + 1:02d}",
                candidates=members,
                query=f"({terms})",
                max_records=max_records,
            )
        )
    return groups, rejected


def attribute_group_articles(
    group: GroupedQuery,
    articles: list[dict],
    *,
    start_utc: datetime,
    end_utc: datetime,
    cfg: dict,
) -> tuple[dict[str, list[dict]], AttributionMetrics]:
    """Attribute only articles whose title contains exactly one grouped company name.

    Articles matching zero company names or several company names are discarded,
    never guessed. This strictness is intentional: the shadow exists to measure
    whether batching is safe, not to manufacture equivalence.
    """

    windowed = base.filter_articles_to_window(
        articles,
        start_utc,
        end_utc,
        require_timestamp=bool(cfg.get("news", {}).get("require_parseable_article_timestamp", True)),
    )
    by_isin: dict[str, list[dict]] = {member.isin: [] for member in group.candidates}
    ambiguous = 0
    unattributed = 0
    attributed = 0
    for article in windowed:
        title = _normalise_match(article.get("title"))
        matches = [member for member in group.candidates if _contains_phrase(title, member.match_phrase)]
        if len(matches) == 1:
            by_isin[matches[0].isin].append(article)
            attributed += 1
        elif len(matches) > 1:
            ambiguous += 1
        else:
            unattributed += 1
    return by_isin, AttributionMetrics(len(windowed), attributed, ambiguous, unattributed)


def fetch_candidate_news_grouped_shadow(
    candidates: list[dict],
    *,
    start_utc: datetime,
    end_utc: datetime,
    phase: str,
    cfg: dict,
    group_size: int = DEFAULT_GROUP_SIZE,
) -> NewsBatch:
    """Fetch grouped GDELT queries for A/B research only.

    This function is intentionally not referenced by any scheduled workflow or
    production catalyst runner. Missing attribution is represented as an error,
    not as zero-news evidence, so the shadow cannot create false confidence.
    """

    groups, rejected = build_grouped_queries(candidates, cfg=cfg, group_size=group_size)
    candidate_count = sum(len(group.candidates) for group in groups)
    if not groups:
        return NewsBatch(metrics={
            "version": VERSION,
            "mode": "SHADOW_ONLY_NOT_WIRED",
            "candidate_count": 0,
            "grouped_request_count": 0,
            "rejected": rejected,
            "promotion_authority": False,
        })

    news_cfg = cfg["news"]
    timespan = news_cfg["preopen_fetch_timespan"] if str(phase).upper() == "PREOPEN" else news_cfg["postmarket_fetch_timespan"]
    timeout = int(news_cfg.get("request_timeout_seconds", 12))
    delay = float(news_cfg.get("parallelism", {}).get("start_delay_seconds", 0.12))
    limiter = StartRateLimiter(delay)
    workers = max(1, min(4, len(groups)))
    started = monotonic()
    raw_by_group: dict[str, tuple[list[dict], str | None]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_articles,
                group.query,
                timespan=timespan,
                max_records=group.max_records,
                timeout=timeout,
                limiter=limiter,
            ): group
            for group in groups
        }
        for future in as_completed(futures):
            group = futures[future]
            try:
                raw_by_group[group.group_id] = future.result()
            except Exception as exc:
                raw_by_group[group.group_id] = ([], f"{type(exc).__name__}: {str(exc)[:160]}")

    batch = NewsBatch()
    total_windowed = 0
    total_attributed = 0
    total_ambiguous = 0
    total_unattributed = 0
    group_errors = 0
    for group in groups:
        articles, error = raw_by_group.get(group.group_id, ([], "GROUP_RESULT_MISSING"))
        if error:
            group_errors += 1
        by_isin, attribution = attribute_group_articles(
            group,
            [dict(x) for x in articles if isinstance(x, dict)],
            start_utc=start_utc,
            end_utc=end_utc,
            cfg=cfg,
        )
        total_windowed += attribution.windowed_articles
        total_attributed += attribution.attributed_articles
        total_ambiguous += attribution.ambiguous_articles
        total_unattributed += attribution.unattributed_articles
        for member in group.candidates:
            member_articles = by_isin[member.isin]
            member_error = error
            if member_error is None and not member_articles and attribution.windowed_articles > 0:
                member_error = "GROUPED_SHADOW_NO_STRICT_TITLE_ATTRIBUTION"
            batch[member.isin] = score_windowed_articles(
                member_articles,
                start_utc=start_utc,
                end_utc=end_utc,
                cfg=cfg,
                error=member_error,
                cache_hit=False,
            )

    individual_request_count = candidate_count
    grouped_request_count = len(groups)
    batch.metrics = {
        "version": VERSION,
        "mode": "SHADOW_ONLY_NOT_WIRED",
        "candidate_count": candidate_count,
        "individual_request_count_reference": individual_request_count,
        "grouped_request_count": grouped_request_count,
        "theoretical_request_reduction_pct_before_fallback": round(
            100.0 * (1.0 - grouped_request_count / max(1, individual_request_count)), 2
        ),
        "group_size": group_size,
        "windowed_articles": total_windowed,
        "strictly_attributed_articles": total_attributed,
        "ambiguous_articles_dropped": total_ambiguous,
        "unattributed_articles_dropped": total_unattributed,
        "group_errors": group_errors,
        "rejected": rejected,
        "elapsed_seconds": round(monotonic() - started, 4),
        "production_path_changed": False,
        "scheduled_workflow_wired": False,
        "promotion_authority": False,
    }
    return batch


def compare_individual_vs_grouped(
    individual: dict[str, CatalystNews],
    grouped: NewsBatch,
) -> dict:
    """Evaluate exact equivalence and the fallback needed to preserve baseline output."""

    rows: list[dict] = []
    fallback: list[str] = []
    baseline_with_articles = 0
    baseline_with_articles_recalled = 0
    critical_event_candidates = 0
    critical_event_candidates_recalled = 0
    exact_equivalent = 0

    for isin, baseline_item in individual.items():
        grouped_item = grouped.get(isin)
        if grouped_item is None:
            fallback.append(isin)
            rows.append({"isin": isin, "exact_equivalent": False, "reason": "GROUPED_RESULT_MISSING"})
            continue

        if baseline_item.article_count > 0:
            baseline_with_articles += 1
            if grouped_item.article_count > 0:
                baseline_with_articles_recalled += 1
        baseline_critical = {x for x in baseline_item.event_types if x != "OTHER_NEWS"}
        grouped_critical = {x for x in grouped_item.event_types if x != "OTHER_NEWS"}
        if baseline_critical:
            critical_event_candidates += 1
            if baseline_critical <= grouped_critical:
                critical_event_candidates_recalled += 1

        equivalent = (
            baseline_item.error == grouped_item.error
            and baseline_item.article_count == grouped_item.article_count
            and baseline_item.magnitude_score == grouped_item.magnitude_score
            and baseline_item.direction_score == grouped_item.direction_score
            and baseline_item.event_types == grouped_item.event_types
            and baseline_item.top_headlines == grouped_item.top_headlines
        )
        if equivalent:
            exact_equivalent += 1
        else:
            fallback.append(isin)
        rows.append({
            "isin": isin,
            "exact_equivalent": equivalent,
            "baseline_articles": baseline_item.article_count,
            "grouped_articles": grouped_item.article_count,
            "baseline_magnitude": baseline_item.magnitude_score,
            "grouped_magnitude": grouped_item.magnitude_score,
            "baseline_direction": baseline_item.direction_score,
            "grouped_direction": grouped_item.direction_score,
            "missing_critical_events": sorted(baseline_critical - grouped_critical),
        })

    grouped_requests = int(grouped.metrics.get("grouped_request_count", 0))
    baseline_requests = max(1, len(individual))
    fallback_unique = sorted(set(fallback))
    projected_requests = grouped_requests + len(fallback_unique)
    return {
        "version": VERSION,
        "comparison_mode": "EXACT_EQUIVALENCE_FAIL_CLOSED",
        "candidate_count": len(individual),
        "exact_equivalent_count": exact_equivalent,
        "exact_equivalence_rate": round(exact_equivalent / max(1, len(individual)), 4),
        "news_presence_recall": None if baseline_with_articles == 0 else round(baseline_with_articles_recalled / baseline_with_articles, 4),
        "critical_event_recall": None if critical_event_candidates == 0 else round(critical_event_candidates_recalled / critical_event_candidates, 4),
        "fallback_isins": fallback_unique,
        "fallback_count": len(fallback_unique),
        "baseline_request_count": baseline_requests,
        "grouped_request_count": grouped_requests,
        "projected_requests_with_exact_fallback": projected_requests,
        "projected_request_reduction_pct": round(100.0 * (1.0 - projected_requests / baseline_requests), 2),
        "promotion_ready_exact_equivalence": len(fallback_unique) == 0 and len(individual) > 0,
        "production_activation": False,
        "rows": rows,
    }
