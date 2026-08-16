from pathlib import Path
import json

import pandas as pd

from v182.features.sector_rotation_v2 import append_history, build_sector_rotation_v2


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))


def _sector_rows(name: str, n: int, **kw):
    rows = []
    for i in range(n):
        row = {
            "isin": f"{name[:3].upper()}{i:09d}",
            "sector": name,
            "perf_1m_pct": kw.get("perf_1m_pct", 5.0) + i * 0.05,
            "perf_3m_pct": kw.get("perf_3m_pct", 8.0) + i * 0.05,
            "perf_6m_pct": kw.get("perf_6m_pct", 12.0) + i * 0.05,
            "distance_high_52w_pct": kw.get("distance_high_52w_pct", 4.0),
            "above_mm50": kw.get("above_mm50", True),
            "above_mm200": kw.get("above_mm200", True),
            "forward_pe": kw.get("forward_pe", 20.0) + i * 0.02,
            "price_to_book": kw.get("price_to_book", 3.0),
            "price_to_sales": kw.get("price_to_sales", 4.0),
            "revenue_growth_pct": kw.get("revenue_growth_pct", 12.0),
            "earnings_growth_pct": kw.get("earnings_growth_pct", 15.0),
            "eps_revision_pct": kw.get("eps_revision_pct", 3.0),
            "volume_ratio_20d": kw.get("volume_ratio_20d", 1.10),
            "volatility_20d_pct": kw.get("volatility_20d_pct", 22.0),
            "beta_3y": kw.get("beta_3y", 1.0),
        }
        rows.append(row)
    return rows


def test_v2_builds_explainable_shadow_scores_without_mutating_input():
    rows = []
    rows += _sector_rows("Semiconductors", 8, perf_1m_pct=14, perf_3m_pct=28, forward_pe=46, price_to_sales=12, revenue_growth_pct=14, earnings_growth_pct=16, eps_revision_pct=4, volume_ratio_20d=1.8)
    rows += _sector_rows("Electrical Grid", 8, perf_1m_pct=9, perf_3m_pct=14, forward_pe=22, price_to_sales=3, revenue_growth_pct=18, earnings_growth_pct=22, eps_revision_pct=8, volume_ratio_20d=1.35)
    rows += _sector_rows("Banks", 8, perf_1m_pct=2, perf_3m_pct=5, forward_pe=10, price_to_sales=2, revenue_growth_pct=4, earnings_growth_pct=5, eps_revision_pct=-1, volume_ratio_20d=0.9)
    actions = pd.DataFrame(rows)
    before = actions.copy(deep=True)

    result = build_sector_rotation_v2(actions, CFG, as_of="2026-08-16")

    pd.testing.assert_frame_equal(actions, before)
    assert result.diagnostic["status"] == "OK"
    assert result.diagnostic["mode"] == "SHADOW_ONLY"
    assert len(result.sectors) == 3
    required = {"RLS", "SQS", "CTS", "STS", "MCS", "AVCR", "DQS", "RARS", "state", "warnings", "new_position_action", "existing_position_action"}
    assert required.issubset(set(result.sectors.columns))
    for col in ("RLS", "SQS", "CTS", "STS", "MCS", "AVCR", "DQS", "RARS"):
        assert result.sectors[col].between(0, 100).all()


def test_expensive_sector_has_higher_adjusted_correction_risk_than_cheaper_peer():
    rows = []
    rows += _sector_rows("Expensive Tech", 10, perf_1m_pct=18, perf_3m_pct=35, forward_pe=60, price_to_book=15, price_to_sales=18, revenue_growth_pct=8, earnings_growth_pct=9, eps_revision_pct=1, volume_ratio_20d=2.0)
    rows += _sector_rows("Reasonable Infra", 10, perf_1m_pct=10, perf_3m_pct=17, forward_pe=18, price_to_book=2.5, price_to_sales=2.2, revenue_growth_pct=19, earnings_growth_pct=23, eps_revision_pct=8, volume_ratio_20d=1.25)
    rows += _sector_rows("Neutral", 10, perf_1m_pct=4, perf_3m_pct=8, forward_pe=25, price_to_book=4, price_to_sales=5, revenue_growth_pct=10, earnings_growth_pct=10, eps_revision_pct=2, volume_ratio_20d=1.0)

    result = build_sector_rotation_v2(pd.DataFrame(rows), CFG, as_of="2026-08-16")
    out = result.sectors.set_index("sector")
    assert out.loc["Expensive Tech", "AVCR"] > out.loc["Reasonable Infra", "AVCR"]
    assert out.loc["Expensive Tech", "valuation_justification"] < out.loc["Reasonable Infra", "valuation_justification"]


def test_missing_families_reduce_dqs_and_do_not_create_high_conviction_decision():
    actions = pd.DataFrame(
        [{"isin": f"X{i}", "sector": "Sparse", "perf_1m_pct": 12 + i} for i in range(5)]
        + [{"isin": f"Y{i}", "sector": "Sparse2", "perf_1m_pct": 2 + i} for i in range(5)]
        + [{"isin": f"Z{i}", "sector": "Sparse3", "perf_1m_pct": -2 + i} for i in range(5)]
    )
    result = build_sector_rotation_v2(actions, CFG, as_of="2026-08-16")
    assert (result.sectors["DQS"] < CFG["governance"]["minimum_dqs_for_decision"]).all()
    assert not result.sectors["new_position_action"].isin(["PRIORITY_BUY_ZONE", "BUY_ZONE"]).any()


def test_history_drives_velocity_without_lookahead():
    rows = []
    rows += _sector_rows("Sector A", 8, perf_1m_pct=12, perf_3m_pct=18, revenue_growth_pct=18, earnings_growth_pct=20, eps_revision_pct=7)
    rows += _sector_rows("Sector B", 8, perf_1m_pct=4, perf_3m_pct=8, revenue_growth_pct=8, earnings_growth_pct=9, eps_revision_pct=1)
    rows += _sector_rows("Sector C", 8, perf_1m_pct=-2, perf_3m_pct=-4, revenue_growth_pct=2, earnings_growth_pct=1, eps_revision_pct=-4, above_mm50=False)
    current = build_sector_rotation_v2(pd.DataFrame(rows), CFG, as_of="2026-08-16").sectors
    history = current.copy()
    history["as_of"] = "2026-08-09"
    history["RLS"] = history["RLS"] - 10.0
    history["breadth_score"] = history["breadth_score"]

    rerun = build_sector_rotation_v2(pd.DataFrame(rows), CFG, history=history, as_of="2026-08-16").sectors
    assert (rerun["RLS_velocity"] >= 9.9).all()


def test_history_append_is_idempotent_for_same_sector_date_version(tmp_path):
    rows = []
    rows += _sector_rows("A", 4)
    rows += _sector_rows("B", 4, perf_1m_pct=1)
    rows += _sector_rows("C", 4, perf_1m_pct=-1)
    snap = build_sector_rotation_v2(pd.DataFrame(rows), CFG, as_of="2026-08-16").sectors
    path = tmp_path / "history.csv"
    append_history(snap, path)
    append_history(snap, path)
    history = pd.read_csv(path, sep=";", encoding="utf-8-sig")
    assert len(history) == len(snap)
