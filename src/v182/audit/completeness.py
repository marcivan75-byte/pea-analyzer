from __future__ import annotations

from v182.io.frames import is_missing


def completeness(rows: list[dict], fields: list[str]) -> dict:
    """Measure observed cells using the same canonical missing policy as merges/audits."""
    possible=len(rows)*len(fields)
    observed=sum(
        1 for row in rows for field in fields
        if not is_missing(row.get(field))
    )
    return {
        "observed":observed,
        "possible":possible,
        "coverage_pct":round(observed/possible*100,1) if possible else 0,
    }
