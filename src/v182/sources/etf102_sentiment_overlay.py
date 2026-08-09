from __future__ import annotations

from pathlib import Path
import json
import pandas as pd

from v182.sources.market_sentiment import collect_market_sentiment

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "outputs/V20.4.3_ETF102_DIRECT_ENRICHED.csv"
AUDIT = ROOT / "outputs/audit/V20.4.3_ETF102_SENTIMENT.json"


def main() -> None:
    if not TARGET.exists():
        raise RuntimeError(f"ETF102 target missing: {TARGET}")
    sentiment = collect_market_sentiment()
    fg, aaii = sentiment["fear_greed"], sentiment["aaii"]
    df = pd.read_csv(TARGET, sep=";", dtype=object, encoding="utf-8-sig", low_memory=False)
    if len(df) != 102 or df["isin"].astype(str).nunique() != 102:
        raise RuntimeError("ETF102 sentiment overlay canonical gate failed")
    values = {
        "fear_greed_index": fg["score"],
        "fear_greed_rating": fg["rating"],
        "fear_greed_asof": fg["asof"],
        "fear_greed_source": fg["source"],
        "aaii_bullish_pct": aaii["bullish_pct"],
        "aaii_neutral_pct": aaii["neutral_pct"],
        "aaii_bearish_pct": aaii["bearish_pct"],
        "aaii_bull_bear_spread": aaii["bull_bear_spread"],
        "aaii_asof": aaii["asof"],
        "aaii_source": aaii["source"],
        "sentiment_data_status": sentiment["status"],
        "sentiment_collected_at_utc": sentiment["collected_at_utc"],
    }
    for key, value in values.items():
        df[key] = value
    df.to_csv(TARGET, sep=";", index=False, encoding="utf-8-sig")
    payload = {**sentiment, "rows": len(df), "legacy_266_used": False, "target": TARGET.name}
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("V20.4.3_ETF102_SENTIMENT_OK", json.dumps({"fear_greed": fg["score"], "aaii_spread": aaii["bull_bear_spread"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
