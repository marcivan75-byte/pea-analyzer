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

    Candidate links are restricted to Action/ETF quote routes. ISIN is the
    strongest discriminator; ticker and name are secondary context. A unique
    unscored quote result is accepted only when the caller explicitly searched
    by ISIN, never for a broad ticker/name search.
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
        if "/bourse/trackers/cours/" in href.lower():
            score += 2
        candidates.append((score, urljoin("https://www.boursorama.com", href.split("?", 1)[0])))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, best_url = candidates[0]
    if best_score <= 0 and (isin_u or ticker_u or name_u):
        if allow_single_unscored and len(candidates) == 1:
            return best_url
        return None
    return best_url


def resolve_boursorama_url(row, requests, limiter, headers: dict[str, str] | None = None) -> str | None:
    """Resolve a Boursorama Action/ETF URL with ISIN-first public search.

    Explicit stored Boursorama URLs win. Search failure is non-fatal and callers
    must keep the observation missing rather than fabricate a market prefix.
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
