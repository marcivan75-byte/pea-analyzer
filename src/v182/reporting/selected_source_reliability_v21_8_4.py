from __future__ import annotations

from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit

import pandas as pd

from v182.reporting import selected_source_enrichment as selected_source
from v182.sources import investing_technical as investing
from v182.sources import boursorama_selected_etf as boursorama_etf


VERSION = "SELECTED_SOURCE_RELIABILITY_V21_8_5"
ETF_RESERVED_SLOTS = 10
JINA_READER_PREFIX = "https://r.jina.ai/"
_ORIGINAL_SELECT = selected_source.select_preselected_rows
_ORIGINAL_VALIDATE = investing._validate_scoreboard
_ORIGINAL_DEFAULT_FETCHER = investing._default_fetcher
_ORIGINAL_ETF_PARSE = boursorama_etf.parse_etf_sheet_html
_INSTALLED = False


class _SnapshotResponse:
    def __init__(self, *, text: str, url: str, status_code: int = 200):
        self.text = text
        self.url = url
        self.status_code = int(status_code)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP_{self.status_code}")


def _reader_fetch(url: str, *, timeout: float):
    """Rendered HTML snapshot fallback for public Investing pages.

    Reader renders JavaScript-heavy pages in a browser/curl-impersonation layer.
    Only public instrument URLs are sent.  The returned response deliberately keeps
    the original Investing URL so downstream identity/URL audits remain canonical.
    """
    import requests

    reader_url = JINA_READER_PREFIX + str(url)
    response = requests.get(
        reader_url,
        headers={
            "User-Agent": "PEA-Analyzer/21.15 investing-public-snapshot",
            "X-Respond-With": "html",
            "X-Timeout": "10",
        },
        timeout=max(15.0, float(timeout) + 10.0),
    )
    response.raise_for_status()
    return _SnapshotResponse(text=str(response.text or ""), url=str(url), status_code=response.status_code)


def _reliable_investing_fetcher(url: str, *, timeout: float):
    target = str(url or "")
    # Search API must remain a normal JSON request because the legacy resolver calls .json().
    if "api.investing.com/" in target:
        return _ORIGINAL_DEFAULT_FETCHER(target, timeout=timeout)

    path = urlsplit(target).path.lower()
    browser_preferred = any(
        marker in path
        for marker in ("-technical", "-candlestick", "-historical-data", "-scoreboard")
    )
    if browser_preferred:
        try:
            return _reader_fetch(target, timeout=timeout)
        except Exception:
            return _ORIGINAL_DEFAULT_FETCHER(target, timeout=timeout)

    try:
        response = _ORIGINAL_DEFAULT_FETCHER(target, timeout=timeout)
        text = str(getattr(response, "text", "") or "")
        lower = text.lower()
        blocked = any(token in lower for token in ("access denied", "captcha", "cloudflare", "enable javascript"))
        if getattr(response, "status_code", 200) < 400 and len(text) >= 500 and not blocked:
            return response
    except Exception:
        pass
    return _reader_fetch(target, timeout=timeout)


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
    """Prove Investing identity by exact ISIN before reading technical signals."""
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
            validated = investing._clean_base_url(base_url)
            if validated:
                return validated, sha256(html.encode("utf-8", errors="replace")).hexdigest()
        except Exception:
            continue
    return None, None


def _parse_etf_sheet_without_false_proof_overwrite(html: str) -> dict[str, object]:
    """Absence of a Morningstar widget is not negative proof.

    The risk page often lacks the Morningstar widget.  Returning False there used
    to overwrite a valid True proof previously collected from the course page.
    Missing proof is now represented by absence; a positive proof is still explicit.
    """
    fields = dict(_ORIGINAL_ETF_PARSE(html) or {})
    if fields.get("boursorama_morningstar_rating_proof_valid") is False:
        fields.pop("boursorama_morningstar_rating_proof_valid", None)
    return fields


def _select_with_etf_reserve(
    rows: pd.DataFrame,
    *,
    max_unique_instruments: int = 40,
    accepted_statuses: tuple[str, ...] = ("BUY_CANDIDATE", "WATCH", "REVIEW", "SHADOW_CANDIDATE"),
) -> pd.DataFrame:
    if rows.empty or "isin" not in rows or "asset_class" not in rows:
        return _ORIGINAL_SELECT(rows, max_unique_instruments=max_unique_instruments, accepted_statuses=accepted_statuses)
    frame = rows.copy()
    if "decision" in frame:
        mask = frame["decision"].astype(str).str.upper().isin(accepted_statuses)
    elif "dynamic_decision" in frame:
        mask = frame["dynamic_decision"].astype(str).str.upper().isin(accepted_statuses)
    else:
        return _ORIGINAL_SELECT(rows, max_unique_instruments=max_unique_instruments, accepted_statuses=accepted_statuses)
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
        investing._default_fetcher = _reliable_investing_fetcher
        investing._validate_scoreboard = _validate_public_identity
        boursorama_etf.parse_etf_sheet_html = _parse_etf_sheet_without_false_proof_overwrite
        selected_source.select_preselected_rows = _select_with_etf_reserve
        _INSTALLED = True
    return {
        "version": VERSION,
        "installed": True,
        "investing_identity_policy": "EXACT_ISIN_MULTI_PUBLIC_PAGE_CANDLESTICK_FIRST",
        "investing_snapshot_fallback": "JINA_READER_RENDERED_HTML",
        "investing_technical_page_identity_not_required": True,
        "etf_reserved_source_slots": ETF_RESERVED_SLOTS,
        "morningstar_positive_proof_preserved_across_course_and_risk_pages": True,
        "score_influence": 0.0,
        "decision_influence": False,
        "can_create_buy": False,
    }
