from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import re
import time
from typing import Iterable

from bs4 import BeautifulSoup
import requests

SEC_FORMS_DISCOVERY = {"S-1", "F-1"}
SEC_FORMS_PROSPECTUS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4", "424B3"}
SEC_FORM_PRIORITY = {"424B4": 6, "424B3": 5, "S-1/A": 4, "F-1/A": 4, "S-1": 3, "F-1": 3}
TIER1_UNDERWRITERS = (
    "goldman sachs", "morgan stanley", "j.p. morgan", "jp morgan", "bofa securities", "bank of america securities",
    "citigroup", "barclays", "ubs securities", "deutsche bank securities",
)
TIER2_UNDERWRITERS = (
    "jefferies", "wells fargo securities", "rbc capital markets", "evercore isi", "piper sandler", "william blair",
    "stifel", "needham", "canaccord genuity", "raymond james",
)


def _norm_name(value: object) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"\b(INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|S A|SA|N V|NV|HOLDINGS?|GROUP)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


def _headers(user_agent: str, host: str | None = None) -> dict[str, str]:
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    if host:
        headers["Host"] = host
    return headers


def _quarter(value: date) -> int:
    return (value.month - 1) // 3 + 1


def _quarters(start: date, end: date) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    year, quarter = start.year, _quarter(start)
    while (year, quarter) <= (end.year, _quarter(end)):
        result.append((year, quarter))
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return result


def parse_form_index(text: str, forms: set[str] | None = None) -> list[dict]:
    wanted = forms or SEC_FORMS_DISCOVERY
    rows: list[dict] = []
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        form, company, cik, filed, filename = parts[0], parts[1], parts[-3], parts[-2], parts[-1]
        if form in wanted and cik.isdigit() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed):
            rows.append({"form": form, "company": company.strip(), "cik": cik, "filed": filed, "filename": filename})
    return rows


def collect_recent_registrations(start: date, end: date, user_agent: str, timeout: int = 20) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    errors: list[str] = []
    for year, quarter in _quarters(start, end):
        try:
            response = requests.get(
                f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx",
                headers=_headers(user_agent, "www.sec.gov"), timeout=timeout,
            )
            response.raise_for_status()
            rows.extend(parse_form_index(response.text))
        except Exception as exc:
            errors.append(f"QTR{quarter}-{year}:{type(exc).__name__}")
        time.sleep(0.12)
    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        filed = date.fromisoformat(row["filed"])
        if not start <= filed <= end:
            continue
        key = (row["cik"], row["form"])
        if key not in dedup or row["filed"] > dedup[key]["filed"]:
            dedup[key] = row
    output = sorted(dedup.values(), key=lambda row: row["filed"], reverse=True)
    listed_ciks, listed_status = collect_listed_ciks(user_agent, timeout)
    before_filter = len(output)
    if listed_ciks:
        output = [row for row in output if str(int(row["cik"])) not in listed_ciks]
    status = "SUCCESS" if not errors and listed_status["status"] == "SUCCESS" else "PARTIAL" if output else "FAILED"
    detail_parts = errors + [f"listed_filter={listed_status['status']}", f"filtered_listed={before_filter-len(output)}"]
    return output, {"source": "SEC_EDGAR", "status": status, "count": len(output), "detail": "|".join(detail_parts)[:300]}


def collect_listed_ciks(user_agent: str, timeout: int = 20) -> tuple[set[str], dict]:
    try:
        response = requests.get(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            headers=_headers(user_agent, "www.sec.gov"), timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        fields, data = payload.get("fields") or [], payload.get("data") or []
        cik_index, exchange_index = fields.index("cik"), fields.index("exchange")
        listed = {str(int(row[cik_index])) for row in data if len(row) > exchange_index and str(row[exchange_index] or "").strip()}
        return listed, {"source": "SEC_LISTED_CIK", "status": "SUCCESS", "count": len(listed)}
    except Exception as exc:
        return set(), {"source": "SEC_LISTED_CIK", "status": "FAILED", "count": 0, "detail": f"{type(exc).__name__}: {str(exc)[:180]}"}


def registration_candidates(rows: Iterable[dict], listed_ciks: set[str] | None = None) -> list[dict]:
    listed = listed_ciks or set()
    result: list[dict] = []
    for row in rows:
        cik = str(int(row["cik"]))
        if cik in listed:
            continue
        result.append({
            "candidate_id": f"SEC:{cik}", "identity_key": f"CIK:{cik}", "name": row["company"], "symbol": "",
            "exchange": "SEC_PRIVATE", "expected_date": "", "status": "filed", "price_range": "", "price_low": None,
            "price_high": None, "price_mid": None, "number_of_shares": None, "offer_value": None, "issuer_country": "",
            "sources": "SEC_EDGAR", "source_count": 1, "sec_cik": cik, "sec_initial_form": row["form"],
            "sec_initial_filing_date": row["filed"],
        })
    return result


def match_registration(candidate: dict, registrations: list[dict]) -> dict | None:
    cik = str(candidate.get("sec_cik") or "").strip()
    if cik:
        normalized = str(int(cik))
        matches = [row for row in registrations if str(int(row["cik"])) == normalized]
        return max(matches, key=lambda row: row["filed"]) if matches else None
    target = _norm_name(candidate.get("name"))
    if not target:
        return None
    exact = [row for row in registrations if _norm_name(row.get("company")) == target]
    if exact:
        return max(exact, key=lambda row: row["filed"])
    scored = [(SequenceMatcher(None, target, _norm_name(row.get("company"))).ratio(), row) for row in registrations]
    scored = [item for item in scored if item[0] >= 0.94]
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]["filed"]), reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.015 and scored[0][1]["cik"] != scored[1][1]["cik"]:
        return None
    return scored[0][1]


def _recent_filing(submissions: dict) -> dict | None:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms, dates = recent.get("form") or [], recent.get("filingDate") or []
    accessions, docs = recent.get("accessionNumber") or [], recent.get("primaryDocument") or []
    candidates = [
        {"form": form, "filing_date": dates[idx], "accession": accessions[idx], "primary_document": docs[idx]}
        for idx, form in enumerate(forms)
        if form in SEC_FORMS_PROSPECTUS and idx < len(dates) and idx < len(accessions) and idx < len(docs)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row["filing_date"], SEC_FORM_PRIORITY.get(row["form"], 0)), reverse=True)
    return candidates[0]


def _filing_url(cik: str, accession: str, primary_document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_document}"


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _snippet(text: str, anchor: str, length: int = 10000) -> str:
    index = text.lower().find(anchor.lower())
    return "" if index < 0 else text[index:index + length]


def _lockup_days(text: str) -> int | None:
    lower = text.lower()
    for match in re.finditer(r"lock[- ]?up", lower):
        values = [int(value) for value in re.findall(r"\b(\d{2,3})\s+days?\b", lower[match.start():match.start() + 1800])]
        plausible = [value for value in values if 30 <= value <= 730]
        if plausible:
            return max(plausible)
    return None


def _underwriter_score(text: str) -> tuple[float | None, list[str]]:
    lower = text.lower()
    tier1 = [name for name in TIER1_UNDERWRITERS if name in lower]
    tier2 = [name for name in TIER2_UNDERWRITERS if name in lower]
    if tier1:
        return 90.0, tier1[:5]
    if tier2:
        return 75.0, tier2[:5]
    return (55.0, []) if "underwriter" in lower or "underwriting" in lower else (None, [])


def _use_of_proceeds_score(text: str) -> float | None:
    section = _snippet(text, "use of proceeds").lower()
    if not section:
        return None
    score = 55.0
    if any(term in section for term in ("research and development", "capital expenditures", "expand", "growth initiatives", "acquisitions")):
        score += 15.0
    if any(term in section for term in ("repay indebtedness", "repayment of debt", "repay debt")):
        score -= 15.0
    if "general corporate purposes" in section and not any(term in section for term in ("research and development", "capital expenditures", "acquisitions")):
        score -= 5.0
    return max(20.0, min(85.0, score))


def prospectus_text_scores(text: str) -> dict:
    lower = text.lower()
    going_concern = any(term in lower for term in (
        "substantial doubt about our ability to continue as a going concern",
        "substantial doubt about the company's ability to continue as a going concern",
    ))
    material_weakness = "material weakness" in lower
    dual_class = any(term in lower for term in ("dual-class", "dual class", "super-voting", "super voting"))
    customer_concentration = "customer concentration" in lower or bool(re.search(r"customer.{0,120}accounted for.{0,80}\b\d{1,3}(?:\.\d+)?%", lower))
    regulatory_material = any(term in lower for term in ("government investigation", "regulatory investigation", "material litigation", "criminal investigation"))
    selling_secondary = any(term in lower for term in ("selling stockholders", "selling shareholders")) and any(term in lower for term in ("will not receive any proceeds", "we will not receive proceeds"))
    lockup_days = _lockup_days(text)
    underwriter_score, underwriters = _underwriter_score(text)
    return {
        "opportunity_use_of_proceeds_quality": _use_of_proceeds_score(text),
        "opportunity_underwriter_quality": underwriter_score,
        "opportunity_insider_alignment": None if lockup_days is None else 82.0 if lockup_days >= 180 else 65.0 if lockup_days >= 90 else 40.0,
        "risk_governance_dual_class": 82.0 if dual_class else 22.0,
        "risk_lockup_overhang": None if lockup_days is None else 38.0 if lockup_days >= 180 else 65.0 if lockup_days >= 90 else 82.0,
        "risk_customer_concentration": 78.0 if customer_concentration else 25.0,
        "risk_regulatory_legal": 78.0 if regulatory_material else 25.0,
        "risk_accounting_controls": 85.0 if material_weakness else 22.0,
        "risk_dilution_secondary": 80.0 if selling_secondary else 45.0 if "dilution" in lower else None,
        "hard_flags": "going_concern" if going_concern else "",
        "sec_lockup_days": lockup_days, "sec_underwriters_detected": "|".join(underwriters),
        "sec_flag_going_concern": going_concern, "sec_flag_material_weakness": material_weakness,
        "sec_flag_dual_class": dual_class, "sec_flag_customer_concentration": customer_concentration,
        "sec_flag_regulatory_material": regulatory_material, "sec_flag_secondary_selling": selling_secondary,
    }


def _concept_units(companyfacts: dict, concepts: tuple[str, ...]) -> list[dict]:
    facts = companyfacts.get("facts") or {}
    for taxonomy in ("us-gaap", "ifrs-full"):
        nodes = facts.get(taxonomy) or {}
        for concept in concepts:
            node = nodes.get(concept)
            if not node:
                continue
            units = node.get("units") or {}
            for unit in ("USD", "EUR", "GBP", "JPY", "CNY"):
                values = units.get(unit) or []
                if values:
                    return values
    return []


def _annual_values(companyfacts: dict, concepts: tuple[str, ...]) -> list[dict]:
    selected: dict[str, dict] = {}
    for item in _concept_units(companyfacts, concepts):
        value, end = item.get("val"), item.get("end")
        if not isinstance(value, (int, float)) or not end:
            continue
        annual = item.get("fp") == "FY"
        start = item.get("start")
        if start:
            try:
                span = (date.fromisoformat(end) - date.fromisoformat(start)).days
            except ValueError:
                continue
            annual = annual or 250 <= span <= 460
        if not annual:
            continue
        prior = selected.get(end)
        if prior is None or str(item.get("filed", "")) > str(prior.get("filed", "")):
            selected[end] = item
    return sorted(selected.values(), key=lambda item: item["end"])


def _latest_instant(companyfacts: dict, concepts: tuple[str, ...]) -> float | None:
    values = [item for item in _concept_units(companyfacts, concepts) if isinstance(item.get("val"), (int, float)) and item.get("end")]
    if not values:
        return None
    values.sort(key=lambda item: (item.get("end", ""), item.get("filed", "")))
    return float(values[-1]["val"])


def _growth_score(growth_pct: float) -> float:
    if growth_pct < -10:
        return 15.0
    if growth_pct < 0:
        return 30.0
    if growth_pct < 10:
        return 50.0
    if growth_pct < 20:
        return 65.0
    if growth_pct < 40:
        return 80.0
    if growth_pct < 70:
        return 92.0
    return 100.0


def financial_scores(companyfacts: dict) -> dict:
    revenue = _annual_values(companyfacts, ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues", "Revenue"))
    gross = _annual_values(companyfacts, ("GrossProfit",))
    operating = _annual_values(companyfacts, ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"))
    net_income = _annual_values(companyfacts, ("NetIncomeLoss", "ProfitLoss"))
    operating_cf = _annual_values(companyfacts, ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"))
    cash = _latest_instant(companyfacts, ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"))
    assets = _latest_instant(companyfacts, ("Assets",))
    liabilities = _latest_instant(companyfacts, ("Liabilities",))
    result: dict[str, float | None] = {
        "opportunity_revenue_growth": None, "opportunity_gross_margin_quality": None, "opportunity_operating_leverage": None,
        "opportunity_balance_sheet_post_ipo": None, "risk_loss_cash_burn": None, "sec_revenue_growth_pct": None,
        "sec_latest_revenue": float(revenue[-1]["val"]) if revenue else None, "sec_latest_gross_margin_pct": None,
        "sec_cash": cash, "sec_assets": assets, "sec_liabilities": liabilities, "sec_cash_runway_years_pre_ipo": None,
    }
    if len(revenue) >= 2 and revenue[-2]["val"]:
        growth = round((float(revenue[-1]["val"]) / float(revenue[-2]["val"]) - 1.0) * 100.0, 6)
        result["sec_revenue_growth_pct"], result["opportunity_revenue_growth"] = round(growth, 2), _growth_score(growth)
    if revenue and gross and revenue[-1]["val"]:
        margin = float(gross[-1]["val"]) / float(revenue[-1]["val"]) * 100.0
        result["sec_latest_gross_margin_pct"] = round(margin, 2)
        result["opportunity_gross_margin_quality"] = max(10.0, min(95.0, 30.0 + margin))
    if len(operating) >= 2 and len(revenue) >= 2 and revenue[-1]["val"] and revenue[-2]["val"]:
        latest_margin = float(operating[-1]["val"]) / float(revenue[-1]["val"]) * 100.0
        prior_margin = float(operating[-2]["val"]) / float(revenue[-2]["val"]) * 100.0
        result["opportunity_operating_leverage"] = max(10.0, min(95.0, 55.0 + (latest_margin - prior_margin) * 2.0 + (15.0 if latest_margin > 0 else 0.0)))
    if assets and assets > 0 and liabilities is not None:
        cash_ratio = 0.0 if cash is None else cash / assets
        liability_ratio = liabilities / assets
        result["opportunity_balance_sheet_post_ipo"] = max(15.0, min(90.0, 70.0 + min(15.0, cash_ratio * 50.0) - max(0.0, (liability_ratio - 0.65) * 80.0)))
    latest_loss = bool(net_income and float(net_income[-1]["val"]) < 0)
    latest_ocf = float(operating_cf[-1]["val"]) if operating_cf else None
    if latest_ocf is not None:
        if latest_ocf < 0 and cash is not None and abs(latest_ocf) > 0:
            runway = cash / abs(latest_ocf)
            result["sec_cash_runway_years_pre_ipo"] = round(runway, 2)
            risk = 92.0 if runway < 0.75 else 80.0 if runway < 1.25 else 65.0 if runway < 2 else 50.0 if runway < 3 else 35.0
            result["risk_loss_cash_burn"] = min(100.0, risk + (5.0 if latest_loss else 0.0))
        elif latest_ocf >= 0 and not latest_loss:
            result["risk_loss_cash_burn"] = 15.0
        else:
            result["risk_loss_cash_burn"] = 35.0
    return result


def enrich_candidate(candidate: dict, registration: dict, user_agent: str, timeout: int = 20) -> tuple[dict, dict]:
    cik = str(int(registration["cik"]))
    output = dict(candidate)
    output["sec_cik"] = cik
    status = {"candidate_id": candidate.get("candidate_id"), "cik": cik, "status": "FAILED"}
    try:
        response = requests.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers=_headers(user_agent), timeout=timeout)
        response.raise_for_status()
        submissions = response.json()
        output["sec_sic"] = submissions.get("sic")
        output["sec_sic_description"] = submissions.get("sicDescription")
        output["sec_state_of_incorporation"] = submissions.get("stateOfIncorporation")
        filing = _recent_filing(submissions)
        if not filing:
            status.update({"status": "NO_PROSPECTUS", "detail": "No current S-1/F-1/424B prospectus"})
            return output, status
        url = _filing_url(cik, filing["accession"], filing["primary_document"])
        filing_response = requests.get(url, headers=_headers(user_agent, "www.sec.gov"), timeout=timeout)
        filing_response.raise_for_status()
        text = _clean_html(filing_response.text)
        output.update(prospectus_text_scores(text))
        output.update({"sec_form": filing["form"], "sec_filing_date": filing["filing_date"], "sec_accession": filing["accession"], "sec_prospectus_url": url, "sec_prospectus_chars": len(text), "sec_analysis_status": "PROSPECTUS_PARSED"})
        time.sleep(0.12)
        facts_response = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json", headers=_headers(user_agent), timeout=timeout)
        if facts_response.ok:
            output.update(financial_scores(facts_response.json()))
            output["sec_companyfacts_status"] = "SUCCESS"
        else:
            output["sec_companyfacts_status"] = f"HTTP_{facts_response.status_code}"
        status.update({"status": "SUCCESS", "form": filing["form"], "filing_date": filing["filing_date"], "url": url})
        return output, status
    except Exception as exc:
        status.update({"status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:220]}"})
        return output, status
