from __future__ import annotations
from dataclasses import dataclass
import math

FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"

# Risk-regime subset used only for the global macro block. Country macro remains
# separately identified and can use market-implied fallback until ECB/Eurostat
# direct feeds are wired.
GLOBAL_SERIES = {
    "VIXCLS": {"weight": 0.40, "direction": "LOW"},
    "BAMLH0A0HYM2": {"weight": 0.35, "direction": "LOW"},
    "T10Y2Y": {"weight": 0.25, "direction": "HIGH"},
}


@dataclass(frozen=True)
class MacroScore:
    score: float | None
    coverage: float
    components: dict[str, float]
    errors: dict[str, str]
    source: str


def _series_score(values: list[float], direction: str) -> float | None:
    if len(values) < 20:
        return None
    latest=values[-1]
    ordered=sorted(values)
    rank=sum(v <= latest for v in ordered) / len(ordered) * 100.0
    return round(100.0-rank if direction == "LOW" else rank, 4)


def fetch_series(series_id: str, api_key: str, *, limit: int = 180, timeout: int = 20) -> tuple[list[float], str | None]:
    import requests
    try:
        response=requests.get(
            FRED_OBSERVATIONS,
            params={
                "series_id":series_id,
                "api_key":api_key,
                "file_type":"json",
                "sort_order":"desc",
                "limit":limit,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        observations=response.json().get("observations",[])
        values=[]
        for row in reversed(observations):
            raw=row.get("value") if isinstance(row,dict) else None
            try:
                values.append(float(raw))
            except (TypeError,ValueError):
                continue
        return values,None
    except Exception as exc:
        return [],f"{type(exc).__name__}: {str(exc)[:180]}"


def global_macro_score(api_key: str | None) -> MacroScore:
    if not api_key:
        return MacroScore(None,0.0,{}, {"FRED_API_KEY":"MISSING"}, "FRED")
    components={}; errors={}; weighted=0.0; denom=0.0; total=sum(v["weight"] for v in GLOBAL_SERIES.values())
    for series_id,spec in GLOBAL_SERIES.items():
        values,error=fetch_series(series_id,api_key)
        if error:
            errors[series_id]=error
            continue
        score=_series_score(values,spec["direction"])
        if score is None:
            errors[series_id]="INSUFFICIENT_HISTORY"
            continue
        components[series_id]=score
        w=float(spec["weight"])
        weighted+=score*w
        denom+=w
    score=round(weighted/denom,4) if denom else None
    return MacroScore(score,round(denom/total,4) if total else 0.0,components,errors,"FRED")
