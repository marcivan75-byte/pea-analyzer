from pathlib import Path
import json

import pandas as pd

from v182.features.sector_rotation_v2_final import build_sector_rotation_v2


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))


def _rows(sector: str, offset: float) -> list[dict]:
    return [
        {
            "isin": f"{sector[:2].upper()}{i:010d}",
            "sector_yf": sector,
            "perf_1m_pct": 4.0 + offset + i * 0.1,
            "perf_3m_pct": 9.0 + offset + i * 0.1,
            "perf_6m_pct": 15.0 + offset + i * 0.1,
            "distance_high_52w_pct": 6.0,
            "above_mm50": True,
            "above_mm200": True,
            "rvol20": 1.1 + offset * 0.02,
            "volatility_20d": 20.0 + offset,
            "per_forward_yf": 18.0 + offset,
            "pb": 2.5 + offset * 0.1,
            "revenue_growth_yf": 0.12 + offset * 0.01,
            "earnings_growth_yf": 0.15 + offset * 0.01,
            "broker_weighted_revision_30d": 2.0 + offset,
            "beta": 1.0,
        }
        for i in range(5)
    ]


def test_v2_resolves_actual_ohlcv_volume_and_volatility_fields():
    actions = pd.DataFrame(_rows("Technology", 3.0) + _rows("Industrials", 1.0) + _rows("Banks", -1.0))
    result = build_sector_rotation_v2(actions, CFG, as_of="2026-08-16")
    resolved = result.diagnostic["field_resolution"]
    assert resolved["volume_ratio"] == "rvol20"
    assert resolved["volatility"] == "volatility_20d"
    assert result.sectors["crowding"].notna().all()
    assert result.sectors["volatility_risk"].notna().all()


def test_production_ohlcv_aliases_have_priority():
    aliases = CFG["field_aliases"]
    assert aliases["volume_ratio"][:2] == ["rvol20", "rvol20_3d_avg"]
    assert aliases["volatility"][:3] == ["volatility_20d", "volatility_60d", "volatility_1y_pct"]
