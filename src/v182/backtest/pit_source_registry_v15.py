"""Source-registry contract for CDC PIT V15.

A PIT source cannot be admitted to V15 unless its provenance, timestamp semantics,
granularity, lag, PEA coverage, licence and cost are documented. This module validates
that registry only; it does not fetch data or run performance tests.
"""
from __future__ import annotations
import pandas as pd


class PITSourceRegistryError(ValueError):
    pass


REQUIRED_COLUMNS = {
    "source", "provider", "timestamp_field", "publication_field", "granularity",
    "observed_lag_definition", "pea_coverage_definition", "licence", "cost",
    "point_in_time_guarantee", "active"
}
ALLOWED_SOURCES = {"P1_FACTSET", "P2_EPS", "P3_MODEL"}


def validate_source_registry(df: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise PITSourceRegistryError("missing source-registry columns: " + ", ".join(missing))
    z = df.copy()
    if z["source"].duplicated().any():
        raise PITSourceRegistryError("duplicate source registry entry")
    if (~z["source"].isin(ALLOWED_SOURCES)).any():
        raise PITSourceRegistryError("unsupported source in registry")
    active = z["active"].astype(bool)
    for c in ["provider", "timestamp_field", "publication_field", "granularity",
              "observed_lag_definition", "pea_coverage_definition", "licence", "cost"]:
        if (active & z[c].astype(str).str.strip().isin(["", "nan", "None"])).any():
            raise PITSourceRegistryError(f"active source missing documentation: {c}")
    if (active & ~z["point_in_time_guarantee"].astype(bool)).any():
        raise PITSourceRegistryError("active source without explicit PIT guarantee")
    return z


def readiness(df: pd.DataFrame) -> dict:
    z = validate_source_registry(df)
    active = set(z.loc[z["active"].astype(bool), "source"])
    return {
        "active_sources": sorted(active),
        "p1_documented": "P1_FACTSET" in active,
        "p2_documented": "P2_EPS" in active,
        "p3_documented": "P3_MODEL" in active,
        "primary_source_documentation_ready": bool({"P1_FACTSET", "P2_EPS"}.issubset(active)),
        "performance_backtest_authorized_by_this_module": False,
    }
