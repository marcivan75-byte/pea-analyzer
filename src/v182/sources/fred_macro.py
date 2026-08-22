from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

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
    if len(values) < 20: return None
    latest=values[-1]; ordered=sorted(values); rank=sum(v <= latest for v in ordered) / len(ordered) * 100.0
    return round(100.0-rank if direction == "LOW" else rank, 4)


def fetch_series(series_id: str, api_key: str, *, limit: int = 180, timeout: int = 20) -> tuple[list[float], str | None]:
    import requests
    try:
        response=requests.get(FRED_OBSERVATIONS,params={"series_id":series_id,"api_key":api_key,"file_type":"json","sort_order":"desc","limit":limit},timeout=timeout)
        response.raise_for_status(); observations=response.json().get("observations",[]); values=[]
        for row in reversed(observations):
            raw=row.get("value") if isinstance(row,dict) else None
            try: values.append(float(raw))
            except (TypeError,ValueError): continue
        return values,None
    except Exception as exc:
        return [],f"{type(exc).__name__}: {str(exc)[:180]}"


def global_macro_score(api_key: str | None) -> MacroScore:
    """Score the same three FRED series with bounded independent I/O overlap."""
    if not api_key: return MacroScore(None,0.0,{}, {"FRED_API_KEY":"MISSING"}, "FRED")
    components={}; errors={}; weighted=0.0; denom=0.0; total=sum(v["weight"] for v in GLOBAL_SERIES.values())

    workers=max(1,min(3,len(GLOBAL_SERIES)))
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="fred-global") as pool:
        futures={series_id:pool.submit(fetch_series,series_id,api_key) for series_id in GLOBAL_SERIES}
        # Consume in governed GLOBAL_SERIES order so diagnostics and floating-point
        # accumulation remain deterministic even though network I/O overlaps.
        for series_id,spec in GLOBAL_SERIES.items():
            values,error=futures[series_id].result()
            if error: errors[series_id]=error; continue
            score=_series_score(values,spec["direction"])
            if score is None: errors[series_id]="INSUFFICIENT_HISTORY"; continue
            components[series_id]=score; w=float(spec["weight"]); weighted+=score*w; denom+=w
    score=round(weighted/denom,4) if denom else None
    return MacroScore(score,round(denom/total,4) if total else 0.0,components,errors,"FRED")
