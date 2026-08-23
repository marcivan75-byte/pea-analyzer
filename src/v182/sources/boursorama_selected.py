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
import unicodedata
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

CACHE_VERSION = "BOURSORAMA_SELECTED_V2"
BOURSORAMA_BASE = "https://www.boursorama.com"


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
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _first_num(value: object) -> float | None:
    """Parse only the primary number from cells such as '6,34 EUR 9%'."""
    text = str(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
    match = re.search(r"[-+]?\d{1,3}(?: \d{3})+(?:[,.]\d+)?|[-+]?\d+(?:[,.]\d+)?", text)
    return _num(match.group(0)) if match else None


def _match_num(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return _num(match.group(1)) if match else None


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(text.replace("\xa0", " ").split())


def parse_quote_context_html(html: str) -> dict[str, object]:
    text = _text(html)
    if not text:
        return {}
    fields: dict[str, object] = {}
    patterns = {
        "boursorama_last_price": r"derni[eè]re valeur\s+([0-9\s,.]+)",
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
    if "boursorama_last_price" in fields and "boursorama_previous_close" in fields and fields["boursorama_previous_close"]:
        fields["boursorama_perf_1d_pct"] = (fields["boursorama_last_price"] / fields["boursorama_previous_close"] - 1.0) * 100.0
    sector = re.search(r"\bsecteur\s+(.+?)\s+Indice de r[eé]f[eé]rence", text, flags=re.IGNORECASE)
    if sector:
        fields["boursorama_sector"] = " ".join(sector.group(1).split())[:160]
    last_dividend = re.search(r"dernier dividende.*?([0-9]+(?:[,.][0-9]+)?)\s*EUR\s*\((\d{2}/\d{2}/\d{2,4})\)", text, flags=re.IGNORECASE)
    if last_dividend:
        fields["boursorama_last_dividend_eur"] = _num(last_dividend.group(1))
        fields["boursorama_last_dividend_date"] = last_dividend.group(2)
    next_dividend = re.search(r"prochain dividende.*?([0-9]+(?:[,.][0-9]+)?)\s*EUR.*?(\d{2}/\d{2}/\d{2,4})", text, flags=re.IGNORECASE)
    if next_dividend:
        fields["boursorama_next_dividend_eur"] = _num(next_dividend.group(1))
        fields["boursorama_next_dividend_date"] = next_dividend.group(2)
    fields["boursorama_pea_eligible_displayed"] = bool(re.search(r"\b[ÉE]ligibilit[eé].{0,250}\bPEA\b", text, flags=re.IGNORECASE))
    return fields


def _tables(html: str) -> list[pd.DataFrame]:
    try:
        return pd.read_html(StringIO(html), decimal=",", thousands=" ")
    except (ValueError, ImportError):
        return []


def parse_forward_forecasts_html(html: str) -> dict[str, object]:
    """Parse FactSet estimate tables without concatenating growth percentages into values."""
    fields: dict[str, object] = {}
    wanted = {
        "benefice net par action": ("eps", ""),
        "per": ("per", ""),
        "dividende par action": ("dividend", ""),
        "rendement": ("yield", "_pct"),
        "chiffre d'affaires": ("revenue_m", ""),
        "ebitda": ("ebitda_m", ""),
        "ebit": ("ebit_m", ""),
        "dette financiere nette": ("net_debt_m", ""),
        "actif net par action": ("book_value_per_share", ""),
        "cash flow par action": ("cash_flow_per_share", ""),
    }
    for frame in _tables(html):
        if frame.empty or frame.shape[1] < 2:
            continue
        headers = [str(col) for col in frame.columns]
        year_columns: dict[int, int] = {}
        for idx, header in enumerate(headers):
            match = re.search(r"20\d{2}", header)
            if match:
                year_columns[int(match.group(0))] = idx
        if not year_columns:
            continue
        for ridx in range(len(frame)):
            label = _norm(frame.iloc[ridx, 0])
            matched = next((spec for key, spec in wanted.items() if key in label), None)
            if matched is None:
                continue
            kind, suffix = matched
            for year, cidx in year_columns.items():
                value = _first_num(frame.iloc[ridx, cidx])
                if value is not None:
                    fields[f"boursorama_{kind}_est_{year}{suffix}"] = value
    return fields


def parse_consensus_revision_context_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    weights = {"acheter": 5.0, "renforcer": 4.0, "conserver": 3.0, "alleger": 2.0, "vendre": 1.0}
    for frame in _tables(html):
        if frame.empty or frame.shape[1] < 4:
            continue
        labels = [_norm(v) for v in frame.iloc[:, 0].tolist()]
        if not any("nombre d'analystes" in label for label in labels) or not any("acheter" in label for label in labels):
            continue
        headers = [_norm(c) for c in frame.columns]
        current_idx = frame.shape[1] - 1
        idx_7d = next((i for i, h in enumerate(headers) if "7 jours" in h), None)
        idx_1m = next((i for i, h in enumerate(headers) if "1 mois" in h), None)

        def counts_at(cidx: int | None) -> dict[str, float]:
            result: dict[str, float] = {}
            if cidx is None:
                return result
            for ridx, label in enumerate(labels):
                key = next((name for name in weights if name in label), None)
                if key:
                    value = _first_num(frame.iloc[ridx, cidx])
                    if value is not None:
                        result[key] = value
            return result

        def score(counts: dict[str, float]) -> float | None:
            total = sum(counts.values())
            return sum(weights[k] * v for k, v in counts.items()) / total if total else None

        def row_value(needle: str, cidx: int | None) -> float | None:
            if cidx is None:
                return None
            ridx = next((i for i, label in enumerate(labels) if needle in label), None)
            return _first_num(frame.iloc[ridx, cidx]) if ridx is not None else None

        current_counts = counts_at(current_idx)
        current_score = score(current_counts)
        current_n = row_value("nombre d'analystes", current_idx)
        current_target = row_value("objectif", current_idx)
        total = sum(current_counts.values())
        current_buy_ratio = ((current_counts.get("acheter", 0.0) + current_counts.get("renforcer", 0.0)) / total) if total else None
        current_sell_ratio = ((current_counts.get("alleger", 0.0) + current_counts.get("vendre", 0.0)) / total) if total else None
        for suffix, cidx in (("7d", idx_7d), ("1m", idx_1m)):
            old_counts = counts_at(cidx)
            old_score = score(old_counts)
            old_n = row_value("nombre d'analystes", cidx)
            old_target = row_value("objectif", cidx)
            old_total = sum(old_counts.values())
            old_buy = ((old_counts.get("acheter", 0.0) + old_counts.get("renforcer", 0.0)) / old_total) if old_total else None
            old_sell = ((old_counts.get("alleger", 0.0) + old_counts.get("vendre", 0.0)) / old_total) if old_total else None
            if current_score is not None and old_score is not None:
                fields[f"boursorama_consensus_delta_{suffix}"] = current_score - old_score
            if current_n is not None and old_n is not None:
                fields[f"boursorama_analyst_count_delta_{suffix}"] = current_n - old_n
            if current_target is not None and old_target is not None:
                fields[f"boursorama_target_price_delta_{suffix}"] = current_target - old_target
                if old_target:
                    fields[f"boursorama_target_price_delta_{suffix}_pct"] = (current_target / old_target - 1.0) * 100.0
            if current_buy_ratio is not None and old_buy is not None:
                fields[f"boursorama_buy_ratio_delta_{suffix}"] = current_buy_ratio - old_buy
            if current_sell_ratio is not None and old_sell is not None:
                fields[f"boursorama_sell_ratio_delta_{suffix}"] = current_sell_ratio - old_sell
        break
    return fields


def parse_course_performance_html(html: str) -> dict[str, object]:
    fields: dict[str, object] = {}
    mapping = {
        "1er janvier": "ytd",
        "1 semaine": "1w",
        "1 mois": "1m",
        "3 mois": "3m",
        "6 mois": "6m",
        "1 an": "1y",
        "3 ans": "3y",
        "5 ans": "5y",
        "10 ans": "10y",
    }
    for frame in _tables(html):
        if frame.empty or frame.shape[1] < 2:
            continue
        labels = [_norm(v) for v in frame.iloc[:, 0].tolist()]
        if not any(label in mapping for label in labels):
            continue
        for ridx, label in enumerate(labels):
            if label in mapping:
                tag = mapping[label]
                perf = _first_num(frame.iloc[ridx, 1])
                if perf is not None:
                    fields[f"boursorama_perf_{tag}_pct"] = perf
                if frame.shape[1] >= 4:
                    high = _first_num(frame.iloc[ridx, 2])
                    low = _first_num(frame.iloc[ridx, 3])
                    if high is not None:
                        fields[f"boursorama_period_high_{tag}"] = high
                    if low is not None:
                        fields[f"boursorama_period_low_{tag}"] = low
            elif label in {"mm20", "mm50", "mm100", "rsi14"}:
                values = [_first_num(frame.iloc[ridx, c]) for c in range(1, frame.shape[1])]
                value = next((v for v in reversed(values) if v is not None), None)
                if value is not None:
                    fields[f"boursorama_{label}"] = value
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
    return requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; PEA-Analyzer/21.16; selected-public-context)"}, timeout=timeout)


def _family_failure_active(entry: dict, family: str, now: datetime, retry_ttl_hours: float) -> bool:
    return _age_hours(entry.get(f"{family}_last_failed_at_utc"), now) < max(0.0, float(retry_ttl_hours))


def collect_selected_action_context_cached(
    rows: pd.DataFrame,
    cache_path: str | Path,
    *,
    dynamic_ttl_hours: float = 8.0,
    performance_ttl_hours: float = 72.0,
    deep_ttl_hours: float = 336.0,
    failed_refresh_retry_ttl_hours: float = 2.0,
    refresh_budget: int = 40,
    request_start_interval_seconds: float = 1.0,
    timeout_seconds: float = 15.0,
    max_workers: int = 4,
    fetcher: Callable[..., object] | None = None,
    limiter: StartRateLimiter | None = None,
    allow_network: bool = True,
    now: datetime | None = None,
) -> BoursoramaSelectedResult:
    current = (now or _now_utc()).astimezone(timezone.utc)
    cache_file = Path(cache_path)
    payload = _load(cache_file)
    entries: dict[str, dict] = payload["entries"]
    fetch = fetcher or _default_fetcher
    rate_limiter = limiter or StartRateLimiter(request_start_interval_seconds)
    failures: list[dict] = []
    unique = rows.drop_duplicates("isin").copy() if "isin" in rows else pd.DataFrame()
    cooldown = {"dynamic": 0, "performance": 0, "deep": 0}
    work: list[tuple[str, str, bool, bool, bool]] = []
    for _, row in unique.iterrows():
        isin = str(row.get("isin") or "").strip()
        code = boursorama_code(row, "ACTION") if isin else None
        if not isin or not code:
            if isin:
                failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DETERMINISTIC_CODE"})
            continue
        entry = entries.get(isin, {})
        stale = {
            "dynamic": _age_hours(entry.get("dynamic_fetched_at_utc"), current) >= dynamic_ttl_hours,
            "performance": _age_hours(entry.get("performance_fetched_at_utc"), current) >= performance_ttl_hours,
            "deep": _age_hours(entry.get("deep_fetched_at_utc"), current) >= deep_ttl_hours,
        }
        due: dict[str, bool] = {}
        for family in ("dynamic", "performance", "deep"):
            blocked = stale[family] and _family_failure_active(entry, family, current, failed_refresh_retry_ttl_hours)
            if blocked:
                cooldown[family] += 1
            due[family] = bool(allow_network and stale[family] and not blocked)
        if any(due.values()):
            work.append((isin, code, due["dynamic"], due["performance"], due["deep"]))
    work = work[: max(0, int(refresh_budget))]

    def worker(item: tuple[str, str, bool, bool, bool]) -> tuple[str, dict, list[dict], int, bool]:
        isin, code, dynamic_due, performance_due, deep_due = item
        entry = dict(entries.get(isin, {}))
        urls = action_urls(code)
        consensus_url = urls["consensus"]
        key_url = urls["key_figures"]
        course_url = f"{BOURSORAMA_BASE}/cours/{code}/"
        local_failures: list[dict] = []
        fields = dict(entry.get("fields") or {})
        state_changed = False
        family_successes = 0

        def clear_failure(family: str) -> None:
            nonlocal state_changed
            for key in (f"{family}_last_failed_at_utc", f"{family}_failure_reason", f"{family}_failure_count"):
                if key in entry:
                    entry.pop(key, None)
                    state_changed = True

        def mark_failure(family: str, reason: str) -> None:
            nonlocal state_changed
            entry[f"{family}_last_failed_at_utc"] = current.isoformat()
            entry[f"{family}_failure_reason"] = reason
            entry[f"{family}_failure_count"] = min(9999, int(entry.get(f"{family}_failure_count") or 0) + 1)
            state_changed = True

        def replace_family(name: str, family: str, fresh: dict[str, object], fetched_key: str, url_key: str, url: str, hash_key: str, html: str) -> None:
            nonlocal state_changed, family_successes
            old = set(entry.get(name) or [])
            for field in old:
                fields.pop(field, None)
            fields.update(fresh)
            entry[name] = sorted(fresh)
            entry[fetched_key] = current.isoformat()
            entry[url_key] = url
            entry[hash_key] = sha256(html.encode("utf-8", errors="replace")).hexdigest()
            clear_failure(family)
            state_changed = True
            family_successes += 1

        if dynamic_due:
            try:
                rate_limiter.wait()
                response = fetch(consensus_url, timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                dynamic = parse_action_consensus_html(html)
                dynamic.update(parse_quote_context_html(html))
                dynamic.update(parse_forward_forecasts_html(html))
                dynamic.update(parse_consensus_revision_context_html(html))
                if dynamic:
                    replace_family("dynamic_fields", "dynamic", dynamic, "dynamic_fetched_at_utc", "consensus_url", consensus_url, "consensus_sha256", html)
                else:
                    mark_failure("dynamic", "NO_DYNAMIC_FIELDS")
                    local_failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DYNAMIC_FIELDS", "url": consensus_url})
            except Exception as exc:
                reason = type(exc).__name__
                mark_failure("dynamic", reason)
                local_failures.append({"isin": isin, "source": "Boursorama", "reason": reason, "detail": str(exc)[:160], "url": consensus_url})
        if performance_due:
            try:
                rate_limiter.wait()
                response = fetch(course_url, timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                performance = parse_course_performance_html(html)
                performance.update(parse_quote_context_html(html))
                if performance:
                    replace_family("performance_fields", "performance", performance, "performance_fetched_at_utc", "course_url", course_url, "course_sha256", html)
                else:
                    mark_failure("performance", "NO_PERFORMANCE_FIELDS")
                    local_failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_PERFORMANCE_FIELDS", "url": course_url})
            except Exception as exc:
                reason = type(exc).__name__
                mark_failure("performance", reason)
                local_failures.append({"isin": isin, "source": "Boursorama", "reason": reason, "detail": str(exc)[:160], "url": course_url})
        if deep_due:
            try:
                rate_limiter.wait()
                response = fetch(key_url, timeout=timeout_seconds)
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                html = str(getattr(response, "text", "") or "")
                deep = parse_action_key_figures_html(html)
                if deep:
                    replace_family("deep_fields", "deep", deep, "deep_fetched_at_utc", "key_figures_url", key_url, "key_figures_sha256", html)
                else:
                    mark_failure("deep", "NO_DEEP_FIELDS")
                    local_failures.append({"isin": isin, "source": "Boursorama", "reason": "NO_DEEP_FIELDS", "url": key_url})
            except Exception as exc:
                reason = type(exc).__name__
                mark_failure("deep", reason)
                local_failures.append({"isin": isin, "source": "Boursorama", "reason": reason, "detail": str(exc)[:160], "url": key_url})
        entry["status"] = "OK" if fields else "EMPTY"
        entry["boursorama_code"] = code
        entry["fields"] = fields
        return isin, entry, local_failures, family_successes, state_changed

    refreshed_instruments = 0
    refreshed_families = 0
    changed_any = False
    workers = max(1, min(int(max_workers), len(work))) if work else 0
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="boursorama-selected") as pool:
            futures = [pool.submit(worker, item) for item in work]
            for future in as_completed(futures):
                isin, entry, local_failures, successes, state_changed = future.result()
                entries[isin] = entry
                failures.extend(local_failures)
                refreshed_instruments += int(successes > 0)
                refreshed_families += int(successes)
                changed_any = changed_any or state_changed
    if changed_any:
        payload["updated_at_utc"] = current.isoformat()
        payload["policy"] = {
            "selected_only": True,
            "dynamic_ttl_hours": dynamic_ttl_hours,
            "performance_ttl_hours": performance_ttl_hours,
            "deep_ttl_hours": deep_ttl_hours,
            "failed_refresh_retry_ttl_hours": failed_refresh_retry_ttl_hours,
            "refresh_budget": refresh_budget,
            "request_start_interval_seconds": request_start_interval_seconds,
            "max_workers": max_workers,
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
        families = [
            (set(entry.get("dynamic_fields") or []), entry.get("dynamic_fetched_at_utc"), entry.get("consensus_url")),
            (set(entry.get("performance_fields") or []), entry.get("performance_fetched_at_utc"), entry.get("course_url")),
            (set(entry.get("deep_fields") or []), entry.get("deep_fetched_at_utc"), entry.get("key_figures_url")),
        ]
        fields = dict(entry.get("fields") or {})
        for field, value in fields.items():
            if value is None:
                continue
            fetched = None
            source_url = None
            for names, ts, url in families:
                if field in names:
                    fetched, source_url = ts, url
                    break
            observations.append({"isin": isin, "asset_class": "ACTION", "horizon": str(row.get("horizon") or ""), "field": field, "value": value, "source": "Boursorama public priority fiche", "source_url": source_url, "collected_at": fetched, "validation_status": "POST_SELECTION_PRIORITY_CONTEXT"})
        metadata = {
            "boursorama_dynamic_age_hours": _age_hours(entry.get("dynamic_fetched_at_utc"), current),
            "boursorama_performance_age_hours": _age_hours(entry.get("performance_fetched_at_utc"), current),
            "boursorama_deep_age_hours": _age_hours(entry.get("deep_fetched_at_utc"), current),
        }
        for field, value in metadata.items():
            observations.append({"isin": isin, "asset_class": "ACTION", "horizon": str(row.get("horizon") or ""), "field": field, "value": value, "source": "Boursorama cache metadata", "source_url": entry.get("consensus_url"), "collected_at": current.isoformat(), "validation_status": "SOURCE_FRESHNESS_METADATA"})

    return BoursoramaSelectedResult(
        observations,
        failures,
        {
            "cache_version": CACHE_VERSION,
            "requested_rows": int(len(rows)),
            "unique_instruments": int(len(unique)),
            "refresh_requested": int(len(work)),
            "refresh_success": int(refreshed_instruments),
            "families_refreshed": int(refreshed_families),
            "failure_cooldown_skipped": {key: int(value) for key, value in cooldown.items()},
            "failed_refresh_retry_ttl_hours": float(failed_refresh_retry_ttl_hours),
            "usable_rows": int(usable),
            "observations": int(len(observations)),
            "selected_only": True,
            "priority_source": True,
            "network_allowed": bool(allow_network),
            "cache_write_performed": bool(changed_any),
            "raw_html_persisted": False,
            "decision_influence": False,
            "score_influence": 0.0,
        },
    )
