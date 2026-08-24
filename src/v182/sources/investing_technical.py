from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import math
import re
import unicodedata
from typing import Callable
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.rate_limit import StartRateLimiter

INVESTING_BASE = "https://www.investing.com"
INVESTING_SEARCH_API = "https://api.investing.com/api/search/v2/search"
INVESTING_SEARCH_PAGE = f"{INVESTING_BASE}/search/"
CACHE_VERSION = "INVESTING_TECHNICAL_V2"
MAPPING_VERSION = "INVESTING_URL_MAP_V2"
SIGNAL_SCORE = {"STRONG_SELL": -2, "SELL": -1, "NEUTRAL": 0, "BUY": 1, "STRONG_BUY": 2}


@dataclass(frozen=True)
class InvestingResult:
    observations: list[dict]
    failures: list[dict]
    metrics: dict


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: object, now: datetime) -> float:
    parsed = _parse_utc(value)
    return math.inf if parsed is None else max(0.0, (now - parsed).total_seconds() / 3600.0)


def _visible_text(html: str) -> str:
    try:
        return " ".join(BeautifulSoup(html, "lxml").stripped_strings)
    except Exception:
        return ""


def _canon_signal(value: object) -> str | None:
    text = " ".join(str(value or "").strip().upper().replace("_", " ").split())
    return {
        "STRONG SELL": "STRONG_SELL", "SELL": "SELL", "NEUTRAL": "NEUTRAL", "BUY": "BUY", "STRONG BUY": "STRONG_BUY",
        "VENTE FORTE": "STRONG_SELL", "VENTE": "SELL", "NEUTRE": "NEUTRAL", "ACHAT": "BUY", "ACHAT FORT": "STRONG_BUY",
        "VENDI ADESSO": "STRONG_SELL", "VENDI": "SELL", "NEUTRALE": "NEUTRAL", "COMPRA": "BUY", "COMPRA ADESSO": "STRONG_BUY",
    }.get(text)


def parse_technical_summary_html(html: str) -> dict[str, object]:
    text = _visible_text(html)
    if not text:
        return {}
    state = r"(Strong\s+Sell|Strong\s+Buy|Sell|Buy|Neutral|Vente\s+Forte|Achat\s+Fort|Vente|Achat|Neutre|Vendi\s+Adesso|Compra\s+Adesso|Vendi|Compra|Neutrale)"
    labels = {
        "daily": r"(?:Daily|Journalier|Journali[eè]re|Giornaliero)",
        "weekly": r"(?:Weekly|Hebdomadaire|Settimanale)",
        "monthly": r"(?:Monthly|Mensuel|Mensile)",
    }
    fields: dict[str, object] = {}
    for timeframe, label in labels.items():
        signal = None
        for pattern in (rf"{label}\s*[:\-]?\s*{state}", rf"{label}.{{0,45}}?{state}"):
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                signal = _canon_signal(match.group(1))
                if signal:
                    break
        if signal:
            fields[f"investing_{timeframe}_signal"] = signal
            fields[f"investing_{timeframe}_score"] = SIGNAL_SCORE[signal]
    fields["investing_technical_complete"] = all(f"investing_{tf}_signal" in fields for tf in ("daily", "weekly", "monthly"))
    return fields if any(k.endswith("_signal") for k in fields) else {}


def horizon_signal(fields: dict[str, object], horizon: str) -> tuple[object | None, object | None]:
    tf = {"TCT": "daily", "CT": "weekly", "MT": "monthly"}.get(str(horizon or "").upper())
    if not tf:
        return None, None
    return fields.get(f"investing_{tf}_signal"), fields.get(f"investing_{tf}_score")


def _slug(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-+", "-", text)


def _clean_base_url(url: object) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        raw = urljoin(INVESTING_BASE, raw)
    if raw.startswith("https://fr.investing.com/"):
        raw = INVESTING_BASE + raw.split(".com", 1)[1]
    if not raw.startswith("https://www.investing.com/"):
        return None
    parts = urlsplit(raw)
    path = parts.path.rstrip("/")
    for suffix in ("-technical", "-scoreboard", "-chart", "-news", "-historical-data", "-candlestick"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit(("https", "www.investing.com", path, parts.query, ""))


def _scoreboard_url(base_url: str) -> str:
    parts = urlsplit(_clean_base_url(base_url) or base_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "-scoreboard", parts.query, ""))


def _technical_urls(base_url: str) -> list[str]:
    base = _clean_base_url(base_url) or base_url
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return list(dict.fromkeys([
        urlunsplit((parts.scheme, parts.netloc, path + "-technical", parts.query, "")),
        f"{INVESTING_BASE}/technical/{slug}-technical-analysis",
        base,
    ]))


def _asset_path_ok(url: str, asset_class: str) -> bool:
    p = urlsplit(url).path.lower()
    return "/etfs/" in p if str(asset_class).upper() == "ETF" else "/equities/" in p


def _candidate_base_urls(row: object) -> list[str]:
    get = row.get if hasattr(row, "get") else lambda key, default=None: default
    asset = str(get("asset_class", "ACTION") or "ACTION").upper()
    section = "etfs" if asset == "ETF" else "equities"
    out: list[str] = []
    for key in ("investing_url", "investing_technical_url"):
        u = _clean_base_url(get(key, ""))
        if u and _asset_path_ok(u, asset):
            out.append(u)
    for key in ("name", "long_name_yf"):
        s = _slug(get(key, ""))
        if s:
            out.append(f"{INVESTING_BASE}/{section}/{s}")
    ticker = str(get("yahoo_ticker", "") or "").strip().upper()
    if ticker:
        out.append(f"{INVESTING_BASE}/{section}/{ticker.split('.', 1)[0].lower()}")
    return list(dict.fromkeys(out))[:8]


def _validate_scoreboard(base_url: str, isin: str, *, fetcher: Callable[..., object], limiter: StartRateLimiter, timeout_seconds: float) -> tuple[str | None, str | None]:
    url = _scoreboard_url(base_url)
    limiter.wait()
    response = fetcher(url, timeout=timeout_seconds)
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    html = str(getattr(response, "text", "") or "")
    if str(isin).upper() not in html.upper():
        return None, None
    final = _clean_base_url(str(getattr(response, "url", url) or url)) or _clean_base_url(base_url)
    return final, sha256(html.encode("utf-8", errors="replace")).hexdigest()


def _api_candidates(row: object, isin: str, *, fetcher: Callable[..., object], limiter: StartRateLimiter, timeout_seconds: float) -> list[str]:
    url = f"{INVESTING_SEARCH_API}?q={quote_plus(str(isin).upper())}"
    try:
        limiter.wait(); response = fetcher(url, timeout=timeout_seconds)
        if hasattr(response, "raise_for_status"): response.raise_for_status()
        payload = response.json() if hasattr(response, "json") else json.loads(str(getattr(response, "text", "") or "{}"))
    except Exception:
        return []
    asset = str(row.get("asset_class", "ACTION") if hasattr(row, "get") else "ACTION").upper()
    out = []
    for q in payload.get("quotes", []) if isinstance(payload, dict) else []:
        if not isinstance(q, dict): continue
        u = _clean_base_url(q.get("url"))
        if u and _asset_path_ok(u, asset): out.append(u)
    return list(dict.fromkeys(out))[:12]


def _search_page_candidates(row: object, isin: str, *, fetcher: Callable[..., object], limiter: StartRateLimiter, timeout_seconds: float) -> list[str]:
    url = f"{INVESTING_SEARCH_PAGE}?q={quote_plus(str(isin).upper())}"
    try:
        limiter.wait(); response = fetcher(url, timeout=timeout_seconds)
        if hasattr(response, "raise_for_status"): response.raise_for_status()
        soup = BeautifulSoup(str(getattr(response, "text", "") or ""), "lxml")
    except Exception:
        return []
    asset = str(row.get("asset_class", "ACTION") if hasattr(row, "get") else "ACTION").upper()
    out = []
    for a in soup.find_all("a", href=True):
        u = _clean_base_url(a.get("href"))
        if u and _asset_path_ok(u, asset): out.append(u)
    return list(dict.fromkeys(out))[:20]


def _resolve_base_url(row: object, isin: str, *, fetcher: Callable[..., object], limiter: StartRateLimiter, timeout_seconds: float) -> tuple[str | None, dict]:
    groups = [
        ("PUBLIC_API_SEARCH_EXACT_ISIN", _api_candidates(row, isin, fetcher=fetcher, limiter=limiter, timeout_seconds=timeout_seconds)),
        ("PUBLIC_HTML_SEARCH_EXACT_ISIN", _search_page_candidates(row, isin, fetcher=fetcher, limiter=limiter, timeout_seconds=timeout_seconds)),
        ("DETERMINISTIC_SCOREBOARD_EXACT_ISIN", _candidate_base_urls(row)),
    ]
    seen = set()
    for method, candidates in groups:
        for candidate in candidates:
            if candidate in seen: continue
            seen.add(candidate)
            try:
                validated, proof = _validate_scoreboard(candidate, isin, fetcher=fetcher, limiter=limiter, timeout_seconds=timeout_seconds)
                if validated:
                    return validated, {"validation_method": method, "scoreboard_sha256": proof, "scoreboard_url": _scoreboard_url(candidate)}
            except Exception:
                continue
    return None, {}


def _load(path: Path, version: str) -> dict:
    if not path.exists(): return {"version": version, "entries": {}}
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {"version": version, "entries": {}}
    return data if data.get("version") == version and isinstance(data.get("entries"), dict) else {"version": version, "entries": {}}


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"); tmp.replace(path)


def _default_fetcher(url: str, *, timeout: float):
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.7", "Referer": f"{INVESTING_BASE}/", "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    return requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)


def collect_technical_context_cached(rows: pd.DataFrame, cache_path: str | Path, mapping_path: str | Path, *, refresh_budget: int = 40, ttl_hours: float = 6.0, request_start_interval_seconds: float = 1.0, timeout_seconds: float = 15.0, max_workers: int = 4, fetcher: Callable[..., object] | None = None, now: datetime | None = None) -> InvestingResult:
    current = (now or _now_utc()).astimezone(timezone.utc)
    cache_file, mapping_file = Path(cache_path), Path(mapping_path)
    cache, mappings = _load(cache_file, CACHE_VERSION), _load(mapping_file, MAPPING_VERSION)
    fetch = fetcher or _default_fetcher; limiter = StartRateLimiter(request_start_interval_seconds); failures: list[dict] = []
    unique = rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame()
    due = []
    for _, row in unique.iterrows():
        isin = str(row.get("isin") or "").strip().upper()
        if not isin: continue
        entry = cache["entries"].get(isin)
        if entry is None or _age_hours(entry.get("fetched_at_utc"), current) >= ttl_hours: due.append((isin, row))
    due = due[:max(0, int(refresh_budget))]

    def worker(item):
        isin, row = item; mapped = mappings["entries"].get(isin, {}); base_url = str(mapped.get("base_url") or "").strip()
        if base_url:
            try:
                base_url, _ = _validate_scoreboard(base_url, isin, fetcher=fetch, limiter=limiter, timeout_seconds=timeout_seconds)
                if not base_url: mappings["entries"].pop(isin, None)
            except Exception:
                base_url = None; mappings["entries"].pop(isin, None)
        if not base_url:
            base_url, resolution = _resolve_base_url(row, isin, fetcher=fetch, limiter=limiter, timeout_seconds=timeout_seconds)
            if not base_url: return isin, None, {"isin": isin, "source": "Investing.com", "reason": "NO_VALIDATED_PUBLIC_URL"}
            mappings["entries"][isin] = {"base_url": base_url, "validated_isin": isin, "resolved_at_utc": current.isoformat(), **resolution}
        attempts = []
        for technical_url in _technical_urls(base_url):
            try:
                limiter.wait(); response = fetch(technical_url, timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"): response.raise_for_status()
                html = str(getattr(response, "text", "") or ""); fields = parse_technical_summary_html(html)
                attempts.append({"url": technical_url, "parsed": bool(fields), "complete": bool(fields.get("investing_technical_complete")) if fields else False})
                if fields.get("investing_technical_complete"):
                    return isin, {"fetched_at_utc": current.isoformat(), "source_url": technical_url, "technical_url": technical_url, "overview_url": base_url, "fields": fields, "page_sha256": sha256(html.encode("utf-8", errors="replace")).hexdigest()}, None
            except Exception as exc:
                attempts.append({"url": technical_url, "error": type(exc).__name__, "detail": str(exc)[:100]})
        return isin, None, {"isin": isin, "source": "Investing.com", "reason": "NO_COMPLETE_TECHNICAL_SUMMARY", "attempts": attempts[:3], "base_url": base_url}

    success = 0; workers = max(1, min(int(max_workers), len(due))) if due else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="investing-selected") as pool:
            for future in as_completed([pool.submit(worker, item) for item in due]):
                isin, entry, failure = future.result()
                if failure: failures.append(failure)
                else: cache["entries"][isin] = entry; success += 1
    cache["updated_at_utc"] = current.isoformat(); cache["policy"] = {"selected_only": True, "refresh_budget": int(refresh_budget), "ttl_hours": float(ttl_hours), "raw_html_persisted": False, "decision_influence": False, "entry_exit_governance_influence": True, "url_validation_method": "EXACT_ISIN_SCOREBOARD", "technical_complete_required": True}
    mappings["updated_at_utc"] = current.isoformat(); _save(cache_file, cache); _save(mapping_file, mappings)
    observations = []; usable = 0
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip().upper(); entry = cache["entries"].get(isin)
        if not entry or _age_hours(entry.get("fetched_at_utc"), current) > max(ttl_hours * 4.0, 48.0): continue
        fields = dict(entry.get("fields") or {})
        if not fields.get("investing_technical_complete"): continue
        usable += 1; signal, score = horizon_signal(fields, str(row.get("horizon") or ""))
        if signal is not None: fields["investing_horizon_signal"] = signal; fields["investing_horizon_score"] = score
        fields["investing_public_url"] = entry.get("overview_url"); fields["investing_technical_url"] = entry.get("technical_url")
        for field, value in fields.items():
            if value is None: continue
            observations.append({"isin": isin, "asset_class": str(row.get("asset_class") or ""), "horizon": str(row.get("horizon") or ""), "field": field, "value": value, "source": "Investing.com public technical summary", "source_url": entry.get("technical_url"), "collected_at": entry.get("fetched_at_utc"), "validation_status": "POST_SELECTION_ENTRY_EXIT_CONTEXT"})
    reasons = {}
    for f in failures:
        reasons[f.get("reason", "UNKNOWN")] = reasons.get(f.get("reason", "UNKNOWN"), 0) + 1
    return InvestingResult(observations, failures, {"requested_rows": int(len(rows)), "unique_instruments": int(len(unique)), "refresh_requested": int(len(due)), "refresh_success": int(success), "usable_rows": int(usable), "observations": int(len(observations)), "selected_only": True, "decision_influence": False, "score_influence": 0.0, "entry_exit_governance_influence": True, "technical_complete_required": True, "cache_version": CACHE_VERSION, "failure_reasons": reasons})
