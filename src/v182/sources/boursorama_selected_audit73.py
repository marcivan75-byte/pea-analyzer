from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
import json
from typing import Callable

import pandas as pd

from v182.sources import boursorama_selected as base
from v182.sources.boursorama_consensus_history import (
    current_and_revision,
    parse_factset_consensus_history,
)
from v182.sources.boursorama_public import action_urls, boursorama_code

BoursoramaSelectedResult = base.BoursoramaSelectedResult
_AUDIT73_FIELD = "audit73_consensus_history"
_AUDIT73_VERSION = "AUDIT73_BOURSORAMA_FACTSET_PIT_V1"


def _utc(value: datetime | None) -> datetime:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_payload(path: Path) -> dict:
    if not path.exists():
        return {"version": base.CACHE_VERSION, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"version": base.CACHE_VERSION, "entries": {}}
    if payload.get("version") != base.CACHE_VERSION or not isinstance(payload.get("entries"), dict):
        return {"version": base.CACHE_VERSION, "entries": {}}
    return payload


def _atomic_save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".audit73.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _consensus_url_map(rows: pd.DataFrame) -> dict[str, str]:
    result: dict[str, str] = {}
    if rows.empty or "isin" not in rows:
        return result
    for _, row in rows.drop_duplicates("isin").iterrows():
        isin = str(row.get("isin") or "").strip()
        code = boursorama_code(row, "ACTION") if isin else None
        if not isin or not code:
            continue
        result[action_urls(code)["consensus"]] = isin
    return result


def _merge_captures(cache_path: Path, captures: dict[str, list[dict]]) -> dict[str, int]:
    """Append real live captures after the base collector atomically saved its cache."""
    if not captures:
        return {"captures_appended": 0, "rows_appended": 0}
    payload = _load_payload(cache_path)
    entries: dict[str, dict] = payload["entries"]
    appended = 0
    rows_appended = 0
    for isin, capture_list in captures.items():
        entry = entries.get(isin)
        if not isinstance(entry, dict):
            # The base collector rejected/removed this identity: do not create an
            # Audit73-only entry that could masquerade as a usable current cache.
            continue
        history = list(entry.get(_AUDIT73_FIELD) or [])
        seen = {
            (str(item.get("captured_at_utc") or ""), str(item.get("consensus_sha256") or ""))
            for item in history if isinstance(item, dict)
        }
        for capture in capture_list:
            key = (str(capture["captured_at_utc"]), str(capture["consensus_sha256"]))
            if key in seen:
                continue
            history.append(capture)
            seen.add(key)
            appended += 1
            rows_appended += len(capture.get("rows") or [])
        history.sort(key=lambda item: (str(item.get("captured_at_utc") or ""), str(item.get("consensus_sha256") or "")))
        entry[_AUDIT73_FIELD] = history
        entry["audit73_history_version"] = _AUDIT73_VERSION
        entries[isin] = entry
    payload["audit73_history_version"] = _AUDIT73_VERSION
    payload["audit73_last_merge"] = {
        "captures_appended": appended,
        "rows_appended": rows_appended,
    }
    _atomic_save(cache_path, payload)
    return {"captures_appended": appended, "rows_appended": rows_appended}


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
    """Production-compatible selected collector with append-only Audit73 PIT history.

    The base collector remains the authority for current observations and stale
    fallback. Only successful live consensus HTTP responses can add an Audit73
    capture. Re-reading a stale cache never manufactures a new history point.
    """
    current = _utc(now)
    cache_file = Path(cache_path)
    url_to_isin = _consensus_url_map(rows)
    captures: dict[str, list[dict]] = {}
    capture_lock = Lock()
    delegate = fetcher or base._default_fetcher

    def audit73_fetch(url: str, *, timeout: float):
        response = delegate(url, timeout=timeout)
        isin = url_to_isin.get(url)
        if isin is None:
            return response
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        html = str(getattr(response, "text", "") or "")
        parsed = parse_factset_consensus_history(html, captured_at=current)
        if parsed:
            capture = {
                "captured_at_utc": current.isoformat(),
                "consensus_sha256": sha256(html.encode("utf-8", errors="replace")).hexdigest(),
                "rows": parsed,
            }
            with capture_lock:
                captures.setdefault(isin, []).append(capture)
        return response

    result = base.collect_selected_action_context_cached(
        rows,
        cache_file,
        dynamic_ttl_hours=dynamic_ttl_hours,
        deep_ttl_hours=deep_ttl_hours,
        refresh_budget=refresh_budget,
        request_start_interval_seconds=request_start_interval_seconds,
        timeout_seconds=timeout_seconds,
        max_workers=max_workers,
        fetcher=audit73_fetch,
        now=current,
    )
    merge = _merge_captures(cache_file, captures)
    metrics = dict(result.metrics)
    metrics.update({
        "audit73_pit_history_enabled": True,
        "audit73_captures_appended": merge["captures_appended"],
        "audit73_rows_appended": merge["rows_appended"],
        "audit73_relative_dates_fabricated": False,
    })
    return BoursoramaSelectedResult(result.observations, result.failures, metrics)


def load_audit73_pit_observations(
    cache_path: str | Path,
    *,
    symbol_by_isin: dict[str, str] | None = None,
) -> list[dict]:
    """Flatten preserved captures to the strict-PIT schema used by Audit73 study."""
    payload = _load_payload(Path(cache_path))
    out: list[dict] = []
    symbols = symbol_by_isin or {}
    for isin, entry in payload["entries"].items():
        if not isinstance(entry, dict):
            continue
        symbol = str(symbols.get(isin) or isin).strip().upper()
        for capture in entry.get(_AUDIT73_FIELD) or []:
            if not isinstance(capture, dict):
                continue
            rows = capture.get("rows") or []
            if not isinstance(rows, list):
                continue
            derived = current_and_revision(rows)
            if not derived or derived.get("available_at") is None:
                continue
            out.append({
                "isin": isin,
                "symbol": symbol,
                "available_at": derived.get("available_at"),
                "target_median": derived.get("target_median"),
                "consensus": derived.get("consensus"),
                "n_analysts": derived.get("n_analysts"),
                "consensus_delta_4w": derived.get("consensus_delta_4w"),
                "net_upgrades_30d": derived.get("net_upgrades_30d"),
                "period_kind": "CURRENT",
                "provider": "FactSet via Boursorama",
                "capture_sha256": capture.get("consensus_sha256"),
            })
    out.sort(key=lambda row: (str(row["available_at"]), str(row["symbol"]), str(row.get("capture_sha256") or "")))
    return out
