from pathlib import Path
import json

import pandas as pd

from v182.features.theme_rotation_auto_v2 import (
    build_direct_theme_tags,
    build_theme_rotation_shadow,
    load_auto_theme_rules,
)


ROOT = Path(__file__).resolve().parents[1]
CFG = json.loads((ROOT / "config" / "SECTOR_ROTATION_V2_SHADOW.json").read_text(encoding="utf-8"))
RULES = load_auto_theme_rules(ROOT / "config" / "SECTOR_ROTATION_V2_AUTO_THEME_RULES.csv")


def _row(isin: str, industry: str, perf: float) -> dict:
    return {
        "isin": isin,
        "sector_yf": "Technology" if "Software" in industry or "Semiconductor" in industry else "Industrials",
        "industry_yf": industry,
        "perf_1m_pct": perf,
        "perf_3m_pct": perf * 2,
        "perf_6m_pct": perf * 3,
        "distance_high_52w_pct": 5.0,
        "above_mm50": True,
        "above_mm200": True,
        "rvol20": 1.2,
        "volatility_20d": 25.0,
        "per_forward_yf": 25.0,
        "pb": 4.0,
        "revenue_growth_yf": 0.15,
        "earnings_growth_yf": 0.18,
        "broker_weighted_revision_30d": 3.0,
        "beta": 1.0,
    }


def test_direct_rules_tag_only_high_confidence_industry_evidence():
    actions = pd.DataFrame(
        [
            _row("A", "Semiconductors", 10),
            _row("B", "Semiconductor Equipment & Materials", 9),
            _row("C", "Software - Application", 8),
            _row("D", "Information Technology Services", 7),
            _row("E", "Electrical Equipment & Parts", 6),
        ]
    )
    tags, summary = build_direct_theme_tags(actions, RULES)
    assert summary["status"] == "OK"
    mapped = set(zip(tags["isin"], tags["theme_id"]))
    assert ("A", "SEMIS") in mapped
    assert ("B", "SEMIS") in mapped
    assert ("B", "SEMI_EQUIP") in mapped
    assert ("C", "SOFTWARE") in mapped
    assert ("E", "ELECTRIFICATION") in mapped
    assert ("C", "AI") not in mapped
    assert ("D", "DATA_CENTERS") not in mapped
    assert ("E", "GRID") not in mapped
    assert tags["theme_mapping_confidence_pct"].ge(80).all()
    assert (tags["theme_mapping_status"] == "DIRECT_INDUSTRY").all()


def test_theme_scoring_requires_enough_direct_constituents_and_stays_shadow_only():
    actions = pd.DataFrame(
        [
            _row("S1", "Software - Application", 8),
            _row("S2", "Software - Infrastructure", 9),
            _row("S3", "Software - Application", 10),
            _row("B1", "Biotechnology", 2),
            _row("B2", "Biotechnology", 3),
            _row("B3", "Biotechnology", 4),
            _row("X1", "Information Technology Services", 5),
        ]
    )
    themes, summary, tags = build_theme_rotation_shadow(actions, RULES, CFG, as_of="2026-08-16")
    assert summary["theme_scoring_status"] == "OK"
    assert {"SOFTWARE", "BIOTECH"}.issubset(set(themes["theme_id"]))
    assert "AI" not in set(tags["theme_id"])
    assert "DATA_CENTERS" not in set(tags["theme_id"])
    assert (themes["decision_influence"] == 0.0).all()
    assert (themes["mapping_mode"] == "DIRECT_INDUSTRY_ONLY").all()


def test_low_confidence_disabled_rules_are_explicitly_retained_but_never_applied():
    disabled = RULES.loc[RULES["status"].eq("LOW_CONFIDENCE_DISABLED")]
    assert {"AI", "DATA_CENTERS", "GRID", "CYBER"}.issubset(set(disabled["theme_id"]))
    actions = pd.DataFrame([_row("A", "Software - Application", 5)] * 3)
    actions["isin"] = ["A", "B", "C"]
    tags, _ = build_direct_theme_tags(actions, RULES)
    assert "AI" not in set(tags["theme_id"])
    assert "CYBER" not in set(tags["theme_id"])
