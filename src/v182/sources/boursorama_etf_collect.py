from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
from typing import Callable

import pandas as pd

from v182.sources.boursorama_etf_sheet import parse_etf_risk_html, parse_etf_sheet_html
from v182.sources.boursorama_public import boursorama_code, etf_urls
from v182.sources.boursorama_selected_etf import (
    CACHE_VERSION,
    BoursoramaSelectedETFResult,
    _age_hours,
    _now_utc,
    merge_ms_sri_fields,
    palmares_search_url,
    parse_etf_morningstar_sri_html,
    parse_etf_palmares_rows,
)
from v182.sources.rate_limit import StartRateLimiter


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


def _ms_incomplete(fields: dict) -> bool:
    return fields.get("boursorama_etf_morningstar_parse_status") != "OK" or fields.get(
        "boursorama_etf_sri_parse_status"
    ) != "OK"


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
        if entry and entry.get("boursorama_code") != code:
            failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "CACHE_IDENTITY_CHANGED_REFRESH_REQUIRED"})
            entries.pop(isin, None)
            entry = {}
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
            for name in set(entry.get("dynamic_fields") or []):
                fields.pop(name, None)
            entry["dynamic_fields"] = []
            try:
                limiter.wait()
                response = fetch(urls["course"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                dynamic = parse_etf_sheet_html(html)
                if dynamic:
                    fields = merge_ms_sri_fields(fields, dynamic)
                    fields.update({k: v for k, v in dynamic.items() if not str(k).startswith(("boursorama_etf_morningstar", "boursorama_etf_sri"))})
                    entry["dynamic_fields"] = sorted(dynamic)
                    entry["dynamic_fetched_at_utc"] = current.isoformat()
                    entry["course_url"] = urls["course"]
                    entry["composition_url"] = urls["course"]
                    digest = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                    entry["course_sha256"] = digest
                    entry["composition_sha256"] = digest
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "NO_DYNAMIC_FIELDS", "url": urls["course"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["course"]})
        if deep_due:
            for name in set(entry.get("deep_fields") or []):
                if not str(name).startswith(("boursorama_etf_morningstar", "boursorama_etf_sri")):
                    fields.pop(name, None)
            entry["deep_fields"] = []
            try:
                limiter.wait()
                response = fetch(urls["risk"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                deep = parse_etf_sheet_html(html)
                deep.update(parse_etf_risk_html(html))
                if deep:
                    fields = merge_ms_sri_fields(fields, parse_etf_morningstar_sri_html(html))
                    fields.update({k: v for k, v in deep.items() if not str(k).startswith(("boursorama_etf_morningstar", "boursorama_etf_sri"))})
                    entry["deep_fields"] = sorted(deep)
                    entry["deep_fetched_at_utc"] = current.isoformat()
                    entry["risk_url"] = urls["risk"]
                    entry["risk_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": "NO_DEEP_FIELDS", "url": urls["risk"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama ETF", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["risk"]})
        if _ms_incomplete(fields):
            palmares_url = palmares_search_url(isin)
            try:
                limiter.wait()
                response = fetch(palmares_url, timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                mapped = parse_etf_palmares_rows(html)
                hit = mapped.get(code) or next(iter(mapped.values()), None) if len(mapped) == 1 else mapped.get(code)
                if hit:
                    fields = merge_ms_sri_fields(fields, hit)
                    entry["palmares_url"] = palmares_url
                    entry["palmares_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama ETF palmares", "reason": "NO_PALMARES_ROW", "url": palmares_url})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama ETF palmares", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": palmares_url})
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
        "morningstar_rating_promoted": False,
        "shadow_ms_sri": True,
        "palmares_fallback": True,
    }
    _save(cache_file, payload)
    observations: list[dict] = []
    usable = 0
    for _, row in rows.iterrows():
        isin = str(row.get("isin") or "").strip()
        entry = entries.get(isin)
        expected_code = boursorama_code(row, "ETF") if isin else None
        if not entry or entry.get("status") != "OK" or entry.get("boursorama_code") != expected_code:
            continue
        usable += 1
        dynamic_fields = set(entry.get("dynamic_fields") or [])
        for field, value in dict(entry.get("fields") or {}).items():
            if value is None:
                continue
            is_dynamic = field in dynamic_fields
            collected_at = entry.get("dynamic_fetched_at_utc") if is_dynamic else entry.get("deep_fetched_at_utc")
            if str(field).startswith("boursorama_etf_morningstar") or str(field).startswith("boursorama_etf_sri"):
                source_url = entry.get("palmares_url") or entry.get("course_url") or entry.get("composition_url")
                page_sha256 = entry.get("palmares_sha256") or entry.get("course_sha256") or entry.get("composition_sha256")
            else:
                source_url = entry.get("composition_url") if is_dynamic else entry.get("risk_url")
                page_sha256 = entry.get("composition_sha256") if is_dynamic else entry.get("risk_sha256")
            observations.append({
                "isin": isin,
                "asset_class": "ETF",
                "horizon": str(row.get("horizon") or ""),
                "field": field,
                "value": value,
                "source": "Boursorama public priority ETF fiche",
                "source_url": source_url,
                "collected_at": collected_at,
                "page_sha256": page_sha256,
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
            "morningstar_rating_promoted": False,
            "palmares_fallback": True,
        },
    )
