from __future__ import annotations

from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from v182.reporting import selected_source_enrichment as selected_source
from v182.sources import investing_technical as investing


VERSION = "SELECTED_SOURCE_RELIABILITY_V21_8_4"
ETF_RESERVED_SLOTS = 10
_ORIGINAL_SELECT = selected_source.select_preselected_rows
_ORIGINAL_VALIDATE = investing._validate_scoreboard
_INSTALLED = False


def _identity_urls(base_url: str) -> list[str]:
    base = investing._clean_base_url(base_url) or str(base_url or "").strip()
    if not base:
        return []
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    urls = [
        urlunsplit((parts.scheme, parts.netloc, path + "-candlestick", parts.query, "")),
        urlunsplit((parts.scheme, parts.netloc, path + "-historical-data", parts.query, "")),
        urlunsplit((parts.scheme, parts.netloc, path + "-scoreboard", parts.query, "")),
        base,
    ]
    return list(dict.fromkeys(urls))


def _validate_public_identity(
    base_url: str,
    isin: str,
    *,
    fetcher,
    limiter,
    timeout_seconds: float,
):
    """Validate one Investing instrument by exact ISIN on public identity pages.

    Investing technical pages do not reliably expose the ISIN in their HTML.  Public
    candlestick/historical pages do.  The resolver therefore proves identity first,
    then keeps the technical page solely for the Daily/Weekly/Monthly signals.
    """
    target = str(isin or "").strip().upper()
    if not target:
        return None, None
    for url in _identity_urls(base_url):
        try:
            limiter.wait()
            response = fetcher(url, timeout=timeout_seconds)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            html = str(getattr(response, "text", "") or "")
            if target not in html.upper():
                continue
            final_url = str(getattr(response, "url", url) or url)
            validated = investing._clean_base_url(final_url) or investing._clean_base_url(base_url)
            if validated:
                return validated, sha256(html.encode("utf-8", errors="replace")).hexdigest()
        except Exception:
            continue
    return None, None


def _select_with_etf_reserve(
    rows: pd.DataFrame,
    *,
    max_unique_instruments: int = 40,
    accepted_statuses: tuple[str, ...] = ("BUY_CANDIDATE", "WATCH", "REVIEW", "SHADOW_CANDIDATE"),
) -> pd.DataFrame:
    """Preserve the selected-only contract while ensuring ETFs can be enriched.

    The old global Top40 could be monopolised by actions, which made the Boursorama
    ETF/Morningstar branch impossible to exercise.  This affects source enrichment
    only: no model score, decision, weight or selection threshold is changed.
    """
    if rows.empty or "isin" not in rows or "asset_class" not in rows:
        return _ORIGINAL_SELECT(
            rows,
            max_unique_instruments=max_unique_instruments,
            accepted_statuses=accepted_statuses,
        )
    frame = rows.copy()
    if "decision" in frame:
        mask = frame["decision"].astype(str).str.upper().isin(accepted_statuses)
    elif "dynamic_decision" in frame:
        mask = frame["dynamic_decision"].astype(str).str.upper().isin(accepted_statuses)
    else:
        return _ORIGINAL_SELECT(
            rows,
            max_unique_instruments=max_unique_instruments,
            accepted_statuses=accepted_statuses,
        )
    frame = frame[mask].copy()
    if frame.empty:
        return frame
    ordered = selected_source._score_sort(frame)
    limit = max(0, int(max_unique_instruments))
    if limit == 0:
        return frame.iloc[0:0].copy()
    asset = ordered["asset_class"].astype(str).str.upper()
    etf_ordered = ordered[asset.eq("ETF")]
    reserve = min(int(ETF_RESERVED_SLOTS), limit)
    etf_isins = list(dict.fromkeys(etf_ordered["isin"].astype(str).tolist()))[:reserve]
    chosen = list(etf_isins)
    for isin in ordered["isin"].astype(str).tolist():
        if isin not in chosen:
            chosen.append(isin)
        if len(chosen) >= limit:
            break
    return frame[frame["isin"].astype(str).isin(chosen)].copy()


def install() -> dict:
    global _INSTALLED
    if not _INSTALLED:
        investing._validate_scoreboard = _validate_public_identity
        selected_source.select_preselected_rows = _select_with_etf_reserve
        _INSTALLED = True
    return {
        "version": VERSION,
        "installed": True,
        "investing_identity_policy": "EXACT_ISIN_MULTI_PUBLIC_PAGE_CANDLESTICK_FIRST",
        "investing_technical_page_identity_not_required": True,
        "etf_reserved_source_slots": ETF_RESERVED_SLOTS,
        "score_influence": 0.0,
        "decision_influence": False,
        "can_create_buy": False,
    }
