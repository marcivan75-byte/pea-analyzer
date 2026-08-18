from pathlib import Path

from v182.reporting import unified_runner


def test_unified_runtime_disables_legacy_virtual_execution_under_v21_8():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert '"status": "SKIPPED_GOVERNANCE"' in source
    assert "committee_performance_v21_4.run" not in source
    assert "legacy fixed-stop risk sizing" in source
    assert "performance_workbook" not in source


def test_unified_runtime_publishes_v21_8_state_and_zero_execution_authority():
    source = Path(unified_runner.__file__).read_text(encoding="utf-8")
    assert '"entry_exit_v21_8": "outputs/committee_master/V21_8_ENTRY_EXIT_CHALLENGER.csv"' in source
    assert '"entry_exit_v21_8_state": "state/provenance/V21_8_ENTRY_EXIT_STATE.csv"' in source
    assert '"live_orders_enabled": False' in source
    assert "No real orders are emitted." in source
