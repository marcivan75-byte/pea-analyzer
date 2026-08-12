from __future__ import annotations
from pathlib import Path
import pandas as pd

MISSING_TOKEN = "NON_OBSERVE"


def load_master(path: str | Path) -> pd.DataFrame:
    """Charge un référentiel maître CSV ';' UTF-8 BOM, valeurs vides = NaN."""
    return pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str, keep_default_na=True)


def save_master(frame: pd.DataFrame, path: str | Path) -> None:
    out = Path(path); out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out, sep=";", encoding="utf-8-sig", index=False)


def is_missing(value) -> bool:
    if value is None: return True
    try:
        if pd.isna(value): return True
    except (TypeError, ValueError):
        return False
    text = str(value).strip().upper()
    return text in {"", "MISSING", "UNKNOWN", MISSING_TOKEN, "NOT_LOADED", "NAN", "<NA>", "N/A", "NA", "NULL"}


def _ensure_text_assignable(frame: pd.DataFrame, field: str) -> None:
    """Allow canonical string storage even when pandas inferred a numeric dtype.

    Master files are normally loaded as strings, but secondary refresh modules or
    tests may provide numerically inferred columns. pandas 3.x rejects assigning a
    retained string observation such as ``"45.0"`` into a float64 column. The
    merge contract stores retained master values as strings, so convert only the
    target column to object before an INSERT/REPLACE when required.
    """
    if field in frame.columns and frame[field].dtype != object:
        frame[field] = frame[field].astype(object)


def apply_observations(frame: pd.DataFrame, observations: list[dict]) -> tuple[pd.DataFrame, list[dict]]:
    """Merge observations using per-field provenance when available.

    Persisted evidence metadata is authoritative only when its stored value hash
    matches the value currently present in the master. This prevents a value that
    changed between runs from inheriting the evidence/source of an older value.
    """
    from v182.audit.provenance import append_records, load_latest, retained_meta_matches_value, value_hash
    from v182.core.merge import decide

    frame = frame.set_index("isin", drop=False)
    quarantined: list[dict] = []
    provenance=load_latest()
    provenance_records=[]

    for obs in observations:
        isin = obs.get("isin"); field = obs.get("field")
        if isin is None or field is None or isin not in frame.index:
            provenance_records.append({**obs,"merge_action":"SKIP","merge_reason":"ISIN_OR_FIELD_NOT_IN_MASTER"})
            continue
        if field not in frame.columns: frame[field] = pd.NA

        current_value = frame.at[isin, field]
        key=(str(isin),str(field)); meta=provenance.get(key)
        if is_missing(current_value):
            existing=None
        elif meta and retained_meta_matches_value(meta,current_value):
            existing={"value":current_value,"evidence_level":meta.get("evidence_level","D"),"as_of":meta.get("as_of","")}
        else:
            existing={
                "value":current_value,
                "evidence_level":frame.at[isin,"evidence_level"] if "evidence_level" in frame.columns else "D",
                "as_of":frame.at[isin,"as_of_date"] if "as_of_date" in frame.columns else "",
            }
        decision=decide(existing,obs)
        if decision.action in {"INSERT","REPLACE"}:
            _ensure_text_assignable(frame,field)
            value=obs.get("value"); frame.at[isin,field]="" if value is None else str(value)
            provenance[key]={**obs,"merge_action":decision.action,"merge_reason":decision.reason,"value_sha256":value_hash(value)}
        elif decision.action=="QUARANTINE":
            quarantined.append({**obs,"reason":decision.reason})
        provenance_records.append({**obs,"merge_action":decision.action,"merge_reason":decision.reason})

    append_records(provenance_records)
    return frame.reset_index(drop=True), quarantined
