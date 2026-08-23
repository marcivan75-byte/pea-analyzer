from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pandas as pd

from v182.reporting import etf_fund_flows_shadow_run as flow_runner
from v182.reporting.fund_flow_same_day_reuse import (
    frame_fingerprint,
    load_same_day_reuse,
    successful_snapshot_entries,
    write_same_day_reuse_marker,
)


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"instrument_id": "I0", "ticker": "T0", "name": "Zero"},
            {"instrument_id": "I1", "ticker": "T1", "name": "One"},
            {"instrument_id": "I2", "ticker": "T2", "name": "Two"},
        ]
    )


def _snapshot() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"instrument_id": "I0", "as_of": "2026-08-22", "source_priority": 50, "confidence": "C"},
            {"instrument_id": "I1", "as_of": "2026-08-22", "source_priority": 50, "confidence": "C"},
            {"instrument_id": "I2", "as_of": "2026-08-21", "source_priority": 50, "confidence": "C"},
        ]
    )


def test_frame_fingerprint_is_order_insensitive_for_same_content() -> None:
    frame = _universe()
    reordered = frame[["name", "ticker", "instrument_id"]].iloc[::-1].reset_index(drop=True)
    assert frame_fingerprint(frame) == frame_fingerprint(reordered)


def test_failed_instrument_is_never_marked_reusable() -> None:
    failures = pd.DataFrame([{"instrument_id": "I1", "stage": "YFINANCE", "reason": "TIMEOUT"}])
    entries = successful_snapshot_entries(_snapshot(), failures)
    assert {(entry["instrument_id"], entry["as_of"]) for entry in entries} == {
        ("I0", "2026-08-22"),
        ("I2", "2026-08-21"),
    }


def test_same_day_reuse_requires_matching_day_universe_official_and_history(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    universe = _universe()
    official = pd.DataFrame(
        [{"instrument_id": "I0", "as_of": "2026-08-22", "aum": 100.0, "source_priority": 100}]
    )
    failures = pd.DataFrame([{"instrument_id": "I1", "stage": "YFINANCE", "reason": "TIMEOUT"}])
    entries = successful_snapshot_entries(_snapshot(), failures)
    history_path = tmp_path / "state" / "etf_fund_flows" / "ETF_FUND_FLOW_OBSERVATIONS.csv"
    marker_path = tmp_path / "state" / "etf_fund_flows" / "ETF_FUND_FLOW_SAME_DAY_REUSE_V1.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    _snapshot().to_csv(history_path, sep=";", index=False, encoding="utf-8-sig")
    write_same_day_reuse_marker(marker_path, universe, official, entries, now=now)

    reusable_ids, reusable_entries, audit = load_same_day_reuse(
        marker_path,
        history_path,
        universe,
        official,
        enabled=True,
        now=now + timedelta(hours=1),
    )
    assert reusable_ids == {"I0", "I2"}
    assert {entry["instrument_id"] for entry in reusable_entries} == {"I0", "I2"}
    assert audit["reuse_status"] == "HIT"
    assert audit["reused_instruments"] == 2

    next_day_ids, _entries, next_day_audit = load_same_day_reuse(
        marker_path,
        history_path,
        universe,
        official,
        enabled=True,
        now=now + timedelta(days=1),
    )
    assert next_day_ids == set()
    assert next_day_audit["reuse_status"] == "MISS_DIFFERENT_DAY"

    changed_universe = pd.concat(
        [universe, pd.DataFrame([{"instrument_id": "I3", "ticker": "T3", "name": "Three"}])],
        ignore_index=True,
    )
    changed_ids, _entries, changed_audit = load_same_day_reuse(
        marker_path,
        history_path,
        changed_universe,
        official,
        enabled=True,
        now=now,
    )
    assert changed_ids == set()
    assert changed_audit["reuse_status"] == "MISS_UNIVERSE_CHANGED"

    changed_official = official.copy()
    changed_official.loc[0, "aum"] = 101.0
    changed_ids, _entries, changed_audit = load_same_day_reuse(
        marker_path,
        history_path,
        universe,
        changed_official,
        enabled=True,
        now=now,
    )
    assert changed_ids == set()
    assert changed_audit["reuse_status"] == "MISS_OFFICIAL_INPUT_CHANGED"


def test_history_marker_mismatch_recollects_only_missing_instrument(tmp_path: Path) -> None:
    now = datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc)
    universe = _universe()
    official = pd.DataFrame()
    entries = successful_snapshot_entries(_snapshot(), pd.DataFrame())
    history_path = tmp_path / "history.csv"
    marker_path = tmp_path / "marker.json"
    _snapshot().query("instrument_id != 'I2'").to_csv(history_path, sep=";", index=False, encoding="utf-8-sig")
    write_same_day_reuse_marker(marker_path, universe, official, entries, now=now)

    reusable_ids, reusable_entries, audit = load_same_day_reuse(
        marker_path,
        history_path,
        universe,
        official,
        enabled=True,
        now=now,
    )
    assert reusable_ids == {"I0", "I1"}
    assert {entry["instrument_id"] for entry in reusable_entries} == {"I0", "I1"}
    assert audit["reuse_status"] == "HIT"


def test_fund_flow_runner_and_config_wire_same_day_reuse() -> None:
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config" / "V18.2_MASTER_CONFIG.json").read_text(encoding="utf-8"))
    assert cfg["runtime_optimization"]["etf_fund_flows"]["reuse_previous_snapshot"] is True

    source = (root / "src" / "v182" / "reporting" / "etf_fund_flows_shadow_run.py").read_text(encoding="utf-8")
    assert "load_same_day_reuse" in source
    assert "successful_snapshot_entries" in source
    assert "write_same_day_reuse_marker" in source
    assert 'collection_metrics["mode"] = "SAME_DAY_FULL_REUSE"' in source
    assert 'collection_metrics["mode"] = "SAME_DAY_PARTIAL_REUSE"' in source
    assert 'runtime_opt.get("reuse_previous_snapshot", False)' in source


def test_reuse_marker_is_committed_before_downstream_flow_computation() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "v182" / "reporting" / "etf_fund_flows_shadow_run.py").read_text(encoding="utf-8")
    marker_write = source.index("write_same_day_reuse_marker(", source.index("def run("))
    downstream_compute = source.index("result = build_flow_computation(history, cfg)")
    assert marker_write < downstream_compute
    assert '"same_day_reuse_marker_written": marker_written' in source


def test_reuse_marker_declares_no_decision_logic_change(tmp_path: Path) -> None:
    marker = write_same_day_reuse_marker(
        tmp_path / "marker.json",
        _universe(),
        pd.DataFrame(),
        successful_snapshot_entries(_snapshot(), pd.DataFrame()),
        now=datetime(2026, 8, 22, 20, 0, tzinfo=timezone.utc),
    )
    assert marker["failed_instruments_are_never_reused"] is True
    assert marker["same_day_only"] is True
    assert marker["decision_logic_changed"] is False
