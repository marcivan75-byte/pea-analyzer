from __future__ import annotations

from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
import math
import re
import time
from typing import Iterable

from bs4 import BeautifulSoup
import requests

SEC_FORMS_DISCOVERY = {"S-1", "F-1"}
SEC_FORMS_PROSPECTUS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4", "424B3"}
SEC_FORM_PRIORITY = {"424B4": 6, "424B3": 5, "S-1/A": 4, "F-1/A": 4, "S-1": 3, "F-1": 3}

TIER1_UNDERWRITERS = (
    "goldman sachs", "morgan stanley", "j.p. morgan", "jp morgan", "bofa securities",
    "bank of america securities", "citigroup", "barclays", "ubs securities", "deutsche bank securities",
)
TIER2_UNDERWRITERS = (
    "jefferies", "wells fargo securities", "rbc capital markets", "evercore isi", "piper sandler",
    "william blair", "stifel", "needham", "canaccord genuity", "raymond james",
)


def _norm_name(value: object) -> str:
    text = "" if value is None else str(value).upper()
    text = re.sub(r"\b(INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|S A|SA|N V|NV|HOLDINGS?|GROUP)\b", " ", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


def _headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}


def _data_headers(user_agent: str) -> dict[str, str]:
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _quarter(value: date) -> int:
    return (value.month - 1) // 3 + 1


def _quarter_starts(start: date, end: date) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    cursor = date(start.year, ((_quarter(start) - 1) * 3) + 1, 1)
    while cursor <= end:
        pair = (cursor.year, _quarter(cursor))
        if pair not in pairs:
            pairs.append(pair)
        month = cursor.month + 3
        year = cursor.year
        if month > 12:
            month -= 12
            year += 1
        cursor = date(year, month, 1)
    return pairs


def parse_form_index(text: str, forms: set[str] | None = None) -> list[dict]:
    wanted = forms or SEC_FORMS_DISCOVERY
    rows: list[dict] = []
    for line in text.splitlines():
        if not line or line.startswith("Description:") or line.startswith("Last Data Received") or line.startswith("Comments:"):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        form, company, cik, filed, filename = parts[0], parts[1], parts[-3], parts[-2], parts[-1]
        if form not in wanted or not cik.isdigit() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", filed):
            continue
        rows.append({"form": form, "company": company.strip(), "cik": cik, "filed": filed, "filename": filename})
    return rows


def collect_recent_registrations(
    start: date,
    end: date,
    user_agent: str,
    timeout: int = 20,
) -> tuple[list[dict], dict]:
    source = "SEC_EDGAR"
    rows: list[dict] = []
    errors: list[str] = []
    for year, quarter in _quarter_starts(start, end):
        url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
        try:
            response = requests.get(url, headers=_headers(user_agent), timeout=timeout)
            response.raise_for_status()
            rows.extend(parse_form_index(response.text, SEC_FORMS_DISCOVERY))
        except Exception as exc:
            errors.append(f"QTR{quarter}-{year}:{type(exc).__name__}")
        time.sleep(0.12)
    dedup: dict[tuple[str, str], dict] = {}
    for row in rows:
        filed = date.fromisoformat(row["filed"])
        if not (start <= filed <= end):
            continue
        key = (row["cik"], row["form"])
        if key not in dedup or row["filed"] > dedup[key]["filed"]:
            dedup[key] = row
    out = sorted(dedup.values(), key=lambda row: row["filed"], reverse=True)
    status = "SUCCESS" if not errors else "PARTIAL" if out else "FAILED"
    return out, {"source": source, "status": status, "count": len(out), "detail": "|".join(errors)[:300]}


def registration_candidates(rows: Iterable[dict]) -> list[dict]:
    candidates: list[dict] = []
    for row in rows:
        cik = str(row["cik"])
        candidates.append(
            {
                "candidate_id": f"SEC:{cik}",
                "name": row["company"],
                "symbol": "",
                "exchange": "SEC_PRIVATE",
                "expected_date": "",
                "status": "filed",
                "price_range": "",
                "price_low": None,
                "price_high": None,
                "price_mid": None,
                "number_of_shares": None,
                "offer_value": None,
                "issuer_country": "",
                "sources": "SEC_EDGAR",
                "source_count": 1,
                "sec_cik": cik,
                "sec_initial_form": row["form"],
                "sec_initial_filing_date": row["filed"],
            }
        )
    return candidates


def match_registration(candidate: dict, registrations: list[dict]) -> dict | None:
    cik = str(candidate.get("sec_cik") or "").strip()
    if cik:
        matches = [row for row in registrations if str(row.get("cik")) == cik]
        return max(matches, key=lambda row: row["filed"]) if matches else None
    target = _norm_name(candidate.get("name"))
    if not target:
        return None
    exact = [row for row in registrations if _norm_name(row.get("company")) == target]
    if exact:
        return max(exact, key=lambda row: row["filed"])
    scored: list[tuple[float, dict]] = []
    for row in registrations:
        other = _norm_name(row.get("company"))
        if not other:
            continue
        ratio = SequenceMatcher(None, target, other).ratio()
        if ratio >= 0.94:
            scored.append((ratio, row))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]["filed"]), reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.015 and scored[0][1]["cik"] != scored[1][1]["cik"]:
        return None
    return scored[0][1]


def _recent_filing(submissions: dict) -> dict | None:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    candidates: list[dict] = []
    for idx, form in enumerate(forms):
        if form not in SEC_FORMS_PROSPECTUS:
            continue
        if idx >= len(dates) or idx >= len(accessions) or idx >= len(docs):
            continue
        candidates.append(
            {"form": form, "filing_date": dates[idx], "accession": accessions[idx], "primary_document": docs[idx]}
        )
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row["filing_date"], SEC_FORM_PRIORITY.get(row["form"], 0)), reverse=True)
    return candidates[0]


def _filing_url(cik: str, accession: str, primary_document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_document}"


def _clean_html(text: str) -> str:
    soup = BeautifulSoup(text, "lxml")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _contains(text: str, terms: Iterable[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)


def _snippet(text: str, anchor: str, length: int = 8000) -> str:
    lower = text.lower()
    idx = lower.find(anchor.lower())
    if idx < 0:
        return ""
    return text[idx : idx + length]


def _lockup_days(text: str) -> int | None:
    lower = text.lower()
    for match in re.finditer(r"(lock[- ]?up|lockup)", lower):
        sample = lower[match.start() : match.start() + 1800]
        days = [int(x) for x in re.findall(r"\b(\d{2,3})\s+days?\b", sample)]
        if days:
            plausible = [value for value in days if 30 <= value <= 730]
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
    if "underwriting" in lower or "underwriters" in lower:
        return 55.0, []
    return None, []


def _use_of_proceeds_score(text: str) -> float | None:
    section = _snippet(text, "use of proceeds", 10000).lower()
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


def _concept_units(companyfacts: dict, concepts: tuple[str, ...]) -> list[dict]:
    us_gaap = ((companyfacts.get("facts") or {}).get("us-gaap") or {})
    for concept in concepts:
        node = us_gaap.get(concept)
        if not node:
            continue
        units = node.get("units") or {}
        values = units.get("USD") or []
        if values:
            return values
    return []


def _annual_values(companyfacts: dict, concepts: tuple[str, ...]) -> list[dict]:
    values = _concept_units(companyfacts, concepts)
    selected: dict[str, dict] = {}
    for item in values:
        val = item.get("val")
        end = item.get("end")
        if not isinstance(val, (int, float)) or not end:
            continue
        start = item.get("start")
        is_annual = item.get("fp") == "FY"
        if start:
            try:
                span = (date.fromisoformat(end) - date.fromisoformat(start)).days
                is_annual = is_annual or 250 <= span <= 460
            except Exception:
                pass
        if not is_annual:
            continue
        prior = selected.get(end)
        if prior is None or str(item.get("filed", "")) > str(prior.get("filed", "")):
            selected[end] = item
    return sorted(selected.values(), key=lambda item: item["end"])


def _latest_instant(companyfacts: dict, concepts: tuple[str, ...]) -> float | None:
    values = _concept_units(companyfacts, concepts)
    valid = [item for item in values if isinstance(item.get("val"), (int, float)) and item.get("end")]
    if not valid:
        return None
    valid.sort(key=lambda item: (item.get("end", ""), item.get("filed", "")))
    return float(valid[-1]["val"])


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
    revenue = _annual_values(companyfacts, ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"))
    gross = _annual_values(companyfacts, ("GrossProfit",))
    operating = _annual_values(companyfacts, ("OperatingIncomeLoss",))
    net_income = _annual_values(companyfacts, ("NetIncomeLoss", "ProfitLoss"))
    operating_cf = _annual_values(companyfacts, ("NetCashProvidedByUsedInOperatingActivities",))
    cash = _latest_instant(companyfacts, ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"))
    assets = _latest_instant(companyfacts, ("Assets",))
    liabilities = _latest_instant(companyfacts, ("Liabilities",))
    result: dict[str, float | None] = {
        "opportunity_revenue_growth": None,
        "opportunity_gross_margin_quality": None,
        "opportunity_operating_leverage": None,
        "opportunity_balance_sheet_post_ipo": None,
        "risk_loss_cash_burn": None,
        "sec_revenue_growth_pct": None,
        "sec_latest_revenue": None,
        "sec_latest_gross_margin_pct": None,
        "sec_cash": cash,
        "sec_assets": assets,
        "sec_liabilities": liabilities,
        "sec_cash_runway_years_pre_ipo": None,
    }
    if revenue:
        result["sec_latest_revenue"] = float(revenue[-1]["val"])
    if len(revenue) >= 2 and revenue[-2]["val"]:
        growth = (float(revenue[-1]["val"]) / float(revenue[-2]["val"]) - 1.0) * 100.0
        result["sec_revenue_growth_pct"] = round(growth, 2)
        result["opportunity_revenue_growth"] = _growth_score(growth)
    if revenue and gross and revenue[-1]["val"]:
        margin = float(gross[-1]["val"]) / float(revenue[-1]["val"]) * 100.0
        result["sec_latest_gross_margin_pct"] = round(margin, 2)
        result["opportunity_gross_margin_quality"] = max(10.0, min(95.0, 30.0 + margin))
    if len(operating) >= 2 and len(revenue) >= 2 and revenue[-1]["val"] and revenue[-2]["val"]:
        latest_margin = float(operating[-1]["val"]) / float(revenue[-1]["val"]) * 100.0
        prior_margin = float(operating[-2]["val"]) / float(revenue[-2]["val"]) * 100.0
        improvement = latest_margin - prior_margin
        score = 55.0 + improvement * 2.0 + (15.0 if latest_margin > 0 else 0.0)
        result["opportunity_operating_leverage"] = max(10.0, min(95.0, score))
    if assets and assets > 0 and liabilities is not None:
        cash_ratio = 0.0 if cash is None else cash / assets
        liability_ratio = liabilities / assets
        score = 70.0 + min(15.0, cash_ratio * 50.0) - max(0.0, (liability_ratio - 0.65) * 80.0)
        result["opportunity_balance_sheet_post_ipo"] = max(15.0, min(90.0, score))
    latest_loss = bool(net_income and float(net_income[-1]["val"]) < 0)
    latest_ocf = float(operating_cf[-1]["val"]) if operating_cf else None
    if latest_ocf is not None:
        if latest_ocf < 0 and cash is not None and abs(latest_ocf) > 0:
            runway = cash / abs(latest_ocf)
            result["sec_cash_runway_years_pre_ipo"] = round(runway, 2)
            risk = 92.0 if runway < 0.75 else 80.0 if runway < 1.25 else 65.0 if runway < 2 else 50.0 if runway < 3 else 35.0
            if latest_loss:
                risk = min(100.0, risk + 5.0)
            result["risk_loss_cash_burn"] = risk
        elif latest_ocf >= 0 and not latest_loss:
            result["risk_loss_cash_burn"] = 15.0
        else:
            result["risk_loss_cash_burn"] = 35.0
    return result


def prospectus_text_scores(text: str) -> dict:
    lower = text.lower()
    going_concern = _contains(lower, ("substantial doubt about our ability to continue as a going concern", "substantial doubt about the company's ability to continue as a going concern"))
    material_weakness = "material weakness" in lower
    dual_class = _contains(lower, ("dual-class", "dual class", "super-voting", "super voting"))
    customer_concentration = "customer concentration" in lower or bool(re.search(r"customer.{0,120}accounted for.{0,80}\b\d{1,3}(?:\.\d+)?%", lower))
    regulatory_material = _contains(lower, ("government investigation", "regulatory investigation", "material litigation", "criminal investigation"))
    selling_secondary = _contains(lower, ("selling stockholders", "selling shareholders")) and _contains(lower, ("will not receive any proceeds", "we will not receive proceeds"))
    lockup_days = _lockup_days(text)
    underwriter_score, underwriters = _underwriter_score(text)
    use_proceeds_score = _use_of_proceeds_score(text)
    hard_flags: list[str] = []
    if going_concern:
        hard_flags.append("going_concern")
    result = {
        "opportunity_use_of_proceeds_quality": use_proceeds_score,
        "opportunity_underwriter_quality": underwriter_score,
        "opportunity_insider_alignment": None if lockup_days is None else (82.0 if lockup_days >= 180 else 65.0 if lockup_days >= 90 else 40.0),
        "risk_governance_dual_class": 82.0 if dual_class else 22.0,
        "risk_lockup_overhang": None if lockup_days is None else (38.0 if lockup_days >= 180 else 65.0 if lockup_days >= 90 else 82.0),
        "risk_customer_concentration": 78.0 if customer_concentration else 25.0,
        "risk_regulatory_legal": 78.0 if regulatory_material else 25.0,
        "risk_accounting_controls": 85.0 if material_weakness else 22.0,
        "risk_dilution_secondary": 80.0 if selling_secondary else (45.0 if "dilution" in lower else None),
        "hard_flags": "|".join(hard_flags),
        "sec_lockup_days": lockup_days,
        "sec_underwriters_detected": "|".join(underwriters),
        "sec_flag_going_concern": going_concern,
        "sec_flag_material_weakness": material_weakness,
        "sec_flag_dual_class": dual_class,
        "sec_flag_customer_concentration": customer_concentration,
        "sec_flag_regulatory_material": regulatory_material,
        "sec_flag_secondary_selling": selling_secondary,
    }
    return result


def enrich_candidate(candidate: dict, registration: dict, user_agent: str, timeout: int = 20) -> tuple[dict, dict]:
    cik = str(registration["cik"])
    output = dict(candidate)
    output["sec_cik"] = cik
    status = {"candidate_id": candidate.get("candidate_id"), "cik": cik, "status": "FAILED"}
    try:
        submissions_url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
        response = requests.get(submissions_url, headers=_data_headers(user_agent), timeout=timeout)
        response.raise_for_status()
        submissions = response.json()
        filing = _recent_filing(submissions)
        if not filing:
            status.update({"status": "NO_PROSPECTUS", "detail": "No S-1/F-1/424B prospectus in submissions recent index"})
            return output, status
        url = _filing_url(cik, filing["accession"], filing["primary_document"])
        filing_response = requests.get(url, headers=_headers(user_agent), timeout=timeout)
        filing_response.raise_for_status()
        text = _clean_html(filing_response.text)
        output.update(prospectus_text_scores(text))
        output.update(
            {
                "sec_form": filing["form"],
                "sec_filing_date": filing["filing_date"],
                "sec_accession": filing["accession"],
                "sec_prospectus_url": url,
                "sec_prospectus_chars": len(text),
                "sec_analysis_status": "PROSPECTUS_PARSED",
            }
        )
        time.sleep(0.12)
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
        facts_response = requests.get(facts_url, headers=_data_headers(user_agent), timeout=timeout)
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
