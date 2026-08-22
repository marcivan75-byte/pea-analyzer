from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.committee_ci_explainability_v21_16 import (
    _attach_provenance_preserving_internal,
    _report_context,
    _source_validation,
    _tct_exact_details,
    _tct_reference_integrity,
)


def test_pending_buy_is_translated_only_for_human_facing_context():
    context = pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "decision": "BUY_CANDIDATE", "score": 82.0},
        {"asset_class": "ACTION", "horizon": "CT", "isin": "FR2", "decision": "WATCH", "score": 75.0},
    ])
    source = pd.DataFrame([
        {"asset_class": "ACTION", "horizon": "CT", "isin": "FR1", "ci_final_status": "BUY_INTERNE_ATTENTE_SOURCES"},
        {"asset_class": "ACTION", "horizon": "CT", "isin": "FR2", "ci_final_status": "SURVEILLANCE_INTERNE"},
    ])
    original = context.copy(deep=True)
    displayed = _report_context(context, source)
    assert displayed.loc[0, "decision"] == "BUY_WAIT_SOURCE_CONFIRMATION"
    assert displayed.loc[1, "decision"] == "WATCH"
    assert context.equals(original)


def test_tct_t2_exact_components_reconstruct_published_score():
    selected = pd.DataFrame([
        {
            "asset_class": "ACTION",
            "horizon": "TCT",
            "isin": "FR-T2",
            "name": "T2 Test",
            "decision": "T2_CONFIRM_75_SHADOW",
            "score": 80.5,
            "setup": "T2_CONFIRMATION",
            "t2_component_bandwidth_expansion": 90.0,
            "t2_component_macd_confirmation": 80.0,
            "t2_component_volume_persistence": 70.0,
            "t2_component_breakout_hold": 85.0,
            "t2_component_relative_strength_continuation": 75.0,
            "t2_component_non_extension": 82.5,
        }
    ])
    detail = _tct_exact_details(selected)
    assert len(detail) == 6
    assert set(detail["criterion_status"]) == {"ACTIVE"}
    reconstructed = pd.to_numeric(detail["weighted_contribution_points"], errors="coerce").sum()
    expected = 90 * 0.25 + 80 * 0.20 + 70 * 0.20 + 85 * 0.15 + 75 * 0.10 + 82.5 * 0.10
    assert abs(reconstructed - expected) < 1e-12
    assert abs(detail["effective_weight_pct"].sum() - 100.0) < 1e-12


def test_tct_exact_components_renormalize_missing_weight_like_engine():
    selected = pd.DataFrame([
        {
            "asset_class": "ACTION",
            "horizon": "TCT",
            "isin": "FR-T1",
            "name": "T1 Test",
            "decision": "T1_STARTER_25_SHADOW",
            "score": 80.0,
            "setup": "T1",
            "t1_component_compression": 80.0,
            "t1_component_volume_acceleration": 80.0,
            "t1_component_breakout_quality": 80.0,
            "t1_component_momentum_acceleration": 80.0,
            "t1_component_relative_strength": 80.0,
            "t1_component_risk_control": None,
        }
    ])
    detail = _tct_exact_details(selected)
    active = detail[detail["criterion_status"] == "ACTIVE"]
    assert len(active) == 5
    assert abs(active["effective_weight_pct"].sum() - 100.0) < 1e-9
    assert abs(pd.to_numeric(active["weighted_contribution_points"]).sum() - 80.0) < 1e-9


def _integrity_selected() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "asset_class": "ACTION",
            "horizon": "TCT",
            "isin": "FR-T2",
            "decision": "T2_CONFIRM_75_SHADOW",
            "score": 80.0,
        }
    ])


def _integrity_detail(*, include_baseline: bool = True, active_weight_pct: float = 100.0) -> pd.DataFrame:
    rows = []
    remaining = active_weight_pct
    for idx, weight in enumerate((25.0, 20.0, 20.0, 15.0, 10.0, 10.0)):
        active = remaining > 0
        used = min(weight, remaining) if active else 0.0
        remaining -= used
        rows.append({
            "asset_class": "ACTION",
            "horizon": "TCT",
            "isin": "FR-T2",
            "source": "TCT_V24_1_7_EXACT_COMPONENTS",
            "criterion_status": "ACTIVE" if used == weight else "MISSING",
            "theoretical_weight_pct": weight,
        })
    if include_baseline:
        rows.append({
            "asset_class": "ACTION",
            "horizon": "TCT",
            "isin": "FR-T2",
            "source": "TCT_BASELINE_V24_1_8",
            "criterion_status": "CONTEXT_GATE",
            "theoretical_weight_pct": 100.0,
        })
    return pd.DataFrame(rows)


def test_tct_reference_integrity_requires_exact_and_baseline_families():
    complete = _tct_reference_integrity(_integrity_selected(), _integrity_detail())
    assert complete["complete"] is True
    assert complete["missing_exact_keys"] == []
    assert complete["missing_baseline_keys"] == []

    no_baseline = _tct_reference_integrity(_integrity_selected(), _integrity_detail(include_baseline=False))
    assert no_baseline["complete"] is False
    assert no_baseline["missing_baseline_keys"] == [["ACTION", "TCT", "FR-T2"]]


def test_tct_reference_integrity_blocks_exact_component_weight_below_80pct():
    result = _tct_reference_integrity(_integrity_selected(), _integrity_detail(active_weight_pct=70.0))
    assert result["complete"] is False
    assert result["undercovered_exact_keys"] == [["ACTION", "TCT", "FR-T2"]]


def test_tct_source_validation_distinguishes_t1_t2_and_never_promotes_t2_to_buy(tmp_path: Path):
    committee = tmp_path / "outputs" / "committee_master"
    committee.mkdir(parents=True)
    pd.DataFrame([
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "T2", "decision": "T2_CONFIRM_75_SHADOW",
            "source_fully_validated": True, "ci_source_eligible": False,
        },
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "T1", "decision": "T1_STARTER_25_SHADOW",
            "source_fully_validated": True, "ci_source_eligible": False,
        },
    ]).to_csv(committee / "V21_8_ENTRY_EXIT_CHALLENGER.csv", sep=";", index=False, encoding="utf-8-sig")
    selected = pd.DataFrame([
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "T2", "decision": "T2_CONFIRM_75_SHADOW",
            "score": 82.0, "source_fully_validated": True, "ci_source_eligible": False,
        },
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "T1", "decision": "T1_STARTER_25_SHADOW",
            "score": 81.0, "source_fully_validated": True, "ci_source_eligible": False,
        },
    ])
    out = _source_validation(tmp_path, selected)
    states = dict(zip(out["isin"], out["ci_final_status"]))
    assert states["T2"] == "TCT_T2_SOURCE_CONFIRMED"
    assert states["T1"] == "TCT_T1_SURVEILLANCE"
    assert "RECOMMANDATION_TOTALEMENT_VALIDEE" not in states.values()


def test_internal_tct_provenance_is_preserved_without_raw_ledger_lookup(monkeypatch, tmp_path: Path):
    detail = pd.DataFrame([
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "FR1", "criterion": "TCT_T2_BANDWIDTH_EXPANSION",
            "source_field": "t2_component_bandwidth_expansion", "source": "TCT_V24_1_7_EXACT_COMPONENTS",
            "source_url": None, "as_of": "2026-08-22T20:00:00+00:00", "evidence_level": "A_INTERNAL_GOVERNED",
            "validation_status": "T2_EXACT_COMPONENTS_AVAILABLE_WEIGHT_RENORMALIZED",
        },
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "FR1B", "criterion": "TCT_BASELINE_GATE_SQUEEZE",
            "source_field": "tct_baseline_component_squeeze", "source": "TCT_BASELINE_V24_1_8",
            "source_url": None, "as_of": "2026-08-22T20:00:00+00:00", "evidence_level": "A_INTERNAL_GOVERNED",
            "validation_status": "BASELINE_TOP20_AND_COVERAGE_PREREQUISITE_CONTEXT_ONLY",
        },
        {
            "asset_class": "ACTION", "horizon": "CT", "isin": "FR2", "criterion": "momentum",
            "source_field": "momentum", "source": None, "source_url": None, "as_of": None,
            "evidence_level": None, "validation_status": None,
        },
    ])

    def fake_attach(root: Path, frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out["source"] = "LEDGER_SOURCE"
        out["as_of"] = "2026-08-22T19:00:00+00:00"
        out["evidence_level"] = "A"
        out["validation_status"] = "VALID"
        return out

    from v182.reporting import committee_ci_explainability_v21_16 as module
    monkeypatch.setattr(module.legacy, "_attach_provenance", fake_attach)
    out = _attach_provenance_preserving_internal(tmp_path, detail)
    assert out[out["isin"] == "FR1"].iloc[0]["source"] == "TCT_V24_1_7_EXACT_COMPONENTS"
    assert out[out["isin"] == "FR1B"].iloc[0]["source"] == "TCT_BASELINE_V24_1_8"
    assert out[out["isin"] == "FR2"].iloc[0]["source"] == "LEDGER_SOURCE"
