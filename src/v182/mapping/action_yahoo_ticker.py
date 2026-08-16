from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from v182.io.frames import is_missing

# Yahoo suffixes are derived from the actual source-market MIC carried by the
# Euronext instrument, not from v182_ticker_canonical_mic.  The latter is a
# normalized field and historical data contains misclassifications for some
# Access/secondary venues (for example XMLI instruments listed in Paris).
#
# Only venue families with a stable Yahoo suffix are enabled. Secondary trading
# venues such as MTAH and ETLX are intentionally excluded: their local symbol can
# differ from the issuer's primary Yahoo symbol and must not be guessed.
YAHOO_SUFFIX_BY_EURONEXT_MIC = {
    # Paris
    "XPAR": ".PA",
    "ALXP": ".PA",
    "XMLI": ".PA",
    # Brussels
    "XBRU": ".BR",
    "ALXB": ".BR",
    "MLXB": ".BR",
    # Amsterdam
    "XAMS": ".AS",
    # Lisbon
    "XLIS": ".LS",
    "ENXL": ".LS",
    # Milan
    "MTAA": ".MI",
    "EXGM": ".MI",
    # Oslo
    "XOSL": ".OL",
    "MERK": ".OL",
    "XOAS": ".OL",
    # Dublin
    "XMSM": ".IR",
    "XESM": ".IR",
}


@dataclass(frozen=True)
class TickerQualification:
    isin: str
    original_ticker: str
    qualified_ticker: str
    market_mic: str
    rule: str = "SOURCE_MARKET_MIC_YAHOO_SUFFIX"


def _clean(value) -> str:
    if is_missing(value):
        return ""
    return str(value).strip()


def qualify_action_yahoo_tickers(actions_df: pd.DataFrame) -> list[TickerQualification]:
    """Qualify ambiguous Action symbols using the instrument's source-market MIC.

    Raw symbols such as ``ABP`` or ``AASB`` are unsafe Yahoo identifiers: Yahoo
    can resolve an unqualified symbol to a security listed on another exchange,
    producing plausible OHLCV for the wrong company.  The Euronext source MIC is
    therefore used as the venue authority for supported markets.

    The operation is deterministic and mutates only ``yahoo_ticker``. Existing
    qualified tickers and unsupported/secondary venues are left untouched. No
    issuer-country guessing, fuzzy-name matching, canonical-MIC fallback, or
    fallback to an unqualified symbol is performed.
    """
    required = {"yahoo_ticker", "euronext_mic"}
    if not required.issubset(actions_df.columns):
        return []

    changes: list[TickerQualification] = []
    for idx, row in actions_df.iterrows():
        original = _clean(row.get("yahoo_ticker"))
        if not original or "." in original:
            continue

        market_mic = _clean(row.get("euronext_mic")).upper()
        suffix = YAHOO_SUFFIX_BY_EURONEXT_MIC.get(market_mic)
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
                market_mic=market_mic,
            )
        )

    return changes
