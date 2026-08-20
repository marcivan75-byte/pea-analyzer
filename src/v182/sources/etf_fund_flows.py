from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; PEA-Analyzer/1.0; +https://github.com/)"
BLACKROCK_DATE_RE = re.compile(r"as of\s+(\d{1,2}/[A-Za-z]{3}/\d{4})", re.IGNORECASE)
CONFIDENCE_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, "QUARANTINE": 0}


def _nonempty(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"", "nan", "none", "<na>", "n/a", "na", "null"} else text


def _parse_number(text: str) -> float | None:
    cleaned = re.sub(r"[^0-9.\-]", "", str(text).replace(",", ""))
    try:
        value = float(cleaned)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def _parse_blackrock_date(text: str) -> str:
    match = BLACKROCK_DATE_RE.search(text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%b/%Y").date().isoformat()
        except ValueError:
            return datetime.now(timezone.utc).date().isoformat()
    return datetime.now(timezone.utc).date().isoformat()


def _canonical_economic_family(benchmark: str, category: str, geo: str, name: str) -> str:
    text = " ".join(filter(None, [benchmark, category, geo, name])).upper().replace("–", "-").replace("—", "-")
    rules = (
        (("MSCI ACWI", "ALL COUNTRY WORLD"), "ACWI"),
        (("MSCI WORLD",), "WORLD"),
        (("S&P 500", "SP 500", "S&P500"), "SP500"),
        (("NASDAQ-100", "NASDAQ 100", "NASDAQ100"), "NASDAQ100"),
        (("STOXX EUROPE 600",), "STOXX_EUROPE_600"),
        (("EURO STOXX 50",), "EURO_STOXX_50"),
        (("CAC 40", "CAC40"), "CAC40"),
        (("MSCI EMU",), "MSCI_EMU"),
        (("MSCI EUROPE",), "MSCI_EUROPE"),
        (("EMERGING MARKET", "MARCHES EMERGENTS", "MARCHÉS ÉMERGENTS"), "EMERGING"),
        (("JAPAN", "JAPON"), "JAPAN"),
    )
    for patterns, label in rules:
        if any(pattern in text for pattern in patterns):
            return label
    return benchmark or category or geo or name


def build_pea_flow_universe(master: pd.DataFrame) -> pd.DataFrame:
    if "isin" not in master.columns:
        raise ValueError("PEA_ETF_MASTER_MISSING_ISIN")
    rows: list[dict] = []
    sector_terms = {
        "technology": "TECHNOLOGY", "technologie": "TECHNOLOGY", "semiconductor": "SEMICONDUCTORS",
        "semi-conduct": "SEMICONDUCTORS", "health": "HEALTHCARE", "santé": "HEALTHCARE",
        "bank": "BANKS", "banque": "BANKS", "energy": "ENERGY", "énergie": "ENERGY",
        "defence": "DEFENSE", "defense": "DEFENSE", "aerospace": "DEFENSE", "utilities": "UTILITIES",
        "immobilier": "REAL_ESTATE", "real estate": "REAL_ESTATE", "industrial": "INDUSTRIALS",
        "consumer": "CONSUMER",
    }
    for _, row in master.iterrows():
        isin = _nonempty(row.get("isin"))
        if not isin:
            continue
        ticker = _nonempty(row.get("yahoo_ticker")) or _nonempty(row.get("ticker_yahoo_final")) or _nonempty(row.get("ticker_yahoo"))
        benchmark = _nonempty(row.get("official_benchmark"))
        category = _nonempty(row.get("category"))
        geo = _nonempty(row.get("geo_exposure"))
        name = _nonempty(row.get("name")) or isin
        replication = _nonempty(row.get("replication_hint")).upper()
        text = f"{name} {category}".lower()
        sector_theme = next((label for term, label in sector_terms.items() if term in text), "")
        rows.append({
            "instrument_id": f"ISIN:{isin}", "isin": isin, "ticker": ticker, "name": name,
            "universe": "PEA_ETF", "asset_class": "ETF",
            "economic_family": _canonical_economic_family(benchmark, category, geo, name) or isin,
            "region": geo or _nonempty(row.get("region_domicile")) or "EU",
            "sector_or_theme": sector_theme, "benchmark": benchmark, "provider": _nonempty(row.get("provider")),
            "currency": _nonempty(row.get("base_currency")) or _nonempty(row.get("currency")) or _nonempty(row.get("trading_currency")),
            "is_pea": True, "pea_status": _nonempty(row.get("pea_type")),
            "pea_status_confidence": _nonempty(row.get("pea_confidence")),
            "is_synthetic": "SYNTH" in replication or "SWAP" in replication,
            "is_inverse_or_leveraged": bool(re.search(r"\b(short|inverse|leveraged|2x|3x|-1x|-2x|-3x)\b", text)),
            "official_adapter": "", "official_url": _nonempty(row.get("source_url")),
        })
    return pd.DataFrame(rows)


def load_external_flow_universe(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    for col in ("is_pea", "is_synthetic", "is_inverse_or_leveraged"):
        if col not in frame.columns:
            frame[col] = False
        frame[col] = frame[col].astype(str).str.lower().isin({"1", "true", "yes", "y", "oui"})
    return frame


def _blackrock_official_snapshot(row: pd.Series, timeout_seconds: float = 20.0) -> tuple[dict | None, dict | None]:
    url = _nonempty(row.get("official_url"))
    if not url:
        return None, {"instrument_id": row.get("instrument_id"), "stage": "OFFICIAL", "reason": "MISSING_OFFICIAL_URL"}
    try:
        response = requests.get(url, timeout=timeout_seconds, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        return None, {"instrument_id": row.get("instrument_id"), "stage": "OFFICIAL", "reason": type(exc).__name__, "detail": str(exc)[:180]}
    text = " ".join(BeautifulSoup(response.text, "lxml").stripped_strings)
    patterns = {
        "aum": [
            r"(?:Net Assets of Fund|Series Value)\s+as of\s+\d{1,2}/[A-Za-z]{3}/\d{4}\s+(?:USD|EUR|GBP|CHF)\s*([0-9,]+(?:\.[0-9]+)?)",
            r"(?:Net Assets of Fund|Series Value).*?(?:USD|EUR|GBP|CHF)\s*([0-9,]+(?:\.[0-9]+)?)",
        ],
        "shares_outstanding": [
            r"(?:Shares Outstanding|Securities Outstanding)\s+as of\s+\d{1,2}/[A-Za-z]{3}/\d{4}\s+([0-9,]+(?:\.[0-9]+)?)",
            r"(?:Shares Outstanding|Securities Outstanding).*?([0-9][0-9,]+(?:\.[0-9]+)?)",
        ],
        "nav": [r"NAV as of\s+\d{1,2}/[A-Za-z]{3}/\d{4}\s+(?:USD|EUR|GBP|CHF)\s*([0-9,]+(?:\.[0-9]+)?)"],
    }
    values: dict[str, float] = {}
    for field, regexes in patterns.items():
        for regex in regexes:
            match = re.search(regex, text, flags=re.IGNORECASE)
            if match:
                parsed = _parse_number(match.group(1))
                if parsed is not None:
                    values[field] = parsed
                    break
    if "aum" not in values and "shares_outstanding" not in values:
        return None, {"instrument_id": row.get("instrument_id"), "stage": "OFFICIAL", "reason": "OFFICIAL_PAGE_FIELDS_NOT_PARSED", "url": url}
    currency_match = re.search(r"Base Currency\s+(USD|EUR|GBP|CHF)", text, flags=re.IGNORECASE)
    observation = {key: row.get(key, "") for key in row.index}
    observation.update({
        "as_of": _parse_blackrock_date(text), "aum": values.get("aum"), "nav": values.get("nav"),
        "shares_outstanding": values.get("shares_outstanding"), "market_price": np.nan,
        "distribution_per_share": 0.0,
        "currency": currency_match.group(1).upper() if currency_match else _nonempty(row.get("currency")),
        "source": "issuer_official", "source_type": "ISSUER_OFFICIAL", "source_url": url,
        "confidence": "A", "source_priority": 100,
    })
    return observation, None


def _yfinance_snapshot(row: pd.Series) -> tuple[dict | None, dict | None]:
    ticker = _nonempty(row.get("ticker"))
    if not ticker:
        return None, {"instrument_id": row.get("instrument_id"), "stage": "YFINANCE", "reason": "MISSING_TICKER"}
    try:
        import yfinance as yf
        instrument = yf.Ticker(ticker)
        info = instrument.info or {}
        history = instrument.history(period="5d", auto_adjust=False)
        price = None
        if not history.empty and "Close" in history.columns:
            closes = pd.to_numeric(history["Close"], errors="coerce").dropna()
            price = float(closes.iloc[-1]) if not closes.empty else None
        aum, nav, shares = info.get("totalAssets"), info.get("navPrice"), info.get("sharesOutstanding")
        if aum is None and nav is None and shares is None and price is None:
            return None, {"instrument_id": row.get("instrument_id"), "stage": "YFINANCE", "reason": "NO_FLOW_SNAPSHOT_FIELDS"}
        observation = {key: row.get(key, "") for key in row.index}
        observation.update({
            "as_of": datetime.now(timezone.utc).date().isoformat(), "aum": aum, "nav": nav,
            "shares_outstanding": shares, "market_price": price, "distribution_per_share": 0.0,
            "currency": info.get("currency") or info.get("financialCurrency") or _nonempty(row.get("currency")),
            "source": "yfinance.info/history", "source_type": "YFINANCE",
            "source_url": f"https://finance.yahoo.com/quote/{ticker}/", "confidence": "C", "source_priority": 50,
        })
        return observation, None
    except Exception as exc:
        return None, {"instrument_id": row.get("instrument_id"), "stage": "YFINANCE", "reason": type(exc).__name__, "detail": str(exc)[:180]}


def load_official_observations(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    frame = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    if frame.empty:
        return frame
    required = {"instrument_id", "as_of", "source", "source_url", "confidence"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"OFFICIAL_FLOW_INPUT_MISSING_COLUMNS:{','.join(sorted(missing))}")
    if "source_type" not in frame.columns:
        frame["source_type"] = "ISSUER_OFFICIAL"
    if "source_priority" not in frame.columns:
        frame["source_priority"] = 100
    frame["source_priority"] = pd.to_numeric(frame["source_priority"], errors="coerce").fillna(100)
    return frame


def _missing_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not _nonempty(value)
    missing = pd.isna(value)
    return bool(missing) if isinstance(missing, (bool, np.bool_)) else False


def _merge_same_day_observations(snapshot: pd.DataFrame) -> pd.DataFrame:
    fill_fields = ("aum", "nav", "shares_outstanding", "market_price", "distribution_per_share", "currency")
    rows: list[dict] = []
    for _, group in snapshot.groupby(["instrument_id", "as_of"], sort=False):
        ranked = group.sort_values(["source_priority", "_confidence_rank"], ascending=[False, False])
        merged = ranked.iloc[0].to_dict()
        used_confidences = [str(merged.get("confidence") or "D").upper()]
        components = [str(merged.get("source") or "")]
        for _, candidate in ranked.iloc[1:].iterrows():
            used = False
            for field in fill_fields:
                if _missing_value(merged.get(field)) and not _missing_value(candidate.get(field)):
                    merged[field] = candidate.get(field)
                    used = True
            if used:
                used_confidences.append(str(candidate.get("confidence") or "D").upper())
                components.append(str(candidate.get("source") or ""))
        merged["confidence"] = min(used_confidences, key=lambda grade: CONFIDENCE_RANK.get(grade, 0))
        merged["source_components"] = "|".join(dict.fromkeys(filter(None, components)))
        rows.append(merged)
    return pd.DataFrame(rows)


def collect_current_snapshot(
    universe: pd.DataFrame,
    official_input: pd.DataFrame | None = None,
    delay_seconds: float = 0.05,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    observations: list[dict] = []
    failures: list[dict] = []
    if official_input is not None and not official_input.empty:
        observations.extend(official_input.to_dict(orient="records"))
    for _, row in universe.iterrows():
        if _nonempty(row.get("official_adapter")).upper() == "BLACKROCK_HTML":
            official, failure = _blackrock_official_snapshot(row)
            if official is not None:
                observations.append(official)
            if failure is not None:
                failures.append(failure)
        yahoo, failure = _yfinance_snapshot(row)
        if yahoo is not None:
            observations.append(yahoo)
        if failure is not None:
            failures.append(failure)
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    snapshot = pd.DataFrame(observations)
    if snapshot.empty:
        return snapshot, pd.DataFrame(failures)
    snapshot["as_of"] = pd.to_datetime(snapshot["as_of"], errors="coerce", utc=True)
    snapshot = snapshot[snapshot["as_of"].notna()].copy()
    snapshot["as_of"] = snapshot["as_of"].dt.date.astype(str)
    snapshot["source_priority"] = pd.to_numeric(snapshot["source_priority"], errors="coerce").fillna(0)
    snapshot["_confidence_rank"] = snapshot["confidence"].astype(str).str.upper().map(CONFIDENCE_RANK).fillna(0)
    snapshot = _merge_same_day_observations(snapshot).drop(columns="_confidence_rank", errors="ignore")
    return snapshot.reset_index(drop=True), pd.DataFrame(failures)
