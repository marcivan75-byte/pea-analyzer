from __future__ import annotations
from dataclasses import dataclass

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_SERIES = {"macro_vix": "VIXCLS", "macro_curve_10y2y": "T10Y2Y"}


@dataclass(frozen=True)
class FredValue:
    series_id: str
    date: str
    value: float


def fetch_latest_observation(series_id: str, api_key: str, timeout: int = 20) -> FredValue:
    import requests

    if not api_key:
        raise RuntimeError("FRED_API_KEY_MISSING")
    response = requests.get(
        BASE_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("FRED_INVALID_JSON_SHAPE")
    if body.get("error_code") or body.get("error_message"):
        raise RuntimeError(f"FRED_API_ERROR: {body.get('error_code')} {body.get('error_message')}")
    observations = body.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError("FRED_OBSERVATIONS_MISSING")
    for row in observations:
        raw = row.get("value") if isinstance(row, dict) else None
        if raw in (None, "", "."):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        return FredValue(series_id=series_id, date=str(row.get("date") or ""), value=value)
    raise RuntimeError(f"FRED_NO_NUMERIC_OBSERVATION:{series_id}")


def fetch_macro_context(api_key: str, series: dict[str, str] | None = None) -> dict:
    selected = series or DEFAULT_SERIES
    out = {"source": "FRED", "series": {}, "api_calls": 0}
    dates = []
    for field, series_id in selected.items():
        value = fetch_latest_observation(series_id, api_key)
        out["api_calls"] += 1
        out[field] = value.value
        out["series"][field] = {"series_id": series_id, "date": value.date, "value": value.value}
        if value.date:
            dates.append(value.date)
    out["macro_as_of"] = max(dates) if dates else ""
    return out
