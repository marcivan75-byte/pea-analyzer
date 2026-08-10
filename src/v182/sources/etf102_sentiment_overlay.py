from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pandas as pd

from v182.sources.market_sentiment import collect_market_sentiment

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
AUDIT = ROOT / "outputs/audit/V20.4.3_ETF102_SENTIMENT.json"


def _median(df: pd.DataFrame, field: str) -> float | None:
    if field not in df.columns:
        return None
    x = pd.to_numeric(df[field], errors="coerce").dropna()
    return float(x.median()) if not x.empty else None


def _first(df: pd.DataFrame, field: str) -> str:
    if field not in df.columns:
        return ""
    x = df[field].astype("string").dropna().str.strip()
    x = x[x.ne("") & ~x.str.lower().isin({"nan", "none", "null", "n/a"})]
    return str(x.iloc[0]) if not x.empty else ""


def _fallback(df: pd.DataFrame) -> dict | None:
    fg = _median(df, "v211_fear_greed_index")
    spread = _median(df, "v211_aaii_bull_bear_spread")
    if fg is None and spread is None:
        return None
    return {
        "status": "V21.1_FREE_CAPTURE_FALLBACK",
        "collected_at_utc": _first(df, "v211_context_generated_at_utc") or datetime.now(timezone.utc).isoformat(),
        "fear_greed": {
            "score": fg,
            "rating": _first(df, "v211_fear_greed_rating"),
            "asof": _first(df, "v211_fear_greed_asof"),
            "source": "V21.1_COMPLEMENTARY_CONTEXT",
        },
        "aaii": {
            "bullish_pct": _median(df, "v211_aaii_bullish_pct"),
            "neutral_pct": _median(df, "v211_aaii_neutral_pct"),
            "bearish_pct": _median(df, "v211_aaii_bearish_pct"),
            "bull_bear_spread": spread,
            "asof": _first(df, "v211_aaii_asof"),
            "source": "V21.1_COMPLEMENTARY_CONTEXT",
        },
    }


def main() -> None:
    if not TARGET.exists():
        raise RuntimeError(f"ETF102 target missing: {TARGET}")
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("ETF102 sentiment overlay canonical gate failed")

    fallback_used = False
    live_error = ""
    try:
        sentiment = collect_market_sentiment()
    except Exception as exc:
        live_error = f"{type(exc).__name__}: {str(exc)[:240]}"
        sentiment = _fallback(df)
        if sentiment is None:
            raise RuntimeError(f"ETF102 sentiment live and V21.1 fallback unavailable: {live_error}") from exc
        fallback_used = True

    fg, aaii = sentiment["fear_greed"], sentiment["aaii"]
    values = {
        "fear_greed_index": fg.get("score"),
        "fear_greed_rating": fg.get("rating"),
        "fear_greed_asof": fg.get("asof"),
        "fear_greed_source": fg.get("source"),
        "aaii_bullish_pct": aaii.get("bullish_pct"),
        "aaii_neutral_pct": aaii.get("neutral_pct"),
        "aaii_bearish_pct": aaii.get("bearish_pct"),
        "aaii_bull_bear_spread": aaii.get("bull_bear_spread"),
        "aaii_asof": aaii.get("asof"),
        "aaii_source": aaii.get("source"),
        "sentiment_data_status": sentiment.get("status"),
        "sentiment_collected_at_utc": sentiment.get("collected_at_utc"),
    }
    for key, value in values.items():
        df[key] = value
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")

    payload = {
        **sentiment,
        "rows": len(df),
        "legacy_266_used": False,
        "target": TARGET.name,
        "v211_fallback_used": fallback_used,
        "live_error": live_error,
    }
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V20.4.3_ETF102_SENTIMENT_OK", json.dumps({
        "fear_greed": fg.get("score"),
        "aaii_spread": aaii.get("bull_bear_spread"),
        "v211_fallback": fallback_used,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
