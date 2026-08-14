from __future__ import annotations

from urllib.parse import quote_plus, urljoin
import re
from bs4 import BeautifulSoup

BOURSORAMA_SEARCH_URL = "https://www.boursorama.com/recherche/?query={query}"
_ALLOWED_PATTERNS = (
    re.compile(r"^/cours/[^/?#]+/?$", re.IGNORECASE),
    re.compile(r"^/bourse/trackers/cours/[^/?#]+/?$", re.IGNORECASE),
)


def _allowed_href(href: str) -> bool:
    path = href.split("?", 1)[0].split("#", 1)[0]
    return any(pattern.match(path) for pattern in _ALLOWED_PATTERNS)


def extract_boursorama_instrument_url(
    html: str,
    *,
    isin: str = "",
    ticker: str = "",
    name: str = "",
    allow_single_unscored: bool = False,
) -> str | None:
    """Extract an instrument quote URL from a Boursorama search result page.

    Candidate links are restricted to Action/ETF quote routes. Only instrument
    identity evidence contributes to ranking: ISIN first, then ticker and name.
    URL route type is deliberately not a score because it is not identity proof.
    A unique unscored result is accepted only for an explicit ISIN search.
    """
    soup = BeautifulSoup(html or "", "lxml")
    isin_u = str(isin or "").strip().upper()
    ticker_u = str(ticker or "").strip().upper().split(".", 1)[0]
    name_u = str(name or "").strip().upper()
    candidates: list[tuple[int, str]] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", "")).strip()
        if not _allowed_href(href):
            continue
        context = " ".join(filter(None, [link.get_text(" ", strip=True), link.parent.get_text(" ", strip=True) if link.parent else ""])).upper()
        score = 0
        if isin_u and isin_u in context:
            score += 100
        if ticker_u and re.search(rf"\b{re.escape(ticker_u)}\b", context):
            score += 30
        if name_u and len(name_u) >= 4 and name_u in context:
            score += 20
        candidates.append((score, urljoin("https://www.boursorama.com", href.split("?", 1)[0])))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    if best_score <= 0:
        if allow_single_unscored and len(candidates) == 1:
            return candidates[0][1]
        return None
    best = [url for score, url in candidates if score == best_score]
    return best[0] if len(best) == 1 else None


def resolve_boursorama_url(row, requests, limiter, headers: dict[str, str] | None = None) -> str | None:
    """Resolve a Boursorama Action/ETF URL with ISIN-first public search.

    Explicit stored Boursorama URLs win. Search failure or identity ambiguity is
    non-fatal; callers keep the observation missing rather than guess a listing.
    """
    for field in ("boursorama_url", "source_url"):
        value = str(row.get(field, "") or "").strip()
        if "boursorama.com" in value:
            return value
    isin = str(row.get("isin", "") or "").strip()
    ticker = str(row.get("yahoo_ticker", row.get("ticker", "")) or "").strip()
    name = str(row.get("name", "") or "").strip()
    queries = [
        (isin, True),
        (ticker.split(".", 1)[0], False),
        (name, False),
    ]
    hdrs = headers or {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.6.3; +data-quality)",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
    }
    for query, is_isin_query in queries:
        if not query:
            continue
        try:
            limiter.wait()
            response = requests.get(BOURSORAMA_SEARCH_URL.format(query=quote_plus(query)), timeout=20, headers=hdrs)
            if getattr(response, "status_code", 500) >= 400:
                continue
            resolved = extract_boursorama_instrument_url(
                response.text,
                isin=isin,
                ticker=ticker,
                name=name,
                allow_single_unscored=is_isin_query,
            )
            if resolved:
                return resolved
        except Exception:
            continue
    return None
