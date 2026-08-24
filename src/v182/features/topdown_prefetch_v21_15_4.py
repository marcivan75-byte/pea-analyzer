from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
import json

import pandas as pd

from v182.features import topdown_features as base
from v182.sources.fred_macro import global_macro_score
from v182.sources.gdelt_news import safe_query_text, score_queries


VERSION = "TOPDOWN_PREFETCH_V21_15_4"


@dataclass(frozen=True)
class PreparedTopdown:
    instrument_news_top_n: int
    global_query: str
    specs: tuple[dict, ...]
    action_country_queries: dict[str, str]
    action_sector_queries: dict[str, str]
    action_instrument_queries: dict[str, str]
    etf_country_queries: dict[str, str]
    etf_sector_queries: dict[str, str]
    query_fingerprint: str


@dataclass(frozen=True)
class ExternalTopdown:
    macro: object
    news_results: dict
    query_fingerprint: str


def _query_fingerprint(specs: list[dict]) -> str:
    payload = [
        {
            "kind": str(spec.get("kind") or ""),
            "key": str(spec.get("key") or ""),
            "query": str(spec.get("query") or ""),
        }
        for spec in specs
    ]
    return sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def prepare(actions: pd.DataFrame, etfs: pd.DataFrame, *, instrument_news_top_n: int) -> PreparedTopdown:
    """Build only the deterministic external query set; no network and no scoring."""
    global_query = "(markets OR economy OR stocks OR bonds)"
    specs: list[dict] = [base._news_spec("global_news", "GLOBAL", global_query)]
    maps: dict[str, dict[str, str]] = {}

    for asset_class, frame in (("ACTION", actions), ("ETF", etfs)):
        countries = base._country_series(frame)
        sectors = base._sector_series(frame)
        country_queries: dict[str, str] = {}
        sector_queries: dict[str, str] = {}
        instrument_queries: dict[str, str] = {}

        for country in base._valid_group_labels(countries):
            query = f'"{safe_query_text(country)}" (economy OR markets OR rates OR inflation)'
            country_queries[country] = query
            specs.append(base._news_spec(f"{asset_class}_country_news", country, query))
        for sector in base._valid_group_labels(sectors):
            query = f'"{safe_query_text(sector)}" (stocks OR industry OR earnings OR outlook)'
            sector_queries[sector] = query
            specs.append(base._news_spec(f"{asset_class}_sector_news", sector, query))
        if asset_class == "ACTION":
            for _, row in base._instrument_candidates(frame, int(instrument_news_top_n)).iterrows():
                isin = str(row.get("isin", "") or "")
                name = safe_query_text(row.get("name", ""))
                if not isin or len(name) < 3:
                    continue
                query = f'"{name}"'
                instrument_queries[isin] = query
                specs.append(base._news_spec("ACTION_instrument_news", isin, query))

        maps[f"{asset_class}_country"] = country_queries
        maps[f"{asset_class}_sector"] = sector_queries
        maps[f"{asset_class}_instrument"] = instrument_queries

    return PreparedTopdown(
        instrument_news_top_n=int(instrument_news_top_n),
        global_query=global_query,
        specs=tuple(specs),
        action_country_queries=maps.get("ACTION_country", {}),
        action_sector_queries=maps.get("ACTION_sector", {}),
        action_instrument_queries=maps.get("ACTION_instrument", {}),
        etf_country_queries=maps.get("ETF_country", {}),
        etf_sector_queries=maps.get("ETF_sector", {}),
        query_fingerprint=_query_fingerprint(specs),
    )


def fetch_external(prepared: PreparedTopdown, *, fred_api_key: str | None) -> ExternalTopdown:
    """Fetch the exact prepared FRED/GDELT set while the main pipeline does other work."""
    queries = [str(spec["query"]) for spec in prepared.specs]
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="topdown-prefetch-provider") as pool:
        macro_future = pool.submit(global_macro_score, fred_api_key)
        news_future = pool.submit(
            score_queries,
            queries,
            timespan="2d",
            max_records=50,
            delay_seconds=0.12,
            max_workers=base.TOPDOWN_GDELT_MAX_WORKERS,
        )
        macro = macro_future.result()
        news_results = news_future.result()
    return ExternalTopdown(
        macro=macro,
        news_results=news_results,
        query_fingerprint=prepared.query_fingerprint,
    )


def compatible(prepared: PreparedTopdown, actions: pd.DataFrame, etfs: pd.DataFrame) -> bool:
    actual = prepare(actions, etfs, instrument_news_top_n=prepared.instrument_news_top_n)
    return actual.query_fingerprint == prepared.query_fingerprint


def finalize(
    actions: pd.DataFrame,
    etfs: pd.DataFrame,
    prepared: PreparedTopdown,
    external: ExternalTopdown,
):
    """Rebuild the legacy TopDownResult using fresh local OHLCV and prefetched external evidence."""
    if external.query_fingerprint != prepared.query_fingerprint:
        raise RuntimeError("TOPDOWN_PREFETCH_EXTERNAL_FINGERPRINT_MISMATCH")
    actual = prepare(actions, etfs, instrument_news_top_n=prepared.instrument_news_top_n)
    if actual.query_fingerprint != prepared.query_fingerprint:
        raise RuntimeError("TOPDOWN_PREFETCH_QUERY_SET_CHANGED")

    diagnostics: list[dict] = []
    provenance: dict[str, str] = {}
    global_scores: dict[str, float] = {}
    action_scores: dict[str, dict[str, float]] = {}
    etf_scores: dict[str, dict[str, float]] = {}
    combined = pd.concat(
        [actions.assign(__asset="ACTION"), etfs.assign(__asset="ETF")],
        ignore_index=True,
        sort=False,
    )

    sentiment = base._market_regime_score(combined)
    if sentiment is not None:
        global_scores["funnel_market_sentiment_score"] = sentiment
        provenance["funnel_market_sentiment_score"] = "INTERNAL_PIT_BREADTH_MOMENTUM"

    macro = external.macro
    if macro.score is not None and macro.coverage >= 0.50:
        global_scores["funnel_global_macro_score"] = macro.score
        provenance["funnel_global_macro_score"] = "FRED"
    else:
        fallback = base._market_regime_score(combined)
        if fallback is not None:
            global_scores["funnel_global_macro_score"] = fallback
            provenance["funnel_global_macro_score"] = "MARKET_IMPLIED_MACRO_FALLBACK_C"
    diagnostics.append(
        {
            "kind": "global_macro",
            "score": macro.score,
            "coverage": macro.coverage,
            "components": macro.components,
            "errors": macro.errors,
            "effective_source": provenance.get("funnel_global_macro_score"),
        }
    )

    specs = list(prepared.specs)
    results = external.news_results
    base._record_news_diagnostics(specs, results, diagnostics)
    global_score, _ = results.get(prepared.global_query, (None, "GDELT_RESULT_MISSING"))
    if global_score is not None and global_score.score is not None:
        global_scores["funnel_global_news_score"] = global_score.score
        provenance["funnel_global_news_score"] = "GDELT_2D_LEXICAL"

    query_maps = {
        "ACTION": {
            "country": prepared.action_country_queries,
            "sector": prepared.action_sector_queries,
            "instrument": prepared.action_instrument_queries,
        },
        "ETF": {
            "country": prepared.etf_country_queries,
            "sector": prepared.etf_sector_queries,
            "instrument": {},
        },
    }

    for asset_class, frame, target in (
        ("ACTION", actions, action_scores),
        ("ETF", etfs, etf_scores),
    ):
        countries = base._country_series(frame)
        sectors = base._sector_series(frame)
        country_macro = base._group_regime_scores(frame, countries, min_names=3)
        country_news: dict[str, float] = {}
        for country, query in query_maps[asset_class]["country"].items():
            score, _ = results.get(query, (None, None))
            if score is not None and score.score is not None:
                country_news[country] = score.score
        sector_news: dict[str, float] = {}
        for sector, query in query_maps[asset_class]["sector"].items():
            score, _ = results.get(query, (None, None))
            if score is not None and score.score is not None:
                sector_news[sector] = score.score
        instrument_news: dict[str, float] = {}
        for isin, query in query_maps[asset_class]["instrument"].items():
            score, _ = results.get(query, (None, None))
            if score is not None and score.score is not None:
                instrument_news[isin] = score.score

        for idx, row in frame.iterrows():
            isin = str(row.get("isin", "") or "")
            if not isin:
                continue
            item: dict[str, float] = {}
            country = str(countries.loc[idx]) if idx in countries.index else "N/A"
            sector = str(sectors.loc[idx]) if idx in sectors.index else "N/A"
            if country in country_macro:
                item["funnel_country_macro_score"] = country_macro[country]
            if country in country_news:
                item["funnel_country_news_score"] = country_news[country]
            if sector in sector_news:
                item["funnel_sector_news_score"] = sector_news[sector]
            if isin in instrument_news:
                item["funnel_instrument_news_score"] = instrument_news[isin]
                item["news_catalyst_score"] = instrument_news[isin]
            for key, value in global_scores.items():
                item[key] = value
            target[isin] = item

    provenance["funnel_country_macro_score"] = "MARKET_IMPLIED_COUNTRY_REGIME_C"
    provenance["funnel_country_news_score"] = "GDELT_2D_LEXICAL"
    provenance["funnel_sector_news_score"] = "GDELT_2D_LEXICAL"
    provenance["funnel_instrument_news_score"] = "GDELT_2D_LEXICAL_TOP_ACTIONS"
    return base.TopDownResult(global_scores, action_scores, etf_scores, provenance, diagnostics)
