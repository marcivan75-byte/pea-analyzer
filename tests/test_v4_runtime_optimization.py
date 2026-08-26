from pathlib import Path

import pandas as pd

from scripts.audit_v4_runtime_optimization import _decision_fingerprint
from v182.reporting import ci_light_v4


def _write_outputs(root: Path, selected: list[dict], rejected: list[dict]) -> None:
    for relative, rows in ((ci_light_v4.OUTPUT, selected), (ci_light_v4.REJECTED, rejected)):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, sep=";", index=False, encoding="utf-8-sig")


def test_decision_fingerprint_is_order_independent_and_information_sensitive(tmp_path):
    a = {"isin": "A", "asset_class": "ACTION", "horizon": "CT", "CI_LIGHT_INCLUDED": True, "CI_LIGHT_REASON": "PASS"}
    b = {"isin": "B", "asset_class": "ETF", "horizon": "MT", "CI_LIGHT_INCLUDED": False, "CI_LIGHT_REASON": "WAIT"}
    _write_outputs(tmp_path, [a], [b])
    first, rows, fields = _decision_fingerprint(tmp_path)
    _write_outputs(tmp_path, [b], [a])
    second, rows_reordered, _ = _decision_fingerprint(tmp_path)
    assert first == second
    assert rows == rows_reordered == 2
    assert "CI_LIGHT_REASON" in fields

    b["CI_LIGHT_REASON"] = "REJECT"
    _write_outputs(tmp_path, [a], [b])
    changed, _, _ = _decision_fingerprint(tmp_path)
    assert changed != first
