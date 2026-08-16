from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pandas as pd

from v182.decision.ipo_radar_operational_v1_1 import (
    build_alerts,
    classify_candidate,
    history_rows_full,
    parse_date_strict,
)
from v182.reporting import unified_runner

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def test_unified_runner_routes_to_v1_3_runtime_explicitly() -> None:
    assert unified_runner.ipo_radar_v1.__name__.endswith("ipo_radar_v1_3")


def test_date_parser_handles_european_dates_without_ambiguous_pandas_first_pass() -> None:
    assert parse_date_strict("16/08/2026") == date(2026, 8, 16)
    assert parse_date_strict("08/20/2026") == date(2026, 8, 20)
    assert parse_date_strict("2026-08-16") == date(2026, 8, 16)


def test_market_readiness_threshold_is_driven_by_referential() -> None:
    config = _config()
    config["decision_thresholds"]["priority_dd"]["market_readiness_min"] = 90
    config["decision_thresholds"]["deep_dd"]["market_readiness_min"] = 80
    row = {
        "status": "expected",
        "hard_flags": "",
        "opportunity_score": 90,
        "risk_score": 10,
        "net_ipo_score": 90,
        "market_readiness_score": 70,
        "opportunity_coverage_pct": 100,
        "risk_coverage_pct": 100,
    }
    assert classify_candidate(row, config) == "WATCH"


def test_new_candidate_hard_flag_is_immediately_critical() -> None:
    row = {
        "identity_key": "CIK:123",
        "candidate_id": "SEC:123",
        "name": "Fragile IPO Inc",
        "decision": "AVOID_HARD_BLOCK",
        "status": "filed",
        "hard_flags": "going_concern",
    }
    alerts = build_alerts([row], pd.DataFrame())
    kinds = {(alert["alert"], alert["severity"]) for alert in alerts}
    assert ("NEW_CANDIDATE", "MEDIUM") in kinds
    assert ("NEW_HARD_FLAG", "CRITICAL") in kinds


def test_full_history_preserves_euronext_and_sec_evidence() -> None:
    rows = [{
        "identity_key": "ISIN:FR0000000001",
        "candidate_id": "EURONEXT:TEST",
        "name": "Test SA",
        "symbol": "TEST",
        "isin": "FR0000000001",
        "exchange": "EURONEXT",
        "euronext_location": "Paris",
        "issuer_country": "FR",
        "decision": "WATCH",
        "live_order_allowed": False,
        "sec_sic": "7372",
        "sec_prospectus_url": "https://example.invalid/prospectus",
        "opportunity_revenue_growth": 80,
        "risk_valuation": 40,
    }]
    history = history_rows_full(rows, "2026-08-16T08:00:00Z")
    assert history.loc[0, "euronext_location"] == "Paris"
    assert history.loc[0, "isin"] == "FR0000000001"
    assert history.loc[0, "sec_sic"] == "7372"
    assert history.loc[0, "opportunity_revenue_growth"] == 80
    assert history.loc[0, "risk_valuation"] == 40
