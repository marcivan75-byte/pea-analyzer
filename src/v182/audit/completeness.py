from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from v182.io.frames import is_missing

TECHNICAL_FIELDS={"canonical_seed_status"}
MISSING_TEXT_TOKENS={"", "MISSING", "UNKNOWN", "NON_OBSERVE", "NOT_LOADED", "NAN", "<NA>", "N/A", "NA", "NULL"}


def _measured_fields(fields: Sequence[str]) -> list[str]:
    return [str(field) for field in fields if str(field) not in TECHNICAL_FIELDS]


def _frame_completeness(frame: pd.DataFrame, fields: Sequence[str]) -> dict:
    measured=_measured_fields(fields)
    possible=len(frame)*len(measured)
    if not possible:
        return {"observed":0,"possible":possible,"coverage_pct":0}

    # Reindex rather than filtering columns: the historical row.get(field) path
    # counted an absent requested column in the denominator and treated every cell
    # in it as missing. Preserve that exact contract.
    data=frame.reindex(columns=measured)
    missing=data.isna()
    # Masters are overwhelmingly strings. Pandas' nullable string dtype keeps
    # genuine NA values as NA while vectorizing the exact canonical text-token
    # normalization used by frames.is_missing.
    normalized=data.astype("string").apply(lambda series: series.str.strip().str.upper())
    missing |= normalized.isin(MISSING_TEXT_TOKENS)
    observed=int((~missing).to_numpy().sum())
    return {
        "observed":observed,
        "possible":possible,
        "coverage_pct":round(observed/possible*100,1),
    }


def completeness(rows: pd.DataFrame | list[dict], fields: list[str]) -> dict:
    """Measure real observed cells with canonical missing semantics.

    DataFrame callers use a vectorized path that avoids materialising the complete
    master as a list of Python dictionaries. The historical list-of-dicts contract
    remains available for existing callers/tests. Canonical bookkeeping fields are
    excluded in both paths.
    """
    if isinstance(rows,pd.DataFrame):
        return _frame_completeness(rows,fields)

    measured_fields=_measured_fields(fields)
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
