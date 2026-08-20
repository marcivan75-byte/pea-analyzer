from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v182.features.etf_ct_lt_shadow_v21_14 import score_ct_lt_shadow, score_horizon_shadow

ROOT = Path(__file__).resolve().parents[1]


def _registry() -> dict:
    return json.loads((ROOT / "config" / "V20_7_1_ETF_CRITERIA_REGISTRY.json").read_text(encoding="utf-8"))


def _cfg() -> dict:
    return json.loads((ROOT / "config" / "ETF_CT_LT_SHADOW_V21_14.json").read_text(encoding="utf-8"))


def _frame(rows: int = 102) -> pd.DataFrame:
    registry = _registry()
    fields = sorted(set(registry["weights"]["CT"]) | set(registry["weights"]["LT"]))
    data: dict[str, object] = {
        "isin": [f"FR{i:010d}" for i in range(rows)],
        "name": [f"ETF {i}" for i in range(rows)],
    }
    for idx, field in enumerate(fields):
        if field == "distribution_policy":
            data[field] = ["ACC" if i % 2 == 0 else "DIST" for i in range(rows)]
        else:
            data[field] = np.linspace(1.0 + idx, 10.0 + idx, rows)
    return pd.DataFrame(data)


def test_registry_ct_lt_weights_are_preserved_exactly():
    registry = _registry()
    for horizon in ("CT", "LT"):
        assert abs(sum(float(v) for v in registry["weights"][horizon].values()) - 1.0) < 1e-6
        assert set(registry["weights"][horizon]) == set(registry["directions"][horizon])
    assert registry["governance"]["t1_t2_forbidden"] is True


def test_ct_lt_never_emit_buy_sell_or_real_orders():
    rows, summary = score_ct_lt_shadow(_frame(), _registry(), _cfg())
    assert not rows["shadow_context"].astype(str).str.contains("BUY|SELL", case=False, regex=True).any()
    assert not rows["real_orders_allowed"].any()
    assert not rows["promotion_allowed"].any()
    assert not rows["t1_t2_enabled"].any()
    assert rows["decision_influence"].eq(0.0).all()
    assert rows["mt_reference_score_influence"].eq(0.0).all()
    assert summary["real_orders_allowed"] is False
    assert summary["t1_t2_forbidden"] is True
    assert summary["stress_calibration_weight"] == 0.0


def test_missing_high_weight_fields_block_instead_of_neutral_imputation():
    frame = _frame()
    ct_weights = _registry()["weights"]["CT"]
    remaining_weight = 1.0
    for field, weight in sorted(ct_weights.items(), key=lambda item: item[1], reverse=True):
        if field in frame.columns and field != "distribution_policy":
            frame.loc[0, field] = np.nan
            remaining_weight -= float(weight)
            if remaining_weight < 0.70:
                break
    scored, _ = score_horizon_shadow(frame, _registry(), _cfg(), "CT")
    first = scored.loc[scored["isin"].eq(frame.loc[0, "isin"])].iloc[0]
    assert float(first["weighted_coverage"]) < 0.70
    assert pd.isna(first["shadow_score"])
    assert first["shadow_context"] == "DATA_INSUFFICIENT"
    assert first["neutral_imputation_used"] == False


def test_distribution_policy_is_not_invented_into_numeric_score():
    scored, summary = score_horizon_shadow(_frame(), _registry(), _cfg(), "LT")
    assert "distribution_policy" in summary["unsupported_categorical_fields"]
    assert "distribution_policy" in summary["blocked_cross_section_criteria"]
    expected_max = 1.0 - float(_registry()["weights"]["LT"]["distribution_policy"])
    assert scored["weighted_coverage"].max() <= expected_max + 1e-9


def test_sparse_cross_section_field_is_removed_from_score_denominator():
    frame = _frame()
    field = "perf_1m_pct"
    frame[field] = np.nan
    frame.loc[0, field] = 999.0
    scored, summary = score_horizon_shadow(frame, _registry(), _cfg(), "CT")
    assert summary["minimum_cross_section_observations"] == 21
    assert summary["cross_section_observed_counts"][field] == 1
    assert field in summary["blocked_cross_section_criteria"]
    expected_max = 1.0 - float(_registry()["weights"]["CT"][field])
    assert scored["weighted_coverage"].max() <= expected_max + 1e-9


def test_low_direction_is_ranked_in_reverse():
    frame = _frame()
    for field in _registry()["weights"]["CT"]:
        if field in frame.columns and field != "ter_pct":
            frame[field] = 1.0
    frame["ter_pct"] = np.arange(1, len(frame) + 1, dtype=float)
    scored, _ = score_horizon_shadow(frame, _registry(), _cfg(), "CT")
    low_ter = scored.loc[scored["isin"].eq(frame.iloc[0]["isin"]), "shadow_score"].iloc[0]
    high_ter = scored.loc[scored["isin"].eq(frame.iloc[-1]["isin"]), "shadow_score"].iloc[0]
    assert low_ter > high_ter


def test_contract_fails_closed_if_t1_t2_enabled():
    cfg = _cfg()
    cfg["governance"]["t1_t2_forbidden"] = False
    with pytest.raises(ValueError, match="T1_T2"):
        score_horizon_shadow(_frame(), _registry(), cfg, "CT")


def test_contract_fails_closed_on_weight_drift():
    registry = _registry()
    first = next(iter(registry["weights"]["LT"]))
    registry["weights"]["LT"][first] += 0.01
    with pytest.raises(ValueError, match="WEIGHT_TOTAL_DRIFT"):
        score_horizon_shadow(_frame(), registry, _cfg(), "LT")


def test_contract_fails_closed_on_neutral_imputation_or_stress_weight():
    cfg = _cfg()
    cfg["scoring"]["neutral_imputation"] = True
    with pytest.raises(ValueError, match="NEUTRAL_IMPUTATION"):
        score_horizon_shadow(_frame(), _registry(), cfg, "CT")
    cfg = _cfg()
    cfg["validation"]["stress_calibration_weight"] = 0.01
    with pytest.raises(ValueError, match="STRESS_WEIGHT"):
        score_horizon_shadow(_frame(), _registry(), cfg, "LT")


def test_legacy_thresholds_are_context_only_and_history_gates_preserved():
    cfg = _cfg()
    for horizon in ("CT", "LT"):
        assert cfg["horizons"][horizon]["historical_performance_attribution"].startswith("NONE_")
    assert cfg["scoring"]["threshold_role"] == "CONTEXT_BANDS_ONLY_NOT_BUY_RULES"
    assert cfg["governance"]["can_create_buy"] is False
    assert cfg["governance"]["can_create_sell"] is False
    assert cfg["governance"]["weights_changed"] is False
    assert cfg["governance"]["thresholds_changed"] is False
    assert cfg["validation"]["v21_13_history_depth_gates_apply_to_future_validation"] is True
    assert cfg["validation"]["primary_calibration_eligible_status"] == "PRIMARY_FULL_FROM_ANCHOR"
    assert cfg["validation"]["stress_library_eligible_status"] == "STRESS_FULL_2020_2022"
