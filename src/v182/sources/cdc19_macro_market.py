from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd

ECB_DATA = "https://data-api.ecb.europa.eu/service/data"
EUROSTAT_STATS = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
EIA_BASE = "https://api.eia.gov/v2/seriesid"
MARKETSTACK_LATEST = "https://api.marketstack.com/v2/eod/latest"

ECB_SERIES = {
    "ecb_deposit_facility_rate_pct": ("FM", "D.U2.EUR.4F.KR.DFR.LEV"),
    "ecb_mro_rate_pct": ("FM", "D.U2.EUR.4F.KR.MRR_FR.LEV"),
}
EIA_SERIES = {
    "eia_wti_spot_usd_bbl": "PET.RWTC.D",
    "eia_brent_spot_usd_bbl": "PET.RBRTE.D",
}


def _requests(requests_module=None):
    if requests_module is not None:
        return requests_module
    import requests
    return requests


def _latest_csv_value(text: str) -> tuple[float | None, str | None]:
    try:
        frame = pd.read_csv(StringIO(text))
    except Exception:
        return None, None
    if frame.empty:
        return None, None
    value_col = next((c for c in ("OBS_VALUE", "obs_value", "value", "VALUE") if c in frame.columns), None)
    time_col = next((c for c in ("TIME_PERIOD", "time_period", "period", "DATE") if c in frame.columns), None)
    if value_col is None:
        return None, None
    work = frame.copy()
    work["_value"] = pd.to_numeric(work[value_col], errors="coerce")
    work = work[work["_value"].notna()]
    if work.empty:
        return None, None
    if time_col:
        work["_time"] = work[time_col].astype(str)
        work = work.sort_values("_time")
    row = work.iloc[-1]
    return float(row["_value"]), str(row.get(time_col, "")) if time_col else None


def fetch_ecb_rates(*, requests_module=None, timeout: int = 20) -> tuple[dict[str, Any], list[dict]]:
    requests_module = _requests(requests_module)
    fields: dict[str, Any] = {}
    failures: list[dict] = []
    as_of: list[str] = []
    for field, (flow, key) in ECB_SERIES.items():
        try:
            response = requests_module.get(
                f"{ECB_DATA}/{flow}/{key}",
                params={"format": "csvdata", "lastNObservations": 2, "detail": "dataonly"},
                timeout=timeout,
            )
            response.raise_for_status()
            value, period = _latest_csv_value(response.text)
            if value is None:
                failures.append({"source": "ECB", "field": field, "reason": "NO_NUMERIC_OBSERVATION"})
                continue
            fields[field] = value
            if period:
                as_of.append(period)
        except Exception as exc:
            failures.append({"source": "ECB", "field": field, "reason": type(exc).__name__, "detail": str(exc)[:180]})
    if as_of:
        fields["ecb_macro_as_of"] = max(as_of)
    return fields, failures


def _jsonstat_latest(payload: dict) -> tuple[float | None, str | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), list) or not isinstance(payload.get("size"), list):
        return None, None
    ids = payload["id"]
    sizes = payload["size"]
    if "time" not in ids:
        return None, None
    dimension = payload.get("dimension", {})
    time_index = dimension.get("time", {}).get("category", {}).get("index", {})
    if not isinstance(time_index, dict) or not time_index:
        return None, None
    time_positions = {int(pos): str(label) for label, pos in time_index.items() if isinstance(pos, int)}
    if not time_positions:
        return None, None
    time_axis = ids.index("time")
    values = payload.get("value")
    if isinstance(values, list):
        iterable = ((i, v) for i, v in enumerate(values))
    elif isinstance(values, dict):
        iterable = ((int(i), v) for i, v in values.items() if str(i).isdigit())
    else:
        return None, None
    strides = []
    for axis in range(len(sizes)):
        stride = 1
        for later in sizes[axis + 1:]:
            stride *= int(later)
        strides.append(stride)
    candidates = []
    for flat_index, raw in iterable:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        time_pos = (flat_index // strides[time_axis]) % int(sizes[time_axis])
        label = time_positions.get(time_pos)
        if label is not None:
            candidates.append((label, value))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    period, value = candidates[-1]
    return value, period


def fetch_eurostat_hicp(*, requests_module=None, timeout: int = 20) -> tuple[dict[str, Any], list[dict]]:
    """Fetch euro-area all-items HICP annual rate from Eurostat JSON-stat.

    Dataset prc_hicp_manr is the monthly annual rate of change. Filters keep the
    request small; no missing value is replaced with a neutral macro value.
    """
    requests_module = _requests(requests_module)
    try:
        response = requests_module.get(
            f"{EUROSTAT_STATS}/prc_hicp_manr",
            params={"format": "JSON", "lang": "en", "geo": "EA20", "coicop": "CP00", "lastTimePeriod": 3},
            timeout=timeout,
        )
        response.raise_for_status()
        value, period = _jsonstat_latest(response.json())
        if value is None:
            return {}, [{"source": "Eurostat", "field": "eurostat_hicp_yoy_pct", "reason": "NO_NUMERIC_OBSERVATION"}]
        return {"eurostat_hicp_yoy_pct": value, "eurostat_hicp_as_of": period}, []
    except Exception as exc:
        return {}, [{"source": "Eurostat", "field": "eurostat_hicp_yoy_pct", "reason": type(exc).__name__, "detail": str(exc)[:180]}]


def _eia_latest(payload: dict) -> tuple[float | None, str | None]:
    rows = payload.get("response", {}).get("data", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return None, None
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("value")
        if raw is None:
            for key in ("price", "Value", "VALUE"):
                if key in row:
                    raw = row[key]
                    break
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        period = str(row.get("period") or row.get("date") or "")
        candidates.append((period, value))
    if not candidates:
        return None, None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1], candidates[-1][0]


def fetch_eia_energy(api_key: str | None, *, requests_module=None, timeout: int = 20) -> tuple[dict[str, Any], list[dict]]:
    if not api_key:
        return {}, [{"source": "EIA", "reason": "EIA_API_KEY_MISSING"}]
    requests_module = _requests(requests_module)
    fields: dict[str, Any] = {}
    failures: list[dict] = []
    as_of = []
    for field, series_id in EIA_SERIES.items():
        try:
            response = requests_module.get(f"{EIA_BASE}/{series_id}", params={"api_key": api_key}, timeout=timeout)
            response.raise_for_status()
            value, period = _eia_latest(response.json())
            if value is None:
                failures.append({"source": "EIA", "field": field, "reason": "NO_NUMERIC_OBSERVATION"})
                continue
            fields[field] = value
            if period:
                as_of.append(period)
        except Exception as exc:
            failures.append({"source": "EIA", "field": field, "reason": type(exc).__name__, "detail": str(exc)[:180]})
    if as_of:
        fields["eia_energy_as_of"] = max(as_of)
    return fields, failures


def fetch_marketstack_latest(
    tickers: list[str],
    api_key: str | None,
    *,
    batch_size: int = 50,
    max_requests: int = 2,
    requests_module=None,
    timeout: int = 20,
) -> tuple[list[dict], list[dict], dict]:
    """Fetch a bounded Marketstack EOD fallback for rows still missing prices.

    Calls are deliberately bounded because the current free plan is request
    limited. This function is a fallback only and never replaces observed Yahoo
    data of equal or better freshness.
    """
    if not api_key:
        return [], [{"source": "Marketstack", "reason": "MARKETSTACK_API_KEY_MISSING"}], {"requested": len(tickers), "attempted_requests": 0}
    requests_module = _requests(requests_module)
    unique = sorted({str(t).strip() for t in tickers if str(t).strip()})
    observations: list[dict] = []
    failures: list[dict] = []
    attempted = 0
    for start in range(0, len(unique), max(1, int(batch_size))):
        if attempted >= max(1, int(max_requests)):
            break
        batch = unique[start:start + max(1, int(batch_size))]
        attempted += 1
        try:
            response = requests_module.get(
                MARKETSTACK_LATEST,
                params={"access_key": api_key, "symbols": ",".join(batch), "limit": len(batch)},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and payload.get("error"):
                failures.append({"source": "Marketstack", "reason": "API_ERROR", "detail": str(payload.get("error"))[:180]})
                continue
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                failures.append({"source": "Marketstack", "reason": "INVALID_PAYLOAD"})
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol") or "").strip()
                if not symbol:
                    continue
                observations.append({
                    "ticker": symbol,
                    "date": str(row.get("date") or "")[:10],
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                })
        except Exception as exc:
            failures.append({"source": "Marketstack", "reason": type(exc).__name__, "detail": str(exc)[:180]})
    return observations, failures, {
        "requested_tickers": len(unique),
        "attempted_requests": attempted,
        "observed_rows": len(observations),
        "bounded_fallback": True,
    }
