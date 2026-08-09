from __future__ import annotations

from datetime import datetime, timezone
import math

import requests

UA = "PEA-Analyzer-V20.5-Eurostat/1.0"


def _float(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


def _period_age_days(period: str) -> int | None:
    try:
        # Monthly HICP is considered observed at month end for freshness.
        y, m = [int(x) for x in str(period).split("-")[:2]]
        if m == 12:
            next_month = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(y, m + 1, 1, tzinfo=timezone.utc)
        month_end = next_month.timestamp() - 86400
        return max(0, int((datetime.now(timezone.utc).timestamp() - month_end) / 86400))
    except Exception:
        return None


def eurostat_hicp(country: str, cfg: dict) -> dict:
    ecfg = cfg["eurostat"]
    dataset = ecfg["hicp_dataset"]
    url = f"https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
    dimension = str(ecfg.get("coicop_dimension") or "coicop18")
    category = str(ecfg.get("coicop") or "TOTAL")
    try:
        params = {
            "lang": "en",
            "unit": ecfg["unit"],
            dimension: category,
            "geo": country,
        }
        r = requests.get(url, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        d = r.json()
        values = d.get("value", {})
        time_dim = d.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
        if isinstance(time_dim, list):
            time_map = {str(v): i for i, v in enumerate(time_dim)}
        else:
            time_map = {str(k): int(v) for k, v in time_dim.items()}
        candidates: list[tuple[str, float]] = []
        for period, pos in time_map.items():
            raw = values.get(str(pos), values.get(pos))
            v = _float(raw)
            if v is not None:
                candidates.append((period, v))
        if not candidates:
            raise RuntimeError("Eurostat HICP empty")
        period, value = sorted(candidates, key=lambda x: x[0])[-1]
        age = _period_age_days(period)
        max_age = int(ecfg.get("max_age_days", 55))
        if age is None or age > max_age:
            return {
                "status": "STALE",
                "country": country,
                "hicp_yoy_pct": value,
                "period": period,
                "age_days": age,
                "max_age_days": max_age,
                "inflation_score": None,
                "source": url,
                "dimension": dimension,
                "category": category,
            }
        score = _clip(100.0 - abs(value - 2.0) * 15.0, 20.0, 90.0)
        return {
            "status": "OK",
            "country": country,
            "hicp_yoy_pct": value,
            "period": period,
            "age_days": age,
            "max_age_days": max_age,
            "inflation_score": round(score, 2),
            "source": url,
            "dimension": dimension,
            "category": category,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "country": country,
            "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            "source": url,
            "dimension": dimension,
            "category": category,
        }
