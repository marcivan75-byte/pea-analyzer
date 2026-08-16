from pathlib import Path
import json

import pandas as pd

from v182.features.sector_rotation_v2_final import build_sector_rotation_v2


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))


def _rows(sector: str, offset: float) -> list[dict]:
    rows = []
    for i in range(5):
        rows.append(
            {
                "isin": f"{sector[:2].upper()}{i:010d}",
                "sector_yf": sector,
                "perf_1m_pct": 4.0 + offset + i * 0.1,
                "perf_3m_pct": 9.0 + offset + i * 0.1,
                "distance_high_52w_pct": 6.0,
                "above_mm50": True,
                "above_mm200": True,
                "per_forward_yf": 18.0 + offset,
                "per_ttm_yf": 20.0 + offset,
                "pb": 2.5 + offset * 0.1,
                "revenue_growth_yf": 0.12 + offset * 0.01,
                "earnings_growth_yf": 0.15 + offset * 0.01,
                "broker_weighted_revision_30d": 2.0 + offset,
                "beta": 1.0,
            }
        )
    return rows


def test_v2_aliases_resolve_actual_v18_2_yfinance_and_finnhub_fields():
    actions = pd.DataFrame(_rows("Technology", 3.0) + _rows("Industrials", 1.0) + _rows("Banks", -1.0))
    result = build_sector_rotation_v2(actions, CFG, as_of="2026-08-16")
    resolved = result.diagnostic["field_resolution"]
    assert resolved["pe"] == "per_forward_yf"
    assert resolved["pb"] == "pb"
    assert resolved["revenue_growth"] == "revenue_growth_yf"
    assert resolved["earnings_growth"] == "earnings_growth_yf"
    assert resolved["eps_revision"] == "broker_weighted_revision_30d"
    assert result.sectors["data_completeness_pct"].min() > 40.0
    assert result.sectors["RLS_coverage"].min() > 50.0


def test_production_aliases_precede_generic_legacy_aliases():
    aliases = CFG["field_aliases"]
    assert aliases["pe"][:2] == ["per_forward_yf", "per_ttm_yf"]
    assert aliases["pb"][0] == "pb"
    assert aliases["revenue_growth"][0] == "revenue_growth_yf"
    assert aliases["earnings_growth"][0] == "earnings_growth_yf"
    assert aliases["eps_revision"][:2] == ["broker_weighted_revision_30d", "consensus_delta_4w"]
