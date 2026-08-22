from __future__ import annotations

import json
import numpy as np
import pandas as pd

from v182.io.frames import is_missing
from v182.reporting.collection_audit import _missing_mask, write_collection_audit
from v182.reporting.runtime_telemetry import RuntimeTelemetry


def test_vectorized_missing_mask_matches_scalar_missing_policy() -> None:
    values=[
        None,
        np.nan,
        pd.NA,
        "",
        "   ",
        "MISSING",
        "unknown",
        "NON_OBSERVE",
        "NOT_LOADED",
        "NaN",
        "<NA>",
        "N/A",
        "NA",
        "NULL",
        "none",
        "0",
        0,
        1.5,
        True,
        [1,2],
    ]
    series=pd.Series(values,dtype=object)
    expected=pd.Series([is_missing(value) for value in values],dtype=bool)
    actual=_missing_mask(series).reset_index(drop=True)
    pd.testing.assert_series_equal(actual,expected)


def _small_frames() -> tuple[pd.DataFrame,pd.DataFrame]:
    actions=pd.DataFrame(
        {
            "isin":["FR0000000001","FR0000000002"],
            "name":["Action A","Action B"],
            "metric_a":["1","NA"],
            "metric_b":["","2"],
        }
    )
    etfs=pd.DataFrame(
        {
            "isin":["FR0010000001"],
            "name":["ETF A"],
            "metric_a":["3"],
        }
    )
    return actions,etfs


def test_github_production_intermediate_is_csv_but_final_remains_excel(tmp_path,monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS","true")
    monkeypatch.setenv("PEA_SLOW_SOURCE_MODE","LIVE")
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"missing_provenance.csv"))
    actions,etfs=_small_frames()

    intermediate=write_collection_audit(actions,etfs,"WAVE_04_ACTION_FUNDAMENTALS",tmp_path,write_excel=True)
    final=write_collection_audit(actions,etfs,"WAVE_99_FINAL",tmp_path,write_excel=True)

    assert intermediate.endswith(".csv")
    assert final.endswith(".xlsx")
    assert (tmp_path/"COLLECTION_AUDIT_WAVE_04_ACTION_FUNDAMENTALS.csv").exists()
    assert (tmp_path/"COLLECTION_AUDIT_WAVE_99_FINAL.xlsx").exists()
    assert (tmp_path/"COLLECTION_DATA_AVAILABILITY_LATEST.xlsx").exists()
    assert (tmp_path/"COLLECTION_AUDIT_HISTORY.csv").exists()
    assert __import__("os").environ["PEA_EFFECTIVE_INTERMEDIATE_AUDIT_FORMAT"] == "CSV"


def test_runtime_telemetry_reports_effective_compact_format(tmp_path,monkeypatch) -> None:
    monkeypatch.setenv("PEA_EFFECTIVE_INTERMEDIATE_AUDIT_FORMAT","CSV")
    telemetry=RuntimeTelemetry(tmp_path,run_id="TEST",profile="FULL")
    telemetry.finalize("SUCCESS",intermediate_collection_audit_format="XLSX")
    payload=json.loads(telemetry.json_path.read_text(encoding="utf-8"))
    assert payload["intermediate_collection_audit_format"] == "CSV"


def test_local_explicit_excel_behavior_is_preserved(tmp_path,monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS",raising=False)
    monkeypatch.delenv("PEA_EFFECTIVE_INTERMEDIATE_AUDIT_FORMAT",raising=False)
    monkeypatch.setenv("PEA_SLOW_SOURCE_MODE","LIVE")
    monkeypatch.setenv("PEA_PROVENANCE_PATH",str(tmp_path/"missing_provenance.csv"))
    actions,etfs=_small_frames()

    path=write_collection_audit(actions,etfs,"WAVE_04_ACTION_FUNDAMENTALS",tmp_path,write_excel=True)

    assert path.endswith(".xlsx")
    assert (tmp_path/"COLLECTION_AUDIT_WAVE_04_ACTION_FUNDAMENTALS.xlsx").exists()
