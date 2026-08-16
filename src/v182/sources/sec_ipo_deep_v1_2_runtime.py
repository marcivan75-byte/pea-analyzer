from __future__ import annotations

import re

from v182.sources import sec_ipo_deep_v1_2 as core

_ORIGINAL_FINANCIAL_SCORES = core.financial_scores_from_inline_xbrl
_ORIGINAL_EXTRACT_OFFER_TERMS = core.extract_offer_terms


def _growth_score_stable(growth_pct: float) -> float:
    growth = round(float(growth_pct), 6)
    if growth < -10:
        return 15.0
    if growth < 0:
        return 30.0
    if growth < 10:
        return 50.0
    if growth < 20:
        return 65.0
    if growth < 40:
        return 80.0
    if growth < 70:
        return 92.0
    return 100.0


def _post_offering_shares(text: str) -> float | None:
    patterns = (
        r"([0-9][0-9,]*)\s+shares\s+of\s+(?:common|ordinary)\s+(?:stock|shares?)\s+(?:will\s+be|to\s+be)?\s*outstanding\s+(?:immediately\s+)?after\s+(?:this|the)\s+offering",
        r"([0-9][0-9,]*)\s+(?:common|ordinary)\s+shares\s+(?:will\s+be|to\s+be)?\s*outstanding\s+(?:immediately\s+)?after\s+(?:this|the)\s+offering",
        r"shares\s+outstanding\s+(?:immediately\s+)?after\s+(?:this|the)\s+offering\s*[:\-]?\s*([0-9][0-9,]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def financial_scores_from_inline_xbrl(html: str) -> dict:
    core._growth_score = _growth_score_stable
    return _ORIGINAL_FINANCIAL_SCORES(html)


def extract_offer_terms(text: str) -> dict:
    result = _ORIGINAL_EXTRACT_OFFER_TERMS(text)
    post_shares = _post_offering_shares(text)
    if post_shares is not None:
        result["sec_post_offering_shares"] = post_shares
        price = result.get("sec_ipo_price")
        result["sec_implied_market_cap"] = float(price) * post_shares if isinstance(price, (int, float)) else None
    return result


# The core enrichment resolves these names dynamically at runtime. Installing the
# hardened functions here fixes the shared V1.2 enrichment path without duplicating it.
core._growth_score = _growth_score_stable
core.financial_scores_from_inline_xbrl = financial_scores_from_inline_xbrl
core.extract_offer_terms = extract_offer_terms

enrich_candidate = core.enrich_candidate
extract_inline_xbrl_facts = core.extract_inline_xbrl_facts
_post_ipo_metrics = core._post_ipo_metrics
_dilution_risk = core._dilution_risk

__all__ = [
    "extract_inline_xbrl_facts",
    "financial_scores_from_inline_xbrl",
    "extract_offer_terms",
    "enrich_candidate",
]
