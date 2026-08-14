from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin
import math
import re
import pandas as pd
from bs4 import BeautifulSoup

from v182.sources.boursorama_resolver import resolve_boursorama_url
from v182.sources.rate_limit import StartRateLimiter

_SIGNAL_SCORE = {
    "STRONG_BUY": 2.0,
    "BUY": 1.0,
    "NEUTRAL": 0.0,
    "SELL": -1.0,
    "STRONG_SELL": -2.0,
}
_NUM_TOKEN = r"[+\-−]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
_BOURSORAMA_BUCKETS = {1:"ACHETER",2:"RENFORCER",3:"CONSERVER",4:"ALLEGER",5:"VENDRE"}
_INVESTING_SIGNAL_PATTERN = r"Strong Buy|Strong Sell|Achat fort|Acheter fort|Vente forte|Vendre fort|Neutral|Neutre|Buy|Sell|Achat|Acheter|Vente|Vendre"


def _clean_text(html: str) -> str:
    text = BeautifulSoup(html or "", "lxml").get_text(" ", strip=True).replace(" ", " ")
    return re.sub(r"\s+", " ", text)


def _number(value: str | None) -> float | None:
    if not value:
        return None
    raw = re.sub(r"\s+", "", str(value)).replace("%", "").replace("€", "").replace("−", "-").replace(",", ".")
    try:
        number = float(raw)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def normalize_signal(value: str | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper().replace("É", "E").replace("È", "E").replace("À", "A"))
    aliases = {
        "STRONG BUY": "STRONG_BUY",
        "ACHAT FORT": "STRONG_BUY",
        "ACHETER FORT": "STRONG_BUY",
        "BUY": "BUY",
        "ACHAT": "BUY",
        "ACHETER": "BUY",
        "RENFORCER": "BUY",
        "NEUTRAL": "NEUTRAL",
        "NEUTRE": "NEUTRAL",
        "HOLD": "NEUTRAL",
        "CONSERVER": "NEUTRAL",
        "SELL": "SELL",
        "VENTE": "SELL",
        "VENDRE": "SELL",
        "ALLEGER": "SELL",
        "STRONG SELL": "STRONG_SELL",
        "VENTE FORTE": "STRONG_SELL",
        "VENDRE FORT": "STRONG_SELL",
    }
    return aliases.get(text)


def _find_signal_near(text: str, label: str, radius: int = 220) -> str | None:
    signal_pattern = rf"({_INVESTING_SIGNAL_PATTERN}|Renforcer|Conserver|All[eé]ger)"
    for match in re.finditer(re.escape(label), text, flags=re.IGNORECASE):
        start = max(0, match.start() - radius)
        end = min(len(text), match.end() + radius)
        window = text[start:end]
        candidates = list(re.finditer(signal_pattern, window, flags=re.IGNORECASE))
        if not candidates:
            continue
        label_mid = match.start() - start + len(label) / 2.0
        nearest = min(candidates, key=lambda m: abs((m.start() + m.end()) / 2.0 - label_mid))
        normalized = normalize_signal(nearest.group(1))
        if normalized:
            return normalized
    return None


def _investing_timeframe_sequence(text: str) -> tuple[str | None, str | None]:
    """Parse Investing's explicit Daily/Weekly/Monthly summary sequence first.

    This prevents a proximity parser from swapping adjacent weekly and monthly
    values when the flattened page contains all three timeframe labels together.
    """
    pattern = re.compile(
        rf"(?:Daily|Journalier)\s+(?P<daily>{_INVESTING_SIGNAL_PATTERN})"
        rf".{{0,120}}?(?:Weekly|Hebdomadaire)\s+(?P<weekly>{_INVESTING_SIGNAL_PATTERN})"
        rf".{{0,120}}?(?:Monthly|Mensuel)\s+(?P<monthly>{_INVESTING_SIGNAL_PATTERN})",
        flags=re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None, None
    return normalize_signal(match.group("weekly")), normalize_signal(match.group("monthly"))


def extract_investing_technical(html: str) -> dict[str, str]:
    text = _clean_text(html)
    weekly, monthly = _investing_timeframe_sequence(text)
    if weekly is None:
        weekly = _find_signal_near(text, "Weekly") or _find_signal_near(text, "Hebdomadaire")
    if monthly is None:
        monthly = _find_signal_near(text, "Monthly") or _find_signal_near(text, "Mensuel")
    return {k: v for k, v in {"investing_weekly_signal": weekly, "investing_monthly_signal": monthly}.items() if v}


def _boursorama_note_bucket(note: float) -> tuple[str, str]:
    nearest=max(1,min(5,int(math.floor(note+0.5))))
    bucket=_BOURSORAMA_BUCKETS[nearest]
    signal="BUY" if nearest <= 2 else "NEUTRAL" if nearest == 3 else "SELL"
    return bucket,signal


def _latest_ints_between(text: str, start_pattern: str, end_pattern: str) -> int | None:
    match=re.search(start_pattern+r"(?P<body>.{0,220}?)"+end_pattern,text,flags=re.IGNORECASE)
    if not match:
        return None
    values=re.findall(r"\b\d{1,4}\b",match.group("body"))
    return int(values[-1]) if values else None


def _extract_boursorama_consensus_detail(text: str) -> dict[str, object]:
    out: dict[str, object]={}
    current_note=re.search(
        rf"Note m[eé]diane\*?\s+des analystes\s+au\s+\d{{2}}[./]\d{{2}}[./]\d{{4}}\s+({_NUM_TOKEN})",
        text,flags=re.IGNORECASE,
    )
    if current_note:
        note=_number(current_note.group(1))
        if note is not None and 1 <= note <= 5:
            bucket,signal=_boursorama_note_bucket(note)
            out["boursorama_consensus_note_median"]=note
            out["boursorama_consensus_bucket"]=bucket
            out["boursorama_consensus_signal"]=signal

    count_specs=(
        ("boursorama_acheter_n",r"1\.\s*Acheter",r"2\.\s*Renforcer"),
        ("boursorama_renforcer_n",r"2\.\s*Renforcer",r"3\.\s*Conserver"),
        ("boursorama_conserver_n",r"3\.\s*Conserver",r"4\.\s*All[eé]ger"),
        ("boursorama_alleger_n",r"4\.\s*All[eé]ger",r"5\.\s*Vendre"),
        ("boursorama_vendre_n",r"5\.\s*Vendre",r"Nombre d['’]analystes"),
        ("boursorama_analyst_count",r"Nombre d['’]analystes",r"Note m[eé]diane"),
    )
    for field,start,end in count_specs:
        value=_latest_ints_between(text,start,end)
        if value is not None:
            out[field]=value

    target_history=re.search(
        r"Historique des objectifs? de cours m[eé]dian\s*\(en\s*(EUR|USD|CHF|GBP|SEK|NOK|DKK|PLN|CZK)\)(?P<body>.{1,500}?)(?=Potentiel\s*:)",
        text,flags=re.IGNORECASE,
    )
    if target_history:
        values=[_number(value) for value in re.findall(_NUM_TOKEN,target_history.group("body"))]
        values=[value for value in values if value is not None]
        if values:
            out["boursorama_target_price"]=values[-1]
            out["boursorama_target_currency"]=target_history.group(1).upper()
    return out


def extract_boursorama_action(html: str) -> dict[str, object]:
    """Extract explicitly displayed Boursorama Action quote and consensus values."""
    text = _clean_text(html)
    out: dict[str, object] = _extract_boursorama_consensus_detail(text)

    if "boursorama_consensus_signal" not in out:
        median = re.search(
            r"Note m[eé]diane.{0,220}?(Acheter|Renforcer|Conserver|All[eé]ger|Vendre)",
            text,
            flags=re.IGNORECASE,
        )
        consensus = normalize_signal(median.group(1)) if median else _find_signal_near(text, "Consensus", radius=500)
        if consensus:
            out["boursorama_consensus_signal"] = consensus

    if "boursorama_target_price" not in out:
        target = re.search(
            rf"objectif(?: de cours)?(?:\s+\d+\s+mois)?(?: moyen| median| médian)?\s*:?\s*({_NUM_TOKEN})\s*(EUR|USD|CHF|GBP|SEK|NOK|DKK|PLN|CZK|€)",
            text,
            flags=re.IGNORECASE,
        )
        if target:
            value = _number(target.group(1))
            if value is not None:
                out["boursorama_target_price"] = value
                out["boursorama_target_currency"] = "EUR" if target.group(2) == "€" else target.group(2).upper()
    potential = re.search(rf"potentiel\s*:?\s*({_NUM_TOKEN})\s*%", text, flags=re.IGNORECASE)
    if potential:
        value = _number(potential.group(1))
        if value is not None:
            out["boursorama_target_upside_pct"] = value

    per_patterns = (
        rf"\bper\s+estim[eé]\s+\d{{4}}\s*(?:[^0-9+\-−]{{0,50}})?({_NUM_TOKEN})",
        rf"\bper\b(?!\s+estim[eé]\s+\d{{4}})[^0-9+\-−]{{0,30}}({_NUM_TOKEN})",
    )
    for pattern in per_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _number(match.group(1))
            if value is not None and -500 < value < 500:
                out["boursorama_per"] = value
                break

    yield_patterns = (
        rf"rendement\s+estim[eé]\s+\d{{4}}\s*(?:[^0-9+\-−]{{0,50}})?({_NUM_TOKEN})\s*%",
        rf"\brendement\b[^0-9+\-−]{{0,50}}({_NUM_TOKEN})\s*%",
    )
    for pattern in yield_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _number(match.group(1))
            if value is not None and 0 <= value <= 100:
                out["boursorama_dividend_yield_pct"] = value
                break

    one_year = re.search(
        rf"\b1\s+an\b\s*{_NUM_TOKEN}\s*%\s*({_NUM_TOKEN})\s+({_NUM_TOKEN})",
        text,
        flags=re.IGNORECASE,
    )
    if one_year:
        high = _number(one_year.group(1))
        low = _number(one_year.group(2))
        if high is not None and low is not None:
            out["boursorama_52w_high"] = high
            out["boursorama_52w_low"] = low
    else:
        patterns = {
            "boursorama_52w_high": (
                rf"(?:plus haut|haut)[^0-9+\-−]{{0,30}}(?:52 semaines|1 an)[^0-9+\-−]{{0,30}}({_NUM_TOKEN})",
                rf"(?:52 semaines|1 an)[^0-9+\-−]{{0,30}}(?:plus haut|haut)[^0-9+\-−]{{0,30}}({_NUM_TOKEN})",
            ),
            "boursorama_52w_low": (
                rf"(?:plus bas|bas)[^0-9+\-−]{{0,30}}(?:52 semaines|1 an)[^0-9+\-−]{{0,30}}({_NUM_TOKEN})",
                rf"(?:52 semaines|1 an)[^0-9+\-−]{{0,30}}(?:plus bas|bas)[^0-9+\-−]{{0,30}}({_NUM_TOKEN})",
            ),
        }
        for field, field_patterns in patterns.items():
            for pattern in field_patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    value = _number(match.group(1))
                    if value is not None:
                        out[field] = value
                        break
    return out


def _direct_boursorama_fallback(row: pd.Series) -> str | None:
    """Use only a verified deterministic Paris fallback; other venues use ISIN search."""
    ticker = str(row.get("yahoo_ticker", "") or "").strip().upper()
    if ticker.endswith(".PA") and len(ticker) > 3:
        return f"https://www.boursorama.com/cours/1rP{ticker[:-3]}/"
    return None


def _consensus_url(quote_url: str) -> str:
    if "/cours/consensus/" in quote_url:
        return quote_url
    if "/cours/" in quote_url:
        return quote_url.replace("/cours/", "/cours/consensus/", 1)
    return quote_url


def _investing_url_from_row(row: pd.Series) -> str | None:
    value = str(row.get("investing_url", "") or "").strip()
    if "investing.com" not in value or "/equities/" not in value:
        return None
    return value.split("?",1)[0].rstrip("/")


def _investing_search_candidates(html: str, *, isin: str, ticker: str, name: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    isin_u = isin.strip().upper()
    ticker_root = ticker.strip().upper().split(".",1)[0]
    name_u = re.sub(r"\s+", " ", name.strip().upper())
    candidates: dict[str, int] = {}
    for link in soup.find_all("a", href=True):
        href = str(link.get("href", "")).strip()
        if "/equities/" not in href or href.rstrip("/").endswith("-technical"):
            continue
        url = urljoin("https://www.investing.com", href.split("?",1)[0]).rstrip("/")
        context = " ".join(filter(None, [
            link.get_text(" ", strip=True),
            link.parent.get_text(" ", strip=True) if link.parent else "",
        ])).upper()
        score = 0
        if isin_u and isin_u in context:
            score += 100
        if ticker_root and re.search(rf"\b{re.escape(ticker_root)}\b", context):
            score += 35
        if name_u and len(name_u) >= 4 and name_u in context:
            score += 25
        candidates[url] = max(score, candidates.get(url, -1))
    return sorted(((score, url) for url, score in candidates.items()), reverse=True)


def _select_investing_candidate(candidates: list[tuple[int, str]], *, allow_unique_unscored: bool) -> str | None:
    if not candidates:
        return None
    if len(candidates) == 1 and (candidates[0][0] > 0 or allow_unique_unscored):
        return candidates[0][1]
    top_score = candidates[0][0]
    if top_score <= 0:
        return None
    top = [url for score, url in candidates if score == top_score]
    return top[0] if len(top) == 1 else None


def _discover_investing_url(row: pd.Series, requests, limiter: StartRateLimiter, headers: dict[str, str]) -> str | None:
    """Resolve an Investing equity URL without arbitrarily choosing an ADR/venue.

    Explicit URLs win. ISIN search may accept a single result even when the page
    omits identifying text. Ticker/name searches require a unique positive match;
    equal-scoring venue/ADR candidates are left unresolved instead of guessed.
    """
    direct = _investing_url_from_row(row)
    if direct:
        return direct
    isin = str(row.get("isin", "") or "").strip()
    ticker = str(row.get("yahoo_ticker", row.get("ticker", "")) or "").strip()
    name = str(row.get("name", "") or "").strip()
    queries = [
        (isin, True),
        (ticker.split(".",1)[0], False),
        (name, False),
    ]
    for query, is_isin_query in queries:
        if not query:
            continue
        try:
            limiter.wait()
            response = requests.get(f"https://www.investing.com/search/?q={quote_plus(query)}", timeout=20, headers=headers)
            if response.status_code >= 400:
                continue
            candidates = _investing_search_candidates(response.text, isin=isin, ticker=ticker, name=name)
            resolved = _select_investing_candidate(candidates, allow_unique_unscored=is_isin_query)
            if resolved:
                return resolved
        except Exception:
            continue
    return None


def _technical_url(base: str) -> str:
    clean = base.rstrip("/")
    if clean.endswith("-technical"):
        return clean
    return clean + "-technical"


def technical_alignment(weekly: str | None, monthly: str | None) -> str:
    w = _SIGNAL_SCORE.get(str(weekly or ""))
    m = _SIGNAL_SCORE.get(str(monthly or ""))
    if w is None or m is None:
        return "PARTIAL"
    if w > 0 and m > 0:
        return "CONFIRMS_LONG"
    if w < 0 and m < 0:
        return "CONTRADICTS_LONG"
    if w * m < 0:
        return "DIVERGENCE"
    return "MIXED_NEUTRAL"


def _shadow_confirmation(row: dict[str, object]) -> tuple[float | None, str]:
    values = [
        _SIGNAL_SCORE.get(str(row.get("investing_weekly_signal", ""))),
        _SIGNAL_SCORE.get(str(row.get("investing_monthly_signal", ""))),
        _SIGNAL_SCORE.get(str(row.get("boursorama_consensus_signal", ""))),
    ]
    observed = [v for v in values if v is not None]
    if not observed:
        return None, "NO_CONFIRMATION_DATA"
    average = sum(observed) / len(observed)
    score = max(0.0, min(100.0, 50.0 + 25.0 * average))
    if average >= 1.0:
        label = "CONFIRMS_LONG"
    elif average <= -1.0:
        label = "CONTRADICTS_LONG"
    elif max(observed) > 0 and min(observed) < 0:
        label = "DIVERGENCE"
    else:
        label = "MIXED_NEUTRAL"
    return round(score, 4), label


def _fetch_action(row: pd.Series, requests, limiter: StartRateLimiter) -> tuple[dict[str, object], list[dict]]:
    isin = str(row.get("isin", "") or "").strip()
    result: dict[str, object] = {"isin": isin}
    failures: list[dict] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.6.3; +data-quality)",
        "Accept-Language": "en-US,en;q=0.8,fr;q=0.6",
    }

    b_url = resolve_boursorama_url(row, requests, limiter, headers) or _direct_boursorama_fallback(row)
    if b_url:
        combined: dict[str, object] = {}
        for url in (b_url, _consensus_url(b_url)):
            try:
                limiter.wait()
                response = requests.get(url, timeout=20, headers=headers)
                if response.status_code >= 400:
                    failures.append({"isin": isin, "source": "Boursorama", "reason": f"HTTP_{response.status_code}"})
                    continue
                combined.update(extract_boursorama_action(response.text))
            except Exception as exc:
                failures.append({"isin": isin, "source": "Boursorama", "reason": type(exc).__name__, "detail": str(exc)[:160]})
        result.update(combined)
        result["boursorama_url"] = b_url
    else:
        failures.append({"isin": isin, "source": "Boursorama", "reason": "URL_NOT_RESOLVED"})

    i_url = _discover_investing_url(row, requests, limiter, headers)
    if i_url:
        tech_url = _technical_url(i_url)
        try:
            limiter.wait()
            response = requests.get(tech_url, timeout=20, headers=headers)
            if response.status_code >= 400:
                failures.append({"isin": isin, "source": "Investing", "reason": f"HTTP_{response.status_code}"})
            else:
                result.update(extract_investing_technical(response.text))
                result["investing_url"] = i_url
        except Exception as exc:
            failures.append({"isin": isin, "source": "Investing", "reason": type(exc).__name__, "detail": str(exc)[:160]})
    else:
        failures.append({"isin": isin, "source": "Investing", "reason": "URL_NOT_RESOLVED_OR_AMBIGUOUS"})

    result["investing_weekly_monthly_alignment"] = technical_alignment(
        result.get("investing_weekly_signal"), result.get("investing_monthly_signal")
    )
    score, confirmation = _shadow_confirmation(result)
    result["postselection_confirmation_score_shadow"] = score
    result["postselection_confirmation"] = confirmation
    result["postselection_data_status"] = "AVAILABLE" if score is not None or len(result) > 5 else "MISSING"
    return result, failures


def enrich_postselection(
    actions: pd.DataFrame,
    shortlisted_isins: set[str] | list[str],
    *,
    requests_module=None,
    observed_at: datetime | None = None,
    max_workers: int = 6,
    delay_seconds: float = 0.25,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Enrich shortlisted Actions only. Output is confirmation/shadow and never mutates a model decision."""
    import requests as requests_default

    requests = requests_module or requests_default
    wanted = {str(x).strip() for x in shortlisted_isins if str(x).strip()}
    subset = actions.loc[actions.get("isin", pd.Series("", index=actions.index)).astype(str).isin(wanted)].drop_duplicates("isin").copy()
    if subset.empty:
        return pd.DataFrame(columns=["isin"]), pd.DataFrame(columns=["isin", "source", "reason"])
    limiter = StartRateLimiter(delay_seconds)
    rows: list[dict[str, object]] = []
    failures: list[dict] = []
    workers = max(1, min(int(max_workers), len(subset)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_fetch_action, row, requests, limiter) for _, row in subset.iterrows()]
        for future in as_completed(futures):
            result, failed = future.result()
            rows.append(result)
            failures.extend(failed)
    stamp = (observed_at or datetime.now(timezone.utc)).isoformat()
    for row in rows:
        row["postselection_collected_at"] = stamp
        row["postselection_decision_influence"] = 0.0
    return pd.DataFrame(rows).sort_values("isin").reset_index(drop=True), pd.DataFrame(failures)
