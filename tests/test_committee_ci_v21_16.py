from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting.committee_ci_explainability_v21_16 import (
    _attach_provenance_preserving_internal,
    _report_context,
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


def test_internal_tct_provenance_is_preserved_without_raw_ledger_lookup(monkeypatch, tmp_path: Path):
    detail = pd.DataFrame([
        {
            "asset_class": "ACTION", "horizon": "TCT", "isin": "FR1", "criterion": "TCT_BASELINE_SQUEEZE",
            "source_field": "tct_baseline_component_squeeze", "source": "TCT_BASELINE_V24_1_8",
            "source_url": None, "as_of": "2026-08-22T20:00:00+00:00", "evidence_level": "A_INTERNAL_GOVERNED",
            "validation_status": "ACTIVE_AVAILABLE_PILLARS_RENORMALIZED_TO_100_SETUP_EXCLUDED",
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
    tct = out[out["isin"] == "FR1"].iloc[0]
    ct = out[out["isin"] == "FR2"].iloc[0]
    assert tct["source"] == "TCT_BASELINE_V24_1_8"
    assert tct["evidence_level"] == "A_INTERNAL_GOVERNED"
    assert ct["source"] == "LEDGER_SOURCE"
