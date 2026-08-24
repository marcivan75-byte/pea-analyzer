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

from v182.sources.boursorama_public import (
    action_urls,
    boursorama_code,
    parse_action_consensus_html,
    parse_action_key_figures_html,
)
from v182.sources.rate_limit import StartRateLimiter

CACHE_VERSION = "BOURSORAMA_SELECTED_V1"


@dataclass(frozen=True)
class BoursoramaSelectedResult:
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
        value_f = float(text)
    except ValueError:
        return None
    return value_f if math.isfinite(value_f) else None


def _match_num(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _num(match.group(1)) if match else None


def parse_quote_context_html(html: str) -> dict[str, object]:
    """Extract factual quote/fundamental context exposed on a Boursorama instrument sheet."""
    text = _text(html)
    if not text:
        return {}
    fields: dict[str, object] = {}
    patterns = {
        "boursorama_open": r"\bouverture\s+([0-9\s,.]+)",
        "boursorama_previous_close": r"\bcl[oô]ture veille\s+([0-9\s,.]+)",
        "boursorama_day_high": r"\+ haut\s+([0-9\s,.]+)",
        "boursorama_day_low": r"\+ bas\s+([0-9\s,.]+)",
        "boursorama_volume": r"\bvolume\s+([0-9\s]+)",
        "boursorama_market_cap_meur": r"\bvalorisation\s+([0-9\s,.]+)\s*MEUR",
        "boursorama_estimated_yield_pct": r"rendement estim[eé]\s+\d{4}\s+([0-9\s,.]+)%",
        "boursorama_estimated_per": r"PER estim[eé]\s+\d{4}.*?([0-9]+(?:[,.][0-9]+)?)",
        "boursorama_esg_risk_score": r"Risque ESG.*?([0-9]+(?:[,.][0-9]+)?)/100",
    }
    for field, pattern in patterns.items():
        value = _match_num(text, pattern)
        if value is not None:
            fields[field] = value
    sector = re.search(r"\bsecteur\s+(.+?)\s+Indice de r[eé]f[eé]rence", text, flags=re.IGNORECASE)
    if sector:
        fields["boursorama_sector"] = " ".join(sector.group(1).split())[:160]
    last_dividend = re.search(r"dernier dividende.*?([0-9]+(?:[,.][0-9]+)?)\s*EUR\s*\((\d{2}/\d{2}/\d{2,4})\)", text, flags=re.IGNORECASE)
    if last_dividend:
        fields["boursorama_last_dividend_eur"] = _num(last_dividend.group(1))
        fields["boursorama_last_dividend_date"] = last_dividend.group(2)
    fields["boursorama_pea_eligible_displayed"] = bool(re.search(r"\b[ÉE]ligibilit[eé].{0,250}\bPEA\b", text, flags=re.IGNORECASE))
    return fields


def _table_labels(frame: pd.DataFrame) -> list[str]:
    if frame.empty:
        return []
    return [" ".join(str(value).lower().replace("\xa0", " ").split()) for value in frame.iloc[:, 0].tolist()]


def parse_forward_forecasts_html(html: str) -> dict[str, object]:
    """Extract the public FactSet 2026/2027 forecast rows without renaming them as canonical model fields."""
    fields: dict[str, object] = {}
    try:
        tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return fields
    for frame in tables:
        labels = _table_labels(frame)
        if not labels or frame.shape[1] < 3:
            continue
        wanted = {
            "dividende par action": "dividend",
            "rendement": "yield",
            "bénéfice net par action": "eps",
            "benefice net par action": "eps",
            "per": "per",
        }
        if not any(any(key in label for key in wanted) for label in labels):
            continue
        headers = [" ".join(str(col).lower().split()) for col in frame.columns]
        idx_2026 = next((i for i, col in enumerate(headers) if "2026" in col), None)
        idx_2027 = next((i for i, col in enumerate(headers) if "2027" in col), None)
        if idx_2026 is None and frame.shape[1] >= 3:
            idx_2026 = frame.shape[1] - 2
        if idx_2027 is None and frame.shape[1] >= 2:
            idx_2027 = frame.shape[1] - 1
        for ridx, label in enumerate(labels):
            kind = next((mapped for key, mapped in wanted.items() if key in label), None)
            if kind is None:
                continue
            for year, cidx in ((2026, idx_2026), (2027, idx_2027)):
                if cidx is None or cidx >= frame.shape[1]:
                    continue
                value = _num(frame.iloc[ridx, cidx])
                if value is not None:
                    suffix = "_pct" if kind == "yield" else ""
                    fields[f"boursorama_{kind}_est_{year}{suffix}"] = value
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


def collect_selected_action_context_cached(
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
) -> BoursoramaSelectedResult:
    """Priority enrichment for already-selected Action titles only.

    The consensus/quote page has a short TTL. Chiffres-clés use a longer TTL.
    This restores the historical two-stage contract: score first, enrich shortlist second.
    """
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
        code = boursorama_code(row, "ACTION") if isin else None
        if not isin or not code:
            if isin:
                failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DETERMINISTIC_CODE"})
            continue
        entry = entries.get(isin, {})
        if entry and entry.get("boursorama_code") != code:
            failures.append({"isin": isin, "source": "Boursorama", "reason": "CACHE_IDENTITY_CHANGED_REFRESH_REQUIRED"})
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
        urls = action_urls(code)
        local_failures: list[dict] = []
        fields = dict(entry.get("fields") or {})
        if dynamic_due:
            for name in set(entry.get("dynamic_fields") or []):
                fields.pop(name, None)
            entry["dynamic_fields"] = []
            try:
                limiter.wait()
                response = fetch(urls["consensus"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                dynamic = parse_action_consensus_html(html)
                dynamic.update(parse_quote_context_html(html))
                dynamic.update(parse_forward_forecasts_html(html))
                if dynamic:
                    fields.update(dynamic)
                    entry["dynamic_fields"] = sorted(dynamic)
                    entry["dynamic_fetched_at_utc"] = current.isoformat()
                    entry["consensus_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                    entry["consensus_url"] = urls["consensus"]
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DYNAMIC_FIELDS", "url": urls["consensus"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["consensus"]})
        if deep_due:
            for name in set(entry.get("deep_fields") or []):
                fields.pop(name, None)
            entry["deep_fields"] = []
            try:
                limiter.wait()
                response = fetch(urls["key_figures"], timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                deep = parse_action_key_figures_html(html)
                deep.update(parse_quote_context_html(html))
                if deep:
                    fields.update(deep)
                    entry["deep_fields"] = sorted(deep)
                    entry["deep_fetched_at_utc"] = current.isoformat()
                    entry["key_figures_sha256"] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
                    entry["key_figures_url"] = urls["key_figures"]
                else:
                    local_failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DEEP_FIELDS", "url": urls["key_figures"]})
            except Exception as exc:
                local_failures.append({"isin": isin, "source": "Boursorama", "reason": type(exc).__name__, "detail": str(exc)[:160], "url": urls["key_figures"]})
        entry["status"] = "OK" if fields else "EMPTY"
        entry["boursorama_code"] = code
        entry["fields"] = fields
        return isin, entry, local_failures

    workers = max(1, min(int(max_workers), len(work))) if work else 0
    refreshed = 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="boursorama-selected") as pool:
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
        expected_code = boursorama_code(row, "ACTION") if isin else None
        if not entry or entry.get("status") != "OK" or entry.get("boursorama_code") != expected_code:
            continue
        usable += 1
        dynamic_fields = set(entry.get("dynamic_fields") or [])
        for field, value in dict(entry.get("fields") or {}).items():
            if value is None:
                continue
            is_dynamic = field in dynamic_fields
            collected_at = entry.get("dynamic_fetched_at_utc") if is_dynamic else entry.get("deep_fetched_at_utc")
            source_url = entry.get("consensus_url") if is_dynamic else entry.get("key_figures_url")
            page_sha256 = entry.get("consensus_sha256") if is_dynamic else entry.get("key_figures_sha256")
            observations.append({
                "isin": isin,
                "asset_class": "ACTION",
                "horizon": str(row.get("horizon") or ""),
                "field": field,
                "value": value,
                "source": "Boursorama public priority fiche",
                "source_url": source_url,
                "collected_at": collected_at,
                "page_sha256": page_sha256,
                "validation_status": "POST_SELECTION_PRIORITY_CONTEXT",
            })

    return BoursoramaSelectedResult(
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
