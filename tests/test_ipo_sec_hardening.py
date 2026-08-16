from __future__ import annotations

from v182.sources.sec_ipo import financial_scores, registration_candidates


def test_already_listed_cik_is_not_emitted_as_early_ipo_candidate() -> None:
    rows = [
        {"form": "S-1", "company": "Listed Follow On Inc", "cik": "123", "filed": "2026-08-15", "filename": "a.txt"},
        {"form": "S-1", "company": "True Private IPO Inc", "cik": "456", "filed": "2026-08-15", "filename": "b.txt"},
    ]
    candidates = registration_candidates(rows, {"123"})
    assert [candidate["sec_cik"] for candidate in candidates] == ["456"]


def test_ifrs_companyfacts_are_supported_for_f1_issuers() -> None:
    facts = {
        "facts": {
            "ifrs-full": {
                "Revenue": {"units": {"EUR": [
                    {"start": "2024-01-01", "end": "2024-12-31", "val": 100_000_000, "fp": "FY", "filed": "2025-03-01"},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 140_000_000, "fp": "FY", "filed": "2026-03-01"},
                ]}},
                "GrossProfit": {"units": {"EUR": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 70_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "ProfitLossFromOperatingActivities": {"units": {"EUR": [
                    {"start": "2024-01-01", "end": "2024-12-31", "val": -10_000_000, "fp": "FY", "filed": "2025-03-01"},
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 7_000_000, "fp": "FY", "filed": "2026-03-01"},
                ]}},
                "ProfitLoss": {"units": {"EUR": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 5_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "CashFlowsFromUsedInOperatingActivities": {"units": {"EUR": [
                    {"start": "2025-01-01", "end": "2025-12-31", "val": 8_000_000, "fp": "FY", "filed": "2026-03-01"}
                ]}},
                "CashAndCashEquivalents": {"units": {"EUR": [{"end": "2025-12-31", "val": 40_000_000, "filed": "2026-03-01"}]}},
                "Assets": {"units": {"EUR": [{"end": "2025-12-31", "val": 120_000_000, "filed": "2026-03-01"}]}},
                "Liabilities": {"units": {"EUR": [{"end": "2025-12-31", "val": 50_000_000, "filed": "2026-03-01"}]}},
            }
        }
    }
    scores = financial_scores(facts)
    assert scores["sec_revenue_growth_pct"] == 40.0
    assert scores["opportunity_revenue_growth"] >= 90
    assert scores["sec_latest_gross_margin_pct"] == 50.0
    assert scores["risk_loss_cash_burn"] == 15.0
