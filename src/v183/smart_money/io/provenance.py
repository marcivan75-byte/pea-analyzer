from __future__ import annotations
from pathlib import Path
import pandas as pd

COLUMNS = ["universe", "isin", "field", "source", "evidence_level", "validation_status", "as_of", "collected_at"]


def upsert_provenance(existing: pd.DataFrame | None, observations: list[dict]) -> pd.DataFrame:
    rows = [] if existing is None else existing.to_dict("records")
    by_key = {(r.get("universe"), r.get("isin"), r.get("field")): r for r in rows}
    rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    for o in observations:
        key = (o.get("universe"), o.get("isin"), o.get("field"))
        incoming = {c: o.get(c) for c in COLUMNS}
        current = by_key.get(key)
        if current is None:
            by_key[key] = incoming
            continue
        old_rank = rank.get(current.get("evidence_level", "D"), 0)
        new_rank = rank.get(incoming.get("evidence_level", "D"), 0)
        if new_rank > old_rank or (new_rank == old_rank and str(incoming.get("as_of") or "") > str(current.get("as_of") or "")):
            by_key[key] = incoming
    return pd.DataFrame(list(by_key.values()), columns=COLUMNS)


def save(frame: pd.DataFrame, path: str | Path) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p, index=False)
