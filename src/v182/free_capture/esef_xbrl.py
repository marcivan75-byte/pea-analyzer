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


def _links(url: str, session: requests.Session) -> list[str]:
    r = session.get(url, timeout=30, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    return [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)]


def _latest_json_url(lei: str, country: str, session: requests.Session) -> tuple[str, str] | None:
    root = urljoin(BASE, f"{lei}/")
    periods = []
    for href in _links(root, session):
        m = re.search(r"/(20\d{2}-\d{2}-\d{2})/$", href)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                if d <= date.today():
                    periods.append((d, href))
            except ValueError:
                pass
    for period, period_url in sorted(periods, reverse=True)[:4]:
        esef_url = urljoin(period_url, "ESEF/")
        country_url = urljoin(esef_url, f"{country.upper()}/")
        report_dirs = [u for u in _links(country_url, session) if re.search(r"/\d+/$", u)]
        if not report_dirs:
            # Some entity-country relationships differ from ISIN prefix. Discover the country subdirectory.
            country_dirs = [u for u in _links(esef_url, session) if re.search(r"/[A-Z]{2}/$", u)]
            for cdir in country_dirs:
                report_dirs.extend([u for u in _links(cdir, session) if re.search(r"/\d+/$", u)])
        for report_url in sorted(set(report_dirs)):
            files = _links(report_url, session)
            json_files = [u for u in files if u.lower().endswith(".json.gz") and "metadata.json.gz" not in u.lower()]
            if json_files:
                return json_files[0], period.isoformat()
    return None


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
        period = _period_end(dims.get("period") or fact.get("period")) or report_period
        # Consolidated/group-level facts typically have fewer extra dimensions than segmented facts.
        extra_dims = sum(1 for k in dims if k not in {"concept", "entity", "period", "unit", "language"})
        score = extra_dims * 10 + (0 if period == report_period else 2)
        candidates.setdefault((field, period), []).append((score, val))
    out = []
    for (field, period), vals in candidates.items():
        vals.sort(key=lambda x: x[0])
        out.append((field, vals[0][1], period))
    return out


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
        country = clean_text(row.get("country")).upper() or isin[:2].upper()
        targets.append((row, lei_map[isin], country))
        if len(targets) >= max_symbols:
            break

    session = requests.Session()
    rows: list[dict] = []
    filings = 0
    no_filing = 0
    errors = 0
    for row, lei, country in targets:
        isin = str(row["isin"])
        try:
            found = _latest_json_url(lei, country, session)
            if not found:
                no_filing += 1
                continue
            url, report_period = found
            r = session.get(url, timeout=60, headers={"User-Agent": os.getenv("V182_USER_AGENT", "PEA-FreeCapture/1.0")})
            r.raise_for_status()
            raw = gzip.decompress(r.content)
            payload = json.loads(raw.decode("utf-8"))
            extracted = _extract(payload, report_period)
            if not extracted:
                no_filing += 1
                continue
            filings += 1
            for field, value, period in extracted:
                rows.append({
                    "isin": isin, "field": field, "value": value, "value_text": "", "as_of": period,
                    "source": "ESEF_XBRL_JSON", "evidence": "A_OFFICIAL_STRUCTURED",
                    "confidence": 0.95, "status": "OBSERVED", "observed_at_utc": utcnow(),
                })
        except Exception:
            errors += 1
        time.sleep(0.12)

    added = store.upsert_facts(rows)
    status = "OK" if filings else "NO_NEW_DATA"
    store.add_health(
        "ESEF_XBRL_JSON", status, len(targets), filings, no_filing + errors,
        message=f"facts_added={added}; no_filing={no_filing}; errors={errors}; xBRL-JSON from filings.xbrl.org"
    )
    return {"status": status, "attempted": len(targets), "filings": filings, "facts_added": added,
            "no_filing": no_filing, "errors": errors}
