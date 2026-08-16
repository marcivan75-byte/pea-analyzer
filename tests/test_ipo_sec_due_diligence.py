from __future__ import annotations

from pathlib import Path
import json

import pandas as pd

from v182.decision.ipo_radar_v1 import _standard_candidate, build_alerts, evaluate_candidates
from v182.sources.sec_ipo import financial_scores, parse_form_index, prospectus_text_scores

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return json.loads((ROOT / "config" / "IPO_RADAR_V1.json").read_text(encoding="utf-8"))


def test_sec_form_index_extracts_only_initial_registration_forms() -> None:
    text = """Form Type   Company Name                                                  CIK        Date Filed  File Name
----------  ------------------------------------------------------------ ----------  ----------  --------------------------------------------
S-1         Alpha Robotics Inc                                           1234567890  2026-08-14  edgar/data/1234567890/0001.txt
S-1/A       Alpha Robotics Inc                                           1234567890  2026-08-15  edgar/data/1234567890/0002.txt
F-1         Euro Cloud PLC                                               9876543210  2026-08-13  edgar/data/9876543210/0003.txt
10-K        Listed Corp                                                  1111111111  2026-08-12  edgar/data/1111111111/0004.txt
"""
    rows = parse_form_index(text)
    assert [(row["form"], row["company"]) for row in rows] == [("S-1", "Alpha Robotics Inc"), ("F-1", "Euro Cloud PLC")]


def test_prospectus_text_extracts_material_risk_signals() -> None:
    text = """
    RISK FACTORS. We have identified a material weakness in our internal control over financial reporting.
    Our dual-class capital structure gives founders enhanced voting rights. Customer concentration is significant.
    We are subject to a regulatory investigation. The selling stockholders will sell shares and we will not receive any proceeds.
    Underwriting. Goldman Sachs & Co. LLC is an underwriter. Lock-up agreements restrict sales for 180 days.
    Use of Proceeds. We intend to invest in research and development and capital expenditures.
    """
    scored = prospectus_text_scores(text)
    assert scored["risk_accounting_controls"] >= 80
    assert scored["risk_governance_dual_class"] >= 80
    assert scored["risk_customer_concentration"] >= 70
    assert scored["risk_regulatory_legal"] >= 70
    assert scored["risk_dilution_secondary"] >= 70
    assert scored["opportunity_underwriter_quality"] >= 85
    assert scored["opportunity_insider_alignment"] >= 75
    assert scored["opportunity_use_of_proceeds_quality"] >= 65


def test_companyfacts_generate_financial_scores_without_lookahead_logic() -> None:
    facts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
                    {"start": "2024-01-01", "end": "2024-12-31", "val": 100_000_000, "fp": "FY", "filed": "2025-03-01"},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 140_000_000, "fp": "FY", "filed": "2026-03-01"},
                ]}},
                "GrossProfit": {"units": {"USD": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 84_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "OperatingIncomeLoss": {"units": {"USD": [
                    {"start": "2024-01-01", "end": "2024-12-31", "val": -30_000_000, "fp": "FY", "filed": "2025-03-01"},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": -14_000_000, "fp": "FY", "filed": "2026-03-01"},
                ]}},
                "NetIncomeLoss": {"units": {"USD": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": -18_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": -20_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
                    {"end": "2025-12-31", "val": 60_000_000, "filed": "2026-03-01"}
                ]}},
                "Assets": {"units": {"USD": [{"end": "2025-12-31", "val": 120_000_000, "filed": "2026-03-01"}]}},
                "Liabilities": {"units": {"USD": [{"end": "2025-12-31", "val": 45_000_000, "filed": "2026-03-01"}]}},
            }
        }
    }
    scores = financial_scores(facts)
    assert scores["sec_revenue_growth_pct"] == 40.0
    assert scores["opportunity_revenue_growth"] >= 90
    assert scores["sec_latest_gross_margin_pct"] == 60.0
    assert scores["sec_cash_runway_years_pre_ipo"] == 3.0
    assert scores["risk_loss_cash_burn"] <= 55


def test_alert_engine_detects_deterioration_and_prospectus_update() -> None:
    current = _standard_candidate(name="Alert SA", symbol="ALT", exchange="NASDAQ", expected_date="2026-09-20", status="expected", price_range="15-17", source="NASDAQ")
    current.update({"sec_cik": "123", "identity_key": "CIK:123", "risk_score": 60, "decision": "WATCH", "sec_accession": "new", "hard_flags": "going_concern"})
    history = pd.DataFrame([{
        "observed_at_utc": "2026-08-15T08:00:00Z", "identity_key": "CIK:123", "candidate_id": "NASDAQ:ALT", "name": "Alert SA",
        "status": "expected", "price_mid": "20", "expected_date": "2026-09-10", "risk_score": "40", "decision": "DEEP_DD",
        "sec_accession": "old", "hard_flags": ""
    }])
    alerts = build_alerts([current], history)
    kinds = {alert["alert"] for alert in alerts}
    assert "PRICE_RANGE_REVISION" in kinds
    assert "IPO_DELAY" in kinds
    assert "RISK_DETERIORATION" in kinds
    assert "DECISION_DOWNGRADE" in kinds
    assert "PROSPECTUS_UPDATE" in kinds
    assert "NEW_HARD_FLAG" in kinds


def test_early_sec_filing_cannot_be_promoted_to_due_diligence_without_readiness() -> None:
    config = _config()
    candidate = _standard_candidate(name="Early Filing Inc", exchange="SEC_PRIVATE", status="filed", source="SEC_EDGAR", sec_cik="999")
    for criterion in config["opportunity_weights"]:
        candidate[f"opportunity_{criterion}"] = 90
    for criterion in config["risk_weights"]:
        candidate[f"risk_{criterion}"] = 10
    evaluated = evaluate_candidates([candidate], config, pd.DataFrame())[0]
    assert evaluated["market_readiness_score"] < 50
    assert evaluated["decision"] == "WATCH_EARLY_FILING"
    assert evaluated["live_order_allowed"] is False
