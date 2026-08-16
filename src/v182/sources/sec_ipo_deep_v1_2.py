from __future__ import annotations

from datetime import date
import math
import re
import time
from typing import Iterable

from bs4 import BeautifulSoup, Tag
import requests

from v182.sources import sec_ipo as base

# Inline-XBRL concepts are intentionally limited to high-confidence core financials.
# We do not invent peer-relative valuation or sector scores from these facts.
CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
        "Revenue",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss", "ProfitLossFromOperatingActivities"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": (
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
    ),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndCashEquivalents",
    ),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
}

_ALIAS_PRIORITY: dict[str, tuple[str, int]] = {
    alias.lower(): (metric, index)
    for metric, aliases in CONCEPT_ALIASES.items()
    for index, alias in enumerate(aliases)
}


def _local_name(value: object) -> str:
    text = "" if value is None else str(value)
    return text.split(":")[-1].strip()


def _parse_numeric(value: object, scale: object = None, sign: object = None) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"—", "-", "n/a", "na", "none", "nan"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace("−", "-"))
    if cleaned in {"", "-", "."}:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if negative and number > 0:
        number = -number
    if str(sign or "").strip() == "-" and number > 0:
        number = -number
    try:
        exponent = int(str(scale or "0").strip() or "0")
    except ValueError:
        exponent = 0
    return number * (10**exponent)


def _context_dates(soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    contexts: dict[str, dict[str, str]] = {}
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag) or not str(tag.name).lower().endswith("context"):
            continue
        context_id = str(tag.get("id") or "").strip()
        if not context_id:
            continue
        result: dict[str, str] = {}
        for child in tag.find_all(True):
            name = str(child.name).lower()
            text = child.get_text(" ", strip=True)
            if name.endswith("startdate"):
                result["start"] = text
            elif name.endswith("enddate"):
                result["end"] = text
            elif name.endswith("instant"):
                result["instant"] = text
        contexts[context_id] = result
    return contexts


def extract_inline_xbrl_facts(html: str) -> list[dict]:
    """Extract a conservative subset of numeric facts from an Inline-XBRL filing."""
    soup = BeautifulSoup(html, "lxml")
    contexts = _context_dates(soup)
    facts: list[dict] = []
    for tag in soup.find_all(True):
        if not isinstance(tag, Tag) or not str(tag.name).lower().endswith("nonfraction"):
            continue
        concept = _local_name(tag.get("name"))
        alias = _ALIAS_PRIORITY.get(concept.lower())
        if alias is None:
            continue
        value = _parse_numeric(tag.get_text(" ", strip=True), tag.get("scale"), tag.get("sign"))
        if value is None or not math.isfinite(value):
            continue
        context_ref = str(tag.get("contextref") or tag.get("contextRef") or "").strip()
        context = contexts.get(context_ref, {})
        metric, priority = alias
        facts.append(
            {
                "metric": metric,
                "concept": concept,
                "priority": priority,
                "value": float(value),
                "context_ref": context_ref,
                "start": context.get("start", ""),
                "end": context.get("end", ""),
                "instant": context.get("instant", ""),
            }
        )
    return facts


def _safe_date(value: object) -> date | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _annual_metric(facts: Iterable[dict], metric: str) -> list[dict]:
    selected: dict[str, dict] = {}
    for fact in facts:
        if fact.get("metric") != metric:
            continue
        start = _safe_date(fact.get("start"))
        end = _safe_date(fact.get("end"))
        if start is None or end is None:
            continue
        span = (end - start).days
        if not 250 <= span <= 460:
            continue
        key = end.isoformat()
        prior = selected.get(key)
        if prior is None or int(fact.get("priority", 99)) < int(prior.get("priority", 99)):
            selected[key] = fact
    return [selected[key] for key in sorted(selected)]


def _latest_instant_metric(facts: Iterable[dict], metric: str) -> float | None:
    candidates: list[tuple[date, int, float]] = []
    for fact in facts:
        if fact.get("metric") != metric:
            continue
        instant = _safe_date(fact.get("instant") or fact.get("end"))
        if instant is None:
            continue
        candidates.append((instant, -int(fact.get("priority", 99)), float(fact["value"])))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]


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


def financial_scores_from_inline_xbrl(html: str) -> dict:
    facts = extract_inline_xbrl_facts(html)
    revenue = _annual_metric(facts, "revenue")
    gross = _annual_metric(facts, "gross_profit")
    operating = _annual_metric(facts, "operating_income")
    net_income = _annual_metric(facts, "net_income")
    operating_cf = _annual_metric(facts, "operating_cash_flow")
    cash = _latest_instant_metric(facts, "cash")
    assets = _latest_instant_metric(facts, "assets")
    liabilities = _latest_instant_metric(facts, "liabilities")

    result: dict[str, object] = {
        "sec_ixbrl_status": "SUCCESS" if facts else "NO_SUPPORTED_FACTS",
        "sec_ixbrl_fact_count": len(facts),
        "sec_financial_source": "INLINE_XBRL_PROSPECTUS" if facts else "",
        "opportunity_revenue_growth": None,
        "opportunity_gross_margin_quality": None,
        "opportunity_operating_leverage": None,
        # Deliberately left blank until net proceeds are known and a true pro-forma balance sheet can be computed.
        "opportunity_balance_sheet_post_ipo": None,
        "risk_loss_cash_burn": None,
        "sec_revenue_growth_pct": None,
        "sec_latest_revenue": float(revenue[-1]["value"]) if revenue else None,
        "sec_latest_gross_margin_pct": None,
        "sec_cash": cash,
        "sec_assets": assets,
        "sec_liabilities": liabilities,
        "sec_cash_runway_years_pre_ipo": None,
        "sec_latest_operating_cash_flow": float(operating_cf[-1]["value"]) if operating_cf else None,
    }

    if len(revenue) >= 2 and float(revenue[-2]["value"]) != 0:
        growth = (float(revenue[-1]["value"]) / float(revenue[-2]["value"]) - 1.0) * 100.0
        result["sec_revenue_growth_pct"] = round(growth, 2)
        result["opportunity_revenue_growth"] = _growth_score(growth)

    gross_by_end = {str(item.get("end")): float(item["value"]) for item in gross}
    if revenue:
        latest_end = str(revenue[-1].get("end"))
        latest_revenue = float(revenue[-1]["value"])
        latest_gross = gross_by_end.get(latest_end)
        if latest_gross is not None and latest_revenue != 0:
            margin = latest_gross / latest_revenue * 100.0
            result["sec_latest_gross_margin_pct"] = round(margin, 2)
            result["opportunity_gross_margin_quality"] = max(10.0, min(95.0, 30.0 + margin))

    operating_by_end = {str(item.get("end")): float(item["value"]) for item in operating}
    if len(revenue) >= 2:
        current_end = str(revenue[-1].get("end"))
        prior_end = str(revenue[-2].get("end"))
        current_revenue = float(revenue[-1]["value"])
        prior_revenue = float(revenue[-2]["value"])
        if current_revenue and prior_revenue and current_end in operating_by_end and prior_end in operating_by_end:
            latest_margin = operating_by_end[current_end] / current_revenue * 100.0
            prior_margin = operating_by_end[prior_end] / prior_revenue * 100.0
            result["opportunity_operating_leverage"] = max(
                10.0,
                min(95.0, 55.0 + (latest_margin - prior_margin) * 2.0 + (15.0 if latest_margin > 0 else 0.0)),
            )

    latest_loss = bool(net_income and float(net_income[-1]["value"]) < 0)
    latest_ocf = float(operating_cf[-1]["value"]) if operating_cf else None
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


def _first_number(text: str, patterns: tuple[str, ...], flags: int = re.IGNORECASE | re.DOTALL) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if not match:
            continue
        raw = match.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def _money_value(number: float | None, suffix: str | None) -> float | None:
    if number is None:
        return None
    multiplier = 1.0
    normalized = str(suffix or "").lower()
    if normalized.startswith("b"):
        multiplier = 1_000_000_000.0
    elif normalized.startswith("m"):
        multiplier = 1_000_000.0
    elif normalized.startswith("t"):
        multiplier = 1_000.0
    return number * multiplier


def _net_proceeds(text: str) -> float | None:
    patterns = (
        r"net proceeds(?:\s+to\s+us)?\s+from\s+(?:this|the)\s+offering.{0,220}?approximately\s+\$?([0-9]+(?:\.[0-9]+)?)\s*(million|billion|thousand)?",
        r"estimate\s+that\s+the\s+net\s+proceeds.{0,220}?approximately\s+\$?([0-9]+(?:\.[0-9]+)?)\s*(million|billion|thousand)?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return _money_value(float(match.group(1)), match.group(2))
    return None


def extract_offer_terms(text: str) -> dict:
    price = _first_number(
        text,
        (
            r"initial\s+public\s+offering\s+price(?:\s+is|\s+of)?\s*\$?([0-9]+(?:\.[0-9]+)?)\s+per\s+share",
            r"public\s+offering\s+price.{0,80}?\$?([0-9]+(?:\.[0-9]+)?)\s+per\s+share",
        ),
    )
    primary_shares = _first_number(
        text,
        (
            r"we\s+are\s+offering\s+([0-9][0-9,]*)\s+(?:shares|ordinary\s+shares|common\s+shares)",
            r"company\s+is\s+offering\s+([0-9][0-9,]*)\s+(?:shares|ordinary\s+shares|common\s+shares)",
        ),
    )
    secondary_shares = _first_number(
        text,
        (
            r"selling\s+(?:stockholders|shareholders).{0,100}?offering\s+([0-9][0-9,]*)\s+(?:shares|ordinary\s+shares|common\s+shares)",
            r"([0-9][0-9,]*)\s+(?:shares|ordinary\s+shares|common\s+shares).{0,80}?offered\s+by\s+the\s+selling\s+(?:stockholders|shareholders)",
        ),
    )
    post_shares = _first_number(
        text,
        (
            r"([0-9][0-9,]*)\s+shares.{0,100}?outstanding\s+(?:immediately\s+)?after\s+(?:this|the)\s+offering",
            r"shares.{0,80}?outstanding\s+(?:immediately\s+)?after\s+(?:this|the)\s+offering.{0,80}?([0-9][0-9,]*)",
        ),
    )
    dilution_per_share = _first_number(
        text,
        (
            r"immediate\s+dilution.{0,160}?\$?([0-9]+(?:\.[0-9]+)?)\s+per\s+share",
            r"dilution.{0,160}?to\s+new\s+investors.{0,80}?\$?([0-9]+(?:\.[0-9]+)?)\s+per\s+share",
        ),
    )
    net_proceeds = _net_proceeds(text)
    total_offered = None
    if primary_shares is not None or secondary_shares is not None:
        total_offered = (primary_shares or 0.0) + (secondary_shares or 0.0)
    secondary_pct = None
    if total_offered and total_offered > 0 and secondary_shares is not None:
        secondary_pct = secondary_shares / total_offered * 100.0
    dilution_pct = None
    if price and price > 0 and dilution_per_share is not None:
        dilution_pct = dilution_per_share / price * 100.0
    implied_market_cap = price * post_shares if price and post_shares else None
    gross_primary = price * primary_shares if price and primary_shares else None
    confidence_inputs = [price, primary_shares, post_shares, net_proceeds, dilution_per_share]
    confidence = round(sum(item is not None for item in confidence_inputs) / len(confidence_inputs) * 100.0, 2)
    return {
        "sec_ipo_price": price,
        "sec_primary_shares_offered": primary_shares,
        "sec_secondary_shares_offered": secondary_shares,
        "sec_total_shares_offered_detected": total_offered,
        "sec_secondary_share_pct": None if secondary_pct is None else round(secondary_pct, 2),
        "sec_post_offering_shares": post_shares,
        "sec_primary_gross_proceeds": gross_primary,
        "sec_net_proceeds": net_proceeds,
        "sec_dilution_per_share": dilution_per_share,
        "sec_dilution_pct": None if dilution_pct is None else round(dilution_pct, 2),
        "sec_implied_market_cap": implied_market_cap,
        "sec_offer_terms_confidence_pct": confidence,
    }


def _dilution_risk(terms: dict) -> float | None:
    dilution = terms.get("sec_dilution_pct")
    secondary = terms.get("sec_secondary_share_pct")
    signals: list[float] = []
    if isinstance(dilution, (int, float)):
        signals.append(25.0 if dilution < 15 else 40.0 if dilution < 30 else 60.0 if dilution < 50 else 78.0 if dilution < 70 else 92.0)
    if isinstance(secondary, (int, float)):
        signals.append(25.0 if secondary < 10 else 45.0 if secondary < 30 else 65.0 if secondary < 50 else 85.0)
    return max(signals) if signals else None


def _post_ipo_metrics(financial: dict, terms: dict) -> dict:
    cash = financial.get("sec_cash")
    assets = financial.get("sec_assets")
    liabilities = financial.get("sec_liabilities")
    latest_ocf = financial.get("sec_latest_operating_cash_flow")
    net_proceeds = terms.get("sec_net_proceeds")
    revenue = financial.get("sec_latest_revenue")
    implied_market_cap = terms.get("sec_implied_market_cap")
    output: dict[str, object] = {
        "sec_pro_forma_cash_before_use_of_proceeds": None,
        "sec_cash_runway_years_post_ipo_upper_bound": None,
        "sec_ipo_price_to_sales": None,
        "shadow_absolute_valuation_risk": None,
        "opportunity_balance_sheet_post_ipo": None,
        "hard_flags": "",
    }
    if isinstance(implied_market_cap, (int, float)) and isinstance(revenue, (int, float)) and revenue > 0:
        ps = implied_market_cap / revenue
        output["sec_ipo_price_to_sales"] = round(ps, 2)
        output["shadow_absolute_valuation_risk"] = 20.0 if ps < 2 else 35.0 if ps < 4 else 50.0 if ps < 7 else 70.0 if ps < 12 else 88.0
    if not isinstance(net_proceeds, (int, float)) or net_proceeds <= 0:
        return output
    if isinstance(cash, (int, float)):
        pro_forma_cash = cash + net_proceeds
        output["sec_pro_forma_cash_before_use_of_proceeds"] = pro_forma_cash
        if isinstance(latest_ocf, (int, float)) and latest_ocf < 0:
            runway = pro_forma_cash / abs(latest_ocf)
            output["sec_cash_runway_years_post_ipo_upper_bound"] = round(runway, 2)
            # This is an upper bound before planned uses of proceeds. If even this is <1y,
            # the 12-month liquidity hard block is conservative rather than optimistic.
            if runway < 1.0:
                output["hard_flags"] = "insufficient_12m_liquidity_post_offering"
    if isinstance(assets, (int, float)) and assets > 0 and isinstance(liabilities, (int, float)):
        pro_assets = assets + net_proceeds
        pro_cash = (cash if isinstance(cash, (int, float)) else 0.0) + net_proceeds
        cash_ratio = pro_cash / pro_assets if pro_assets > 0 else 0.0
        liability_ratio = liabilities / pro_assets if pro_assets > 0 else 1.0
        output["opportunity_balance_sheet_post_ipo"] = max(
            15.0,
            min(95.0, 70.0 + min(18.0, cash_ratio * 55.0) - max(0.0, (liability_ratio - 0.65) * 80.0)),
        )
    return output


def _merge_missing(primary: dict, fallback: dict) -> dict:
    result = dict(primary)
    for key, value in fallback.items():
        current = result.get(key)
        missing = current is None or current == "" or (isinstance(current, float) and math.isnan(current))
        if missing and value not in (None, ""):
            result[key] = value
    return result


def enrich_candidate(candidate: dict, registration: dict, user_agent: str, timeout: int = 20) -> tuple[dict, dict]:
    """V1.2 SEC enrichment: prospectus Inline-XBRL first, Company Facts only as fallback."""
    cik = str(int(registration["cik"]))
    output = dict(candidate)
    output["sec_cik"] = cik
    status = {"candidate_id": candidate.get("candidate_id"), "cik": cik, "status": "FAILED", "runtime": "DEEP_V1_2"}
    try:
        response = requests.get(
            f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
            headers=base._headers(user_agent),
            timeout=timeout,
        )
        response.raise_for_status()
        submissions = response.json()
        output["sec_sic"] = submissions.get("sic")
        output["sec_sic_description"] = submissions.get("sicDescription")
        output["sec_state_of_incorporation"] = submissions.get("stateOfIncorporation")
        filing = base._recent_filing(submissions)
        if not filing:
            status.update({"status": "NO_PROSPECTUS", "detail": "No current S-1/F-1/424B prospectus"})
            return output, status

        url = base._filing_url(cik, filing["accession"], filing["primary_document"])
        filing_response = requests.get(url, headers=base._headers(user_agent, "www.sec.gov"), timeout=timeout)
        filing_response.raise_for_status()
        raw_html = filing_response.text
        clean_text = base._clean_html(raw_html)
        output.update(base.prospectus_text_scores(clean_text))

        inline_financial = financial_scores_from_inline_xbrl(raw_html)
        output.update(inline_financial)
        terms = extract_offer_terms(clean_text)
        output.update(terms)

        companyfacts_status = "NOT_NEEDED"
        if not inline_financial.get("sec_ixbrl_fact_count") or inline_financial.get("sec_latest_revenue") is None:
            time.sleep(0.12)
            facts_response = requests.get(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
                headers=base._headers(user_agent),
                timeout=timeout,
            )
            if facts_response.ok:
                output = _merge_missing(output, base.financial_scores(facts_response.json()))
                companyfacts_status = "SUCCESS_FALLBACK"
                if output.get("sec_financial_source") in (None, ""):
                    output["sec_financial_source"] = "SEC_COMPANYFACTS_FALLBACK"
            else:
                companyfacts_status = f"HTTP_{facts_response.status_code}"
        output["sec_companyfacts_status"] = companyfacts_status

        dilution_risk = _dilution_risk(terms)
        if dilution_risk is not None:
            output["risk_dilution_secondary"] = dilution_risk
        post_metrics = _post_ipo_metrics(output, terms)
        output["hard_flags"] = base_flags(output.get("hard_flags"), post_metrics.pop("hard_flags", ""))
        output.update(post_metrics)

        output.update(
            {
                "sec_form": filing["form"],
                "sec_filing_date": filing["filing_date"],
                "sec_accession": filing["accession"],
                "sec_prospectus_url": url,
                "sec_prospectus_chars": len(clean_text),
                "sec_analysis_status": "PROSPECTUS_DEEP_PARSED_V1_2",
            }
        )
        status.update(
            {
                "status": "SUCCESS",
                "form": filing["form"],
                "filing_date": filing["filing_date"],
                "url": url,
                "ixbrl_fact_count": output.get("sec_ixbrl_fact_count", 0),
                "offer_terms_confidence_pct": output.get("sec_offer_terms_confidence_pct", 0),
            }
        )
        return output, status
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        status.update({"status": "FAILED", "detail": f"{type(exc).__name__}: {str(exc)[:220]}"})
        return output, status


def base_flags(current: object, incoming: object) -> str:
    flags = {
        flag.strip()
        for source in (current, incoming)
        for flag in str(source or "").split("|")
        if flag.strip()
    }
    return "|".join(sorted(flags))


__all__ = [
    "extract_inline_xbrl_facts",
    "financial_scores_from_inline_xbrl",
    "extract_offer_terms",
    "enrich_candidate",
]
