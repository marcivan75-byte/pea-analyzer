from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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

# The provenance ledger is append-only and can become large. A single Committee
# process used to reread and re-reduce the complete CSV for every merge and every
# collection audit. Keep only the latest retained rows in memory, keyed by the
# exact on-disk file signature. Any external file change invalidates the cache;
# append_records updates it only when the cached signature still matches the file
# immediately before our append. This changes I/O only, never merge authority.
_LATEST_RETAINED_CACHE: dict[str, tuple[tuple[int,int] | None, pd.DataFrame]] = {}
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


def _latest_retained_for_path(path: Path) -> pd.DataFrame:
    """Return exact latest retained rows, reusing them while the file is unchanged."""
    key=_cache_key(path); signature=_file_signature(path)
    with _CACHE_LOCK:
        cached=_LATEST_RETAINED_CACHE.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]

    # Read outside the lock: the normal pipeline is single-writer and this avoids
    # holding the cache mutex across a potentially large CSV parse.
    latest=_latest_retained_rows(_read_ledger(path))
    signature_after=_file_signature(path)
    with _CACHE_LOCK:
        _LATEST_RETAINED_CACHE[key]=(signature_after,latest)
    return latest


def load_latest(path: str | Path | None = None) -> dict[tuple[str,str],dict]:
    """Return metadata for the value actually retained in the master.

    KEEP/QUARANTINE/SKIP events remain in the append-only observation ledger but
    must never supersede the provenance of the currently retained field value.
    Callers must additionally bind this metadata to their current value hash.
    """
    p=Path(path) if path is not None else provenance_path(); latest=_latest_retained_for_path(p)
    if latest.empty: return {}
    return {(str(r["isin"]),str(r["field"])):r.to_dict() for _,r in latest.iterrows()}


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

    # If our cached view described exactly the pre-append file, update that view
    # from the newly appended retained rows. Otherwise invalidate and force one
    # authoritative disk reload on the next read.
    with _CACHE_LOCK:
        if cached is not None and cached[0] == signature_before:
            new_retained=_latest_retained_rows(rows_df)
            if new_retained.empty:
                latest=cached[1]
            elif cached[1].empty:
                latest=new_retained
            else:
                latest=_latest_retained_rows(pd.concat([cached[1],new_retained],ignore_index=True,sort=False))
            _LATEST_RETAINED_CACHE[key]=(signature_after,latest)
        else:
            _LATEST_RETAINED_CACHE.pop(key,None)


def actual_sources_by_field(path:str|Path|None=None)->pd.DataFrame:
    """Aggregate only the latest retained source per universe+ISIN+field."""
    p=Path(path) if path is not None else provenance_path(); retained=_latest_retained_for_path(p)
    columns=["universe","field","sources_reelles","source_urls","evidence_levels","last_as_of"]
    if retained.empty or not {"universe","field"}.issubset(retained.columns): return pd.DataFrame(columns=columns)
    def join_unique(s:pd.Series)->str:
        return " | ".join(sorted({str(x).strip() for x in s.dropna() if str(x).strip() and str(x).lower()!="nan"}))
    return retained.groupby(["universe","field"],dropna=False).agg(sources_reelles=("source",join_unique),source_urls=("source_url",join_unique),evidence_levels=("evidence_level",join_unique),last_as_of=("as_of","max")).reset_index()
