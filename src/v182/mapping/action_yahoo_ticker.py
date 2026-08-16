from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from v182.io.frames import is_missing

# Yahoo exchange suffixes are enabled only where the current production universe
# already contains validated, successful examples for the same canonical MIC.
# This is intentionally conservative: unsupported venues remain unchanged rather
# than guessing a Yahoo symbol from issuer country or company name.
YAHOO_SUFFIX_BY_CANONICAL_MIC = {
    "XPAR": ".PA",
    "XBRU": ".BR",
    "XAMS": ".AS",
    "XLIS": ".LS",
    "XMIL": ".MI",
    "XOSL": ".OL",
    "XDUB": ".IR",
}


@dataclass(frozen=True)
class TickerQualification:
    isin: str
    original_ticker: str
    qualified_ticker: str
    canonical_mic: str
    rule: str = "CANONICAL_MIC_YAHOO_SUFFIX"


def _clean(value) -> str:
    if is_missing(value):
        return ""
    return str(value).strip()


def qualify_action_yahoo_tickers(actions_df: pd.DataFrame) -> list[TickerQualification]:
    """Qualify ambiguous Action symbols with the validated canonical venue.

    Raw symbols such as ``ABP`` or ``AASB`` are unsafe Yahoo identifiers: Yahoo
    can resolve an unqualified symbol to a security listed on another exchange,
    producing plausible OHLCV for the wrong company.  When the referential has a
    supported canonical MIC, this function rewrites only unqualified symbols to
    the venue-qualified Yahoo form (for example ``ABP.MI`` or ``AASB.OL``).

    The operation is deterministic and mutates only ``yahoo_ticker``. Existing
    qualified tickers and unsupported/unknown venues are left untouched. No
    issuer-country guessing, fuzzy-name matching, or fallback to an unqualified
    symbol is performed.
    """
    required = {"yahoo_ticker", "v182_ticker_canonical_mic"}
    if not required.issubset(actions_df.columns):
        return []

    changes: list[TickerQualification] = []
    for idx, row in actions_df.iterrows():
        original = _clean(row.get("yahoo_ticker"))
        if not original or "." in original:
            continue

        canonical_mic = _clean(row.get("v182_ticker_canonical_mic")).upper()
        suffix = YAHOO_SUFFIX_BY_CANONICAL_MIC.get(canonical_mic)
        if not suffix:
            continue

        market_symbol = _clean(row.get("v182_ticker_market_symbol"))
        base = market_symbol or original
        if not base or "." in base:
            continue

        qualified = f"{base}{suffix}"
        if qualified == original:
            continue

        actions_df.at[idx, "yahoo_ticker"] = qualified
        changes.append(
            TickerQualification(
                isin=_clean(row.get("isin")),
                original_ticker=original,
                qualified_ticker=qualified,
                canonical_mic=canonical_mic,
            )
        )

    return changes
