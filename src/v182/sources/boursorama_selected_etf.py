from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
from pathlib import Path
import json
import math
import re
from typing import Callable

from bs4 import BeautifulSoup
import pandas as pd

from v182.sources.boursorama_public import boursorama_code, etf_urls
from v182.sources.rate_limit import StartRateLimiter

CACHE_VERSION = "BOURSORAMA_SELECTED_ETF_V1"


@dataclass(frozen=True)
class BoursoramaSelectedETFResult:
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
    if parsed is None:
        return math.inf
    return max(0.0, (now - parsed).total_seconds() / 3600.0)


def _text(html: str) -> str:
    try:
        return " ".join(BeautifulSoup(html, "lxml").stripped_strings)
    except Exception:
        return ""


def _num(value: object) -> float | None:
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    text = re.sub(r"[^0-9,+.\- ]", "", text).replace(" ", "")
    if not text or text in {"-", "+"}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _capture(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else None


def _capture_num(text: str, pattern: str) -> float | None:
    value = _capture(text, pattern)
    return _num(value) if value is not None else None


def parse_etf_sheet_html(html: str) -> dict[str, object]:
    text = _text(html)
    if not text:
        return {}
    fields: dict[str, object] = {}
    numeric = {
        "boursorama_etf_theoretical_open": r"Ouverture th[eé]orique\s+([0-9\s,.]+)",
        "boursorama_etf_open": r"\bouverture\s+([0-9\s,.]+)",
        "boursorama_etf_previous_close": r"cl[oô]ture veille\s+([0-9\s,.]+)",
        "boursorama_etf_day_high": r"\+ haut\s+([0-9\s,.]+)",
        "boursorama_etf_day_low": r"\+ bas\s+([0-9\s,.]+)",
        "boursorama_etf_volume": r"\bvolume\s+([0-9\s]+)",
        "boursorama_etf_management_fee_pct": r"Frais de gestion maximum\s+([0-9\s,.]+)\s*%",
    }
    for field, pattern in numeric.items():
        value = _capture_num(text, pattern)
        if value is not None:
            fields[field] = value
    assets = re.search(r"Actif net \(EUR\)\s+([0-9\s,.]+)([KMB])?\s*/", text, flags=re.IGNORECASE)
    if assets:
        number = _num(assets.group(1))
        scale = {"K": 0.001, "M": 1.0, "B": 1000.0}.get((assets.group(2) or "M").upper(), 1.0)
        if number is not None:
            fields["boursorama_etf_aum_eur_m"] = number * scale
    strings = {
        "boursorama_etf_morningstar_category": r"cat[eé]gorie morningstar\s+(.+?)\s+(?:ouverture|cl[oô]ture veille|Date de cr[eé]ation|Forme juridique)",
        "boursorama_etf_management_company": r"Soci[eé]t[eé] de gestion\s+(.+?)\s+(?:G[eé]rants|Cat[eé]gorie morningstar)",
        "boursorama_etf_asset_class": r"Classe d'actifs\s+(.+?)\s+Zone g[eé]ographique",
        "boursorama_etf_geographic_zone": r"Zone g[eé]ographique\s+(.+?)\s+(?:Dividende|Affectation des r[eé]sultats)",
        "boursorama_etf_distribution_policy": r"Affectation des r[eé]sultats\s+(.+?)\s+R[eé]plication",
        "boursorama_etf_replication": r"R[eé]plication\s+(.+?)\s+(?:Frais d'entr[eé]e|Frais de gestion maximum)",
    }
    for field, pattern in strings.items():
        value = _capture(text, pattern)
        if value:
            fields[field] = value[:200]
    fields["boursorama_etf_pea_eligible_displayed"] = bool(
        re.search(r"\b[ÉE]ligibilit[eé].{0,250}\bPEA\b", text, flags=re.IGNORECASE)
    )
    return fields


def parse_etf_risk_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    try:
        tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return fields
    for frame in tables:
        headers = [str(col).upper() for col in frame.columns]
        joined = " ".join(headers)
        if "VOLATILITE" not in joined and "VOLATILIT" not in joined:
            continue
        if "BETA" not in joined or frame.empty:
            continue
        row = frame.iloc[0]
        mapping = {
            "VOLATIL": "boursorama_etf_volatility_1y_pct",
            "ALPHA": "boursorama_etf_alpha_1y",
            "R²": "boursorama_etf_r2_1y",
            "R2": "boursorama_etf_r2_1y",
            "BETA": "boursorama_etf_beta_1y",
        }
        for idx, header in enumerate(headers):
            out = next((field for token, field in mapping.items() if token in header), None)
            if out is None or idx >= len(row):
                continue
            value = _num(row.iloc[idx])
            if value is not None:
                fields[out] = value
        if fields:
            break
    return fields


def _load(path: Path) -> dict:
    if not path.exists():
        return {"version": CACHE_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": CACHE_VERSION, "entries": {}}
    if payload.get("version") != CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        return {"version": CACHE_VERSION, "entries": {}}
    return payload


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _default_fetcher(url: str, *, timeout: float):
    import requests

    return requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.15; selected-public-context)"},
        timeout=timeout,
    )


def collect_selected_etf_context_cached(
    rows: pd.DataFrame,
    cache_path: str | Path,
    *,
    dynamic_ttl_hours: float = 8.0,
    deep_ttl_hours: float = 168.0,
    refresh_budget: int = 40,
    request_start_interval_seconds: float = 1.0,
    timeout_seconds: float = 15.0,
    max_workers: int = 4,
    fetcher: Callable[..., object] | None = None,
    now: datetime | None = None,
) -> BoursoramaSelectedETFResult:
    current = (now or _now_utc()).astimezone(timezone.utc)
    cache_file = Path(cache_path)
    payload = _load(cache_file)
    entries: dict[str, dict] = payload["entries"]
    fetch = fetcher or _default_fetcher
    limiter = StartRateLimiter(request_start_interval_seconds)
    failures: list[dict] = []
    unique = rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame()
    work: list[tuple[str, str, bool, bool]] = []
    for _, row in unique.iterrows():
        isin = str(row.get("isin") or "").strip()
        code = boursorama_code(row, "ETF") if isin else None
        if not isin or not code:
            if isin:
                failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "NO_DETERMINISTIC_CODE"})
            continue
        entry = entries.get(isin, {})
        dynamic_due = _age_hours(entry.get("dynamic_fetched_at_utc"), current) >= dynamic_ttl_hours
        deep_due = _age_hours(entry.get("deep_fetched_at_utc"), current) >= deep_ttl_hours
        if dynamic_due or deep_due:
            work.append((isin, code, dynamic_due, deep_due))
    work = work[: max(0, int(refresh_budget))]

    def worker(item: tuple[str, str, bool, bool]) -> tuple[str, dict, list[dict]]:
        isin, code, dynamic_due, deep_due = item
        entry = dict(entries.get(isin, {}))
        urls = etf_urls(code)
        local_failures: list[dict] = []
        fields = dict(entry.get("fields") or {})
        if dynamic_due:
            try:
                limiter.wait()
                response = fetch(urls["composition"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                dynamic = parse_etf_sheet_html(html)
                if dynamic:
                    old = set(entry.get("dynamic_fields") or [])
                    for name in old:
                        fields.pop(name, None)
                    fields.update(dynamic)
                    entry["dynamic_fields"] = sorted(dynamic)
                    entry["dynamic_fetched_at_utc"] = current.isoformat()
                    entry["composition_url"] = urls["composition"]
                    entry["composition_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "NO_DYNAMIC_FIELDS", "url": urls["composition"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["composition"]})
        if deep_due:
            try:
                limiter.wait()
                response = fetch(urls["risk"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                deep = parse_etf_sheet_html(html)
                deep.update(parse_etf_risk_html(html))
                if deep:
                    old = set(entry.get("deep_fields") or [])
                    for name in old:
                        fields.pop(name, None)
                    fields.update(deep)
                    entry["deep_fields"] = sorted(deep)
                    entry["deep_fetched_at_utc"] = current.isoformat()
                    entry["risk_url"] = urls["risk"]
                    entry["risk_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "NO_DEEP_FIELDS", "url": urls["risk"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["risk"]})
        entry["status"] = "OK" if fields else "EMPTY"
        entry["boursorama_code"] = code
        entry["fields"] = fields
        return isin, entry, local_failures

    workers = max(1, min(int(max_workers), len(work))) if work else 0
    refreshed = 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="boursorama-selected-etf") as pool:
            futures = [pool.submit(worker, item) for item in work]
            for future in as_completed(futures):
                isin, entry, local_failures = future.result()
                entries[isin] = entry
                failures.extend(local_failures)
                if entry.get("status") == "OK":
                    refreshed += 1

    payload["updated_at_utc"] = current.isoformat()
    payload["policy"] = {
        "selected_only": True,
        "dynamic_ttl_hours": float(dynamic_ttl_hours),
        "deep_ttl_hours": float(deep_ttl_hours),
        "refresh_budget": int(refresh_budget),
        "request_start_interval_seconds": float(request_start_interval_seconds),
        "max_workers": int(max_workers),
        "raw_html_persisted": False,
        "priority_source": True,
    }
    _save(cache_file, payload)

    observations: list[dict] = []
    usable = 0
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip()
        entry = entries.get(isin)
        if not entry or entry.get("status") != "OK":
            continue
        usable += 1
        collected_at = entry.get("dynamic_fetched_at_utc") or entry.get("deep_fetched_at_utc")
        for field, value in dict(entry.get("fields") or {}).items():
            if value is None:
                continue
            observations.append({
                "isin": isin,
                "asset_class": "ETF",
                "horizon": str(row.get("horizon") or ""),
                "field": field,
                "value": value,
                "source": "Boursorama public priority ETF fiche",
                "source_url": entry.get("composition_url") or entry.get("risk_url"),
                "collected_at": collected_at,
                "validation_status": "POST_SELECTION_PRIORITY_CONTEXT",
            })
    return BoursoramaSelectedETFResult(
        observations=observations,
        failures=failures,
        metrics={
            "requested_rows": int(len(rows)),
            "unique_instruments": int(len(unique)),
            "refresh_requested": int(len(work)),
            "refresh_success": int(refreshed),
            "usable_rows": int(usable),
            "observations": int(len(observations)),
            "selected_only": True,
            "priority_source": True,
            "raw_html_persisted": False,
            "decision_influence": False,
            "score_influence": 0.0,
        },
    )
