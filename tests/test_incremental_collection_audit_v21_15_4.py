from __future__ import annotations

from pathlib import Path

import pandas as pd

from v182.reporting import collection_audit as base
from v182.reporting import incremental_collection_audit_v21_15_4 as incremental
from v182.reporting.incremental_collection_audit_v21_15_4 import IncrementalCollectionAuditor


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    actions = pd.DataFrame(
        {
            "isin": ["A1", "A2"],
            "name": ["A", "B"],
            "field_a": ["1", "2"],
            "field_b": ["x", None],
        }
    )
    etfs = pd.DataFrame(
        {
            "isin": ["E1", "E2"],
            "name": ["E", "F"],
            "field_e": ["1", "2"],
        }
    )
    return actions, etfs


def _original(output_root: Path):
    def run(actions, etfs, wave_id, *, failures=None, source_context=""):
        base.write_collection_audit(
            actions,
            etfs,
            wave_id,
            output_root,
            failures=failures,
            source_context=source_context,
            write_excel=False,
        )
    return run


def _isolate_provenance(monkeypatch) -> None:
    empty = lambda: pd.DataFrame()
    monkeypatch.setattr(base, "actual_sources_by_field", empty)
    monkeypatch.setattr(incremental, "actual_sources_by_field", empty)


def test_incremental_patch_recomputes_only_touched_field_and_final_is_exhaustive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEA_RUN_PROFILE", "DAILY_TACTICAL")
    _isolate_provenance(monkeypatch)
    base._reset_audit_cache_for_tests()
    actions, etfs = _frames()
    auditor = IncrementalCollectionAuditor(tmp_path)
    original = _original(tmp_path)

    auditor.audit(actions, etfs, "WAVE_00_INITIAL_STATE", failures=[], source_context="initial", original_audit=original)
    assert auditor.full_scans == 1

    actions2 = actions.copy()
    actions2.loc[1, "field_b"] = "y"
    auditor.note([{"universe": "ACTION", "isin": "A2", "field": "field_b", "value": "y"}])
    auditor.audit(actions2, etfs, "WAVE_03", failures=[], source_context="delta", original_audit=original)

    assert auditor.incremental_scans == 1
    assert auditor.fields_recomputed == 1
    latest = base._LAST_INVENTORY
    assert latest is not None
    row = latest[(latest["asset_class"] == "ACTION") & (latest["field"] == "field_b")].iloc[0]
    assert int(row["available_rows"]) == 2
    assert row["status"] == "AVAILABLE"

    auditor.audit(actions2, etfs, "WAVE_99_FINAL", failures=[], source_context="final", original_audit=original)
    assert auditor.full_scans == 2
    assert auditor.payload()["final_wave_exhaustive"] is True


def test_unchanged_wave_reuses_inventory_without_field_scan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEA_RUN_PROFILE", "DAILY_TACTICAL")
    _isolate_provenance(monkeypatch)
    base._reset_audit_cache_for_tests()
    actions, etfs = _frames()
    auditor = IncrementalCollectionAuditor(tmp_path)
    original = _original(tmp_path)

    auditor.audit(actions, etfs, "WAVE_00_INITIAL_STATE", failures=[], source_context="initial", original_audit=original)
    auditor.audit(actions, etfs, "WAVE_01_ACTION_OHLCV", failures=[], source_context="cache-only", original_audit=original)
    assert auditor.reused_scans == 1
    assert auditor.fields_recomputed == 0


def test_incremental_internal_failure_falls_back_to_full_scan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEA_RUN_PROFILE", "DAILY_TACTICAL")
    _isolate_provenance(monkeypatch)
    base._reset_audit_cache_for_tests()
    actions, etfs = _frames()
    auditor = IncrementalCollectionAuditor(tmp_path)
    original = _original(tmp_path)

    auditor.audit(actions, etfs, "WAVE_00_INITIAL_STATE", failures=[], source_context="initial", original_audit=original)
    auditor.note([{"universe": "ACTION", "isin": "A1", "field": "field_a", "value": "3"}])
    monkeypatch.setattr(auditor, "_patch_inventory", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced")))
    auditor.audit(actions, etfs, "WAVE_03", failures=[], source_context="forced", original_audit=original)
    assert auditor.fallback_full_scans == 1
