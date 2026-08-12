from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
import pandas as pd


def _missing(value) -> bool:
    if value is None: return True
    try:
        if pd.isna(value): return True
    except (TypeError, ValueError):
        return False
    return str(value).strip().lower() in {"","nan","none","n/a","na","missing","non_observe","unknown"}


def load_authorized_snapshot(actions: pd.DataFrame, snapshot_path: str | Path, worklist_path: str | Path) -> tuple[list[dict],list[dict]]:
    """Load attributed Morningstar stock ratings without protected scraping.

    Required columns: isin, morningstar_rating, as_of, source_url. The attributed
    validation status is accepted by the central merge policy and fully logged in
    the per-field provenance ledger.
    """
    path=Path(snapshot_path); worklist=Path(worklist_path); worklist.parent.mkdir(parents=True,exist_ok=True)
    valid_isins=set(actions["isin"].astype(str)) if "isin" in actions.columns else set(); observations=[]; failures=[]; covered=set()
    if path.exists():
        try: snap=pd.read_csv(path,sep=None,engine="python",encoding="utf-8-sig",dtype=str)
        except (OSError,ValueError,pd.errors.ParserError) as exc:
            failures.append({"source":"Morningstar","reason":"SNAPSHOT_READ_ERROR","detail":f"{type(exc).__name__}: {str(exc)[:180]}"}); snap=pd.DataFrame()
        required={"isin","morningstar_rating","as_of","source_url"}
        if not snap.empty and not required.issubset(snap.columns):
            failures.append({"source":"Morningstar","reason":"SNAPSHOT_SCHEMA_INVALID","missing_columns":",".join(sorted(required-set(snap.columns)))})
        elif not snap.empty:
            for _,row in snap.iterrows():
                isin=str(row.get("isin","")).strip()
                if isin not in valid_isins: continue
                try: rating=float(str(row.get("morningstar_rating","")).replace(",","."))
                except (TypeError,ValueError): failures.append({"isin":isin,"source":"Morningstar","reason":"INVALID_RATING"}); continue
                source_url=str(row.get("source_url","")).strip(); as_of=str(row.get("as_of","")).strip()
                if rating<1 or rating>5 or not as_of or "morningstar" not in source_url.lower(): failures.append({"isin":isin,"source":"Morningstar","reason":"UNATTRIBUTED_OR_OUT_OF_RANGE"}); continue
                now=datetime.now(timezone.utc).isoformat(); covered.add(isin)
                base={"universe":"ACTION","isin":isin,"source":"Morningstar attributed snapshot","source_url":source_url,"collected_at":now,"as_of":as_of,"evidence_level":"B","validation_status":"ATTRIBUTED"}
                observations.append({**base,"field":"morningstar_rating","value":rating})
                observations.append({**base,"field":"morningstar_rating_source_url","value":source_url})
    missing=actions[~actions["isin"].astype(str).isin(covered)].copy() if "isin" in actions.columns else pd.DataFrame(); rows=[]
    for _,row in missing.iterrows():
        name=str(row.get("name","") or "").strip(); ticker=str(row.get("yahoo_ticker","") or "").strip(); query=" ".join(x for x in (name,ticker) if x)
        rows.append({"isin":row.get("isin"),"name":name,"yahoo_ticker":ticker,"status":"MISSING_MORNINGSTAR_ACTION_RATING","source_concernee":"Morningstar Rating for Stocks","official_search_url":f"https://www.morningstar.com/search?query={quote_plus(query)}"})
    pd.DataFrame(rows).to_csv(worklist,sep=";",index=False,encoding="utf-8-sig")
    return observations,failures
