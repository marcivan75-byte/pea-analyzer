from __future__ import annotations

from typing import Any

import pandas as pd

# Hard data-integrity domains. These are not investment thresholds: values
# outside these ranges are considered unsafe for automated scoring until source
# review. No clipping or neutral replacement is ever performed.
NUMERIC_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "dividend_yield_pct": (0.0, 100.0),
    "ter_pct": (0.0, 10.0),
    "aum_m": (0.0, None),
    "fund_total_assets_eur_m": (0.0, None),
    "holdings": (0.0, None),
    "market_cap": (0.0, None),
    "ohlcv_last": (0.0, None),
    "last_close": (0.0, None),
    "volatility_1y_pct": (0.0, 500.0),
    "risk_indicator": (1.0, 7.0),
    "morningstar_rating": (1.0, 5.0),
    "rank_cat_1y": (0.0, 100.0),
    "rank_cat_3y": (0.0, 100.0),
    "rank_cat_5y": (0.0, 100.0),
}
MISSING_TEXT = {"", "NON_OBSERVE", "MISSING", "UNKNOWN", "N/A", "NA", "NULL", "NAN", "<NA>"}


def bounds_for_field(field: str) -> tuple[float | None, float | None] | None:
    if field in NUMERIC_BOUNDS:
        return NUMERIC_BOUNDS[field]
    if field.startswith("perf_") and field.endswith("_pct"):
        # Simple total return cannot lose more than 100%. The upper bound is
        # deliberately broad because multi-year winners can exceed 100%.
        return (-100.0, 100000.0)
    if field in {"max_drawdown_1y_pct", "max_drawdown_1y"}:
        return (-100.0, 0.0)
    return None


def is_effectively_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        return False
    return str(value).strip().upper() in MISSING_TEXT


def parse_finite_number(value: Any) -> float | None:
    if is_effectively_missing(value):
        return None
    text = str(value).strip().replace("\u202f", "").replace(" ", "").replace(",", ".").replace("%", "")
    try:
        number = float(text)
    except ValueError:
        return None
    if not pd.notna(number) or number in (float("inf"), float("-inf")):
        return None
    return number


def validate_numeric_value(field: str, value: Any) -> tuple[bool, str]:
    """Validate a bounded numeric field while preserving missing-value semantics."""
    bounds = bounds_for_field(field)
    if bounds is None:
        return True, "UNBOUNDED_FIELD"
    if is_effectively_missing(value):
        return True, "MISSING_NO_OBSERVATION"
    number = parse_finite_number(value)
    if number is None:
        return False, "NUMERIC_UNPARSABLE"
    low, high = bounds
    if low is not None and number < low:
        return False, f"NUMERIC_BELOW_BOUND:{low}"
    if high is not None and number > high:
        return False, f"NUMERIC_ABOVE_BOUND:{high}"
    return True, "VALID"


def filter_numeric_series(series: pd.Series, field: str) -> tuple[pd.Series, pd.Series]:
    """Return numeric values with out-of-domain cells set to NaN plus invalid mask."""
    numeric = pd.to_numeric(
        series.astype(str).str.strip().str.replace(",", ".", regex=False).str.replace("%", "", regex=False),
        errors="coerce",
    )
    bounds = bounds_for_field(field)
    if bounds is None:
        return numeric, pd.Series(False, index=series.index, dtype=bool)
    source_missing = series.map(is_effectively_missing)
    invalid = numeric.isna() & ~source_missing
    low, high = bounds
    if low is not None:
        invalid |= numeric < low
    if high is not None:
        invalid |= numeric > high
    return numeric.mask(invalid), invalid
