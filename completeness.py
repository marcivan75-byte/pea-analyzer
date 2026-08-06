from __future__ import annotations

def completeness(rows: list[dict], fields: list[str]) -> dict:
    possible=len(rows)*len(fields)
    observed=sum(
        1 for row in rows for field in fields
        if str(row.get(field,"")).strip().upper()
        not in {"","MISSING","UNKNOWN","NON_OBSERVE","NOT_LOADED"}
    )
    return {
        "observed":observed,
        "possible":possible,
        "coverage_pct":round(observed/possible*100,1) if possible else 0,
    }
