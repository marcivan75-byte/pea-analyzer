from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def synthetic_snapshot(count: int = 10, *, as_of: str = "2026-08-24T20:00:00Z") -> dict[str, Any]:
    base = datetime(2025, 8, 25, tzinfo=timezone.utc).timestamp() * 1000
    assets: dict[str, Any] = {}
    for index in range(count):
        asset_id = "bitcoin" if index == 0 else f"asset-{index:03d}"
        price_base = 50_000.0 if index == 0 else 10.0 + index
        trend = 0.0012 - index * 0.000002
        history = []
        for day in range(365):
            price = price_base * math.exp(trend * day) * (1.0 + math.sin(day / 13.0 + index) * 0.025)
            history.append({
                "ts": int(base + day * 86_400_000),
                "price": price,
                "market_cap": 50_000_000_000 + index * 1_000_000_000,
                "volume": 600_000_000 + index * 5_000_000 + math.sin(day / 7.0) * 40_000_000,
            })
        spec = {"id": asset_id, "symbol": "BTC" if index == 0 else f"A{index:03d}", "name": asset_id, "category": "L1", "contract": None}
        assets[asset_id] = {
            "spec": spec,
            "history": history,
            "market": {
                "price": history[-1]["price"], "market_cap": history[-1]["market_cap"], "market_cap_rank": index + 1,
                "fdv": history[-1]["market_cap"] * 1.15,
                "volume_24h": history[-1]["volume"], "circulating_supply": 850_000_000, "total_supply": 1_000_000_000,
                "last_updated": as_of, "source": "COINGECKO",
            },
            "venues": {
                "binance": {"price": history[-1]["price"] * 1.0002, "spread_bps": 1.5},
                "kraken": {"price": history[-1]["price"] * 0.9998, "spread_bps": 2.0},
            },
            "derivatives": {"last_funding_rate": 0.0001, "funding_history": [0.00008 + math.sin(i) * 0.00002 for i in range(60)]},
            "network": {
                "coinmetrics": {
                    "AdrActCnt": [100_000 + i * 120 + index for i in range(90)],
                    "TxCnt": [500_000 + i * 500 + index for i in range(90)],
                    "FeeTotUSD": [1_000_000 + i * 1200 + index for i in range(90)],
                },
                "chain_tvl": [5_000_000_000 + i * 10_000_000 + index for i in range(90)],
            },
            "evidence": [{"type": "CATALYST", "score": 68.0, "severity": "LOW"}],
        }
    return {"schema_version": "CRYPTO_SNAPSHOT_V1", "as_of": as_of, "assets": assets, "source_status": {"SYNTHETIC": {"state": "OK"}}}
