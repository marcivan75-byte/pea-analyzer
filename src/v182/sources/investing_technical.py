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
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.rate_limit import StartRateLimiter

INVESTING_BASE = "https://www.investing.com"
CACHE_VERSION = "INVESTING_TECHNICAL_V1"
MAPPING_VERSION = "INVESTING_URL_MAP_V1"
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


def _canon_signal(value: str) -> str | None:
    text = " ".join(str(value or "").strip().upper().replace("_", " ").split())
    return {
        "STRONG SELL": "STRONG_SELL",
        "SELL": "SELL",
        "NEUTRAL": "NEUTRAL",
        "BUY": "BUY",
        "STRONG BUY": "STRONG_BUY",
        "VENTE FORTE": "STRONG_SELL",
        "VENTE": "SELL",
        "NEUTRE": "NEUTRAL",
        "ACHAT": "BUY",
        "ACHAT FORT": "STRONG_BUY",
        "VENDI ADESSO": "STRONG_SELL",
        "VENDI": "SELL",
        "NEUTRALE": "NEUTRAL",
        "COMPRA": "BUY",
        "COMPRA ADESSO": "STRONG_BUY",
    }.get(text)


def parse_technical_summary_html(html: str) -> dict[str, object]:
    text = _visible_text(html)
    if not text:
        return {}
    state = r"(Strong Sell|Strong Buy|Sell|Buy|Neutral|Vente Forte|Achat Fort|Vente|Achat|Neutre|Vendi Adesso|Compra Adesso|Vendi|Compra|Neutrale)"
    patterns = {
        "daily": rf"(?:Daily|Journalier|Giornaliero)\s+{state}",
        "weekly": rf"(?:Weekly|Hebdomadaire|Settimanale)\s+{state}",
        "monthly": rf"(?:Monthly|Mensuel|Mensile)\s+{state}",
    }
    fields: dict[str, object] = {}
    for timeframe, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        signal = _canon_signal(match.group(1)) if match else None
        if signal:
            fields[f"investing_{timeframe}_signal"] = signal
            fields[f"investing_{timeframe}_score"] = SIGNAL_SCORE[signal]
    if all(f"investing_{tf}_signal" in fields for tf in ("daily", "weekly", "monthly")):
        fields["investing_technical_complete"] = True
    return fields


def horizon_signal(fields: dict[str, object], horizon: str) -> tuple[object | None, object | None]:
    tf = {"TCT": "daily", "CT": "weekly", "MT": "monthly"}.get(str(horizon or "").upper())
    return (None, None) if tf is None else (fields.get(f"investing_{tf}_signal"), fields.get(f"investing_{tf}_score"))


def _slug(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"\b(sa|se|nv|ag|plc|spa|s\.p\.a|inc|ltd|limited)\b", " ", text).replace("&", " and ")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text).strip("-"))


def _ticker_market_slug(ticker: str) -> str | None:
    ticker = str(ticker or "").strip().upper()
    if "." not in ticker:
        return None
    base, suffix = ticker.rsplit(".", 1)
    market = {"PA": "paris", "AS": "amsterdam", "BR": "brussels", "MC": "madrid", "MI": "milan", "DE": "xetra", "LS": "lisbon"}.get(suffix)
    return f"{base.lower()}-{market}" if market and re.fullmatch(r"[A-Z0-9-]{1,20}", base) else None


def _safe_investing_url(url: str, *, allow_technical: bool = True) -> bool:
    """Allow only public Investing instrument pages used by this collector."""
    try:
        parsed = urlparse(str(url or ""))
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.hostname not in {"www.investing.com", "fr.investing.com"}:
        return False
    path = parsed.path.rstrip("/")
    suffix = r"(?:-technical)?" if allow_technical else ""
    return bool(re.fullmatch(rf"/(?:equities|etfs)/[A-Za-z0-9._%\-]+{suffix}", path))


def _candidate_base_urls(row: object) -> list[str]:
    getter = row.get if hasattr(row, "get") else lambda key, default=None: default
    asset = str(getter("asset_class", "ACTION") or "ACTION").upper()
    section = "etfs" if asset == "ETF" else "equities"
    urls: list[str] = []
    explicit = str(getter("investing_url", "") or getter("investing_technical_url", "") or "").strip()
    if explicit and _safe_investing_url(explicit):
        base = explicit.split("?", 1)[0].rstrip("/")
        base = base[: -len("-technical")] if base.endswith("-technical") else base
        path = urlparse(base).path
        urls.append(f"{INVESTING_BASE}{path}")
    ticker_market = _ticker_market_slug(str(getter("yahoo_ticker", "") or ""))
    if asset == "ETF" and ticker_market:
        urls.append(f"{INVESTING_BASE}/{section}/{ticker_market}")
    for key in ("name", "long_name_yf"):
        candidate = _slug(getter(key, ""))
        if candidate:
            urls.append(f"{INVESTING_BASE}/{section}/{candidate}")
    if asset != "ETF" and ticker_market:
        urls.append(f"{INVESTING_BASE}/{section}/{ticker_market}")
    return list(dict.fromkeys(urls))[:4]


def _load(path: Path, version: str) -> dict:
    if not path.exists():
        return {"version": version, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": version, "entries": {}}
    return data if data.get("version") == version and isinstance(data.get("entries"), dict) else {"version": version, "entries": {}}


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _default_fetcher(url: str, *, timeout: float):
    import requests

    return requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.16; selected-public-context)",
            "Accept-Language": "en-US,en;q=0.8",
        },
        timeout=timeout,
        allow_redirects=True,
    )


def _resolve_base_url(
    row: object,
    isin: str,
    *,
    fetcher: Callable[..., object],
    limiter: StartRateLimiter,
    timeout_seconds: float,
) -> tuple[str | None, str | None, int]:
    attempts = 0
    for url in _candidate_base_urls(row):
        try:
            attempts += 1
            limiter.wait()
            response = fetcher(url, timeout=timeout_seconds)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            html = str(getattr(response, "text", "") or "")
            if isin and isin.upper() not in html.upper():
                continue
            final = str(getattr(response, "url", url) or url).split("?", 1)[0].rstrip("/")
            final = final[: -len("-technical")] if final.endswith("-technical") else final
            if not _safe_investing_url(final, allow_technical=False):
                continue
            return final, sha256(html.encode("utf-8", errors="replace")).hexdigest(), attempts
        except Exception:
            continue
    return None, None, attempts


def _unresolved_cooldown_active(mapping: dict, current: datetime, retry_ttl_hours: float) -> bool:
    return (
        str(mapping.get("status") or "").upper() == "UNRESOLVED"
        and _age_hours(mapping.get("last_failed_at_utc"), current) < max(0.0, float(retry_ttl_hours))
    )


def collect_technical_context_cached(
    rows: pd.DataFrame,
    cache_path: str | Path,
    mapping_path: str | Path,
    *,
    refresh_budget: int = 40,
    ttl_hours: float = 6.0,
    unmapped_retry_ttl_hours: float = 24.0,
    request_start_interval_seconds: float = 1.0,
    timeout_seconds: float = 15.0,
    max_workers: int = 4,
    fetcher: Callable[..., object] | None = None,
    allow_network: bool = True,
    now: datetime | None = None,
) -> InvestingResult:
    current = (now or _now_utc()).astimezone(timezone.utc)
    cache_file = Path(cache_path)
    mapping_file = Path(mapping_path)
    cache = _load(cache_file, CACHE_VERSION)
    mappings = _load(mapping_file, MAPPING_VERSION)
    fetch = fetcher or _default_fetcher
    limiter = StartRateLimiter(request_start_interval_seconds)
    failures: list[dict] = []
    mapping_changed = False
    cache_changed = False
    cooldown_skipped = 0

    unique = rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame()
    due: list[tuple[str, object]] = []
    for _, row in unique.iterrows():
        isin = str(row.get("isin") or "").strip()
        if not isin or not allow_network:
            continue
        entry = cache["entries"].get(isin)
        if entry is not None and _age_hours(entry.get("fetched_at_utc"), current) < ttl_hours:
            continue
        mapping = mappings["entries"].get(isin, {})
        base_url = str(mapping.get("base_url") or "").strip()
        if not _safe_investing_url(base_url, allow_technical=False) and _unresolved_cooldown_active(mapping, current, unmapped_retry_ttl_hours):
            cooldown_skipped += 1
            continue
        due.append((isin, row))
    due = due[: max(0, int(refresh_budget))]

    def worker(item: tuple[str, object]):
        isin, row = item
        previous_mapping = dict(mappings["entries"].get(isin, {}) or {})
        base_url = str(previous_mapping.get("base_url") or "").strip()
        resolved_now = False
        mapping_entry: dict | None = None
        if not _safe_investing_url(base_url, allow_technical=False):
            base_url, overview_hash, attempts = _resolve_base_url(
                row,
                isin,
                fetcher=fetch,
                limiter=limiter,
                timeout_seconds=timeout_seconds,
            )
            if not base_url:
                failure_count = min(9999, int(previous_mapping.get("failure_count") or 0) + 1)
                mapping_entry = {
                    "status": "UNRESOLVED",
                    "last_failed_at_utc": current.isoformat(),
                    "reason": "NO_VALIDATED_PUBLIC_URL",
                    "failure_count": failure_count,
                    "last_candidate_attempts": int(attempts),
                }
                return isin, mapping_entry, None, {
                    "isin": isin,
                    "source": "Investing.com",
                    "reason": "NO_VALIDATED_PUBLIC_URL",
                    "candidate_attempts": int(attempts),
                }
            resolved_now = True
            mapping_entry = {
                "status": "RESOLVED",
                "base_url": base_url,
                "validated_isin": isin,
                "resolved_at_utc": current.isoformat(),
                "overview_sha256": overview_hash,
            }

        technical_url = base_url.rstrip("/") + "-technical"
        try:
            limiter.wait()
            response = fetch(technical_url, timeout=timeout_seconds)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            final_url = str(getattr(response, "url", technical_url) or technical_url).split("?", 1)[0].rstrip("/")
            if not _safe_investing_url(final_url, allow_technical=True) or not final_url.endswith("-technical"):
                raise ValueError("UNSAFE_OR_NON_TECHNICAL_REDIRECT")
            html = str(getattr(response, "text", "") or "")
            fields = parse_technical_summary_html(html)
            if not fields:
                return isin, mapping_entry if resolved_now else None, None, {
                    "isin": isin,
                    "source": "Investing.com",
                    "reason": "NO_TECHNICAL_SUMMARY",
                    "url": technical_url,
                }
            cache_entry = {
                "fetched_at_utc": current.isoformat(),
                "source_url": final_url,
                "fields": fields,
                "page_sha256": sha256(html.encode("utf-8", errors="replace")).hexdigest(),
            }
            return isin, mapping_entry if resolved_now else None, cache_entry, None
        except Exception as exc:
            return isin, mapping_entry if resolved_now else None, None, {
                "isin": isin,
                "source": "Investing.com",
                "reason": type(exc).__name__,
                "detail": str(exc)[:160],
                "url": technical_url,
            }

    success = 0
    workers = max(1, min(int(max_workers), len(due))) if due else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="investing-selected") as pool:
            futures = [pool.submit(worker, item) for item in due]
            for future in as_completed(futures):
                isin, mapping_entry, cache_entry, failure = future.result()
                if mapping_entry is not None:
                    mappings["entries"][isin] = mapping_entry
                    mapping_changed = True
                if cache_entry is not None:
                    cache["entries"][isin] = cache_entry
                    cache_changed = True
                    success += 1
                if failure:
                    failures.append(failure)

    if cache_changed:
        cache["updated_at_utc"] = current.isoformat()
        cache["policy"] = {
            "selected_only": True,
            "refresh_budget": int(refresh_budget),
            "ttl_hours": float(ttl_hours),
            "request_start_interval_seconds": float(request_start_interval_seconds),
            "max_workers": int(max_workers),
            "raw_html_persisted": False,
            "decision_influence": False,
        }
        _save(cache_file, cache)
    if mapping_changed:
        mappings["updated_at_utc"] = current.isoformat()
        mappings["policy"] = {
            "unmapped_retry_ttl_hours": float(unmapped_retry_ttl_hours),
            "negative_cache_is_temporary": True,
            "permanent_blacklist": False,
            "raw_html_persisted": False,
        }
        _save(mapping_file, mappings)

    observations: list[dict] = []
    usable = 0
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip()
        entry = cache["entries"].get(isin)
        age = _age_hours(entry.get("fetched_at_utc"), current) if entry else math.inf
        if not entry or age > max(ttl_hours * 8.0, 96.0):
            continue
        usable += 1
        fields = dict(entry.get("fields") or {})
        signal, score = horizon_signal(fields, str(row.get("horizon") or ""))
        if signal is not None:
            fields["investing_horizon_signal"] = signal
            fields["investing_horizon_score"] = score
        fields["investing_age_hours"] = age
        for field, value in fields.items():
            metadata = field == "investing_age_hours"
            observations.append(
                {
                    "isin": isin,
                    "asset_class": str(row.get("asset_class") or ""),
                    "horizon": str(row.get("horizon") or ""),
                    "field": field,
                    "value": value,
                    "source": "Investing cache metadata" if metadata else "Investing.com public technical summary",
                    "source_url": entry.get("source_url"),
                    "collected_at": entry.get("fetched_at_utc"),
                    "validation_status": "SOURCE_FRESHNESS_METADATA" if metadata else "POST_SELECTION_CONTEXT_ONLY",
                }
            )

    return InvestingResult(
        observations,
        failures,
        {
            "requested_rows": int(len(rows)),
            "unique_instruments": int(len(unique)),
            "live_refresh_requested": int(len(due)),
            "live_refresh_success": int(success),
            "resolution_cooldown_skipped": int(cooldown_skipped),
            "unmapped_retry_ttl_hours": float(unmapped_retry_ttl_hours),
            "usable_rows": int(usable),
            "observations": int(len(observations)),
            "selected_only": True,
            "network_allowed": bool(allow_network),
            "cache_write_performed": bool(cache_changed),
            "mapping_write_performed": bool(mapping_changed),
            "negative_cache_is_temporary": True,
            "permanent_blacklist": False,
            "decision_influence": False,
            "score_influence": 0.0,
            "raw_html_persisted": False,
        },
    )
