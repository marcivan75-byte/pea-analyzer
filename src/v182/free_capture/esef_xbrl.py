from __future__ import annotations

from datetime import date
import gzip
import json
import os
import re
import time
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .core import CaptureStore, clean_text, number, utcnow

BASE = "https://filings.xbrl.org/"
API = "https://filings.xbrl.org/api/filings"

CONCEPTS = {
    "revenue_esef": [
        "Revenue", "RevenueFromContractsWithCustomers", "RevenueFromContractsWithCustomersExcludingAssessedTax",
        "SalesRevenue", "SalesRevenueGoods", "InterestRevenueExpenseNet"
    ],
    "net_income_esef": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "NetIncomeLoss"],
    "operating_income_esef": ["ProfitLossFromOperatingActivities", "OperatingProfitLoss", "OperatingIncomeLoss"],
    "assets_esef": ["Assets"],
    "equity_esef": ["Equity", "EquityAttributableToOwnersOfParent", "StockholdersEquity"],
    "liabilities_esef": ["Liabilities"],
    "cash_esef": ["CashAndCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"],
    "cfo_esef": ["CashFlowsFromUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivities"],
    "capex_esef": [
        "PurchaseOfPropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
    ],
    "borrowings_current_esef": ["CurrentBorrowings", "ShorttermBorrowings", "ShortTermBorrowings"],
    "borrowings_noncurrent_esef": ["NoncurrentBorrowings", "LongtermBorrowings", "LongTermDebt"],
    "ebitda_esef": ["EarningsBeforeInterestTaxesDepreciationAndAmortisation", "EBITDA"],
    "eps_basic_esef": ["BasicEarningsLossPerShare", "EarningsPerShareBasic"],
    "eps_diluted_esef": ["DilutedEarningsLossPerShare", "EarningsPerShareDiluted"],
}


def _period_end(period: object) -> str:
    s = clean_text(period)
    if "/" in s:
        s = s.split("/")[-1]
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", s)
    if not m:
        return ""
    # xBRL-JSON duration periods use an exclusive end date. For annual concepts this is commonly
    # one day after the reporting date; retain the API report date separately for selection.
    return m.group(1)


def _concept_local(concept: object) -> str:
    s = clean_text(concept)
    return s.split(":")[-1].split("}")[-1]


def _fact_candidates(payload: dict) -> list[dict]:
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if isinstance(facts, dict):
        return [v for v in facts.values() if isinstance(v, dict)]
    if isinstance(facts, list):
        return [v for v in facts if isinstance(v, dict)]
    return []


def _extract(payload: dict, report_period: str) -> list[tuple[str, float, str]]:
    aliases = {alias.lower(): field for field, xs in CONCEPTS.items() for alias in xs}
    candidates: dict[tuple[str, str], list[tuple[int, float]]] = {}
    report_ts = pd.Timestamp(report_period) if report_period else pd.NaT
    for fact in _fact_candidates(payload):
        dims = fact.get("dimensions") or {}
        concept = dims.get("concept") or fact.get("concept")
        local = _concept_local(concept)
        field = aliases.get(local.lower())
        if not field:
            continue
        val = number(fact.get("value"))
        if val is None:
            continue
        raw_period = _period_end(dims.get("period") or fact.get("period")) or report_period
        fact_ts = pd.Timestamp(raw_period) if raw_period else pd.NaT
        # xBRL duration facts often end at T+1 because the interval end is exclusive.
        normalized_period = report_period
        if pd.notna(fact_ts) and pd.notna(report_ts):
            if abs((fact_ts - report_ts).days) > 2:
                normalized_period = fact_ts.date().isoformat()
        elif raw_period:
            normalized_period = raw_period
        extra_dims = sum(1 for k in dims if k not in {"concept", "entity", "period", "unit", "language"})
        unit = clean_text(dims.get("unit") or fact.get("unit")).lower()
        unit_penalty = 0 if ("eur" in unit or not unit) else 1
        score = extra_dims * 10 + unit_penalty
        candidates.setdefault((field, normalized_period), []).append((score, val))
    out = []
    for (field, period), vals in candidates.items():
        vals.sort(key=lambda x: x[0])
        out.append((field, vals[0][1], period))
    return out


def _json_from_filing_page(session: requests.Session, filing_index: str) -> str:
    if not filing_index:
        return ""
    url = urljoin(BASE, f"filing/{filing_index}")
    r = session.get(url, timeout=25, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    if not r.ok:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        href = urljoin(url, a["href"])
        if text == "json" or href.lower().endswith((".json", ".json.gz")):
            return href
    return ""


def _latest_json_url(lei: str, session: requests.Session) -> tuple[str, str, str] | None:
    """Resolve latest filing through public JSON:API, filtered directly by LEI."""
    params = {
        "filter[entity.identifier]": lei,
        "sort": "-added_time",
        "page[size]": 10,
        "include": "entity",
    }
    r = session.get(API, params=params, timeout=30, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        return None
    today = date.today().isoformat()
    candidates = []
    for item in data:
        attrs = item.get("attributes") or {}
        report_period = clean_text(attrs.get("last_end_date") or attrs.get("reporting_date"))[:10]
        if report_period and report_period > today:
            continue
        index = clean_text(attrs.get("filing_index"))
        json_url = clean_text(attrs.get("json_url"))
        if json_url and not json_url.startswith("http"):
            json_url = urljoin(BASE, json_url)
        if not json_url:
            json_url = _json_from_filing_page(session, index)
        if json_url:
            candidates.append((report_period or "0000-00-00", json_url, index))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1], candidates[0][0], candidates[0][2]


def _load_json_response(r: requests.Response, url: str) -> dict:
    raw = r.content
    if url.lower().endswith(".gz") or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8-sig"))


def capture(universe: pd.DataFrame, store: CaptureStore, max_symbols: int = 80) -> dict:
    identity = store.identity()
    if identity.empty:
        store.add_health("ESEF_XBRL_JSON", "NO_LEI_INPUT")
        return {"status": "NO_LEI_INPUT", "attempted": 0, "filings": 0, "facts_added": 0}
    lei_rows = identity[identity["lei"].astype(str).str.len().eq(20)].copy()
    if lei_rows.empty:
        store.add_health("ESEF_XBRL_JSON", "NO_LEI_INPUT")
        return {"status": "NO_LEI_INPUT", "attempted": 0, "filings": 0, "facts_added": 0}
    lei_map = lei_rows.drop_duplicates("isin", keep="last").set_index("isin")["lei"].astype(str).to_dict()

    facts_old = store.facts()
    completed = set()
    if not facts_old.empty:
        e = facts_old[facts_old["source"].eq("ESEF_XBRL_JSON")]
        completed = set(e["isin"].astype(str))

    targets = []
    for _, row in universe.iterrows():
        isin = str(row["isin"])
        if isin in completed or isin not in lei_map:
            continue
        targets.append((row, lei_map[isin]))
        if len(targets) >= max_symbols:
            break

    session = requests.Session()
    rows: list[dict] = []
    filings = 0
    no_filing = 0
    errors = 0
    extracted_zero = 0
    samples: list[dict] = []
    for row, lei in targets:
        isin = str(row["isin"])
        try:
            found = _latest_json_url(lei, session)
            if not found:
                no_filing += 1
                continue
            url, report_period, filing_index = found
            r = session.get(url, timeout=60, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
            r.raise_for_status()
            payload = _load_json_response(r, url)
            extracted = _extract(payload, report_period)
            if not extracted:
                extracted_zero += 1
                if len(samples) < 5:
                    samples.append({"isin": isin, "lei": lei, "filing": filing_index, "reason": "NO_CANONICAL_CONCEPTS"})
                continue
            filings += 1
            if len(samples) < 5:
                samples.append({"isin": isin, "lei": lei, "filing": filing_index, "facts": len(extracted)})
            for field, value, period in extracted:
                rows.append({
                    "isin": isin, "field": field, "value": value, "value_text": "", "as_of": period,
                    "source": "ESEF_XBRL_JSON", "evidence": "A_OFFICIAL_STRUCTURED",
                    "confidence": 0.95, "status": "OBSERVED", "observed_at_utc": utcnow(),
                })
        except Exception as exc:
            errors += 1
            if len(samples) < 5:
                samples.append({"isin": isin, "lei": lei, "error": f"{type(exc).__name__}:{str(exc)[:120]}"})
        time.sleep(0.10)

    added = store.upsert_facts(rows)
    status = "OK" if filings else "NO_NEW_DATA"
    store.add_health(
        "ESEF_XBRL_JSON", status, len(targets), filings, no_filing + errors + extracted_zero,
        message=f"facts_added={added}; no_filing={no_filing}; no_concepts={extracted_zero}; errors={errors}; samples={samples}"
    )
    return {"status": status, "attempted": len(targets), "filings": filings, "facts_added": added,
            "no_filing": no_filing, "no_concepts": extracted_zero, "errors": errors, "samples": samples}
