from __future__ import annotations

from dataclasses import dataclass
import os

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_SERIES = {
    "macro_vix": "VIXCLS",
    "macro_curve_10y2y": "T10Y2Y",
    "macro_cpi_index": "CPIAUCSL",
}


@dataclass(frozen=True)
class FredValue:
    series_id: str
    date: str
    value: float


def fetch_observations(series_id: str, api_key: str, limit: int = 24, timeout: int = 20) -> list[FredValue]:
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
            "limit": max(1, int(limit)),
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
    out: list[FredValue] = []
    for row in observations:
        raw = row.get("value") if isinstance(row, dict) else None
        if raw in (None, "", "."):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        out.append(FredValue(series_id=series_id, date=str(row.get("date") or ""), value=value))
    if not out:
        raise RuntimeError(f"FRED_NO_NUMERIC_OBSERVATION:{series_id}")
    return out


def fetch_latest_observation(series_id: str, api_key: str, timeout: int = 20) -> FredValue:
    return fetch_observations(series_id, api_key, limit=10, timeout=timeout)[0]


def _cpi_yoy(api_key: str, series_id: str = "CPIAUCSL") -> dict:
    observations = fetch_observations(series_id, api_key, limit=18)
    latest = observations[0]
    # CPI is monthly. Select the observation closest to 12 months before the latest month,
    # not merely the 13th raw row, so missing values cannot shift the comparison silently.
    import pandas as pd

    latest_date = pd.Timestamp(latest.date)
    target = latest_date - pd.DateOffset(years=1)
    candidates = []
    for obs in observations[1:]:
        try:
            distance = abs((pd.Timestamp(obs.date) - target).days)
        except Exception:
            continue
        candidates.append((distance, obs))
    if not candidates:
        return {"status": "INSUFFICIENT_HISTORY", "series_id": series_id, "latest": latest.value, "date": latest.date}
    distance, prior = min(candidates, key=lambda x: x[0])
    if distance > 45 or prior.value == 0:
        return {"status": "INSUFFICIENT_12M_MATCH", "series_id": series_id, "latest": latest.value, "date": latest.date}
    yoy = (latest.value / prior.value - 1.0) * 100.0
    return {
        "status": "OK",
        "series_id": series_id,
        "date": latest.date,
        "latest_index": latest.value,
        "prior_12m_date": prior.date,
        "prior_12m_index": prior.value,
        "yoy_pct": round(yoy, 4),
    }


def fetch_macro_context(api_key: str, series: dict[str, str] | None = None) -> dict:
    selected = dict(series or DEFAULT_SERIES)
    # The source document mentions PMI through FRED, but no unstable or obsolete PMI series is
    # hard-coded. Operators may set a current FRED series explicitly if a durable free series is
    # validated later. This prevents stale historical PMI from being presented as current data.
    pmi_series = str(os.getenv("FRED_PMI_SERIES_ID") or "").strip()
    if pmi_series:
        selected["macro_pmi"] = pmi_series

    out = {"source": "FRED", "status": "OK", "series": {}, "api_calls": 0}
    dates = []
    for field, series_id in selected.items():
        if field == "macro_cpi_index":
            inflation = _cpi_yoy(api_key, series_id)
            out["api_calls"] += 1
            out["macro_cpi_index"] = inflation.get("latest_index")
            out["macro_inflation_yoy_pct"] = inflation.get("yoy_pct")
            out["series"][field] = inflation
            if inflation.get("date"):
                dates.append(str(inflation["date"]))
            continue
        value = fetch_latest_observation(series_id, api_key)
        out["api_calls"] += 1
        out[field] = value.value
        out["series"][field] = {"series_id": series_id, "date": value.date, "value": value.value, "status": "OK"}
        if value.date:
            dates.append(value.date)
    out["macro_pmi_status"] = "OBSERVED" if "macro_pmi" in out else "NO_STABLE_DEFAULT_SERIES_CONFIGURED"
    out["macro_as_of"] = max(dates) if dates else ""
    return out
