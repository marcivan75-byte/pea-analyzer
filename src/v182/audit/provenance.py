from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import pandas as pd

DEFAULT_PATH = Path("outputs/audit/OBSERVATION_PROVENANCE.csv")
COLUMNS = [
    "recorded_at_utc","universe","isin","field","source","source_url",
    "evidence_level","as_of","validation_status","merge_action","merge_reason",
    "value_sha256",
]


def provenance_path() -> Path:
    raw=os.environ.get("PEA_PROVENANCE_PATH",str(DEFAULT_PATH))
    return Path(raw)


def _value_hash(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8",errors="replace")).hexdigest()


def load_latest(path: str | Path | None = None) -> dict[tuple[str,str],dict]:
    p=Path(path) if path is not None else provenance_path()
    if not p.exists(): return {}
    try:
        df=pd.read_csv(p,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    except (OSError,ValueError,pd.errors.ParserError):
        return {}
    if df.empty or not {"isin","field"}.issubset(df.columns): return {}
    if "recorded_at_utc" in df.columns: df=df.sort_values("recorded_at_utc")
    latest=df.drop_duplicates(["isin","field"],keep="last")
    return {(str(r["isin"]),str(r["field"])):r.to_dict() for _,r in latest.iterrows()}


def append_records(records: list[dict], path: str | Path | None = None) -> None:
    if not records: return
    p=Path(path) if path is not None else provenance_path(); p.parent.mkdir(parents=True,exist_ok=True)
    now=datetime.now(timezone.utc).isoformat(); rows=[]
    for r in records:
        rows.append({
            "recorded_at_utc":now,
            "universe":r.get("universe",""),"isin":r.get("isin",""),"field":r.get("field",""),
            "source":r.get("source",""),"source_url":r.get("source_url",""),
            "evidence_level":r.get("evidence_level","D"),"as_of":r.get("as_of",""),
            "validation_status":r.get("validation_status",""),"merge_action":r.get("merge_action",""),
            "merge_reason":r.get("merge_reason",""),"value_sha256":_value_hash(r.get("value")),
        })
    pd.DataFrame(rows,columns=COLUMNS).to_csv(
        p,sep=";",encoding="utf-8-sig",index=False,mode="a",header=not p.exists()
    )


def actual_sources_by_field(path: str | Path | None = None) -> pd.DataFrame:
    p=Path(path) if path is not None else provenance_path()
    if not p.exists(): return pd.DataFrame(columns=["field","sources_reelles","source_urls","evidence_levels","last_as_of"])
    try: df=pd.read_csv(p,sep=";",encoding="utf-8-sig",dtype=str,low_memory=False)
    except (OSError,ValueError,pd.errors.ParserError): return pd.DataFrame(columns=["field","sources_reelles","source_urls","evidence_levels","last_as_of"])
    if df.empty: return pd.DataFrame(columns=["field","sources_reelles","source_urls","evidence_levels","last_as_of"])
    accepted=df[df.get("merge_action",pd.Series(index=df.index,dtype=str)).isin(["INSERT","REPLACE","KEEP"])].copy()
    if accepted.empty: accepted=df.copy()
    def join_unique(s:pd.Series)->str:
        vals=sorted({str(x).strip() for x in s.dropna() if str(x).strip() and str(x).lower()!="nan"})
        return " | ".join(vals)
    out=(accepted.groupby("field",dropna=False).agg(
        sources_reelles=("source",join_unique),source_urls=("source_url",join_unique),
        evidence_levels=("evidence_level",join_unique),last_as_of=("as_of","max")
    ).reset_index())
    return out
