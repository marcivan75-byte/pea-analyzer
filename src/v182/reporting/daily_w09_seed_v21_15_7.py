from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.reporting import waves


ROOT = Path(__file__).resolve().parents[3]
VERSION = "DAILY_W09_SEED_V21_15_7"
SEED_PATH = ROOT / "config" / "W09_ACTION_SEED_2026_08_23.json"


def load_seed(path: Path = SEED_PATH) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("DAILY_W09_SEED_MISSING")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "W09_ACTION_SEED_V1":
        raise RuntimeError("DAILY_W09_SEED_VERSION_INVALID")
    if not payload.get("as_of") or not payload.get("source_run_id"):
        raise RuntimeError("DAILY_W09_SEED_METADATA_INVALID")
    if payload.get("funnel_global_macro_score") is None or payload.get("funnel_market_sentiment_score") is None:
        raise RuntimeError("DAILY_W09_SEED_GLOBAL_FIELDS_INVALID")
    return payload


def _clean_text(value) -> str:
    if value is None:
        return ""
    try:
        missing = bool(pd.isna(value))
    except (TypeError, ValueError):
        missing = False
    if missing:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def _seed_obs(universe: str, isin: str, field: str, value, source: str, evidence: str, as_of: str) -> dict:
    obs = waves._obs(universe, isin, field, value, source, evidence)
    obs["as_of"] = as_of
    return obs


def action_observations(actions_df: pd.DataFrame, path: Path = SEED_PATH) -> tuple[list[dict], dict]:
    """Rehydrate the last validated W09 Action fields without any network call.

    Country/sector keys are evaluated after WAVE04 has restored Yahoo metadata,
    matching the labels used by the original W09 calculation. Instrument news is
    keyed directly by ISIN. ETF CT does not use W09; therefore no synthetic ETF
    TopDown values are fabricated in the Daily bootstrap.
    """
    seed = load_seed(path)
    country_macro = dict(seed.get("country_macro_by_country_yf") or {})
    country_news = dict(seed.get("country_news_by_country_yf") or {})
    sector_news = dict(seed.get("sector_news_by_sector_yf") or {})
    instrument_news = dict(seed.get("instrument_news_by_isin") or {})
    as_of = str(seed["as_of"])

    rows: list[dict] = []
    counts = {
        "global_macro": 0,
        "market_sentiment": 0,
        "sentiment_regime": 0,
        "country_macro": 0,
        "country_news": 0,
        "sector_news": 0,
        "instrument_news": 0,
    }
    for _, row in actions_df.iterrows():
        isin = _clean_text(row.get("isin"))
        if not isin:
            continue
        global_macro = seed.get("funnel_global_macro_score")
        market_sentiment = seed.get("funnel_market_sentiment_score")
        sentiment_regime = seed.get("sentiment_regime_score")
        if global_macro is not None:
            rows.append(_seed_obs("ACTION", isin, "funnel_global_macro_score", global_macro, "FRED", "B", as_of))
            counts["global_macro"] += 1
        if market_sentiment is not None:
            rows.append(_seed_obs("ACTION", isin, "funnel_market_sentiment_score", market_sentiment, "INTERNAL_PIT_BREADTH_MOMENTUM", "C", as_of))
            counts["market_sentiment"] += 1
        if sentiment_regime is not None:
            rows.append(_seed_obs("ACTION", isin, "sentiment_regime_score", sentiment_regime, "INTERNAL_PIT_BREADTH_MOMENTUM", "C", as_of))
            counts["sentiment_regime"] += 1

        country = _clean_text(row.get("country_yf"))
        if country in country_macro:
            rows.append(_seed_obs("ACTION", isin, "funnel_country_macro_score", country_macro[country], "FRED", "B", as_of))
            counts["country_macro"] += 1
        if country in country_news:
            rows.append(_seed_obs("ACTION", isin, "funnel_country_news_score", country_news[country], "GDELT", "B", as_of))
            counts["country_news"] += 1

        sector = _clean_text(row.get("sector_yf"))
        if sector in sector_news:
            rows.append(_seed_obs("ACTION", isin, "funnel_sector_news_score", sector_news[sector], "GDELT", "B", as_of))
            counts["sector_news"] += 1

        if isin in instrument_news:
            rows.append(_seed_obs("ACTION", isin, "funnel_instrument_news_score", instrument_news[isin], "GDELT", "B", as_of))
            counts["instrument_news"] += 1

    return rows, {
        "version": VERSION,
        "status": "SUCCESS_SEED_REHYDRATION",
        "seed_version": seed.get("version"),
        "seed_as_of": as_of,
        "source_run_id": seed.get("source_run_id"),
        "rows": len(actions_df),
        "observations": len(rows),
        "counts": counts,
        "network_calls": 0,
        "fred_calls": 0,
        "gdelt_calls": 0,
        "daily_only": True,
        "weekly_wave09_execution_changed": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }


def audit_contract(path: Path = SEED_PATH) -> dict:
    seed = load_seed(path)
    return {
        "version": VERSION,
        "seed_version": seed.get("version"),
        "seed_as_of": seed.get("as_of"),
        "source_run_id": seed.get("source_run_id"),
        "network_calls": 0,
        "weekly_wave09_execution_changed": False,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }