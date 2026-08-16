from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from v182.decision.ipo_identity_resolution_v1 import resolve_symbol_date_conflicts
from v182.decision import ipo_radar_stabilized_v1_1 as stabilized

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def _id_builder(row: dict) -> str:
    exchange = str(row.get("exchange") or "UNKNOWN").upper().replace(" ", "")
    symbol = str(row.get("symbol") or row.get("name") or "UNKNOWN").upper().replace(" ", "")
    return f"{exchange}:{symbol}"


def test_same_us_symbol_and_date_with_materially_different_names_is_one_quarantined_event() -> None:
    rows = [
        {
            "candidate_id": "NASDAQ:PTT",
            "identity_key": "NAME:PTTPCL",
            "name": "Ptt PCL",
            "symbol": "PTT",
            "exchange": "NASDAQ",
            "expected_date": "2026-09-08",
            "status": "expected",
            "sources": "ALPHA_VANTAGE",
            "source_count": 1,
            "price_low": 10.0,
        },
        {
            "candidate_id": "NASDAQCAPITAL:PTT",
            "identity_key": "NAME:SIYATAPTT",
            "name": "SIYATA PTT",
            "symbol": "PTT",
            "exchange": "NASDAQ Capital",
            "expected_date": "2026-09-08",
            "status": "expected",
            "sources": "FINNHUB",
            "source_count": 1,
            "price_high": 12.0,
        },
    ]
    resolved = resolve_symbol_date_conflicts(rows, _id_builder)
    assert len(resolved) == 1
    result = resolved[0]
    assert result["name"] == "SIYATA PTT"
    assert result["sources"] == "FINNHUB|ALPHA_VANTAGE"
    assert result["source_count"] == 2
    assert result["identity_name_conflict"] is True
    assert "Ptt PCL" in result["identity_conflict_names"]
    assert "SIYATA PTT" in result["identity_conflict_names"]
    assert result["price_low"] == 10.0
    assert result["price_high"] == 12.0


def test_near_equivalent_issuer_names_merge_without_identity_quarantine() -> None:
    rows = [
        {
            "name": "Alpha Robotics Inc",
            "symbol": "ALPH",
            "exchange": "NASDAQ",
            "expected_date": "2026-09-15",
            "sources": "FINNHUB",
            "source_count": 1,
        },
        {
            "name": "Alpha Robotics",
            "symbol": "ALPH",
            "exchange": "NASDAQ Global Market",
            "expected_date": "2026-09-15",
            "sources": "ALPHA_VANTAGE",
            "source_count": 1,
        },
    ]
    resolved = resolve_symbol_date_conflicts(rows, _id_builder)
    assert len(resolved) == 1
    assert resolved[0]["identity_name_conflict"] is False


def test_identity_conflict_overrides_positive_score_but_not_hard_block() -> None:
    config = _config()
    good = {
        "status": "expected",
        "hard_flags": "",
        "identity_name_conflict": True,
        "opportunity_score": 95,
        "risk_score": 10,
        "net_ipo_score": 93,
        "market_readiness_score": 95,
        "opportunity_coverage_pct": 100,
        "risk_coverage_pct": 100,
    }
    assert stabilized.classify_candidate_stabilized(good, config) == "WATCH_IDENTITY_CONFLICT"
    blocked = dict(good)
    blocked["hard_flags"] = "going_concern"
    assert stabilized.classify_candidate_stabilized(blocked, config) == "AVOID_HARD_BLOCK"


def test_new_identity_conflict_emits_high_alert() -> None:
    row = {
        "identity_key": "NAME:SIYATAPTT",
        "candidate_id": "NASDAQCAPITAL:PTT",
        "name": "SIYATA PTT",
        "decision": "WATCH_IDENTITY_CONFLICT",
        "status": "expected",
        "hard_flags": "",
        "identity_name_conflict": True,
        "identity_conflict_names": "SIYATA PTT|Ptt PCL",
        "identity_conflict_sources": "FINNHUB|ALPHA_VANTAGE",
    }
    alerts = stabilized.build_alerts_stabilized([row], pd.DataFrame())
    identity = [alert for alert in alerts if alert["alert"] == "IDENTITY_CONFLICT"]
    assert len(identity) == 1
    assert identity[0]["severity"] == "HIGH"
    assert "SIYATA PTT" in identity[0]["detail"]
