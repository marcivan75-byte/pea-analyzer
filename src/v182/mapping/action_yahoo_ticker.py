from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from v182.io.frames import is_missing

# Yahoo suffixes are derived from the actual source-market MIC carried by the
# Euronext instrument, not from v182_ticker_canonical_mic. The latter is a
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

DEFAULT_IDENTITY_OVERLAY = Path(__file__).resolve().parents[3] / "config" / "V21_9_ACTION_IDENTITY_MAP.csv"


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


def apply_configured_action_identity_overlay(actions_df: pd.DataFrame, overlay_path: str | Path | None = None) -> dict:
    """Apply the governed sourced identity overlay in place before market-data waves.

    The overlay is additive and only hydrates rows still explicitly tagged as
    whitelist-only identity skeletons. It never overwrites legacy validated
    identities. Missing/ambiguous rows remain untouched and BLOCK_DATA.
    """
    from v182.mapping.action_isin_resolver import apply_identity_overlay

    path = Path(overlay_path) if overlay_path is not None else DEFAULT_IDENTITY_OVERLAY
    enriched, audit = apply_identity_overlay(actions_df, path)
    if audit.get("applied", 0) == 0:
        return audit
    for field in enriched.columns:
        if field not in actions_df.columns:
            actions_df[field] = pd.NA
        actions_df[field] = enriched[field].to_numpy(copy=True)
    return audit


def qualify_action_yahoo_tickers(actions_df: pd.DataFrame) -> list[TickerQualification]:
    """Hydrate sourced identities, then qualify ambiguous Action symbols by MIC.

    Raw symbols such as ``ABP`` or ``AASB`` are unsafe Yahoo identifiers: Yahoo
    can resolve an unqualified symbol to a security listed on another exchange,
    producing plausible OHLCV for the wrong company. The Euronext source MIC is
    therefore used as the venue authority for supported markets.

    Before legacy symbol qualification, a static V21.9 identity overlay may
    hydrate canonical identity-only rows. That overlay itself is produced only
    from attributed ISIN mapping plus independent Yahoo identity validation.

    Existing qualified tickers and unsupported/secondary venues are left
    untouched. No issuer-country guessing, fuzzy-name-only promotion,
    canonical-MIC fallback, or fallback to an unqualified symbol is performed.
    """
    apply_configured_action_identity_overlay(actions_df)

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
