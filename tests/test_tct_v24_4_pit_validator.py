from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from v182.reporting.tct_v24_4_pit_validator_runtime import validate_ledger


ROOT = Path(__file__).resolve().parents[1]


def _gates() -> dict:
    return json.loads((ROOT / "config" / "TCT_V24_4_0_VALIDATION_GATES.json").read_text(encoding="utf-8"))


def _mature_good_ledger() -> pd.DataFrame:
    rows = []
    for session in range(15):
        day = pd.Timestamp("2026-06-01") + pd.offsets.BDay(session)
        for rank in range(20):
            # V24.4 is deliberately aligned with the realized amplitude;
            # technical-only comparator is deliberately anti-ranked.
            abs_return = 6.0 - rank * 0.20
            realized = abs_return if rank % 2 == 0 else -abs_return
            movement = 96.0 - rank * 3.0
            technical = 35.0 + rank * 2.0
            direction = 60.0 if realized > 0 else -60.0
            technical_direction = -direction
            rows.append(
                {
                    "snapshot_key": f"{day.date()}|PREOPEN|FR{rank:03d}",
                    "snapshot_generated_at_utc": f"{day.date()}T06:40:00+00:00",
                    "phase": "PREOPEN",
                    "isin": f"FR{rank:03d}",
                    "movement_potential_score": movement,
                    "technical_impulse_score": technical,
                    "direction_bias_score": direction,
                    "technical_direction_score": technical_direction,
                    "catalyst_state": "UP_CATALYST_SHADOW" if realized > 0 else "DOWN_CATALYST_SHADOW",
                    "realized_abs_return_pct": abs_return,
                    "realized_close_to_close_return_pct": realized,
                    "sector_yf": "TECH" if rank % 2 == 0 else "INDUSTRIAL",
                    "entry_state": "ENTRY_READY_SHADOW",
                    "global_risk_on_score": 65.0,
                    "days_to_earnings": 3.0,
                    "news_event_types": "MAJOR_CONTRACT" if rank < 5 else "OTHER_NEWS",
                }
            )
    return pd.DataFrame(rows)


def test_empty_ledger_is_not_mature_and_cannot_pass():
    payload, slices, changes = validate_ledger(pd.DataFrame(), _gates())
    assert payload["maturity"]["status"] == "NOT_MATURE_ACCUMULATING_PIT"
    assert payload["research_verdict"]["status"] == "NOT_EVALUABLE_BEFORE_MATURITY"
    assert payload["research_verdict"]["promotion_authority"] is False
    assert slices.empty
    assert changes.empty


def test_maturity_requires_all_pre_registered_gates():
    ledger = _mature_good_ledger().iloc[:59].copy()
    payload, _, _ = validate_ledger(ledger, _gates())
    assert payload["maturity"]["all_maturity_gates_passed"] is False
    assert payload["research_verdict"]["status"] == "NOT_EVALUABLE_BEFORE_MATURITY"


def test_mature_good_ledger_beats_technical_baseline_without_promotion():
    payload, slices, _ = validate_ledger(_mature_good_ledger(), _gates())
    maturity = payload["maturity"]
    primary = payload["primary_metrics"]
    secondary = payload["secondary_metrics"]
    verdict = payload["research_verdict"]

    assert maturity["all_maturity_gates_passed"] is True
    assert maturity["values"]["labeled_preopen_rows"] == 300
    assert maturity["values"]["distinct_isins"] == 20
    assert maturity["values"]["observed_sessions"] == 15
    assert primary["top10_recall_improvement_pp_vs_technical"] > 0
    assert primary["top_decile_lift_improvement_vs_technical"] > 0
    assert primary["spearman_improvement_vs_technical"] > 0
    assert secondary["direction_hit_rate_confident_biases"] == 1.0
    assert secondary["technical_only_direction_hit_rate"] == 0.0
    assert secondary["false_high_potential_rate"] <= 0.60
    assert verdict["status"] == "RESEARCH_CRITERIA_MET"
    assert verdict["movement_validation"] == "PASS"
    assert verdict["direction_validation"] == "PASS"
    assert verdict["promotion_authority"] is False
    assert not slices.empty
    assert {"NEWS_EVENT_TYPE", "SECTOR", "ENTRY_STATE", "MARKET_REGIME", "EARNINGS_PROXIMITY"}.issubset(set(slices["slice_type"]))


def test_future_labels_do_not_enter_validator_before_they_exist():
    ledger = _mature_good_ledger().copy()
    ledger.loc[ledger.index[:50], "realized_abs_return_pct"] = np.nan
    ledger.loc[ledger.index[:50], "realized_close_to_close_return_pct"] = np.nan
    payload, _, _ = validate_ledger(ledger, _gates())
    assert payload["labeled_rows_used"] == 250
    assert payload["unlabeled_preopen_rows"] == 50


def test_postmarket_rows_are_never_counted_as_target_pit_sample():
    ledger = _mature_good_ledger().copy()
    extra = ledger.iloc[:30].copy()
    extra["phase"] = "POSTMARKET"
    extra["snapshot_generated_at_utc"] = extra["snapshot_generated_at_utc"].str.replace("T06:40:00", "T21:15:00", regex=False)
    combined = pd.concat([ledger, extra], ignore_index=True)
    payload, _, _ = validate_ledger(combined, _gates())
    assert payload["labeled_rows_used"] == len(ledger)
