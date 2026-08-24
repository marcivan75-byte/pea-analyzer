from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from time import perf_counter
import json

import pandas as pd

from v182.audit import provenance


VERSION = "DAILY_PROVENANCE_COMPACT_CACHE_V21_15_8_MAP_V3"
ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "state" / "provenance" / "daily_compact_cache_v1"
META = CACHE_DIR / "manifest.json"
MAPS = CACHE_DIR / "provenance_maps.json"
SOURCES = CACHE_DIR / "sources_by_field.json"
SEP = "\x1f"
LEGACY_CACHE_FILES = (
    "retained.parquet",
    "latest_events.parquet",
    "sources_by_field.parquet",
    "provenance_maps.json.gz",
    "sources_by_field.json.gz",
)


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


def _write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), default=str)
    tmp.replace(path)


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _encode(parts: tuple[str, ...]) -> str:
    return SEP.join(str(part) for part in parts)


def _decode(value: str, expected: int) -> tuple[str, ...]:
    parts = tuple(str(value).split(SEP))
    if len(parts) != expected:
        raise ValueError("PROVENANCE_COMPACT_KEY_ARITY")
    return parts


def _load_persisted(path: Path, stats: dict):
    started = perf_counter()
    meta = _read_meta()
    if meta.get("version") != VERSION or meta.get("code_contract") != _code_contract():
        stats["load_status"] = "MISS_CONTRACT"
        return None
    if not MAPS.exists() or not SOURCES.exists():
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
    if meta.get("maps_sha256") != _sha256_file(MAPS) or meta.get("sources_sha256") != _sha256_file(SOURCES):
        stats["load_status"] = "MISS_CACHE_HASH"
        return None

    try:
        packed = _read_json(MAPS)
        source_records = _read_json(SOURCES)
        retained_packed = packed.get("retained", {})
        latest_index = packed.get("latest_index", {})
        events_packed = packed.get("events", {})
        if not isinstance(retained_packed, dict) or not isinstance(latest_index, dict) or not isinstance(events_packed, dict):
            raise ValueError("PROVENANCE_COMPACT_MAP_SCHEMA")
        retained_rows_map = {_decode(key, 3): record for key, record in retained_packed.items()}
        latest_map = {
            _decode(key, 2): retained_packed[row_key]
            for key, row_key in latest_index.items()
            if row_key in retained_packed
        }
        latest_event_map = {_decode(key, 2): record for key, record in events_packed.items()}
        sources = pd.DataFrame.from_records(source_records)
    except Exception:
        stats["load_status"] = "MISS_READ_ERROR"
        return None

    if int(meta.get("retained_rows", -1)) != len(retained_rows_map) or int(meta.get("event_rows", -1)) != len(latest_event_map) or int(meta.get("latest_rows", -1)) != len(latest_map):
        stats["load_status"] = "MISS_ROWCOUNT"
        return None

    signature = provenance._file_signature(path)
    entry = provenance._RetainedCacheEntry(
        signature=signature,
        latest=None,
        latest_map=latest_map,
        retained_rows_map=retained_rows_map,
        latest_event_map=latest_event_map,
        sources_by_field=sources,
    )
    stats["load_status"] = "HIT_EXACT"
    stats["retained_rows"] = int(len(retained_rows_map))
    stats["event_rows"] = int(len(latest_event_map))
    stats["load_seconds"] = round(perf_counter() - started, 6)
    return entry


def install() -> tuple[callable, dict]:
    original = provenance._latest_entry_for_path
    stats = {
        "version": VERSION,
        "representation": "DIRECT_MAP_JSON",
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
    retained_rows_map = dict(entry.retained_rows_map)
    latest_map = dict(entry.latest_map)
    latest_event_map = dict(entry.latest_event_map)
    if entry.sources_by_field is not None:
        sources = entry.sources_by_field.copy(deep=True)
    else:
        retained = provenance._retained_frame(entry)
        sources = provenance._aggregate_sources(retained)

    retained_packed = {_encode(key): record for key, record in retained_rows_map.items()}
    latest_index = {
        _encode(key): _encode(
            (
                str(record.get("universe", "")),
                str(record.get("isin", "")),
                str(record.get("field", "")),
            )
        )
        for key, record in latest_map.items()
    }
    if any(row_key not in retained_packed for row_key in latest_index.values()):
        raise RuntimeError("PROVENANCE_COMPACT_LATEST_INDEX_INCOMPLETE")
    events_packed = {_encode(key): record for key, record in latest_event_map.items()}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(MAPS, {"retained": retained_packed, "latest_index": latest_index, "events": events_packed})
    _write_json(SOURCES, sources.to_dict("records"))

    ledger_hash_started = perf_counter()
    ledger_sha = _sha256_file(p)
    hash_seconds = perf_counter() - ledger_hash_started
    payload = {
        "version": VERSION,
        "validated": True,
        "representation": "DIRECT_MAP_JSON",
        "code_contract": _code_contract(),
        "ledger_size": int(p.stat().st_size) if p.exists() else 0,
        "ledger_sha256": ledger_sha,
        "retained_rows": int(len(retained_rows_map)),
        "latest_rows": int(len(latest_map)),
        "event_rows": int(len(latest_event_map)),
        "sources_rows": int(len(sources)),
        "maps_sha256": _sha256_file(MAPS),
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
    for name in LEGACY_CACHE_FILES:
        legacy = CACHE_DIR / name
        if legacy.exists() and legacy not in {MAPS, SOURCES}:
            try:
                legacy.unlink()
            except OSError:
                pass
    stats["persist_status"] = "SUCCESS"
    stats["persist_ledger_hash_seconds"] = round(hash_seconds, 6)
    stats["persist_seconds"] = round(perf_counter() - started, 6)
    stats["persisted_retained_rows"] = int(len(retained_rows_map))
    stats["persisted_event_rows"] = int(len(latest_event_map))
    return payload
