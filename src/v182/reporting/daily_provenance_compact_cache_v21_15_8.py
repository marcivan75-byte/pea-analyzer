from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json

import pandas as pd

from v182.audit import provenance


VERSION = "DAILY_PROVENANCE_COMPACT_CACHE_V21_15_8"
ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "state" / "provenance" / "daily_compact_cache_v1"
META = CACHE_DIR / "manifest.json"
RETAINED = CACHE_DIR / "retained.parquet"
EVENTS = CACHE_DIR / "latest_events.parquet"
SOURCES = CACHE_DIR / "sources_by_field.parquet"


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _code_contract() -> str:
    digest = sha256()
    for path in (Path(provenance.__file__).resolve(), Path(__file__).resolve()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_meta() -> dict:
    if not META.exists():
        return {}
    try:
        payload = json.loads(META.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_persisted(path: Path, stats: dict):
    started = perf_counter()
    meta = _read_meta()
    if meta.get("version") != VERSION or meta.get("code_contract") != _code_contract():
        stats["load_status"] = "MISS_CONTRACT"
        return None
    if not all(p.exists() for p in (RETAINED, EVENTS, SOURCES)):
        stats["load_status"] = "MISS_FILES"
        return None
    try:
        ledger_size = int(path.stat().st_size)
    except OSError:
        stats["load_status"] = "MISS_LEDGER"
        return None
    if int(meta.get("ledger_size", -1)) != ledger_size:
        stats["load_status"] = "MISS_SIZE"
        return None

    digest_started = perf_counter()
    ledger_sha = _sha256_file(path)
    stats["ledger_hash_seconds"] = round(perf_counter() - digest_started, 6)
    if not ledger_sha or ledger_sha != meta.get("ledger_sha256"):
        stats["load_status"] = "MISS_HASH"
        return None
    if meta.get("retained_sha256") != _sha256_file(RETAINED) or meta.get("events_sha256") != _sha256_file(EVENTS) or meta.get("sources_sha256") != _sha256_file(SOURCES):
        stats["load_status"] = "MISS_CACHE_HASH"
        return None

    try:
        retained = pd.read_parquet(RETAINED)
        events = pd.read_parquet(EVENTS)
        sources = pd.read_parquet(SOURCES)
    except Exception:
        stats["load_status"] = "MISS_READ_ERROR"
        return None
    if not set(provenance.COLUMNS).issubset(retained.columns) or not {"isin", "field"}.issubset(events.columns):
        stats["load_status"] = "MISS_SCHEMA"
        return None
    if int(meta.get("retained_rows", -1)) != len(retained) or int(meta.get("event_rows", -1)) != len(events):
        stats["load_status"] = "MISS_ROWCOUNT"
        return None

    signature = provenance._file_signature(path)
    entry = provenance._RetainedCacheEntry(
        signature=signature,
        latest=retained,
        latest_map=provenance._latest_mapping(retained),
        retained_rows_map=provenance._retained_rows_mapping(retained),
        latest_event_map=provenance._latest_event_mapping(events),
        sources_by_field=sources,
    )
    stats["load_status"] = "HIT_EXACT"
    stats["retained_rows"] = int(len(retained))
    stats["event_rows"] = int(len(events))
    stats["load_seconds"] = round(perf_counter() - started, 6)
    return entry


def install() -> tuple[callable, dict]:
    original = provenance._latest_entry_for_path
    stats = {
        "version": VERSION,
        "load_status": "NOT_USED",
        "exact_ledger_hash_required": True,
        "code_contract_required": True,
        "fallback_full_scan": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }

    def compact_latest_entry(path: Path):
        p = Path(path)
        key = provenance._cache_key(p)
        signature = provenance._file_signature(p)
        with provenance._CACHE_LOCK:
            cached = provenance._LATEST_RETAINED_CACHE.get(key)
            if cached is not None and cached.signature == signature:
                return cached
        loaded = _load_persisted(p, stats)
        if loaded is not None:
            with provenance._CACHE_LOCK:
                provenance._LATEST_RETAINED_CACHE[key] = loaded
            return loaded
        stats["fallback_full_scan_used"] = True
        return original(p)

    provenance._latest_entry_for_path = compact_latest_entry
    return original, stats


def restore(original) -> None:
    provenance._latest_entry_for_path = original


def persist(stats: dict, path: Path | None = None) -> dict:
    started = perf_counter()
    p = Path(path) if path is not None else provenance.provenance_path()
    entry = provenance._latest_entry_for_path(p)
    retained = provenance._retained_frame(entry).copy()
    events_records = list(entry.latest_event_map.values())
    events = pd.DataFrame.from_records(events_records, columns=provenance.COLUMNS) if events_records else pd.DataFrame(columns=provenance.COLUMNS)
    sources = entry.sources_by_field.copy(deep=True) if entry.sources_by_field is not None else provenance._aggregate_sources(retained)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    retained_tmp = RETAINED.with_suffix(".parquet.tmp")
    events_tmp = EVENTS.with_suffix(".parquet.tmp")
    sources_tmp = SOURCES.with_suffix(".parquet.tmp")
    retained.to_parquet(retained_tmp, index=False)
    events.to_parquet(events_tmp, index=False)
    sources.to_parquet(sources_tmp, index=False)
    retained_tmp.replace(RETAINED)
    events_tmp.replace(EVENTS)
    sources_tmp.replace(SOURCES)

    ledger_hash_started = perf_counter()
    ledger_sha = _sha256_file(p)
    hash_seconds = perf_counter() - ledger_hash_started
    payload = {
        "version": VERSION,
        "validated": True,
        "code_contract": _code_contract(),
        "ledger_size": int(p.stat().st_size) if p.exists() else 0,
        "ledger_sha256": ledger_sha,
        "retained_rows": int(len(retained)),
        "event_rows": int(len(events)),
        "sources_rows": int(len(sources)),
        "retained_sha256": _sha256_file(RETAINED),
        "events_sha256": _sha256_file(EVENTS),
        "sources_sha256": _sha256_file(SOURCES),
        "exact_ledger_hash_required": True,
        "decision_logic_changed": False,
        "criteria_changed": False,
        "weights_changed": False,
        "thresholds_changed": False,
    }
    tmp = META.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(META)
    stats["persist_status"] = "SUCCESS"
    stats["persist_ledger_hash_seconds"] = round(hash_seconds, 6)
    stats["persist_seconds"] = round(perf_counter() - started, 6)
    stats["persisted_retained_rows"] = int(len(retained))
    stats["persisted_event_rows"] = int(len(events))
    return payload
