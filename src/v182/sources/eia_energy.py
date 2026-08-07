from __future__ import annotations
from dataclasses import dataclass

BASE_URL = "https://api.eia.gov/v2/seriesid"
DEFAULT_SERIES = {"wti_spot_usd_bbl": "PET.RWTC.D", "brent_spot_usd_bbl": "PET.RBRTE.D"}


@dataclass(frozen=True)
class EiaValue:
    series_id: str
    date: str
    value: float


def fetch_latest_series(series_id: str, api_key: str, timeout: int = 20) -> EiaValue:
    import requests

    if not api_key:
        raise RuntimeError("EIA_API_KEY_MISSING")
    response = requests.get(
        f"{BASE_URL}/{series_id}",
        params={"api_key": api_key, "length": 10},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("EIA_INVALID_JSON_SHAPE")
    if body.get("error"):
        raise RuntimeError(f"EIA_API_ERROR: {str(body.get('error'))[:240]}")
    response_body = body.get("response")
    rows = response_body.get("data") if isinstance(response_body, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("EIA_DATA_MISSING")

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = str(row.get("date") or row.get("period") or "")
        raw = row.get("value")
        if raw in (None, "", "NA", "--"):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        candidates.append((date, value))
    if not candidates:
        raise RuntimeError(f"EIA_NO_NUMERIC_OBSERVATION:{series_id}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return EiaValue(series_id=series_id, date=candidates[0][0], value=candidates[0][1])


def fetch_energy_context(api_key: str, series: dict[str, str] | None = None) -> dict:
    selected = series or DEFAULT_SERIES
    out = {"source": "EIA", "series": {}, "api_calls": 0}
    dates = []
    for field, series_id in selected.items():
        value = fetch_latest_series(series_id, api_key)
        out["api_calls"] += 1
        out[field] = value.value
        out["series"][field] = {"series_id": series_id, "date": value.date, "value": value.value}
        if value.date:
            dates.append(value.date)
    if "brent_spot_usd_bbl" in out and "wti_spot_usd_bbl" in out:
        out["brent_wti_spread_usd_bbl"] = round(out["brent_spot_usd_bbl"] - out["wti_spot_usd_bbl"], 4)
    out["energy_as_of"] = max(dates) if dates else ""
    return out
