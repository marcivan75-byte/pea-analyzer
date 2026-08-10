from __future__ import annotations

from datetime import date, datetime
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
DISCOVERY_SOURCE = "ESEF_DISCOVERY"
DISCOVERY_FIELD = "esef_discovery_status"
NEGATIVE_CACHE_DAYS = 30
SCHEMA_SOURCE = "ESEF_SCHEMA"
SCHEMA_FIELD = "esef_capture_schema"
SCHEMA_VERSION = "V2"
SCHEMA_STATUS = "SCHEMA_V2_COMPLETE"

CONCEPTS = {
    "revenue_esef": [
        "Revenue", "RevenueFromContractsWithCustomers", "RevenueFromContractsWithCustomersExcludingAssessedTax",
        "SalesRevenue", "SalesRevenueGoods", "InterestRevenueExpenseNet"
    ],
    "net_income_esef": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "NetIncomeLoss"],
    "operating_income_esef": ["ProfitLossFromOperatingActivities", "OperatingProfitLoss", "OperatingIncomeLoss"],
    "assets_esef": ["Assets"],
    "current_assets_esef": ["CurrentAssets"],
    "equity_esef": ["Equity", "EquityAttributableToOwnersOfParent", "StockholdersEquity"],
    "liabilities_esef": ["Liabilities"],
    "current_liabilities_esef": ["CurrentLiabilities"],
    "cash_esef": ["CashAndCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"],
    "cfo_esef": ["CashFlowsFromUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivities"],
    "capex_esef": [
        "PurchaseOfPropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
    ],
    "borrowings_current_esef": ["CurrentBorrowings", "ShorttermBorrowings", "ShortTermBorrowings"],
    "borrowings_noncurrent_esef": ["NoncurrentBorrowings", "LongtermBorrowings", "LongTermDebt"],
    "ebitda_esef": ["EarningsBeforeInterestTaxesDepreciationAndAmortisation", "EBITDA"],
    "da_combined_esef": [
        "DepreciationAndAmortisationExpense", "DepreciationAndAmortizationExpense",
        "DepreciationDepletionAndAmortization"
    ],
    "gross_profit_esef": ["GrossProfit"],
    "cost_of_sales_esef": ["CostOfSales", "CostOfRevenue", "CostOfGoodsSold"],
    "interest_expense_esef": ["InterestExpense", "InterestExpenseNonOperating", "InterestAndDebtExpense"],
    "finance_costs_esef": ["FinanceCosts"],
    "eps_basic_esef": ["BasicEarningsLossPerShare", "EarningsPerShareBasic"],
    "eps_diluted_esef": ["DilutedEarningsLossPerShare", "EarningsPerShareDiluted"],
}


def _links(session: requests.Session, url: str) -> list[str]:
    r = session.get(url, timeout=30, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    if r.status_code == 404:
        return []
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)]


def _period_end(period: object) -> str:
    s = clean_text(period)
    if "/" in s:
        s = s.split("/")[-1]
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", s)
    return m.group(1) if m else ""


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
        field = aliases.get(_concept_local(dims.get("concept") or fact.get("concept")).lower())
        if not field:
            continue
        val = number(fact.get("value"))
        if val is None:
            continue
        raw_period = _period_end(dims.get("period") or fact.get("period")) or report_period
        fact_ts = pd.Timestamp(raw_period) if raw_period else pd.NaT
        normalized_period = report_period
        if pd.notna(fact_ts) and pd.notna(report_ts) and abs((fact_ts - report_ts).days) > 2:
            normalized_period = fact_ts.date().isoformat()
        elif raw_period and not report_period:
            normalized_period = raw_period
        extra_dims = sum(1 for k in dims if k not in {"concept", "entity", "period", "unit", "language"})
        unit = clean_text(dims.get("unit") or fact.get("unit")).lower()
        unit_penalty = 0 if ("eur" in unit or not unit) else 1
        candidates.setdefault((field, normalized_period), []).append((extra_dims * 10 + unit_penalty, val))
    out = []
    for (field, period), vals in candidates.items():
        vals.sort(key=lambda x: x[0])
        out.append((field, vals[0][1], period))
    return out


def _latest_json_url(lei: str, session: requests.Session) -> tuple[str, str, str] | None:
    root = urljoin(BASE, f"{lei}/")
    periods: list[tuple[date, str]] = []
    for href in _links(session, root):
        m = re.search(r"/(20\d{2}-\d{2}-\d{2})/$", href)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if d <= date.today():
            periods.append((d, href))
    for period, period_url in sorted(periods, reverse=True)[:5]:
        esef_url = urljoin(period_url, "ESEF/")
        country_dirs = [u for u in _links(session, esef_url) if re.search(r"/[A-Z]{2}/$", u)]
        for country_url in sorted(country_dirs):
            report_dirs = [u for u in _links(session, country_url) if re.search(r"/\d+/$", u)]
            for report_url in sorted(report_dirs):
                files = _links(session, report_url)
                json_files = [u for u in files if u.lower().endswith(".json.gz") and "metadata.json.gz" not in u.lower()]
                if json_files:
                    filing_index = f"{lei}-{period.isoformat()}-{country_url.rstrip('/').split('/')[-1]}-{report_url.rstrip('/').split('/')[-1]}"
                    return sorted(json_files)[0], period.isoformat(), filing_index
    return None


def _load_json_response(r: requests.Response, url: str) -> dict:
    raw = r.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8-sig"))


def _recent_negative_isins(facts: pd.DataFrame) -> set[str]:
    if facts.empty:
        return set()
    d = facts[
        facts["source"].astype(str).eq(DISCOVERY_SOURCE)
        & facts["field"].astype(str).eq(DISCOVERY_FIELD)
        & facts["status"].astype(str).isin({"NO_FILING", "NO_CANONICAL_CONCEPTS"})
    ].copy()
    if d.empty:
        return set()
    d["_observed"] = pd.to_datetime(d["observed_at_utc"], errors="coerce", utc=True)
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=NEGATIVE_CACHE_DAYS)
    return set(d.loc[d["_observed"].ge(cutoff), "isin"].astype(str))


def _schema_completed_isins(facts: pd.DataFrame) -> set[str]:
    if facts.empty:
        return set()
    x = facts[
        facts["source"].astype(str).eq(SCHEMA_SOURCE)
        & facts["field"].astype(str).eq(SCHEMA_FIELD)
        & facts["status"].astype(str).eq(SCHEMA_STATUS)
        & facts["value_text"].astype(str).eq(SCHEMA_VERSION)
    ]
    return set(x["isin"].astype(str))


def _discovery_row(isin: str, status: str, evidence: str) -> dict:
    return {
        "isin": isin,
        "field": DISCOVERY_FIELD,
        "value": "",
        "value_text": status,
        "as_of": date.today().isoformat(),
        "source": DISCOVERY_SOURCE,
        "evidence": evidence,
        "confidence": 0.95,
        "status": status,
        "observed_at_utc": utcnow(),
    }


def _schema_row(isin: str, filing_index: str) -> dict:
    return {
        "isin": isin,
        "field": SCHEMA_FIELD,
        "value": "",
        "value_text": SCHEMA_VERSION,
        "as_of": date.today().isoformat(),
        "source": SCHEMA_SOURCE,
        "evidence": f"A_OFFICIAL_STRUCTURED|{filing_index}",
        "confidence": 1.0,
        "status": SCHEMA_STATUS,
        "observed_at_utc": utcnow(),
    }


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
    schema_completed = _schema_completed_isins(facts_old)
    negative_cached = _recent_negative_isins(facts_old)

    targets = []
    skipped_negative_cache = 0
    skipped_schema_complete = 0
    for _, row in universe.iterrows():
        isin = str(row["isin"])
        if isin not in lei_map:
            continue
        if isin in schema_completed:
            skipped_schema_complete += 1
            continue
        if isin in negative_cached:
            skipped_negative_cache += 1
            continue
        targets.append((row, lei_map[isin]))
        if len(targets) >= max_symbols:
            break

    session = requests.Session()
    rows: list[dict] = []
    discovery_rows: list[dict] = []
    schema_rows: list[dict] = []
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
                discovery_rows.append(_discovery_row(isin, "NO_FILING", "A_OFFICIAL_DIRECTORY_NO_ESEF_JSON"))
                if len(samples) < 5:
                    samples.append({"isin": isin, "lei": lei, "reason": "NO_FILING_CACHED_30D"})
                continue
            url, report_period, filing_index = found
            r = session.get(url, timeout=60, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
            r.raise_for_status()
            extracted = _extract(_load_json_response(r, url), report_period)
            if not extracted:
                extracted_zero += 1
                discovery_rows.append(_discovery_row(isin, "NO_CANONICAL_CONCEPTS", f"A_OFFICIAL_STRUCTURED|{filing_index}"))
                if len(samples) < 5:
                    samples.append({"isin": isin, "lei": lei, "filing": filing_index, "reason": "NO_CANONICAL_CONCEPTS_CACHED_30D"})
                continue
            filings += 1
            schema_rows.append(_schema_row(isin, filing_index))
            if len(samples) < 5:
                samples.append({"isin": isin, "lei": lei, "filing": filing_index, "facts": len(extracted), "schema": SCHEMA_VERSION})
            for field, value, period in extracted:
                rows.append({
                    "isin": isin,
                    "field": field,
                    "value": value,
                    "value_text": "",
                    "as_of": period,
                    "source": "ESEF_XBRL_JSON",
                    "evidence": f"A_OFFICIAL_STRUCTURED_{SCHEMA_VERSION}",
                    "confidence": 0.95,
                    "status": "OBSERVED",
                    "observed_at_utc": utcnow(),
                })
        except Exception as exc:
            errors += 1
            if len(samples) < 5:
                samples.append({"isin": isin, "lei": lei, "error": f"{type(exc).__name__}:{str(exc)[:160]}"})
        time.sleep(0.05)

    added = store.upsert_facts(rows)
    discovery_added = store.upsert_facts(discovery_rows)
    schema_added = store.upsert_facts(schema_rows)
    status = "OK" if filings else ("SCHEMA_V2_COMPLETE" if not targets and schema_completed else "NO_NEW_DATA")
    store.add_health(
        "ESEF_XBRL_JSON",
        status,
        len(targets),
        filings,
        no_filing + errors + extracted_zero,
        message=(
            f"schema={SCHEMA_VERSION}; facts_processed={added}; schema_markers={schema_added}; "
            f"discovery_markers={discovery_added}; no_filing={no_filing}; no_concepts={extracted_zero}; "
            f"errors={errors}; negative_cache_skipped={skipped_negative_cache}; "
            f"schema_complete_skipped={skipped_schema_complete}; samples={samples}"
        ),
    )
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "attempted": len(targets),
        "filings": filings,
        "facts_added": added,
        "schema_markers_added": schema_added,
        "discovery_markers_added": discovery_added,
        "no_filing": no_filing,
        "no_concepts": extracted_zero,
        "errors": errors,
        "negative_cache_skipped": skipped_negative_cache,
        "schema_complete_skipped": skipped_schema_complete,
        "negative_cache_days": NEGATIVE_CACHE_DAYS,
        "samples": samples,
    }
