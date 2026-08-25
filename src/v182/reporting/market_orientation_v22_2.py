from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
import json
import os

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
VERSION = "V22_2_MARKET_ORIENTATION_SHADOW"
CACHE_TTL_HOURS = 6.0
STALE_FALLBACK_MAX_HOURS = 72.0
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
VSTOXX_OFFICIAL_SYMBOL = "V2TX"
VSTOXX_YAHOO_CANDIDATES = ("V2TX.DE", "^V2TX", "^V2X")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_path(root: Path) -> Path:
    return root / "state" / "provenance" / "market_orientation" / "MARKET_ORIENTATION_V22_2_CACHE.json"


def _read_cache(root: Path) -> dict:
    path = _cache_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_cache(root: Path, payload: dict) -> None:
    path = _cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _cache_age_hours(cache: dict) -> float | None:
    stamp = pd.to_datetime(cache.get("collected_at_utc"), errors="coerce", utc=True)
    if pd.isna(stamp):
        return None
    return max(0.0, float((_now() - stamp.to_pydatetime()).total_seconds() / 3600.0))


def _fresh_cache(cache: dict) -> bool:
    age = _cache_age_hours(cache)
    indicators = cache.get("indicators") if isinstance(cache, dict) else None
    return age is not None and age <= CACHE_TTL_HOURS and isinstance(indicators, dict) and bool(indicators)


def _float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if pd.notna(x) else None


def _fetch_vix_fred() -> dict:
    key = str(os.environ.get("FRED_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY_MISSING")
    response = requests.get(
        FRED_URL,
        params={
            "series_id": "VIXCLS",
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        },
        timeout=12,
    )
    response.raise_for_status()
    rows = response.json().get("observations", [])
    for row in rows:
        value = _float(row.get("value"))
        if value is not None:
            return {
                "value": round(value, 4),
                "as_of": str(row.get("date") or ""),
                "source": "FRED:VIXCLS",
                "source_status": "LIVE",
            }
    raise RuntimeError("FRED_VIXCLS_NO_NUMERIC_OBSERVATION")


def _fetch_cnn_fear_greed() -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.cnn.com",
        "Referer": "https://www.cnn.com/",
    }
    response = requests.get(CNN_URL, headers=headers, timeout=12)
    response.raise_for_status()
    block = response.json().get("fear_and_greed", {})
    value = _float(block.get("score"))
    if value is None:
        raise RuntimeError("CNN_FEAR_GREED_NO_SCORE")
    return {
        "value": round(value, 4),
        "rating": str(block.get("rating") or "").strip().upper(),
        "previous_close": _float(block.get("previous_close")),
        "previous_1_week": _float(block.get("previous_1_week")),
        "previous_1_month": _float(block.get("previous_1_month")),
        "as_of": str(block.get("timestamp") or ""),
        "source": "CNN_FEAR_AND_GREED",
        "source_status": "LIVE",
    }


def _latest_close_from_frame(frame) -> tuple[float | None, str | None]:
    if frame is None or frame.empty or "Close" not in frame:
        return None, None
    close = frame["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = pd.to_numeric(close, errors="coerce").dropna()
    if close.empty:
        return None, None
    stamp = pd.Timestamp(close.index[-1])
    return float(close.iloc[-1]), stamp.date().isoformat()


def _fetch_vstoxx() -> dict:
    """Fetch current VSTOXX with bounded Yahoo fallbacks.

    STOXX identifies the official 30-day VSTOXX symbol as V2TX. Yahoo coverage is
    not guaranteed to expose downloadable history consistently, so we first try a
    short history and then a live/fast-info quote. Missing data fail closed and may
    use the governed <=72h cache fallback in ``run``.
    """
    import yfinance as yf

    errors: list[str] = []
    for symbol in VSTOXX_YAHOO_CANDIDATES:
        try:
            frame = yf.download(
                symbol,
                period="10d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=False,
            )
            value, as_of = _latest_close_from_frame(frame)
            if value is not None:
                return {
                    "value": round(value, 4),
                    "as_of": as_of,
                    "source": f"YAHOO:{symbol}:HISTORY",
                    "official_symbol": VSTOXX_OFFICIAL_SYMBOL,
                    "source_status": "LIVE",
                }
        except Exception as exc:
            errors.append(f"{symbol}:history:{type(exc).__name__}:{str(exc)[:90]}")

        try:
            ticker = yf.Ticker(symbol)
            fast = ticker.fast_info
            value = _float(fast.get("last_price") if hasattr(fast, "get") else getattr(fast, "last_price", None))
            if value is not None:
                return {
                    "value": round(value, 4),
                    "as_of": _now().date().isoformat(),
                    "source": f"YAHOO:{symbol}:FAST_INFO",
                    "official_symbol": VSTOXX_OFFICIAL_SYMBOL,
                    "source_status": "LIVE",
                }
            errors.append(f"{symbol}:fast_info:NO_VALUE")
        except Exception as exc:
            errors.append(f"{symbol}:fast_info:{type(exc).__name__}:{str(exc)[:90]}")

    raise RuntimeError("VSTOXX_NO_LIVE_VALUE:" + " | ".join(errors)[:420])


def _volatility_regime(value: float | None, *, europe: bool = False) -> str:
    if value is None:
        return "UNKNOWN"
    low, high = ((20.0, 30.0) if europe else (17.0, 25.0))
    if value < low:
        return "RISK_ON"
    if value <= high:
        return "NEUTRAL"
    return "RISK_OFF"


def _sentiment_regime(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 45.0:
        return "RISK_OFF"
    if value <= 55.0:
        return "NEUTRAL"
    return "RISK_ON"


def _vote(regimes: list[str]) -> str:
    clean = [r for r in regimes if r in {"RISK_ON", "NEUTRAL", "RISK_OFF"}]
    if not clean:
        return "UNKNOWN"
    score = sum({"RISK_ON": 1, "NEUTRAL": 0, "RISK_OFF": -1}[r] for r in clean)
    if score > 0:
        return "RISK_ON"
    if score < 0:
        return "RISK_OFF"
    return "NEUTRAL"


def _stale_fallback(name: str, cache: dict, error: Exception) -> dict:
    age = _cache_age_hours(cache)
    cached = (cache.get("indicators") or {}).get(name) if isinstance(cache, dict) else None
    if age is not None and age <= STALE_FALLBACK_MAX_HOURS and isinstance(cached, dict) and _float(cached.get("value")) is not None:
        result = dict(cached)
        result["source_status"] = "STALE_FALLBACK"
        result["fallback_age_hours"] = round(age, 3)
        result["live_error"] = f"{type(error).__name__}: {str(error)[:180]}"
        return result
    return {
        "value": None,
        "as_of": None,
        "source_status": "MISSING",
        "live_error": f"{type(error).__name__}: {str(error)[:180]}",
    }


def run(root: Path = ROOT) -> dict:
    """Collect three lightweight market-orientation indicators in shadow mode.

    This module is deliberately independent of WAVE09. It does not mutate masters,
    scores, criteria, weights, thresholds, decisions, or orders.
    """
    started = perf_counter()
    cache = _read_cache(root)
    cache_age = _cache_age_hours(cache)
    cache_hit = _fresh_cache(cache)
    source_seconds: dict[str, float] = {}

    if cache_hit:
        indicators = dict(cache.get("indicators") or {})
        for value in indicators.values():
            if isinstance(value, dict):
                value["source_status"] = "CACHE_FRESH"
    else:
        jobs = {
            "vix": _fetch_vix_fred,
            "cnn_fear_greed": _fetch_cnn_fear_greed,
            "vstoxx": _fetch_vstoxx,
        }
        indicators: dict[str, dict] = {}

        def one(name, fn):
            t0 = perf_counter()
            try:
                return name, fn(), None, perf_counter() - t0
            except Exception as exc:
                return name, None, exc, perf_counter() - t0

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="market-orientation") as pool:
            futures = [pool.submit(one, name, fn) for name, fn in jobs.items()]
            for future in futures:
                name, value, error, seconds = future.result()
                source_seconds[name] = round(float(seconds), 6)
                indicators[name] = value if error is None else _stale_fallback(name, cache, error)

    vix = _float((indicators.get("vix") or {}).get("value"))
    cnn = _float((indicators.get("cnn_fear_greed") or {}).get("value"))
    vstoxx = _float((indicators.get("vstoxx") or {}).get("value"))
    regimes = {
        "vix": _volatility_regime(vix, europe=False),
        "cnn_fear_greed": _sentiment_regime(cnn),
        "vstoxx": _volatility_regime(vstoxx, europe=True),
    }
    us_orientation = _vote([regimes["vix"], regimes["cnn_fear_greed"]])
    europe_orientation = regimes["vstoxx"]
    global_orientation = _vote([regimes["vix"], regimes["cnn_fear_greed"], regimes["vstoxx"]])
    overheat = bool(cnn is not None and cnn > 75.0)
    extreme_fear = bool(cnn is not None and cnn < 25.0)

    payload = {
        "version": VERSION,
        "status": "SUCCESS",
        "generated_at_utc": _now().isoformat(),
        "cache": {
            "hit": cache_hit,
            "ttl_hours": CACHE_TTL_HOURS,
            "previous_age_hours": round(cache_age, 3) if cache_age is not None else None,
            "stale_fallback_max_hours": STALE_FALLBACK_MAX_HOURS,
        },
        "indicators": indicators,
        "regimes": regimes,
        "orientation": {
            "us": us_orientation,
            "europe": europe_orientation,
            "global": global_orientation,
            "cnn_extreme_greed_overheat_warning": overheat,
            "cnn_extreme_fear_warning": extreme_fear,
        },
        "source_seconds": source_seconds,
        "total_seconds": round(float(perf_counter() - started), 6),
        "governance": {
            "wave09_dependency": False,
            "shadow_only": True,
            "selection_score_changed": False,
            "selection_decision_changed": False,
            "criteria_changed": False,
            "weights_changed": False,
            "thresholds_changed": False,
            "real_orders_enabled": False,
            "fred_series": ["VIXCLS"],
            "vstoxx_official_symbol": VSTOXX_OFFICIAL_SYMBOL,
        },
    }

    if not cache_hit and any(_float((row or {}).get("value")) is not None for row in indicators.values()):
        _write_cache(root, {
            "version": VERSION,
            "collected_at_utc": _now().isoformat(),
            "indicators": indicators,
        })

    audit_dir = root / "outputs" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "MARKET_ORIENTATION_V22_2.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    ci_dir = root / "outputs" / "committee_master"
    ci_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "generated_at_utc": payload["generated_at_utc"],
        "vix": vix,
        "vix_regime": regimes["vix"],
        "cnn_fear_greed": cnn,
        "cnn_regime": regimes["cnn_fear_greed"],
        "cnn_overheat_warning": overheat,
        "vstoxx": vstoxx,
        "vstoxx_regime": regimes["vstoxx"],
        "orientation_us": us_orientation,
        "orientation_europe": europe_orientation,
        "orientation_global": global_orientation,
        "shadow_only": True,
    }]).to_csv(ci_dir / "CI_MARKET_ORIENTATION_V22_2.csv", sep=";", index=False, encoding="utf-8-sig")
    return payload


if __name__ == "__main__":
    print(json.dumps(run(ROOT), ensure_ascii=False, indent=2))
