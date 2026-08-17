import pandas as pd

from v182.reporting.sector_rotation_v2_decision_context import build_decision_context
from v182.features.instrument_theme_v2 import build_mapping_worklist


def _sector_rows():
    return pd.DataFrame(
        [
            {
                "sector": "Technology",
                "rank": 1,
                "RLS": 82.0,
                "RARS": 70.0,
                "AVCR": 72.0,
                "state": "MATURE_LEADERSHIP",
                "valuation_state": "OVERVALUATION_WARNING",
                "warnings": "['PROMISING_BUT_OVERVALUED']",
                "correction_alert": False,
                "new_position_action": "NO_CHASE",
                "existing_position_action": "HOLD_MONITOR",
                "as_of": "2026-08-17",
                "model_version": "SECTOR_ROTATION_V2.0_SHADOW",
            },
            {
                "sector": "Financial Services",
                "rank": 2,
                "RLS": 68.0,
                "RARS": 66.0,
                "AVCR": 35.0,
                "state": "CONFIRMED_ROTATION",
                "valuation_state": "NORMAL",
                "warnings": "[]",
                "correction_alert": False,
                "new_position_action": "WATCH",
                "existing_position_action": "HOLD",
                "as_of": "2026-08-17",
                "model_version": "SECTOR_ROTATION_V2.0_SHADOW",
            },
        ]
    )


def _pit():
    return {
        "status": "WAIT_FOR_PIT_HISTORY",
        "holdout_locked": True,
        "promotion_ready": False,
        "decision_influence": 0.0,
    }


def test_action_gets_sector_and_direct_theme_context_without_mutating_decision():
    decisions = pd.DataFrame(
        [{"asset_class": "ACTION", "horizon": "MT", "isin": "A", "name": "Alpha", "decision": "WATCH", "score": 71.0, "sector": "TECH"}]
    )
    actions = pd.DataFrame([{"isin": "A", "sector_yf": "Technology"}])
    tags = pd.DataFrame([{"isin": "A", "theme_id": "SOFTWARE"}])
    themes = pd.DataFrame(
        [{"theme_id": "SOFTWARE", "RLS": 80.0, "AVCR": 70.0, "state": "ACCUMULATION", "warnings": "['PROMISING_BUT_OVERVALUED']", "correction_alert": True}]
    )

    context, summary = build_decision_context(decisions, actions, _sector_rows(), tags, themes, _pit())

    assert len(context) == len(decisions) == 1
    row = context.iloc[0]
    assert row["decision"] == "WATCH"
    assert float(row["score"]) == 71.0
    assert row["sector_v2_name"] == "Technology"
    assert row["sector_v2_context_status"] == "SECTOR_AND_THEME_CONTEXT"
    assert row["theme_v2_ids"] == "SOFTWARE"
    assert row["theme_v2_warning_ids"] == "SOFTWARE"
    assert row["theme_v2_correction_alert_ids"] == "SOFTWARE"
    assert row["sector_v2_decision_influence"] == 0.0
    assert row["sector_v2_score_influence"] == 0.0
    assert row["sector_v2_sizing_influence"] == 0.0
    assert row["sector_v2_stop_loss_influence"] == 0.0
    assert summary["decision_influence"] == 0.0


def test_single_sector_etf_maps_but_multisector_etf_does_not():
    decisions = pd.DataFrame(
        [
            {"asset_class": "ETF", "horizon": "MT", "isin": "E1", "name": "Finance ETF", "decision": "WATCH", "score": 70.0, "sector": "FINANCE"},
            {"asset_class": "ETF", "horizon": "MT", "isin": "E2", "name": "World ETF", "decision": "WATCH", "score": 70.0, "sector": "ETF MULTISECTORIEL / PAYS"},
        ]
    )

    context, _ = build_decision_context(decisions, pd.DataFrame(), _sector_rows(), pd.DataFrame(), pd.DataFrame(), _pit())

    finance = context.loc[context["isin"].eq("E1")].iloc[0]
    world = context.loc[context["isin"].eq("E2")].iloc[0]
    assert finance["sector_v2_name"] == "Financial Services"
    assert finance["sector_v2_context_status"] == "SECTOR_CONTEXT_ONLY"
    assert pd.isna(world["sector_v2_name"])
    assert world["sector_v2_context_status"] == "NO_SINGLE_SECTOR_CONTEXT"


def test_unexpected_sector_promotion_is_blocked_and_never_gains_influence():
    decisions = pd.DataFrame(
        [{"asset_class": "ACTION", "horizon": "CT", "isin": "A", "name": "Alpha", "decision": "BUY_CANDIDATE", "score": 85.0, "sector": "TECH"}]
    )
    actions = pd.DataFrame([{"isin": "A", "sector_yf": "Technology"}])
    pit = {"status": "UNEXPECTED", "promotion_ready": True, "decision_influence": 0.25, "holdout_locked": False}

    context, summary = build_decision_context(decisions, actions, _sector_rows(), pd.DataFrame(), pd.DataFrame(), pit)

    assert summary["status"] == "GOVERNANCE_BREACH_BLOCKED"
    assert summary["promotion_ready"] is False
    assert context.iloc[0]["sector_v2_context_status"] == "GOVERNANCE_BREACH_BLOCKED"
    assert context.iloc[0]["sector_v2_decision_influence"] == 0.0
    assert context.iloc[0]["sector_v2_score_influence"] == 0.0
    assert bool(context.iloc[0]["live_orders_enabled"]) is False


def test_mapping_worklist_counts_governed_direct_tags_as_covered_without_inventing_exposure():
    instruments = pd.DataFrame({"isin": ["A", "B", "C"], "name": ["Alpha", "Beta", "Gamma"], "sector_yf": ["Technology", "Energy", "Utilities"]})
    manual = pd.DataFrame([{"universe": "ACTION", "isin": "A", "theme_id": "AI"}])
    worklist = build_mapping_worklist(instruments, manual, universe="ACTION", covered_isins={"B"})
    assert worklist["isin"].tolist() == ["C"]
    assert worklist.iloc[0]["status"] == "MAPPING_REQUIRED"
