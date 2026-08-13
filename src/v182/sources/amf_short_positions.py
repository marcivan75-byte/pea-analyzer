from __future__ import annotations

from datetime import date
from io import BytesIO
import re
import unicodedata

import pandas as pd

AMF_SHORT_STABLE_URL = "https://www.data.gouv.fr/api/1/datasets/r/c2539d1c-8531-4937-9cba-3bd8e9786cc5"


def _norm(text: object) -> str:
    raw=unicodedata.normalize("NFKD",str(text or "")).encode("ascii","ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+","",raw)


def _find_column(columns, *tokens: str) -> str | None:
    normalized={c:_norm(c) for c in columns}
    for c,n in normalized.items():
        if all(token in n for token in tokens):
            return c
    return None


def _read_csv(content: bytes) -> pd.DataFrame:
    last=None
    for encoding in ("utf-8-sig","utf-8","latin-1"):
        try:
            return pd.read_csv(BytesIO(content),sep=None,engine="python",encoding=encoding,dtype=str,low_memory=False)
        except Exception as exc:
            last=exc
    raise ValueError(f"AMF_CSV_PARSE_FAILED:{type(last).__name__ if last else 'UNKNOWN'}")


def _pct(value) -> float | None:
    if value is None or (isinstance(value,float) and pd.isna(value)):
        return None
    text=str(value).strip().replace("%","").replace(" ","").replace(",",".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_current_public_shorts(
    *,
    as_of: date | None = None,
    requests_module=None,
    url: str = AMF_SHORT_STABLE_URL,
) -> tuple[list[dict], list[dict], dict]:
    """Return current AMF-public net short positions aggregated by ISIN.

    Only explicitly published positions are emitted. Missing ISINs are not
    assigned zero because public disclosure is threshold-based and therefore is
    not equivalent to total short interest.
    """
    if requests_module is None:
        import requests as requests_module
    as_of=as_of or date.today()
    try:
        response=requests_module.get(url,timeout=45,allow_redirects=True,headers={"User-Agent":"PEA-Analyzer/AMF-Open-Data"})
        response.raise_for_status()
        frame=_read_csv(response.content)
    except Exception as exc:
        return [],[{"source":"AMF Open Data Short Interest","reason":type(exc).__name__,"detail":str(exc)[:200]}],{"status":"FAILED"}

    isin_col=_find_column(frame.columns,"isin")
    position_col=_find_column(frame.columns,"position","courte","nette") or _find_column(frame.columns,"position","courte") or _find_column(frame.columns,"ratio")
    holder_col=_find_column(frame.columns,"detenteur") or _find_column(frame.columns,"holder")
    start_publication_col=_find_column(frame.columns,"date","debut","publication") or _find_column(frame.columns,"publication","start")
    end_publication_col=_find_column(frame.columns,"date","fin","publication") or _find_column(frame.columns,"publication","end")
    issuer_col=_find_column(frame.columns,"emetteur") or _find_column(frame.columns,"issuer")
    if not isin_col or not position_col:
        return [],[{"source":"AMF Open Data Short Interest","reason":"SCHEMA_MISMATCH","columns":" | ".join(map(str,frame.columns))[:500]}],{"status":"SCHEMA_MISMATCH","rows":len(frame)}

    work=frame.copy()
    work["_isin"]=work[isin_col].astype(str).str.strip().str.upper()
    work["_position_pct"]=work[position_col].map(_pct)
    if end_publication_col:
        raw_end=work[end_publication_col]
        end_dt=pd.to_datetime(raw_end,errors="coerce",dayfirst=True)
        empty_end=raw_end.isna()|raw_end.astype(str).str.strip().str.lower().isin({"","nan","none","na","n/a"})
        active=empty_end|(end_dt.dt.date>=as_of)
        work=work[active].copy()
    if start_publication_col:
        work["_publication_start"]=pd.to_datetime(work[start_publication_col],errors="coerce",dayfirst=True)
    else:
        work["_publication_start"]=pd.NaT
    work=work[work["_isin"].str.match(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$",na=False)&work["_position_pct"].notna()].copy()

    if holder_col:
        work["_holder"]=work[holder_col].astype(str).str.strip()
        # Defensive deduplication: for a holder/ISIN, retain the latest still-
        # published row if the source ever contains overlapping active records.
        work=work.sort_values("_publication_start").drop_duplicates(["_isin","_holder"],keep="last")
    else:
        work["_holder"]="UNKNOWN_HOLDER"

    rows=[]
    for isin,group in work.groupby("_isin"):
        positions=pd.to_numeric(group["_position_pct"],errors="coerce").dropna()
        if positions.empty:
            continue
        total=float(positions.sum())
        gt05=float(positions[positions>=0.5].sum()) if (positions>=0.5).any() else 0.0
        below05=bool((positions<0.5).any())
        issuer=""
        if issuer_col:
            values=group[issuer_col].dropna().astype(str).str.strip()
            issuer=values.iloc[-1] if not values.empty else ""
        rows.append({
            "isin":isin,
            "issuer":issuer,
            "public_short_pct":round(total,6),
            "amf_public_net_short_pct":round(total,6),
            "amf_public_short_ge_0_5_pct":round(gt05,6),
            "amf_public_short_holders_count":int(group["_holder"].nunique()),
            "amf_public_short_max_holder_pct":round(float(positions.max()),6),
            "amf_public_short_below_0_5_warning":below05,
            "amf_short_data_as_of":as_of.isoformat(),
        })
    rows.sort(key=lambda r:r["isin"])
    return rows,[],{
        "status":"SUCCESS","source_rows":int(len(frame)),"active_rows":int(len(work)),"active_isins":len(rows),
        "below_0_5_warning_isins":sum(bool(r["amf_public_short_below_0_5_warning"]) for r in rows),
        "no_zero_imputation":True,
    }
