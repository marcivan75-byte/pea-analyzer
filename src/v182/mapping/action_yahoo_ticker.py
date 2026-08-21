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

ROOT = Path(__file__).resolve().parents[3]


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
    """Apply the governed sourced identity overlay before market-data waves.

    The default overlay is reconstructed deterministically from compressed parts
    whose raw SHA-256, row count and validated-row count are checked at load time.
    A caller may still pass an explicit overlay path for isolated audits/tests.
    The overlay never overwrites legacy validated identities; unresolved rows stay
    explicitly BLOCK_DATA until a later attributed resolution.
    """
    from v182.mapping.action_isin_resolver import apply_identity_overlay

    if overlay_path is None:
        from v182.mapping.identity_overlay_store import materialize_identity_overlay

        path = materialize_identity_overlay(ROOT)
        if path is None:
            return {"status": "NO_OVERLAY", "applied": 0}
    else:
        path = Path(overlay_path)

    enriched, audit = apply_identity_overlay(actions_df, path)
    if audit.get("applied", 0) == 0:
        return audit
    for field in enriched.columns:
        if field not in actions_df.columns:
            actions_df[field] = pd.NA
        actions_df[field] = enriched[field].to_numpy(copy=True)
    return audit


def apply_configured_action_listing_evidence(
    actions_df: pd.DataFrame,
    evidence_path: str | Path | None = None,
) -> dict:
    """Apply V21.17 frozen Euronext listing metadata by exact ISIN.

    The overlay is metadata-only, never fabricates OHLCV and fails closed when a
    pre-existing listing date disagrees with the official frozen evidence.
    """
    from v182.sources.action_listing_evidence import (
        DEFAULT_FROZEN_EVIDENCE,
        apply_frozen_listing_evidence,
    )

    path = DEFAULT_FROZEN_EVIDENCE if evidence_path is None else Path(evidence_path)
    return apply_frozen_listing_evidence(actions_df, path)


def qualify_action_yahoo_tickers(actions_df: pd.DataFrame) -> list[TickerQualification]:
    """Hydrate sourced Action metadata, then qualify ambiguous legacy Yahoo symbols.

    Raw symbols such as ``ABP`` or ``AASB`` are unsafe Yahoo identifiers: Yahoo
    can resolve an unqualified symbol to a security listed on another exchange,
    producing plausible OHLCV for the wrong company. The Euronext source MIC is
    therefore used as the venue authority for supported legacy rows.

    Before legacy symbol qualification, the governed V21.9 identity overlay
    hydrates canonical identity-only rows from attributed ISIN mappings and the
    V21.17 exact-ISIN Euronext overlay adds only official listing-date metadata.
    The latter never creates price history or calibration eligibility. Existing
    qualified tickers and unsupported/secondary venues are left untouched. No
    issuer-country guessing, fuzzy-name-only promotion, canonical-MIC fallback,
    or fallback to an unqualified symbol is performed.
    """
    apply_configured_action_identity_overlay(actions_df)
    apply_configured_action_listing_evidence(actions_df)

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
