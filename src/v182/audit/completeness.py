from __future__ import annotations

from v182.io.frames import is_missing

TECHNICAL_FIELDS={"canonical_seed_status"}


def completeness(rows: list[dict], fields: list[str]) -> dict:
    """Measure real observed cells with canonical missing semantics.

    Canonical bookkeeping fields are excluded so materialising an identity-only
    row cannot improve the apparent data coverage merely by setting its status.
    """
    measured_fields=[field for field in fields if field not in TECHNICAL_FIELDS]
    possible=len(rows)*len(measured_fields)
    observed=sum(
        1 for row in rows for field in measured_fields
        if not is_missing(row.get(field))
    )
    return {
        "observed":observed,
        "possible":possible,
        "coverage_pct":round(observed/possible*100,1) if possible else 0,
    }
