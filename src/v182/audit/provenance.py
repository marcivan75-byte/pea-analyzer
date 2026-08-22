from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping
import hashlib
import os
import threading
import pandas as pd

DEFAULT_PATH = Path("state/provenance/OBSERVATION_PROVENANCE.csv")
COLUMNS = [
    "recorded_at_utc","universe","isin","field","source","source_url",
    "evidence_level","as_of","validation_status","merge_action","merge_reason",
    "value_sha256",
]
RETAINED_ACTIONS={"INSERT","REPLACE"}


@dataclass
class _RetainedCacheEntry:
    """Process-local views derived from one exact append-only ledger signature."""

    signature: tuple[int,int] | None
    latest: pd.DataFrame | None
    latest_map: dict[tuple[str,str],dict]
    retained_rows_map: dict[tuple[str,str,str],dict]
    sources_by_field: pd.DataFrame | None = None


# The on-disk CSV remains the inter-process authority. In-process merges use the
# retained maps directly and materialize a DataFrame only when an audit actually
# needs one. This avoids copying/reducing the whole retained state after every
# observation wave while preserving the exact append-only ledger.
_LATEST_RETAINED_CACHE: dict[str, _RetainedCacheEntry] = {}
_CACHE_LOCK = threading.RLock()


def provenance_path() -> Path:
    raw=os.environ.get("PEA_PROVENANCE_PATH",str(DEFAULT_PATH))
    return Path(raw)


def value_hash(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8",errors="replace")).hexdigest()


def retained_meta_matches_value(meta:dict|None,value)->bool:
    """A persisted provenance record may govern only the exact retained value."""
    if not meta: return False
    expected=str(meta.get("value_sha256") or "").strip()
    return bool(expected) and expected==value_hash(value)


def _read_ledger(path:Path)->pd.DataFrame:
    if not path.exists(): return pd.DataFrame(columns=COLUMNS)
    try: return pd.read_csv(path,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    except (OSError,ValueError,pd.errors.ParserError): return pd.DataFrame(columns=COLUMNS)


def _latest_retained_rows(df:pd.DataFrame)->pd.DataFrame:
    if df.empty or not {"isin","field","merge_action"}.issubset(df.columns): return pd.DataFrame(columns=df.columns)
    retained=df[df["merge_action"].astype(str).isin(RETAINED_ACTIONS)].copy()
    if retained.empty: return retained
    if "recorded_at_utc" in retained.columns: retained=retained.sort_values("recorded_at_utc")
    keys=[c for c in ("universe","isin","field") if c in retained.columns]
    return retained.drop_duplicates(keys,keep="last")


def _latest_mapping(latest: pd.DataFrame) -> dict[tuple[str,str],dict]:
    """Build the historical (ISIN, field) retained lookup without pandas iterrows."""
    if latest.empty or not {"isin","field"}.issubset(latest.columns):
        return {}
    out: dict[tuple[str,str],dict]={}
    for record in latest.to_dict("records"):
        out[(str(record.get("isin","")),str(record.get("field","")))]=record
    return out


def _retained_rows_mapping(latest: pd.DataFrame) -> dict[tuple[str,str,str],dict]:
    """Preserve the full universe+ISIN+field retained state for audit aggregation."""
    if latest.empty or not {"universe","isin","field"}.issubset(latest.columns):
        return {}
    out: dict[tuple[str,str,str],dict]={}
    for record in latest.to_dict("records"):
        key=(str(record.get("universe","")),str(record.get("isin","")),str(record.get("field","")))
        out[key]=record
    return out


def _aggregate_sources(retained: pd.DataFrame) -> pd.DataFrame:
    columns=["universe","field","sources_reelles","source_urls","evidence_levels","last_as_of"]
    if retained.empty or not {"universe","field"}.issubset(retained.columns):
        return pd.DataFrame(columns=columns)

    def join_unique(s:pd.Series)->str:
        return " | ".join(sorted({str(x).strip() for x in s.dropna() if str(x).strip() and str(x).lower()!="nan"}))

    return retained.groupby(["universe","field"],dropna=False).agg(
        sources_reelles=("source",join_unique),
        source_urls=("source_url",join_unique),
        evidence_levels=("evidence_level",join_unique),
        last_as_of=("as_of","max"),
    ).reset_index()


def _cache_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path.absolute())


def _file_signature(path: Path) -> tuple[int,int] | None:
    try:
        stat=path.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns),int(stat.st_size)


def _entry_from_latest(signature: tuple[int,int] | None, latest: pd.DataFrame) -> _RetainedCacheEntry:
    return _RetainedCacheEntry(
        signature=signature,
        latest=latest,
        latest_map=_latest_mapping(latest),
        retained_rows_map=_retained_rows_mapping(latest),
    )


def _latest_entry_for_path(path: Path) -> _RetainedCacheEntry:
    """Return exact retained state and derived lookup while the file is unchanged."""
    key=_cache_key(path); signature=_file_signature(path)
    with _CACHE_LOCK:
        cached=_LATEST_RETAINED_CACHE.get(key)
        if cached is not None and cached.signature == signature:
            return cached

    latest=_latest_retained_rows(_read_ledger(path))
    signature_after=_file_signature(path)
    entry=_entry_from_latest(signature_after,latest)
    with _CACHE_LOCK:
        _LATEST_RETAINED_CACHE[key]=entry
    return entry


def _retained_frame(entry: _RetainedCacheEntry) -> pd.DataFrame:
    """Materialize retained rows lazily from the exact retained-row mapping."""
    with _CACHE_LOCK:
        if entry.latest is not None:
            return entry.latest
        records=list(entry.retained_rows_map.values())
    frame=pd.DataFrame.from_records(records,columns=COLUMNS) if records else pd.DataFrame(columns=COLUMNS)
    with _CACHE_LOCK:
        if entry.latest is None:
            entry.latest=frame
        return entry.latest


def _latest_retained_for_path(path: Path) -> pd.DataFrame:
    """Backward-compatible internal retained-row view used by existing callers/tests."""
    return _retained_frame(_latest_entry_for_path(path))


def load_latest(path: str | Path | None = None) -> dict[tuple[str,str],dict]:
    """Return independent metadata for the value actually retained in the master."""
    p=Path(path) if path is not None else provenance_path(); entry=_latest_entry_for_path(p)
    with _CACHE_LOCK:
        return {key:dict(meta) for key,meta in entry.latest_map.items()}


def load_latest_readonly(path: str | Path | None = None) -> Mapping[tuple[str,str],dict]:
    """Return a zero-copy read-only outer view for trusted in-process merge code.

    The nested metadata dictionaries must be treated as immutable. Public callers
    that need mutation isolation must keep using ``load_latest``.
    """
    p=Path(path) if path is not None else provenance_path(); entry=_latest_entry_for_path(p)
    with _CACHE_LOCK:
        return MappingProxyType(entry.latest_map)


def append_records(records:list[dict],path:str|Path|None=None)->None:
    if not records: return
    p=Path(path) if path is not None else provenance_path(); p.parent.mkdir(parents=True,exist_ok=True); now=datetime.now(timezone.utc).isoformat(); rows=[]
    for r in records:
        rows.append({"recorded_at_utc":now,"universe":r.get("universe",""),"isin":r.get("isin",""),"field":r.get("field",""),"source":r.get("source",""),"source_url":r.get("source_url",""),"evidence_level":r.get("evidence_level","D"),"as_of":r.get("as_of",""),"validation_status":r.get("validation_status",""),"merge_action":r.get("merge_action",""),"merge_reason":r.get("merge_reason",""),"value_sha256":value_hash(r.get("value"))})
    rows_df=pd.DataFrame(rows,columns=COLUMNS)
    key=_cache_key(p); signature_before=_file_signature(p)
    with _CACHE_LOCK:
        cached=_LATEST_RETAINED_CACHE.get(key)
    rows_df.to_csv(p,sep=";",encoding="utf-8-sig",index=False,mode="a",header=not p.exists())
    signature_after=_file_signature(p)

    # Update retained maps directly. The large retained DataFrame is invalidated
    # and rebuilt only if a later audit actually requests it; no full concat/sort/
    # drop_duplicates pass occurs on the critical merge path.
    with _CACHE_LOCK:
        if cached is not None and cached.signature == signature_before:
            new_retained=_latest_retained_rows(rows_df)
            if new_retained.empty:
                _LATEST_RETAINED_CACHE[key]=_RetainedCacheEntry(
                    signature=signature_after,
                    latest=cached.latest,
                    latest_map=cached.latest_map,
                    retained_rows_map=cached.retained_rows_map,
                    sources_by_field=cached.sources_by_field,
                )
            else:
                latest_map=cached.latest_map.copy()
                rows_map=cached.retained_rows_map.copy()
                for record in new_retained.to_dict("records"):
                    isin=str(record.get("isin","")); field=str(record.get("field","")); universe=str(record.get("universe",""))
                    latest_map[(isin,field)]=record
                    row_key=(universe,isin,field)
                    rows_map.pop(row_key,None)
                    rows_map[row_key]=record
                _LATEST_RETAINED_CACHE[key]=_RetainedCacheEntry(
                    signature=signature_after,
                    latest=None,
                    latest_map=latest_map,
                    retained_rows_map=rows_map,
                    sources_by_field=None,
                )
        else:
            _LATEST_RETAINED_CACHE.pop(key,None)


def actual_sources_by_field(path:str|Path|None=None)->pd.DataFrame:
    """Aggregate latest retained sources once per retained-state change."""
    p=Path(path) if path is not None else provenance_path(); key=_cache_key(p)
    entry=_latest_entry_for_path(p)
    with _CACHE_LOCK:
        current=_LATEST_RETAINED_CACHE.get(key)
        if current is entry and current.sources_by_field is not None:
            return current.sources_by_field.copy(deep=True)

    aggregate=_aggregate_sources(_retained_frame(entry))
    with _CACHE_LOCK:
        current=_LATEST_RETAINED_CACHE.get(key)
        if current is entry:
            current.sources_by_field=aggregate
            return aggregate.copy(deep=True)

    fresh=_latest_entry_for_path(p)
    aggregate=_aggregate_sources(_retained_frame(fresh))
    with _CACHE_LOCK:
        current=_LATEST_RETAINED_CACHE.get(key)
        if current is fresh:
            current.sources_by_field=aggregate
    return aggregate.copy(deep=True)
